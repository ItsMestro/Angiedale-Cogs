import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase
from ossapi import Beatmap, GameMode
from ossapi import Score as OsuScore
from ossapi.models import Grade as OsuGrade
from ossapi.models import RankStatus
from pymongo import errors as mongoerrors
from redbot.core.data_manager import cog_data_path

from .abc import MixinMeta
from .utils.beatmapparser import DatabaseBeatmap, parse_beatmap
from .utils.classes import DatabaseLeaderboard, DatabaseScore

log = logging.getLogger("red.angiedale.osu")


class Database(MixinMeta):
    """Handle osu data storage"""

    def __init__(self):
        self.map_path = Path(f'{cog_data_path(raw_name="Osu")}/db/maps')
        self.map_path.mkdir(parents=True, exist_ok=True)

        self.last_caching: Optional[datetime] = None
        self.db_connected = False
        self.mongo_client = None
        self.db: Optional[AsyncIOMotorDatabase] = None
        self.leaderboard_tasks: Set[Optional[asyncio.Task]] = set()

    async def get_last_cache_date(self) -> None:
        """Set the cache date for when the offline beatmap caching script
        was ran, from file.
        """
        try:
            with open(f'{cog_data_path(raw_name="Osu")}/cachedate') as f:
                last_cached = f.read()
            self.last_caching = datetime.strptime(last_cached, "%Y-%m-%dT%H:%M:%S%z")
        except FileNotFoundError:
            log.error("No 'cachedate' file found. Database can't be used without it.")

    async def extra_beatmap_info(self, beatmap: Beatmap) -> Optional[DatabaseBeatmap]:
        """Gathers and returns extra beatmap info that the API doesn't provide.

        Checks the database for a cached version of the beatmap first
        and makes sure the data is up to date before returning it.

        Otherwise downloads and caches the beatmap in database.

        This beatmap info is needed for some of the features I use that
        requires the hitobjects of a .osu file. It's also keeping all the .osu
        files for the future in case I ever decide to add pp calc features, which
        the wrappers for it usually need the .osu file.
        """
        if not self.db_connected:
            return

        if self.last_caching is None:
            return

        # Try to find the beatmap in the database
        map_data: Optional[dict] = await self.db.beatmaps.find_one({"_id": beatmap.id})
        if not map_data:  # If it's not cached. Cache it then find the new entry.
            await self.cache_beatmap(beatmap.id)
            map_data = await self.db.beatmaps.find_one({"_id": beatmap.id})

        map_data: DatabaseBeatmap = DatabaseBeatmap(map_data["data"])

        # If map is ranked and we cached after its rank date, return the map
        if beatmap.status == RankStatus.RANKED and map_data.cachedate > beatmap.last_updated:
            return map_data
        # If map was updated after we cached it
        # or our offline caching is newer than the stored cache(What?)
        # Re-cache then return
        elif beatmap.last_updated > map_data.cachedate or map_data.cachedate < self.last_caching:
            await self.cache_beatmap(beatmap.id, forced=True)
            map_data = await self.db.beatmaps.find_one({"_id": beatmap.id})
            return DatabaseBeatmap(map_data["data"])
        else:
            return map_data

    async def cache_beatmap(self, map_id: int, forced=False) -> None:
        """Caches a beatmaps data in the database
        and optionally downloads the .osu file if we don't already have it
        or are forced to.
        """

        file_path = f"{self.map_path}/{map_id}.osu"

        if not os.path.exists(file_path) or forced:
            await self.download_osu_file(map_id)

        try:
            beatmap = parse_beatmap(file_path)
        # TODO: If this becomes an issue I need to add proper exception handling for it.
        except Exception as e:
            return log.exception("There was an error parsing beatmap", exc_info=e)

        beatmap_entry = {"_id": map_id, "data": beatmap.flatten_to_dict()}

        await self.db.beatmaps.replace_one({"_id": map_id}, beatmap_entry, upsert=True)

    async def download_osu_file(self, map_id):
        """Downloads osu files for storing locally."""

        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://osu.ppy.sh/osu/{map_id}") as r:
                if r.status == 200:
                    with open(f"{self.map_path}/{map_id}.osu", "wb") as f:
                        f.write(await r.read())
                else:
                    log.warning(f"Failed to download .osu file to database with id {map_id}")

        await asyncio.sleep(0.2)

    def queue_leaderboard(self, data: List[OsuScore], mode: GameMode) -> None:
        """Filters out ranked and loved maps from score list
        and creates a task for adding to leaderboard.
        """
        filtered_data = []

        for score in data:
            if (
                score.beatmapset.status == RankStatus.RANKED
                or score.beatmapset.status == RankStatus.LOVED
                or score.rank == OsuGrade.F
            ):
                continue
            filtered_data.append(score)

        if len(filtered_data) == 0:
            return

        task = asyncio.create_task(self.add_to_leaderboard(filtered_data, mode))
        self.leaderboard_tasks.add(task)
        task.add_done_callback(self.leaderboard_tasks.discard)

    async def add_to_leaderboard(self, scores: List[OsuScore], mode: GameMode) -> None:
        """Takes a list of scores and adds it to the leaderboard.

        Also handles checking if the beatmap was updated since we started this
        leaderboard and wipes the scores if that's the case.
        """
        dbcollection = self.db[f"leaderboard_{mode.value}"]

        for score in scores:
            beatmap_entry = DatabaseLeaderboard(
                await dbcollection.find_one({"_id": score.beatmap.id}, {"beatmap": 1})
            )  # Try to get the stored beatmap of the score

            async def add_entry(score: OsuScore) -> None:
                """Add a new beatmap entry to our leaderboard."""
                new_entry = {"_id": score.beatmap.id}
                beatmap_data = await self.api.beatmap(score.beatmap.id)
                beatmapset_data = beatmap_data.beatmapset()
                await asyncio.sleep(0.2)
                new_entry["beatmap"] = {
                    "title": beatmapset_data.title,
                    "version": beatmap_data.version,
                    "artist": beatmapset_data.artist,
                    "last_updated": beatmapset_data.last_updated.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                new_entry["leaderboard"] = {}
                await dbcollection.replace_one({"_id": score.beatmap.id}, new_entry, upsert=True)

            # We don't have the beatmap stored
            if beatmap_entry.id is None:
                await add_entry(score)
            # The stored data is outdated
            elif score.beatmap.last_updated > beatmap_entry.last_updated:
                await add_entry(score)

            score_entry = DatabaseLeaderboard(
                await dbcollection.find_one(
                    {"_id": score.beatmap.id}, {f"leaderboard.{score.user_id}": 1}
                )
            )  # Try to get the stored score for this play

            try:
                # Leaderboard is empty
                if len(score_entry.leaderboard) == 0:
                    await self.push_to_leaderboard(score, dbcollection)
                # Score is improved
                elif score_entry.leaderboard[str(score.user_id)].score > score.score:
                    await self.push_to_leaderboard(score, dbcollection)
            except KeyError:  # Score is not on the leaderboard yet
                await self.push_to_leaderboard(score, dbcollection)

    async def push_to_leaderboard(self, score: OsuScore, dbcollection: AsyncIOMotorCollection):
        """Adds a score to the unranked leaderboards."""
        return await dbcollection.update_one(
            {"_id": score.beatmap.id},
            {
                "$set": {
                    f"leaderboard.{score.user_id}": DatabaseScore(score.user_id, score).to_dict()
                }
            },
            upsert=True,
        )

    async def get_unranked_leaderboard(
        self, map_id: int, mode: GameMode
    ) -> Optional[DatabaseLeaderboard]:
        """Get the unranked leaderboard for a beatmap."""
        dbcollection = self.db[f"leaderboard_{mode.value}"]
        leaderboard = await dbcollection.find_one({"_id": map_id})
        if leaderboard is not None:
            return DatabaseLeaderboard(leaderboard)

    async def connect_to_mongo(self) -> Optional[AsyncIOMotorClient]:
        self.db_connected = False

        if self.mongo_client is not None:  # Close client if there is one already
            self.mongo_client.close()

        config = await self.osu_config.custom("mongodb").all()
        try:
            self.mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(
                **{k: v for k, v in config.items()}
            )
            await self.mongo_client.server_info()
            self.db: AsyncIOMotorDatabase = self.mongo_client["angiedaleosu"]
            self.db_connected = True
        except (
            mongoerrors.ServerSelectionTimeoutError,
            mongoerrors.ConfigurationError,
            mongoerrors.OperationFailure,
        ) as error:
            log.exception(
                "Can't connect to the MongoDB server.\nFollow instructions on Git/online to install MongoDB.",
                exc_info=error,
            )
            self.mongo_client = None
            self.db = None
            self.db_connected = False
        return self.mongo_client
