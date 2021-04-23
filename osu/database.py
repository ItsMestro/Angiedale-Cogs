import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import errors as mongoerrors
from redbot.core import Config
from redbot.core.data_manager import cog_data_path

from .utils import chunks

log = logging.getLogger("red.angiedale.osu.database")


class Database:
    """Handle osu data storage"""

    def __init__(self):
        self.mappath = Path(f'{cog_data_path(raw_name="Osu")}/db/maps')
        self.mappath.mkdir(parents=True, exist_ok=True)

        self.osuconfig: Config = Config.get_conf(self, identifier=1387000, cog_name="Osu")

        self.last_caching = None
        self._db_connected = False
        self.mongoclient = None
        self.db = None

    async def get_last_cache_date(self):
        """Set the default cache date on init."""
        with open(f'{cog_data_path(raw_name="Osu")}/cachedate') as f:
            wascached = f.read()
        self.last_caching = datetime.strptime(wascached, "%Y-%m-%dT%H:%M:%S%z")

    async def extra_beatmap_info(self, beatmap):
        """Gathers and returns extra beatmap info.

        Uses database cache if possible."""

        if not self._db_connected:
            return

        mapdata = await self.db.beatmaps.find_one({"_id": int(beatmap["mapid"])})
        if not mapdata:
            await self.cache_beatmap(beatmap["mapid"])
            mapdata = await self.db.beatmaps.find_one({"_id": int(beatmap["mapid"])})

        mapdata = mapdata["data"]

        cached_update = datetime.strptime(mapdata["Cached"], "%Y-%m-%dT%H:%M:%S%z")
        last_update = datetime.strptime(beatmap["updated"], "%Y-%m-%dT%H:%M:%S%z")

        if beatmap["status"] == "ranked" and cached_update > last_update:
            return mapdata
        elif last_update > cached_update or cached_update < self.last_caching:
            await self.cache_beatmap(beatmap["mapid"], forced=True)
            return await self.db.beatmaps.find_one({"_id": int(beatmap["mapid"])})
        else:
            return mapdata

    async def cache_beatmap(self, mapid, forced=False):
        """Creates and stores a beatmaps data in the database cache."""

        filepath = f"{self.mappath}/{mapid}.osu"

        if not os.path.exists(filepath) or forced:
            await self.download_osu_file(mapid)

        mapjson = chunks.parse_map_to_dict(filepath)
        mapjson = {"_id": int(mapid), "data": mapjson}

        await self.db.beatmaps.replace_one({"_id": int(mapid)}, mapjson, upsert=True)

    async def download_osu_file(self, mapid):
        """Downloads osu files for storing locally."""

        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://osu.ppy.sh/osu/{mapid}") as r:
                if r.status == 200:
                    with open(f"{self.mappath}/{mapid}.osu", "wb") as f:
                        f.write(await r.read())

        await asyncio.sleep(0.5)

    async def _connect_to_mongo(self):
        if self._db_connected:
            self._db_connected = False
        if self.mongoclient:
            self.mongoclient.close()
        config = await self.osuconfig.custom("mongodb").all()
        try:
            self.mongoclient = AsyncIOMotorClient(**{k: v for k, v in config.items()})
            await self.mongoclient.server_info()
            self.db = self.mongoclient["angiedaleosu"]
            self._db_connected = True
        except (
            mongoerrors.ServerSelectionTimeoutError,
            mongoerrors.ConfigurationError,
            mongoerrors.OperationFailure,
        ) as error:
            log.exception(
                "Can't connect to the MongoDB server.\nFollow instructions on Git/online to install MongoDB.",
                exc_info=error,
            )
            self.mongoclient = None
            self.db = None
            self._db_connected = False
        return self.mongoclient
