import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import aiohttp
from redbot.core.data_manager import cog_data_path

from .utils import chunks

log = logging.getLogger("red.angiedale.osu.database")


class Database:
    """Handle osu data storage"""

    def __init__(self):
        self.dbpath = f'{cog_data_path(raw_name="Osu")}/db'
        self.cachepath = Path(f"{self.dbpath}/cache")
        self.mappath = Path(f"{self.dbpath}/maps")
        self.cachepath.mkdir(parents=True, exist_ok=True)
        self.mappath.mkdir(parents=True, exist_ok=True)
        self.last_caching = datetime.strptime("2021-04-19T21:00:00+0000", "%Y-%m-%dT%H:%M:%S%z")

    async def extra_beatmap_info(self, beatmap):
        """Gathers and returns extra beatmap info.

        Uses cache if possible."""

        if not os.path.exists(f'{self.cachepath}/{beatmap["mapid"]}.json'):
            await self.cache_beatmap(beatmap["mapid"])

        with open(f'{self.cachepath}/{beatmap["mapid"]}.json') as f:
            cached_map = json.load(f)

        cached_update = datetime.strptime(cached_map["Cached"], "%Y-%m-%dT%H:%M:%S%z")
        last_update = datetime.strptime(beatmap["updated"], "%Y-%m-%dT%H:%M:%S%z")

        if beatmap["status"] == "ranked" and cached_update > last_update:
            return cached_map
        elif last_update > cached_update or cached_update < self.last_caching:
            await self.cache_beatmap(beatmap["mapid"], forced=True)
            with open(f'{self.cachepath}/{beatmap["mapid"]}.json') as f:
                return json.load(f)
        else:
            return cached_map

    async def cache_beatmap(self, mapid, forced=False):
        """Creates a json cache of a beatmaps data."""

        filepath = f"{self.mappath}/{mapid}.osu"

        if not os.path.exists(filepath) or forced:
            await self.download_osu_file(mapid)

        mapjson = chunks.parse_map_to_dict(filepath)

        with open(f"{self.cachepath}/{mapid}.json", "w+") as f:
            json.dump(mapjson, f, indent=4)

    async def download_osu_file(self, mapid):
        """Downloads osu files for storing locally."""

        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://osu.ppy.sh/osu/{mapid}") as r:
                if r.status == 200:
                    with open(f"{self.mappath}/{mapid}.osu", "wb") as f:
                        f.write(await r.read())

        await asyncio.sleep(0.5)
