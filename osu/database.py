import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set, Union

import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
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
        """Set the default cache date on init."""
        try:
            with open(f'{cog_data_path(raw_name="Osu")}/cachedate') as f:
                last_cached = f.read()
            self.last_caching = datetime.strptime(last_cached, "%Y-%m-%dT%H:%M:%S%z")
        except FileNotFoundError:
            log.error("No 'cachedate' file found. Database can't be used without it.")

    async def extra_beatmap_info(self, beatmap: Beatmap) -> Optional[DatabaseBeatmap]:
        """Gathers and returns extra beatmap info.

        Uses database cache if possible."""
        # Needs testing
        if not self.db_connected:
            return

        if not self.last_caching:
            return

        map_data: Optional[dict] = await self.db.beatmaps.find_one({"_id": beatmap.id})
        if not map_data:
            await self.cache_beatmap(beatmap.id)
            map_data = await self.db.beatmaps.find_one({"_id": beatmap.id})

        map_data: DatabaseBeatmap = DatabaseBeatmap(map_data["data"])

        if beatmap.status.value == 1 and map_data.cachedate > beatmap.last_updated:
            return map_data
        elif beatmap.last_updated > map_data.cachedate or map_data.cachedate < self.last_caching:
            await self.cache_beatmap(beatmap.id, forced=True)
            return await self.db.beatmaps.find_one({"_id": beatmap.id})
        else:
            return map_data

    async def cache_beatmap(self, map_id: int, forced=False) -> None:
        """Creates and stores a beatmaps data in the database cache."""

        file_path = f"{self.map_path}/{map_id}.osu"

        if not os.path.exists(file_path) or forced:
            await self.download_osu_file(map_id)

        try:
            beatmap = parse_beatmap(file_path)
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
        """Filters out ranked and loved maps from score list and creates a task for adding to leaderboard."""
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
        """Takes a set of scores and adds it to the leaderboard."""
        dbcollection = self.db[f"leaderboard_{mode.value}"]

        for score in scores:
            entry = DatabaseLeaderboard(
                await dbcollection.find_one({"_id": score.beatmap.id}, {"beatmap": 1})
            )

            async def add_entry(score):
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

            if entry.id is None:
                await add_entry(score)
            elif score.beatmap.last_updated > datetime.now(timezone.utc):
                await add_entry(score)

            score_entry = DatabaseLeaderboard(
                await dbcollection.find_one(
                    {"_id": score.beatmap.id}, {f"leaderboard.{score.user_id}": 1}
                )
            )

            if len(score_entry.leaderboard) == 0:
                await self.push_to_leaderboard(score, dbcollection)
            elif score_entry.leaderboard[str(score.user_id)].score > score.score:
                await self.push_to_leaderboard(score, dbcollection)

    async def push_to_leaderboard(self, score: OsuScore, dbc):
        """Creates dict with needed entries and updates database."""
        return await dbc.update_one(
            {"_id": score.beatmap.id},
            {
                "$set": {
                    f"leaderboard.{score.user_id}": DatabaseScore(
                        score.user_id, score
                    ).flatten_to_dict()
                }
            },
            upsert=True,
        )

    async def get_unranked_leaderboard(
        self, map_id: int, mode: GameMode
    ) -> Optional[DatabaseLeaderboard]:
        dbc = self.db[f"leaderboard_{mode.value}"]
        leaderboard = await dbc.find_one({"_id": map_id})
        if leaderboard:
            return DatabaseLeaderboard(leaderboard)

    async def connect_to_mongo(self) -> Union[AsyncIOMotorClient, None]:
        self.db_connected = False

        if self.mongo_client is not None:
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
