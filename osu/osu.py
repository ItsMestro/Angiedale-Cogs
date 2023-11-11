import asyncio
import logging
from abc import ABC
from datetime import datetime
from typing import ClassVar, Dict, List, Literal, Optional, Set, Union

import discord
from ossapi import GameMode
from ossapi import Mod as OsuMod
from ossapi import OssapiAsync
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.views import ConfirmView

from .converters import BeatMode
from .database import Database
from .fuwwy import Fuwwy, FuwwyBeatmapIDs
from .misc import Misc
from .osubeat import OsuBeat
from .scores import Scores
from .tracking import Tracking
from .user import User
from .utilities import OsuUrls, Utilities, del_message

log = logging.getLogger("red.angiedale.osu")


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass


class Osu(
    Database,
    User,
    Utilities,
    Scores,
    OsuBeat,
    Tracking,
    Misc,
    Fuwwy,
    commands.Cog,
    metaclass=CompositeMetaClass,
):
    """osu! commands.

    Link your account with `[p]osulink <username>`

    Any command with `standard` in their name can be
    replaced with any mode.

    These versions of modes also work most of the time:
    `std` `osu` `o`
    `t`
    `ctb` `fruits` `f`
    `m`
    """

    default_user_settings: ClassVar[dict[str, str | dict[str, bool | dict] | None]] = {
        "username": None,
        "user_id": None,
        "fuwwy_clan": {
            "member": False,
            "join_date": None,
            FuwwyBeatmapIDs.FULL.name: {},
            FuwwyBeatmapIDs.JACKS.name: {},
            FuwwyBeatmapIDs.STREAMS.name: {},
            FuwwyBeatmapIDs.LN.name: {},
            FuwwyBeatmapIDs.TECH.name: {},
        },
    }
    default_guild_settings: ClassVar[
        dict[str, int | bool | BeatMode | dict[str, dict | int | None]]
    ] = {
        "default_beat_time": 86400,
        "running_beat": False,
        "beat_mode": BeatMode.NORMAL.value,
        "beat_current": {
            "beatmap": {},
            "mode": None,
            "mods": [],
            "created_at": None,
            "ends": None,
            "channel": None,
            "message": None,
            "pinned": False,
        },
        "beat_last": {
            "beatmap": {},
            "mode": None,
            "mods": [],
            "ends": None,
        },
    }
    default_member_settings: ClassVar[dict[str, dict]] = {
        "beat_score": {},
    }
    default_global_settings: ClassVar[dict[str, dict[str, int | str]]] = {
        "tracking": {
            "osu": {},
            "taiko": {},
            "fruits": {},
            "mania": {},
        },
        "fuwwy_clan": {"score": 0, "map_version": "2007-09-16T00:00:00+0000", "role_id": None},
    }
    default_mongodb: ClassVar[dict[str, str | int | None]] = {
        "host": "localhost",
        "port": 27017,
        "username": None,
        "password": None,
    }

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.api: Optional[OssapiAsync] = None
        self.osubeat_maps: Dict[
            int, Dict[int, Dict[str, Union[datetime, List[Union[OsuMod, str]], GameMode]]]
        ] = {}

        self.osu_config: Config = Config.get_conf(
            self, identifier=1387000, cog_name="Osu", force_registration=True
        )

        self.osu_config.register_user(**self.default_user_settings)
        self.osu_config.register_member(**self.default_member_settings)
        self.osu_config.register_global(**self.default_global_settings)
        self.osu_config.register_guild(**self.default_guild_settings)
        self.osu_config.init_custom("mongodb", -1)
        self.osu_config.register_custom("mongodb", **self.default_mongodb)

        self._cache_task: asyncio.Task = asyncio.create_task(self.get_last_cache_date())
        self._init_task: asyncio.Task = asyncio.create_task(self.initialize())
        self.tracking_init_task: asyncio.Task = asyncio.create_task(self.initialize_tracking())
        self.osubeat_check_tasks: Set[Optional[asyncio.Task]] = set()
        self.osubeat_task: Optional[asyncio.Task] = None
        self.tracking_task: Optional[asyncio.Task] = None
        self.tracking_restart_task: Optional[asyncio.Task] = None

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        await self.osu_config.user_from_id(user_id).clear()

    async def cog_load(self) -> None:
        """Should be called straight after cog instantiation."""
        guilds = await self.osu_config.all_guilds()
        for g_id, g_data in guilds.items():
            if g_data["running_beat"]:
                mods: List[OsuMod] = []
                for mod_combo in g_data["beat_current"]["mods"]:
                    mods.append(OsuMod(mod_combo))

                try:
                    self.osubeat_maps[g_data["beat_current"]["beatmap"]["id"]]
                except KeyError:
                    self.osubeat_maps[g_data["beat_current"]["beatmap"]["id"]] = {}

                self.osubeat_maps[g_data["beat_current"]["beatmap"]["id"]][g_id] = {
                    "ends": datetime.strptime(
                        g_data["beat_current"]["ends"], "%Y-%m-%dT%H:%M:%S%z"
                    ),
                    "created_at": datetime.strptime(
                        g_data["beat_current"]["created_at"], "%Y-%m-%dT%H:%M:%S%z"
                    ),
                    "mods": mods,
                    "mode": GameMode(g_data["beat_current"]["mode"]),
                }

        self.osubeat_task: asyncio.Task = asyncio.create_task(self.check_osu_beat())

    async def initialize(self) -> None:
        await self.bot.wait_until_red_ready()

        await self.get_osu_api_object()

        await self.connect_to_mongo()

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name, api_tokens) -> None:
        if service_name == "osu":
            await self.get_osu_api_object(api_tokens)

    async def cog_check(self, ctx) -> bool:
        if ctx.command.parent is self.osu_dev:
            return True
        return self.db_connected

    def cog_unload(self) -> None:
        if self._init_task:
            self._init_task.cancel()
        if self.tracking_task:
            self.tracking_task.cancel()
        if self.tracking_init_task:
            self.tracking_init_task.cancel()
        if self.tracking_restart_task:
            self.tracking_restart_task.cancel()
        if self.osubeat_task:
            self.osubeat_task.cancel()
        if len(self.osubeat_check_tasks) < 0:
            for task in self.osubeat_check_tasks:
                if task:
                    task.cancel()
        if len(self.leaderboard_tasks) < 0:
            for task in self.leaderboard_tasks:
                if task:
                    task.cancel()
        if self.mongo_client:
            self.mongo_client.close()

    async def get_osu_api_object(self, api_tokens: Optional[Dict] = None) -> None:
        tokens = await self.bot.get_shared_api_tokens("osu") if api_tokens is None else api_tokens
        try:
            tokens.get("client_id")
            tokens.get("client_secret")
        except KeyError:
            return log.error(
                "Can't load osu! API object. Missing either client_id or client_secret."
            )

        self.api = OssapiAsync(tokens.get("client_id"), tokens.get("client_secret"))
        if not "dev" in self.bot.user.name:
            self.api.log.setLevel("WARNING")

    @commands.is_owner()
    @commands.group(hidden=True, name="osudev")
    async def osu_dev(self, ctx: commands.Context):
        """Osu cog configuration."""

    @commands.max_concurrency(1, commands.BucketType.default)
    @osu_dev.command(name="cred")
    async def _cred(self, ctx: commands.Context, username: str = None, password: str = None):
        """Set up MongoDB credentials"""
        await self.osu_config.custom("mongodb").username.set(username)
        await self.osu_config.custom("mongodb").password.set(password)

        await ctx.send("MongoDB credentials set.")
        await asyncio.sleep(0.5)
        message = await ctx.send("Now trying to connect...")

        client = await self.connect_to_mongo()

        if not client:
            return await message.edit(
                "Failed to connect. Please try again with valid credentials."
            )
        await message.delete()

    @commands.max_concurrency(1, commands.BucketType.user)
    @commands.command(name="osulink")
    async def osu_link(self, ctx: commands.Context, *, username: str):
        """Link your account with an osu! user profile.

        Username can either be your username or id.
        """

        data = await self.api.user(username)

        if not data:
            return await del_message(ctx, f"Could not find the user {username}.")

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f"Is this the correct profile?",
            icon_url=f"{OsuUrls.FLAG.value}{data.country_code}.png",
        )
        embed.set_thumbnail(url=data.avatar_url)
        embed.title = data.username
        embed.url = f"{OsuUrls.USER.value}{data.id}"

        if await self.bot.use_buttons():
            view = ConfirmView(ctx.author, timeout=30)
            view.message = await ctx.send(embed=embed, view=view)
            await view.wait()
            if not view.result:
                return await view.message.delete()
            embed_msg = view.message
        else:
            embed_msg = await ctx.send(embed=embed, ephemeral=True)
            start_adding_reactions(embed_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(embed_msg, ctx.author)
            try:
                await self.bot.wait_for("reaction_add", check=pred, timeout=20)
            except asyncio.TimeoutError:
                await embed_msg.clear_reactions()
                return await embed_msg.edit(
                    content="Took too long to respond. Try again.", embed=None, delete_after=10
                )
            if not pred.result:
                return await embed_msg.delete()

        await self.osu_config.user(ctx.author).username.set(data.username)
        await self.osu_config.user(ctx.author).user_id.set(data.id)
        await embed_msg.edit(
            content=f"{data.username} is successfully linked to your account!", embed=None
        )
