import asyncio
import json
import logging
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from typing import Literal

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import humanize_timedelta
from redbot.core.utils.menus import menu, start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate

from .database import Database
from .embeds import Data, Embed
from .tools import (
    API,
    MODS_PRETTY,
    Helper,
    TimeConverter,
    del_message,
    multipage,
    singlepage,
    togglepage,
)
from .utils.custommenu import custom_menu, custompage

log = logging.getLogger("red.angiedale.osu")


class Osu(Database, Embed, Data, API, Helper, commands.Cog):
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

    default_user_settings = {
        "username": None,
        "userid": None,
    }
    default_guild_settings = {
        "default_beat_time": 86400,
        "running_beat": False,
        "beat_current": {
            "beatmap": {},
            "mode": None,
            "mods": [],
            "ends": None,
            "channel": None,
        },
        "beat_last": {
            "beatmap": {},
            "mode": None,
            "mods": [],
            "ends": None,
        },
    }
    default_member_settings = {
        "beat_score": {},
    }
    default_global_settings = {
        "tracking": {
            "osu": {},
            "taiko": {},
            "fruits": {},
            "mania": {},
        },
    }
    default_mongodb = {
        "host": "localhost",
        "port": 27017,
        "username": None,
        "password": None,
    }

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.tracking_cache = []
        self.osubeat_maps = {}

        self.osuconfig: Config = Config.get_conf(
            self, identifier=1387000, cog_name="Osu", force_registration=True
        )
        self.osuconfig.register_user(**self.default_user_settings)
        self.osuconfig.register_member(**self.default_member_settings)
        self.osuconfig.register_global(**self.default_global_settings)
        self.osuconfig.register_guild(**self.default_guild_settings)
        self.osuconfig.init_custom("mongodb", -1)
        self.osuconfig.register_custom("mongodb", **self.default_mongodb)

        self._init_task: asyncio.Task = self.bot.loop.create_task(self.initialize())
        self.tracking_task: asyncio.Task = self.bot.loop.create_task(self.update_tracking())
        self.cache_task: asyncio.Task = self.bot.loop.create_task(self.get_last_cache_date())
        self.osubeat_task: asyncio.Task = None

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):

        await self.osuconfig.user_from_id(user_id)

    async def initialize(self) -> None:
        """Should be called straight after cog instantiation."""

        await self.bot.wait_until_ready()

        try:
            await self.get_osu_bearer_token()
        except Exception as error:
            log.exception("Failed to initialize osu cog:", exc_info=error)

        guilds = await self.osuconfig.all_guilds()
        for g_id, g_data in guilds.items():
            if g_data["running_beat"]:
                self.osubeat_maps[g_data["beat_current"]["beatmap"]["mapid"]] = {
                    g_id: {
                        "ends": g_data["beat_current"]["ends"],
                        "mods": g_data["beat_current"]["mods"],
                        "mode": g_data["beat_current"]["mode"],
                    }
                }

        self.osubeat_task: asyncio.Task = self.bot.loop.create_task(self.checkosubeat())

        await self._connect_to_mongo()

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name, api_tokens):
        if service_name == "osu":
            await self.get_osu_bearer_token(api_tokens)

    async def cog_check(self, ctx):
        if ctx.command.parent is self.osudev:
            return True
        return self._db_connected

    def cog_unload(self):
        self.tracking_task.cancel()
        if self.osubeat_task:
            self.osubeat_task.cancel()
        if self.mongoclient:
            self.mongoclient.close()

    def addguildtoosubeat(self, guildid, mapid, enddate, mods, mode):
        self.osubeat_maps[mapid] = {guildid: {"ends": enddate, "mods": mods, "mode": mode}}
        if self.osubeat_task.done():
            self.osubeat_task: asyncio.Task = self.bot.loop.create_task(self.checkosubeat())

    @checks.is_owner()
    @commands.group(hidden=True)
    async def osudev(self, ctx: commands.Context):
        """Osu cog configuration."""

    @osudev.command()
    async def cred(self, ctx: commands.Context, username: str = None, password: str = None):
        """Set up MongoDB credentials"""
        await self.osuconfig.custom("mongodb").username.set(username)
        await self.osuconfig.custom("mongodb").password.set(password)
        message = await ctx.send("MongoDB credentials set.\nNow trying to connect...")
        client = await self._connect_to_mongo()
        if not client:
            return await message.edit(
                content=message.content.replace("Now trying to connect...", "")
                + "Failed to connect. Please try again with valid credentials."
            )
        await message.edit(content=message.content.replace("Now trying to connect...", ""))

    @commands.command()
    async def osulink(self, ctx: commands.Context, *, username: str):
        """Link your account with an osu! user profile."""

        data = await self.fetch_api(f"users/{username}", ctx=ctx)

        if not data:
            return await del_message(ctx, f"Could not find the user {username}.")

        username = data["username"]
        userid = data["id"]
        await self.osuconfig.user(ctx.author).username.set(username)
        await self.osuconfig.user(ctx.author).userid.set(userid)
        await ctx.send(f"{username} is successfully linked to your account!")

    @checks.admin()
    @commands.guild_only()
    @commands.group()
    async def osutrack(self, ctx: commands.Context):
        """Top play tracking"""

    @osutrack.command()
    async def add(self, ctx, channel: discord.TextChannel, mode: str, *, username: str):
        """Track a players top scores.

        Only 1 mode per player and max 15 players in a server.
        """

        mode = mode.lower()
        if (
            mode == "osu"
            or mode == "standard"
            or mode == "std"
            or mode == "s"
            or mode == "o"
            or mode == "0"
        ):
            mode = "osu"
        elif mode == "taiko" or mode == "t" or mode == "1":
            mode = "taiko"
        elif (
            mode == "fruits"
            or mode == "catch"
            or mode == "ctb"
            or mode == "c"
            or mode == "f"
            or mode == "2"
        ):
            mode = "fruits"
        elif mode == "mania" or mode == "m" or mode == "3":
            mode = "mania"
        else:
            return await del_message(ctx, "Invalid mode")

        data = await self.fetch_api(f"users/{username}", ctx=ctx)

        if not data:
            return await del_message(ctx, f"Could not find the user {username}.")

        count = await self.counttracking(channel=channel)

        if not count <= 15:
            return await del_message(
                ctx,
                "Already tracking 15 users in this server. Please remove some before adding more.",
            )

        await self.removetracking(user=str(data["id"]), channel=channel, mode=mode)
        await self.refresh_tracking_cache()
        await ctx.maybe_send_embed(
            f'Now tracking top 100 plays for {data["username"]} in {channel.mention}'
        )

    @osutrack.command()
    async def remove(self, ctx: commands.Context, username: str):
        """Remove a tracked player."""

        data = await self.fetch_api(f"users/{username}", ctx=ctx)

        if not data:
            return await del_message(ctx, f"Could not find the user {username}.")

        count = await self.counttracking(user=str(data["id"]))

        if count == 0:
            return await del_message(
                ctx, f"{data['username']} isn't being tracked in this server."
            )

        await self.removetracking(user=str(data["id"]), channel=ctx.channel)
        await self.refresh_tracking_cache()
        await ctx.maybe_send_embed(f'Stopped tracking {data["username"]}')

    @osutrack.command()
    async def list(self, ctx: commands.Context):
        """Lists currently tracked users in this server."""

        count = await self.counttracking(guild=ctx.guild.id)

        if not len(count) >= 1:
            return await del_message(ctx, "Nobody is being tracked in this server.")

        count = sorted(count, key=lambda item: item["mode"])

        p = ""

        for t in count:
            p = f'{p}{t["id"]} ◈ {t["mode"]} ◈ {t["channel"].mention}\n'

        embeds = []
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_author(
            name=f"{len(count)} players are being tracked in this server.",
            icon_url=self.bot.user.avatar_url,
        )

        embed.description = p

        embeds.append(embed)
        await menu(ctx, embeds, singlepage())

    @checks.is_owner()
    @osutrack.command()
    async def dev(
        self, ctx: commands.Context, channel: discord.TextChannel, mode: str, *, username: str
    ):
        """Track a players top scores."""

        mode = mode.lower()
        if (
            mode == "osu"
            or mode == "standard"
            or mode == "std"
            or mode == "s"
            or mode == "o"
            or mode == "0"
        ):
            mode = "osu"
        elif mode == "taiko" or mode == "t" or mode == "1":
            mode = "taiko"
        elif (
            mode == "fruits"
            or mode == "catch"
            or mode == "ctb"
            or mode == "c"
            or mode == "f"
            or mode == "2"
        ):
            mode = "fruits"
        elif mode == "mania" or mode == "m" or mode == "3":
            mode = "mania"
        else:
            return await del_message(ctx, "Invalid mode")

        data = await self.fetch_api(f"users/{username}", ctx=ctx)

        if not data:
            return await del_message(ctx, f"Could not find the user {username}.")

        await self.removetracking(user=str(data["id"]), channel=channel, mode=mode, dev=True)
        await self.refresh_tracking_cache()
        await ctx.maybe_send_embed(
            f'Now tracking top 100 plays for {data["username"]} in {channel.mention}'
        )

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def map(self, ctx: commands.Context, beatmap: str):
        """Get info about a osu! map."""

        mapid = self.findmap(beatmap)

        if not mapid:
            return await del_message(ctx, f"That doesn't seem to be a valid map.")

        data = await self.fetch_api(f"beatmaps/{mapid}", ctx=ctx)

        if not data:
            return await del_message(ctx, "Cant find the map specified")

        embeds = await self.mapembed(ctx, data)
        await menu(ctx, embeds, singlepage())

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osunews(self, ctx: commands.Context):
        """Shows the news from the osu! front page."""

        data = await self.fetch_api("news", ctx=ctx)

        if data:
            embeds = await self.newsembed(ctx, data)
            await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["osucl"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osuchangelog(self, ctx: commands.Context, release_stream="stable"):
        """Gets the changelog for different parts of osu!.

        Supported Release Streams:
        `stable`
        `fallback`
        `beta`
        `cuttingedge`
        `lazer`
        `web`
        """

        stream = self.stream(release_stream)

        if not stream:
            return await del_message(ctx, f"Please provide a valid release stream.")

        params = {"stream": stream}
        data = await self.fetch_api("changelog", ctx=ctx, params=params)

        if data:
            embeds = await self.changelogembed(ctx, data)
            await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["osur"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osurankings(self, ctx: commands.Context, *arguments):
        """Show the top players from each leaderboard.

        Examples:
            - `[p]osurankings catch SE`
            - `[p]osur mania 4k`

        **Arguments:**

        - `<mode>` one of the 4 gamemodes. Only full names.
        - `<type>` Can only be `score`. Defaults to pp if not specified.
        - `<country>` a 2 character ISO country code to get that countries leaderboard. Does not work with `<type>`.
        - `<variant>` either 4k or 7k when `<mode>` is mania. Leave blank for global. Does not work with `<type>`.
        """

        mode, type, country, variant = self.ranking(arguments)

        if not mode:
            return await del_message(ctx, "You seem to have used too many arguments.")

        if country:
            country = country if len(country) == 2 else False

        if type == "score" and country or type == "score" and variant:
            return await del_message(
                ctx, "Score can not be used with the `<variant>` or `<country>` arguments."
            )

        if country == False:
            return await del_message(ctx, f"Please use the 2 letter ISO code for countries.")

        params = {}
        if country:
            params["country"] = country
        if variant:
            params["variant"] = variant

        data = await self.fetch_api(f'rankings/{"/".join([mode, type])}', ctx=ctx, params=params)

        embeds = await self.rankingsembed(ctx, data, mode, type, country, variant)
        await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["osuc", "oc"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osucompare(self, ctx: commands.Context, *, user=None):
        """Compare your or someone elses score with the last one sent in the channel."""

        userid = await self.user(ctx, user)

        if not userid:
            return

        mapid, params = await self.history(ctx)

        if not mapid:
            return await del_message(
                ctx, "Could not find any recently displayed maps in this channel."
            )

        data = await self.fetch_api(
            f"beatmaps/{mapid}/scores/users/{userid}", ctx=ctx, params=params
        )
        await asyncio.sleep(0.5)

        if not data:
            data = await self.fetch_api(f"beatmaps/{mapid}/scores/users/{userid}")
            await asyncio.sleep(0.5)

        if not data:
            if user:
                return await del_message(ctx, f"I cant find a play from that user on this map")
            else:
                return await del_message(ctx, f"Looks like you don't have a score on that map.")

        mapdata = await self.fetch_api(f"beatmaps/{mapid}")

        if data and mapdata:
            embeds = await self.recentembed(ctx, [data["score"]], page=0, mapdata=mapdata)
            return await menu(ctx, embeds, multipage(embeds))

        if user:
            await del_message(ctx, f"I cant find a play from that user on this map")
        else:
            await del_message(ctx, f"Looks like you don't have a score on that map.")

    @commands.command(aliases=["osus", "os"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osuscore(self, ctx: commands.Context, beatmap, *, user=None):
        """Get your or another users score for a specified map."""

        userid = await self.user(ctx, user)

        if not userid:
            return

        mapid = self.findmap(beatmap)

        if not mapid:
            return await del_message(ctx, f"That doesn't seem to be a valid map.")

        mapdata = await self.fetch_api(f"beatmaps/{mapid}", ctx=ctx)

        if not mapdata:
            return await del_message(ctx, "I can't find the map specified.")

        await asyncio.sleep(0.5)
        data = await self.fetch_api(f"beatmaps/{mapid}/scores/users/{userid}")

        if data:
            # embeds = [] # Solution for whenever peppy adds all mods in a single request
            # page = 0
            # for score in data["score"]:
            #     embed = await self.recentemb(ctx, [score], page=0, mapdata=mapdata)
            #     embeds = embeds + embed
            #     page += 1

            embeds = await self.recentembed(ctx, [data["score"]], page=0, mapdata=mapdata)
            return await menu(ctx, embeds, multipage(embeds))

        if user:
            await del_message(ctx, f"Can't find any plays on that map by {user}.")
        else:
            await del_message(ctx, "Can't find any plays on that map by you.")

    @commands.command(aliases=["osl", "osul"], usage="[beatmap] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osuleaderboard(self, ctx: commands.Context, *beatmap_or_args):
        """Unranked leaderboards for osu! maps.

        To submit scores, use the recent commands after having set scores on a map.
        You need to have your account linked for it to be submitted. Link yours with `[p]osulink`.
        Only works for maps that are not ranked or loved.

        **Arguments:**
        `-m <mode>` Choose the mode to display. Only needed for converts.
        `-me` Starts the embed at the page your score is if your account is linked.
        `-g` Show only scores by users in this guild that have linked accounts.
        """

        if not beatmap_or_args:
            return await ctx.send_help()

        beatmap, guildonly, findself, mode = await self.leaderboard(ctx, beatmap_or_args)

        if not beatmap:
            return

        beatmap = self.findmap(beatmap)

        if not beatmap:
            return await del_message(ctx, "No valid beatmap was provided.")

        mapdata = await self.fetch_api(f"beatmaps/{beatmap}", ctx=ctx)

        if not mapdata:
            return await del_message(ctx, "I can't find the map specified.")

        if (
            mapdata["beatmapset"]["status"] == "ranked"
            or mapdata["beatmapset"]["status"] == "loved"
        ):
            return await del_message(
                ctx, "Leaderboards aren't available for ranked and loved maps."
            )

        if not mode:
            mode = mapdata["mode"]

        storeddata = await self._get_leaderboard(beatmap, mode)

        if not storeddata or len(storeddata["leaderboard"]) == 0:
            return await del_message(
                ctx, "Nobody has set any plays on this map yet. Go ahead and be the first one!"
            )

        userid = await self.osuconfig.user(ctx.author).userid()

        embeds, page_start = await self.leaderboardembed(
            ctx, storeddata, mode, userid, guildonly, findself
        )

        if not embeds:
            return await del_message(
                "Nobody in this server with linked accounts have set scores on that map."
            )

        await menu(ctx, embeds, multipage(embeds), page=page_start)

    @commands.guild_only()
    @commands.group(aliases=["osub", "osb"])
    async def osubeat(self, ctx: commands.Context):
        """osu! competitions run per server."""

    @checks.admin()
    @osubeat.command(name="settime")
    async def _set_beat_time(self, ctx: commands.Context, *, time: TimeConverter = None):
        """Set the time that all future beats last.

        Examples:
            - `[p]osubeat settime 1 week 2 days`
            - `[p]osubeat settime 3d20hr`
        """

        if time:
            await self.osuconfig.guild(ctx.guild).default_beat_time.set(time.total_seconds())
            return await ctx.send(f"Beats will now last for {humanize_timedelta(time)}.")

        await self.osuconfig.guild(ctx.guild).default_beat_time.clear()
        await ctx.send("Default time for beats reset to 1 day.")

    @checks.admin()
    @osubeat.command(name="new")
    async def _new_beat(
        self, ctx: commands.Context, channel: discord.TextChannel, beatmap, mode, *mods
    ):
        """Run a new beat competition in this server.

        Users will sign up through the bot and submit scores with `[p]recent<mode>`
        The maps can be unranked and you're able to limit what mods are allowed

        The beat will last for 1 day by default and can be changed with `[p]osubeat settime`

        Examples:
            - `[p]osubeat new #osu 2929654 mania FM` - Run a mania beat on 2929654 with any mod allowed that is announced in #osu.
            - `[p]osubeat new #osu 378131 osu DTHD DT` - Run a standard beat on 378131 where DTHD or DT is allowed and is announced in #osu.
        """

        if await self.osuconfig.guild(ctx.guild).running_beat():
            return await del_message(
                ctx,
                (
                    f"There is already a beat competition running in this server.\n\n"
                    f"Either wait for it to end first or end it manually with `{ctx.clean_prefix}osubeat end`.\n\n"
                    f"If you don't want a winner picked you can cancel it with `{ctx.clean_prefix}osubeat cancel`."
                ),
                timeout=20,
            )

        if not mods:
            return await del_message(
                ctx,
                (
                    f"Please specify what mods should be allowed.\n\n"
                    f"To allow all mods use `FM`\n\n"
                    f"Valid mods can be found with `{ctx.clean_prefix}osubeat mods`"
                ),
                timeout=20,
            )

        clean_mods = await self.mod_parser(ctx, mods)

        if not clean_mods:
            return

        clean_beatmap = self.findmap(beatmap)
        clean_mode = self.mode_api(mode)

        if not clean_mode:
            return await del_message(ctx, f"{mode} is not a valid mode.")

        if not beatmap:
            return await del_message(ctx, "The beatmap provided isn't valid.")

        mapdata = await self.fetch_api(f"beatmaps/{clean_beatmap}", ctx=ctx)

        if not mapdata:
            return await del_message(ctx, "I can't find the map specified.")

        clean_mapdata = self.osubeatdata(mapdata)

        if not clean_mode == clean_mapdata["mode"] and not clean_mode == "osu":
            return await del_message(
                ctx,
                f'{mode} can\'t be used with {self.mode_prettify(clean_mapdata["mode"])} maps.',
            )

        time = (
            datetime.now(timezone.utc)
            + timedelta(seconds=await self.osuconfig.guild(ctx.guild).default_beat_time())
        ).replace(second=0)

        embed = await self.osubeatannounceembed(
            ctx, clean_mapdata, self.mode_prettify(clean_mode), clean_mods, time
        )

        can_react = ctx.channel.permissions_for(ctx.me).add_reactions
        embedmsg: discord.Message = await ctx.send(embed=embed)
        msg: discord.Message = await ctx.send(
            (
                f"This is a preview of how the embed will look when sent in {channel.mention}\n\n"
                "Are you sure you wish to start this beat? (yes/no)"
            )
        )
        if can_react:
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            event = "reaction_add"
        else:
            pred = MessagePredicate.yes_or_no(ctx)
            event = "message"
        try:
            await ctx.bot.wait_for(event, check=pred, timeout=30)
        except asyncio.TimeoutError:
            await msg.delete()
            await embedmsg.delete()
        if not pred.result:
            await embedmsg.delete()
            await msg.clear_reactions()
            return await msg.edit("Cancelled beat competition creation.")

        async with self.osuconfig.guild(ctx.guild).beat_current() as data:
            data["beatmap"] = clean_mapdata
            data["mode"] = clean_mode
            data["mods"] = clean_mods
            data["ends"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            data["channel"] = channel.id

        await self.osuconfig.clear_all_members(ctx.guild)

        await self.osuconfig.guild(ctx.guild).running_beat.set(True)

        await channel.send(embed=embed)

        await embedmsg.delete()
        await msg.delete()

        self.addguildtoosubeat(
            ctx.guild.id,
            clean_mapdata["mapid"],
            time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            clean_mods,
            clean_mode,
        )

    @osubeat.command(name="mods", hidden=True)
    async def _mods_beat(self, ctx: commands.Context):
        """Displays what mods can be used for beat competitions."""
        out = ""
        for modid, mod in MODS_PRETTY.items():
            out += f"{modid}: {mod}\n"

        out = f"```apache\n{out}```"
        await ctx.send(out)

    @checks.admin()
    @osubeat.command(name="end")
    async def _end_beat(self, ctx: commands.Context):
        """Manually end a beat early."""
        if not await self.osuconfig.guild(ctx.guild).running_beat():
            return await del_message(
                ctx,
                f"There is currently no beat running in this server. Start one with `{ctx.clean_prefix}osubeat new`.",
            )
        mapid = await self.osuconfig.guild(ctx.guild).beat_current()
        await self.endosubeat(ctx.guild.id, mapid["beatmap"]["mapid"])

    @checks.admin()
    @osubeat.command(name="cancel")
    async def _cancel_beat(self, ctx: commands.Context):
        """Cancel a running beat without selecting winners."""
        if not await self.osuconfig.guild(ctx.guild).running_beat():
            return await del_message(
                ctx,
                f"There is currently no beat running in this server. Start one with `{ctx.clean_prefix}osubeat new`.",
            )
        mapid = await self.osuconfig.guild(ctx.guild).beat_current()
        await self.cancelosubeat(ctx, ctx.guild.id, mapid["beatmap"]["mapid"])

    @osubeat.command(name="join")
    async def _join_beat(self, ctx: commands.Context, user: str):
        """Join a beat competition."""

        if not await self.osuconfig.guild(ctx.guild).running_beat():
            return await del_message(
                ctx,
                "There's currently no running beat competition in the server. Maybe encourage the server owner to start one!",
            )

        if await self.osuconfig.member(ctx.author).beat_score():
            return await del_message(ctx, "You're already signed up for this beat. Go play!")

        userid = await self.user(ctx, user)

        if not userid:
            return

        data = await self.fetch_api(f"users/{userid}/osu", ctx=ctx)

        if data:
            signups = await self.osuconfig.all_members(ctx.guild)
            for u in signups.values():
                if u["beat_score"]["userid"] == data["id"]:
                    return await del_message(
                        ctx, f'{data["username"]} is already signed up for this beat.'
                    )

            can_react = ctx.channel.permissions_for(ctx.me).add_reactions
            embedmsg = await ctx.send(embed=await self.osubeatsignup(ctx, data))
            if can_react:
                start_adding_reactions(embedmsg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(embedmsg, ctx.author)
                event = "reaction_add"
            else:
                pred = MessagePredicate.yes_or_no(ctx)
                event = "message"
            try:
                await ctx.bot.wait_for(event, check=pred, timeout=30)
            except asyncio.TimeoutError:
                return await embedmsg.delete()
            if not pred.result:
                await embedmsg.delete()
                return await ctx.send("Cancelled beat signup.")

            async with self.osuconfig.member(ctx.author).beat_score() as osubeat:
                osubeat["score"] = 0
                osubeat["userid"] = data["id"]

            await embedmsg.delete()
            return await ctx.send(
                f'Now signed up as {data["username"]}. Start playing the map and submit your scores with `{ctx.clean_prefix}recent<mode>`.'
            )

        await del_message(ctx, f"I can't seem to find {user}'s profile.")

    @osubeat.command(name="standings", aliases=["leaderboard"])
    async def _standings_beat(self, ctx: commands.Context):
        """Check the current standings in the beat competition."""

        beat_data = await self.osuconfig.guild(ctx.guild).all()
        members = await self.osuconfig.all_members(ctx.guild)
        if beat_data["running_beat"]:
            embeds = await self.osubeatstandingsembed(ctx, beat_data["beat_current"], members)
        elif beat_data["beat_last"]["beatmap"]:
            embeds = await self.osubeatstandingsembed(
                ctx, beat_data["beat_last"], members, last=True
            )
        else:
            return await del_message(
                ctx,
                "There hasn't been any beats run in this server yet. Maybe ask the server owner to host one?",
            )

        await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["osu", "std"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def standard(self, ctx: commands.Context, *, user=None):
        """Get a players osu! profile."""

        userid = await self.user(ctx, user)

        if not userid:
            return

        data = await self.fetch_api(f"users/{userid}/osu", ctx=ctx)

        if data:
            embeds = await self.profileembed(ctx, data, "osu")
            return await menu(ctx, embeds, togglepage(self.bot))

        if user:
            return await del_message(ctx, f"I can't seem to get {user}'s profile.")

        await del_message(ctx, "I can't seem to get your profile.")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def taiko(self, ctx: commands.Context, *, user=None):
        """Get a players osu! profile."""

        userid = await self.user(ctx, user)

        if not userid:
            return

        data = await self.fetch_api(f"users/{userid}/taiko", ctx=ctx)

        if data:
            embeds = await self.profileembed(ctx, data, "taiko")
            return await menu(ctx, embeds, togglepage(self.bot))

        if user:
            return await del_message(ctx, f"I can't seem to get {user}'s profile.")

        await del_message(ctx, "I can't seem to get your profile.")

    @commands.command(aliases=["catch", "ctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def fruits(self, ctx: commands.Context, *, user=None):
        """Get a players osu! profile."""

        userid = await self.user(ctx, user)

        if not userid:
            return

        data = await self.fetch_api(f"users/{userid}/fruits", ctx=ctx)

        if data:
            embeds = await self.profileembed(ctx, data, "fruits")
            return await menu(ctx, embeds, togglepage(self.bot))

        if user:
            return await del_message(ctx, f"I can't seem to get {user}'s profile.")

        await del_message(ctx, "I can't seem to get your profile.")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def mania(self, ctx: commands.Context, *, user=None):
        """Get a players osu! profile."""

        userid = await self.user(ctx, user)

        if not userid:
            return

        data = await self.fetch_api(f"users/{userid}/mania", ctx=ctx)

        if data:
            embeds = await self.profileembed(ctx, data, "mania")
            return await menu(ctx, embeds, togglepage(self.bot))

        if user:
            return await del_message(ctx, f"I can't seem to get {user}'s profile.")

        await del_message(ctx, "I can't seem to get your profile.")

    @commands.command(
        aliases=["rsstd", "recentosu", "rsosu", "rsstandard", "recentstd", "rso", "recento"]
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentstandard(self, ctx: commands.Context, *, user=None):
        """Get a players recent osu! plays.

        Includes failed plays.
        """

        userid = await self.user(ctx, user, withleaderboard=True)

        if not userid:
            return

        useleaderboard = False
        if isinstance(userid, tuple):
            useleaderboard = True
            userid = userid[0]

        params = {"include_fails": "1", "mode": "osu", "limit": "5"}

        data = await self.fetch_api(f"users/{userid}/scores/recent", ctx=ctx, params=params)

        if data:
            if self.osubeat_maps:
                await self.addtoosubeat(ctx, data)
            await custom_menu(
                ctx,
                await self.recentembed(ctx, data, page=0),
                custompage(self.bot, data),
                data=data,
                func=self.recentembed,
            )
            if useleaderboard:
                cleandata = self.leaderboarddata(data)
                await self.addtoleaderboard(cleandata, "osu")
            return

        if user:
            return await del_message(
                ctx, f"Looks like {user} don't have any recent plays in that mode."
            )

        await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["rst", "rstaiko", "recentt"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recenttaiko(self, ctx: commands.Context, *, user=None):
        """Get a players recent osu! plays.

        Includes failed plays.
        """

        userid = await self.user(ctx, user, withleaderboard=True)

        if not userid:
            return

        useleaderboard = False
        if isinstance(userid, tuple):
            useleaderboard = True
            userid = userid[0]

        params = {"include_fails": "1", "mode": "taiko", "limit": "5"}

        data = await self.fetch_api(f"users/{userid}/scores/recent", ctx=ctx, params=params)

        if data:
            if self.osubeat_maps:
                await self.addtoosubeat(ctx, data)
            await custom_menu(
                ctx,
                await self.recentembed(ctx, data, page=0),
                custompage(self.bot, data),
                data=data,
                func=self.recentembed,
            )
            if useleaderboard:
                cleandata = self.leaderboarddata(data)
                await self.addtoleaderboard(cleandata, "taiko")
            return

        if user:
            return await del_message(
                ctx, f"Looks like {user} don't have any recent plays in that mode."
            )

        await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(
        aliases=["rsctb", "recentcatch", "recentctb", "rscatch", "rsfruits", "recentf", "rsf"],
        hidden=True,
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentfruits(self, ctx: commands.Context, *, user=None):
        """Get a players recent osu! plays.

        Includes failed plays.
        """

        userid = await self.user(ctx, user, withleaderboard=True)

        if not userid:
            return

        useleaderboard = False
        if isinstance(userid, tuple):
            useleaderboard = True
            userid = userid[0]

        params = {"include_fails": "1", "mode": "fruits", "limit": "5"}

        data = await self.fetch_api(f"users/{userid}/scores/recent", ctx=ctx, params=params)

        if data:
            if self.osubeat_maps:
                await self.addtoosubeat(ctx, data)
            await custom_menu(
                ctx,
                await self.recentembed(ctx, data, page=0),
                custompage(self.bot, data),
                data=data,
                func=self.recentembed,
            )
            if useleaderboard:
                cleandata = self.leaderboarddata(data)
                await self.addtoleaderboard(cleandata, "fruits")
            return

        if user:
            return await del_message(
                ctx, f"Looks like {user} don't have any recent plays in that mode."
            )

        await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["rsm", "recentm", "rsmania"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentmania(self, ctx: commands.Context, *, user=None):
        """Get a players recent osu! plays.

        Includes failed plays.
        """

        userid = await self.user(ctx, user, withleaderboard=True)

        if not userid:
            return

        useleaderboard = False
        if isinstance(userid, tuple):
            useleaderboard = True
            userid = userid[0]

        params = {"include_fails": "1", "mode": "mania", "limit": "5"}

        data = await self.fetch_api(f"users/{userid}/scores/recent", ctx=ctx, params=params)

        if data:
            if self.osubeat_maps:
                await self.addtoosubeat(ctx, data)
            await custom_menu(
                ctx,
                await self.recentembed(ctx, data, page=0),
                custompage(self.bot, data),
                data=data,
                func=self.recentembed,
            )
            if useleaderboard:
                cleandata = self.leaderboarddata(data)
                await self.addtoleaderboard(cleandata, "mania")
            return
        if user:
            return await del_message(
                ctx, f"Looks like {user} don't have any recent plays in that mode."
            )

        await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["topstd", "toposu", "topo"], usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topstandard(self, ctx: commands.Context, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        userid, recent, pos = await self.top(ctx, user_or_args)

        if not userid:
            return

        params = {"mode": "osu", "limit": "50"}

        data1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not data1:
            return await del_message(
                ctx, f"I can't find any top plays for that user in this mode."
            )

        params["offset"] = "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.topembed(ctx, data, recent, pos)
        await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["topt"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def toptaiko(self, ctx: commands.Context, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        userid, recent, pos = await self.top(ctx, user_or_args)

        if not userid:
            return

        params = {"mode": "taiko", "limit": "50"}

        data1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not data1:
            return await del_message(
                ctx, f"I can't find any top plays for that user in this mode."
            )

        params["offset"] = "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.topembed(ctx, data, recent, pos)
        await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["topcatch", "topctb", "topf"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topfruits(self, ctx: commands.Context, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        userid, recent, pos = await self.top(ctx, user_or_args)

        if not userid:
            return

        params = {"mode": "fruits", "limit": "50"}

        data1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not data1:
            return await del_message(
                ctx, f"I can't find any top plays for that user in this mode."
            )

        params["offset"] = "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.topembed(ctx, data, recent, pos)
        await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["topm"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topmania(self, ctx: commands.Context, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        userid, recent, pos = await self.top(ctx, user_or_args)

        if not userid:
            return

        params = {"mode": "mania", "limit": "50"}

        data1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not data1:
            return await del_message(
                ctx, f"I can't find any top plays for that user in this mode."
            )

        params["offset"] = "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.topembed(ctx, data, recent, pos)
        await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["ppstd", "pposu", "ppo"], usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ppstandard(self, ctx: commands.Context, *user_or_args):
        """Shows pp info for osu!.

        **Arguments:**

        - `-pp <number>` will display how many scores you have above `<number>`"""

        userid, pp = await self.pp(ctx, user_or_args)

        if not userid:
            return

        params = {"mode": "osu", "limit": "50"}

        data1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not data1:
            return await del_message(
                ctx, f"There isn't enough plays by this user to use this command."
            )

        params["offset"] = "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.ppembed(ctx, data, pp)
        await menu(ctx, embeds, singlepage())

    @commands.command(aliases=["ppt"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pptaiko(self, ctx: commands.Context, *user_or_args):
        """Shows pp info for osu!.

        **Arguments:**

        - `-pp <number>` will display how many scores you have above `<number>`"""

        userid, pp = await self.pp(ctx, user_or_args)

        if not userid:
            return

        params = {"mode": "taiko", "limit": "50"}

        data1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not data1:
            return await del_message(
                ctx, f"There isn't enough plays by this user to use this command."
            )

        params["offset"] = "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.ppembed(ctx, data, pp)
        await menu(ctx, embeds, singlepage())

    @commands.command(aliases=["ppf", "ppcatch", "ppctb"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ppfruits(self, ctx: commands.Context, *user_or_args):
        """Shows pp info for osu!.

        **Arguments:**

        - `-pp <number>` will display how many scores you have above `<number>`"""

        userid, pp = await self.pp(ctx, user_or_args)

        if not userid:
            return

        params = {"mode": "fruits", "limit": "50"}

        data1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not data1:
            return await del_message(
                ctx, f"There isn't enough plays by this user to use this command."
            )

        params["offset"] = "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.ppembed(ctx, data, pp)
        await menu(ctx, embeds, singlepage())

    @commands.command(aliases=["ppm"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ppmania(self, ctx: commands.Context, *user_or_args):
        """Shows pp info for osu!.

        **Arguments:**

        - `-pp <number>` will display how many scores you have above `<number>`"""

        userid, pp = await self.pp(ctx, user_or_args)

        if not userid:
            return

        params = {"mode": "mania", "limit": "50"}

        data1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not data1:
            return await del_message(
                ctx, f"There isn't enough plays by this user to use this command."
            )

        params["offset"] = "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.ppembed(ctx, data, pp)
        await menu(ctx, embeds, singlepage())

    @commands.command(
        aliases=["tco", "tcstd", "tcosu", "topcompareosu", "topcomparestd", "topcompareo"],
        usage="[user] [args]",
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def topcomparestandard(self, ctx: commands.Context, *user_or_args):
        """Returns a list of unique maps between you and another user.

        Requires to have your account linked with the bot.

        **Arguments:**

        - `-p <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        if not user_or_args:
            return await ctx.send_help()

        author = await self.osuconfig.user(ctx.author).userid()

        if not author:
            return await del_message(
                ctx,
                f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`",
            )

        userid, rank = await self.topcompare(ctx, user_or_args)

        if rank:
            params = {"cursor[page]": ceil(rank / 50)}

            data = await self.fetch_api(f"rankings/osu/performance", params=params)
            userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

        if not userid:
            return

        params = {"mode": "osu", "limit": "50"}

        udata1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not udata1:
            return await del_message(
                ctx, "That user doesn't seem to have any top plays in this mode."
            )

        adata1 = await self.fetch_api(f"users/{author}/scores/best", params=params)

        if not adata1:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        params["offset"] = "50"
        adata2 = await self.fetch_api(f"users/{author}/scores/best", params=params)
        adata = adata1 + adata2

        udata2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)
        udata = udata1 + udata2

        embeds = await self.topcompareembed(ctx, adata, udata)

        if embeds:
            return await menu(ctx, embeds, multipage(embeds))

        await del_message(ctx, "Your top plays are surprisingly identical.")

    @commands.command(
        aliases=["tct", "tctaiko", "topcomparet"], hidden=True, usage="[user] [args]"
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def topcomparetaiko(self, ctx: commands.Context, *user_or_args):
        """Returns a list of unique maps between you and another user.

        Requires to have your account linked with the bot.

        **Arguments:**

        - `-p <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        if not user_or_args:
            return await ctx.send_help()

        author = await self.osuconfig.user(ctx.author).userid()

        if not author:
            return await del_message(
                ctx,
                f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`",
            )

        userid, rank = await self.topcompare(ctx, user_or_args)

        if rank:
            params = {"cursor[page]": ceil(rank / 50)}

            data = await self.fetch_api(f"rankings/taiko/performance", params=params)
            userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

        if not userid:
            return

        params = {"mode": "taiko", "limit": "50"}

        udata1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not udata1:
            return await del_message(
                ctx, "That user doesn't seem to have any top plays in this mode."
            )

        adata1 = await self.fetch_api(f"users/{author}/scores/best", params=params)

        if not adata1:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        params["offset"] = "50"
        adata2 = await self.fetch_api(f"users/{author}/scores/best", params=params)
        adata = adata1 + adata2

        udata2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)
        udata = udata1 + udata2

        embeds = await self.topcompareembed(ctx, adata, udata)

        if embeds:
            return await menu(ctx, embeds, multipage(embeds))

        await del_message(ctx, "Your top plays are surprisingly identical.")

    @commands.command(
        aliases=[
            "tcf",
            "tcctb",
            "topcomparecatch",
            "topcomparectb",
            "tcfruits",
            "tccatch",
            "topcomparef",
        ],
        hidden=True,
        usage="[user] [args]",
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def topcomparefruits(self, ctx: commands.Context, *user_or_args):
        """Returns a list of unique maps between you and another user.

        Requires to have your account linked with the bot.

        **Arguments:**

        - `-p <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        if not user_or_args:
            return await ctx.send_help()

        author = await self.osuconfig.user(ctx.author).userid()

        if not author:
            return await del_message(
                ctx,
                f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`",
            )

        userid, rank = await self.topcompare(ctx, user_or_args)

        if rank:
            params = {"cursor[page]": ceil(rank / 50)}

            data = await self.fetch_api(f"rankings/fruits/performance", params=params)
            userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

        if not userid:
            return

        params = {"mode": "fruits", "limit": "50"}

        udata1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not udata1:
            return await del_message(
                ctx, "That user doesn't seem to have any top plays in this mode."
            )

        adata1 = await self.fetch_api(f"users/{author}/scores/best", params=params)

        if not adata1:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        params["offset"] = "50"
        adata2 = await self.fetch_api(f"users/{author}/scores/best", params=params)
        adata = adata1 + adata2

        udata2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)
        udata = udata1 + udata2

        embeds = await self.topcompareembed(ctx, adata, udata)

        if embeds:
            return await menu(ctx, embeds, multipage(embeds))

        await del_message(ctx, "Your top plays are surprisingly identical.")

    @commands.command(
        aliases=["tcm", "tcmania", "topcomparem"], hidden=True, usage="[user] [args]"
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def topcomparemania(self, ctx: commands.Context, *user_or_args):
        """Returns a list of unique maps between you and another user.

        Requires to have your account linked with the bot.

        **Arguments:**

        - `-p <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        if not user_or_args:
            return await ctx.send_help()

        author = await self.osuconfig.user(ctx.author).userid()

        if not author:
            return await del_message(
                ctx,
                f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`",
            )

        userid, rank = await self.topcompare(ctx, user_or_args)

        if rank:
            params = {"cursor[page]": ceil(rank / 50)}

            data = await self.fetch_api(f"rankings/mania/performance", params=params)
            userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

        if not userid:
            return

        params = {"mode": "mania", "limit": "50"}

        udata1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if not udata1:
            return await del_message(
                ctx, "That user doesn't seem to have any top plays in this mode."
            )

        adata1 = await self.fetch_api(f"users/{author}/scores/best", params=params)

        if not adata1:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        params["offset"] = "50"
        adata2 = await self.fetch_api(f"users/{author}/scores/best", params=params)
        adata = adata1 + adata2

        udata2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)
        udata = udata1 + udata2

        embeds = await self.topcompareembed(ctx, adata, udata)

        if embeds:
            return await menu(ctx, embeds, multipage(embeds))

        await del_message(ctx, "Your top plays are surprisingly identical.")

    @commands.command()
    @checks.is_owner()
    async def debugtracking(self, ctx: commands.Context):
        await self.update_tracking(False)
        log.error("Manually debugging tracking.")

    async def update_tracking(self, a=True):
        """Checks for new top plays based on list of tracked users."""

        await self.bot.wait_until_ready()

        await self.refresh_tracking_cache()

        path = Path(f"{cog_data_path(self)}/tracking")
        path.mkdir(exist_ok=True)

        while True:
            try:
                await asyncio.sleep(60)

                modes = deepcopy(self.tracking_cache)

                for mode, users in modes.items():
                    for user, channels in users.items():
                        userdata = ""
                        userpath = f"{path}/{user}_{mode}.json"

                        params = {"mode": mode, "limit": "50"}
                        newdata = await self.fetch_api(f"users/{user}/scores/best", params=params)
                        if newdata:
                            params["offset"] = "50"
                            await asyncio.sleep(1)
                            newdata2 = await self.fetch_api(
                                f"users/{user}/scores/best", params=params
                            )
                            newdata = newdata + newdata2

                            newdata = self.topdata(newdata)

                            if not os.path.exists(userpath):
                                with open(userpath, "w+") as data:
                                    json.dump(newdata, data, indent=4)
                            elif a:
                                with open(userpath, "w+") as data:
                                    json.dump(newdata, data, indent=4)

                                await asyncio.sleep(15)
                            else:
                                try:
                                    with open(userpath) as data:
                                        userdata = json.load(data)
                                except FileNotFoundError:
                                    pass

                                if not userdata == newdata:
                                    with open(userpath, "w+") as data:
                                        json.dump(newdata, data, indent=4)

                                    badchannels = await self.trackingembed(
                                        channels, userdata, newdata
                                    )
                                    if len(badchannels) > 0:
                                        for bch in badchannels:
                                            await self.removetracking(channel=bch)
                                            await self.refresh_tracking_cache()
                                    await asyncio.sleep(15)

                            await asyncio.sleep(5)
                        else:
                            await self.removetracking(user=user, mode=mode)
                            await self.refresh_tracking_cache()
                a = False
            except asyncio.CancelledError:
                break
            except:
                log.error("Loop broke", exc_info=1)
                break

    async def refresh_tracking_cache(self):
        """Should be called after every config change to flush the cache with new data."""

        async with self.osuconfig.tracking() as t:
            self.tracking_cache = t

    async def addtoosubeat(self, ctx: commands.Context, data):
        """Finds plays that fit beat criteria and adds to leaderboard."""

        for beatmap in data:
            if not beatmap["beatmap"]["id"] in self.osubeat_maps:
                continue

            for g_id, mapdata in self.osubeat_maps[beatmap["beatmap"]["id"]].items():
                userbeatscore = await self.osuconfig.member_from_ids(
                    g_id, ctx.author.id
                ).beat_score()
                if not userbeatscore:
                    continue
                if (
                    not beatmap["user"]["id"] == userbeatscore["userid"]
                    or not mapdata["mode"] == beatmap["mode"]
                ):
                    continue
                if userbeatscore["score"] < beatmap["score"]:
                    if cleanscore := self.osubeatscoredata(
                        beatmap, self.osubeat_maps[beatmap["beatmap"]["id"]][g_id]["mods"]
                    ):
                        await self.osuconfig.member_from_ids(g_id, ctx.author.id).beat_score.set(
                            cleanscore
                        )

    async def checkosubeat(self):
        """Checks if any beat competitions should end."""

        await self.bot.wait_until_ready()

        while True:
            if not self.osubeat_maps:
                break
            osubeatlist = deepcopy(self.osubeat_maps)

            for mapid, g_ids in osubeatlist.items():
                for g_id, data in g_ids.items():
                    if datetime.strptime(data["ends"], "%Y-%m-%dT%H:%M:%S%z") <= datetime.now(
                        timezone.utc
                    ):
                        await self.endosubeat(g_id, mapid)
                        await asyncio.sleep(1)
                    await asyncio.sleep(1)

            await asyncio.sleep(50)

    async def endosubeat(self, guildid, mapid):
        """Handles ending beat compatitions and sends results."""

        if not mapid in self.osubeat_maps:
            return

        if not guildid in self.osubeat_maps[mapid]:
            return

        del self.osubeat_maps[mapid][guildid]

        guild: discord.Guild = self.bot.get_guild(guildid)
        if not guild:
            return await self.osuconfig.guild_from_id(guildid).clear()

        await self.osuconfig.guild(guild).running_beat.set(False)
        beat_current = await self.osuconfig.guild(guild).beat_current()

        channel: discord.TextChannel = guild.get_channel(beat_current["channel"])
        if not channel:
            del beat_current["channel"]
            await self.osuconfig.guild(guild).beat_current.clear()
            return await self.osuconfig.guild(guild).beat_last.set(beat_current)

        participants = await self.osuconfig.all_members(guild)

        embed = await self.osubeatwinnerembed(channel, beat_current, participants)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.NotFound):
            pass

        del beat_current["channel"]
        beat_current["ends"] = (
            datetime.now(timezone.utc).replace(second=0).strftime("%a %d %b %Y %H:%M:%S")
        )
        await self.osuconfig.guild(guild).beat_current.clear()
        await self.osuconfig.guild(guild).beat_last.set(beat_current)

        if len(self.osubeat_maps[mapid]) == 0:
            del self.osubeat_maps[mapid]

    async def cancelosubeat(self, ctx: commands.Context, guildid, mapid):
        """End a beat and announce it's cancellation."""

        if not mapid in self.osubeat_maps:
            return

        if not guildid in self.osubeat_maps[mapid]:
            return

        del self.osubeat_maps[mapid][guildid]

        await self.osuconfig.guild(ctx.guild).running_beat.set(False)
        beat_current = await self.osuconfig.guild(ctx.guild).beat_current()

        channel: discord.TextChannel = ctx.guild.get_channel(beat_current["channel"])
        if not channel:
            del beat_current["channel"]
            return await self.osuconfig.guild(ctx.guild).beat_current.clear()

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name="Beat competition cancelled manually. No winners will be picked.",
            icon_url=ctx.guild.icon_url,
        )

        embed.title = f'{beat_current["beatmap"]["artist"]} - {beat_current["beatmap"]["title"]} [{beat_current["beatmap"]["version"]}]'
        embed.url = beat_current["beatmap"]["mapurl"]
        embed.set_image(
            url=f'https://assets.ppy.sh/beatmaps/{beat_current["beatmap"]["setid"]}/covers/cover.jpg'
        )

        embed.set_footer(
            text=f'Competition was cancelled on {datetime.now(timezone.utc).replace(second=0).strftime("%a %d %b %Y %H:%M:%S")} UTC'
        )

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.NotFound):
            pass

        await self.osuconfig.guild(ctx.guild).beat_current.clear()

        if len(self.osubeat_maps[mapid]) == 0:
            del self.osubeat_maps[mapid]
