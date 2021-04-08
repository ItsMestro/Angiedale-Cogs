import asyncio
import json
import logging
import os
from asyncio.exceptions import CancelledError
from math import ceil
from typing import Literal, Optional

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path
from redbot.core.utils.menus import menu

from .embeds import Data, Embed
from .tools import API, Helper, del_message, multipage, singlepage, togglepage

log = logging.getLogger("red.angiedale.osu")


class Osu(Embed, Data, API, Helper, commands.Cog):
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

    default_user_settings = {"username": None, "userid": None}
    default_global_settings = {"tracking": {"osu": {}, "taiko": {}, "fruits": {}, "mania": {}}}

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.osuconfig: Config = Config.get_conf(self, identifier=1387000, cog_name="Osu", force_registration=True)
        self.osuconfig.register_user(**self.default_user_settings)
        self.osuconfig.register_global(**self.default_global_settings)

        self.task: Optional[asyncio.Task] = None
        self._ready_event: asyncio.Event = asyncio.Event()
        self._init_task: asyncio.Task = self.bot.loop.create_task(self.initialize())
        self.tracking_task: asyncio.Task = self.bot.loop.create_task(self.update_tracking())

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):

        await self.osuconfig.user_from_id(user_id)

    async def initialize(self) -> None:
        """Should be called straight after cog instantiation.
        """

        await self.bot.wait_until_ready()

        try:
            await self.get_osu_bearer_token()
        except Exception as error:
            log.exception("Failed to initialize osu cog:", exc_info=error)

        self._ready_event.set()

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name, api_tokens):
        if service_name == "osu":
            await self.get_osu_bearer_token(api_tokens)

    async def cog_before_invoke(self, ctx: commands.Context):
        await self._ready_event.wait()

    def cog_unload(self):
        self.tracking_task.cancel()

    @commands.command()
    async def osulink(self, ctx: commands.Context, *, username: str):
        """Link your account with an osu! user profile.
        """

        data = await self.fetch_api(f"users/{username}", ctx=ctx)

        if not data:
            return await del_message(ctx, f"Could not find the user {username}.")

        username = data["username"]
        userid = data["id"]
        await self.osuconfig.user(ctx.author).username.set(username)
        await self.osuconfig.user(ctx.author).userid.set(userid)
        await ctx.send(f"{username} is successfully linked to your account!")

    @checks.guildowner()
    @commands.guild_only()
    @commands.group()
    async def osutrack(self, ctx: commands.Context):
        """Top play tracking
        """

    @osutrack.command()
    async def add(self, ctx, channel: discord.TextChannel, mode: str, *, username: str):
        """Track a players top scores.
        
        Only 1 mode per player and max 15 players in a server.
        """

        mode = mode.lower()
        if mode == "osu" or mode == "standard" or mode == "std" or mode == "s" or mode == "o" or mode == "0":
            mode = "osu"
        elif mode == "taiko" or mode == "t" or mode == "1":
            mode = "taiko"
        elif mode == "fruits" or mode == "catch" or mode == "ctb" or mode == "c" or mode == "f" or mode == "2":
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
            return await del_message(ctx, "Already tracking 15 users in this server. Please remove some before adding more.")

        await self.removetracking(user=str(data["id"]), channel=channel, mode=mode)
        await ctx.maybe_send_embed(f'Now tracking top 100 plays for {data["username"]} in {channel.mention}')

    @osutrack.command()
    async def remove(self, ctx: commands.Context, username: str):
        """Remove a tracked player.
        """

        data = await self.fetch_api(f"users/{username}", ctx=ctx)

        if not data:
            return await del_message(ctx, f"Could not find the user {username}.")

        count = await self.counttracking(user=str(data["id"]))

        if count == 0:
            return await del_message(ctx, f"{data['username']} isn't being tracked in this server.")

        await self.removetracking(user=str(data["id"]), channel=ctx.channel)
        await ctx.maybe_send_embed(f'Stopped tracking {data["username"]}')

    @osutrack.command()
    async def list(self, ctx: commands.Context):
        """Lists currently tracked users in this server.
        """

        count = await self.counttracking(guild=ctx.guild.id)

        if not len(count) >= 1:
            return await del_message(ctx, "Nobody is being tracked in this server.")

        count = sorted(count, key=lambda item: item["mode"])

        p = ""

        for t in count:
            p = f'{p}{t["id"]} ◈ {t["mode"]} ◈ {t["channel"].mention}\n'

        embeds = []
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_author(name=f'{len(count)} players are being tracked in this server.', icon_url=self.bot.user.avatar_url)

        embed.description = p

        embeds.append(embed)
        await menu(ctx, embeds, singlepage())

    @checks.is_owner()
    @osutrack.command()
    async def dev(self, ctx: commands.Context, channel: discord.TextChannel, mode: str, *, username: str):
        """Track a players top scores.
        """

        mode = mode.lower()
        if mode == "osu" or mode == "standard" or mode == "std" or mode == "s" or mode == "o" or mode == "0":
            mode = "osu"
        elif mode == "taiko" or mode == "t" or mode == "1":
            mode = "taiko"
        elif mode == "fruits" or mode == "catch" or mode == "ctb" or mode == "c" or mode == "f" or mode == "2":
            mode = "fruits"
        elif mode == "mania" or mode == "m" or mode == "3":
            mode = "mania"
        else:
            return await del_message(ctx, "Invalid mode")

        data = await self.fetch_api(f"users/{username}", ctx=ctx)

        if not data:
            return await del_message(ctx, f"Could not find the user {username}.")

        await self.removetracking(user=str(data["id"]), channel=channel, mode=mode, dev=True)
        await ctx.maybe_send_embed(f'Now tracking top 100 plays for {data["username"]} in {channel.mention}')

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def map(self, ctx: commands.Context, beatmap: str):
        """Get info about a osu! map.
        """

        mapid = self.findmap(beatmap)

        if not mapid:
            return await del_message(ctx, f"That doesn't seem to be a valid map.")

        data = await self.fetch_api(f'beatmaps/{mapid}', ctx=ctx)

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
    async def osuchangelog(self, ctx: commands.Context, release_stream = "stable"):
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
            country = (country if len(country) == 2 else False)

        if type == "score" and country or type == "score" and variant:
            return await del_message(ctx, "Score can not be used with the `<variant>` or `<country>` arguments.")

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
    async def osucompare(self, ctx: commands.Context, *, user = None):
        """Compare your or someone elses score with the last one sent in the channel.
        """

        userid = await self.user(ctx, user)

        if not userid:
            return 

        mapid, params = await self.history(ctx)

        if not mapid:
            return await del_message(ctx, "Could not find any recently displayed maps in this channel.")

        data = await self.fetch_api(f"beatmaps/{mapid}/scores/users/{userid}", ctx=ctx, params=params)
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
            embeds = await self.recentembed(ctx, [data["score"]], mapdata)
            return await menu(ctx, embeds, multipage(embeds))

        if user:
            await del_message(ctx, f"I cant find a play from that user on this map")
        else:
            await del_message(ctx, f"Looks like you don't have a score on that map.")

    @commands.command(aliases=["osus", "os"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osuscore(self, ctx: commands.Context, beatmap, *, user = None):
        """Get your or another users score for a specified map.
        """
        
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
            embeds = await self.recentembed(ctx, [data["score"]], mapdata)
            return await menu(ctx, embeds, multipage(embeds))

        if user:
            await del_message(ctx, f"Can't find any plays on that map by {user}.")
        else:
            await del_message(ctx, "Can't find any plays on that map by you.")

    @commands.command(aliases=["osu", "std"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def standard(self, ctx: commands.Context, *, user = None):
        """Get a players osu! profile.
        """

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
    async def taiko(self, ctx: commands.Context, *, user = None):
        """Get a players osu! profile.
        """

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
    async def fruits(self, ctx: commands.Context, *, user = None):
        """Get a players osu! profile.
        """

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
    async def mania(self, ctx: commands.Context, *, user = None):
        """Get a players osu! profile.
        """

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

    @commands.command(aliases=["rsstd", "recentosu", "rsosu", "rsstandard", "recentstd", "rso", "recento"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentstandard(self, ctx: commands.Context, *, user = None):
        """Get a players recent osu! plays.
        
        Includes failed plays.
        """

        userid = await self.user(ctx, user)

        if not userid:
            return

        params = {"include_fails": "1", "mode": "osu", "limit": "10"}

        data = await self.fetch_api(f"users/{userid}/scores/recent", ctx=ctx, params=params)

        if data:
            embeds = await self.recentembed(ctx, data)
            return await menu(ctx, embeds, multipage(embeds))

        if user:
            return await del_message(ctx, f"Looks like {user} don't have any recent plays in that mode.")

        await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["rst", "rstaiko", "recentt"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recenttaiko(self, ctx: commands.Context, *, user = None):
        """Get a players recent osu! plays.
        
        Includes failed plays.
        """

        userid = await self.user(ctx, user)

        if not userid:
            return

        params = {"include_fails": "1", "mode": "taiko", "limit": "10"}

        data = await self.fetch_api(f"users/{userid}/scores/recent", ctx=ctx, params=params)

        if data:
            embeds = await self.recentembed(ctx, data)
            return await menu(ctx, embeds, multipage(embeds))

        if user:
            return await del_message(ctx, f"Looks like {user} don't have any recent plays in that mode.")
            
        await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["rsctb", "recentcatch", "recentctb", "rscatch", "rsfruits", "recentf", "rsf"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentfruits(self, ctx: commands.Context, *, user = None):
        """Get a players recent osu! plays.
        
        Includes failed plays.
        """

        userid = await self.user(ctx, user)

        if not userid:
            return

        params = {"include_fails": "1", "mode": "fruits", "limit": "10"}

        data = await self.fetch_api(f"users/{userid}/scores/recent", ctx=ctx, params=params)

        if data:
            embeds = await self.recentembed(ctx, data)
            return await menu(ctx, embeds, multipage(embeds))

        if user:
            return await del_message(ctx, f"Looks like {user} don't have any recent plays in that mode.")
            
        await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["rsm", "recentm", "rsmania"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentmania(self, ctx: commands.Context, *, user = None):
        """Get a players recent osu! plays.
        
        Includes failed plays.
        """

        userid = await self.user(ctx, user)

        if not userid:
            return

        params = {"include_fails": "1", "mode": "mania", "limit": "10"}

        data = await self.fetch_api(f"users/{userid}/scores/recent", ctx=ctx, params=params)

        if data:
            embeds = await self.recentembed(ctx, data)
            return await menu(ctx, embeds, multipage(embeds))

        if user:
            return await del_message(ctx, f"Looks like {user} don't have any recent plays in that mode.")
            
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
            return await del_message(ctx, f"I can't find any top plays for that user in this mode.")

        params["offset"] =  "50"
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
            return await del_message(ctx, f"I can't find any top plays for that user in this mode.")

        params["offset"] =  "50"
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
            return await del_message(ctx, f"I can't find any top plays for that user in this mode.")

        params["offset"] =  "50"
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
            return await del_message(ctx, f"I can't find any top plays for that user in this mode.")

        params["offset"] =  "50"
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
            return await del_message(ctx, f"There isn't enough plays by this user to use this command.")

        params["offset"] =  "50"
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
            return await del_message(ctx, f"There isn't enough plays by this user to use this command.")

        params["offset"] =  "50"
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
            return await del_message(ctx, f"There isn't enough plays by this user to use this command.")

        params["offset"] =  "50"
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
            return await del_message(ctx, f"There isn't enough plays by this user to use this command.")

        params["offset"] =  "50"
        await asyncio.sleep(0.5)

        data2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)

        data = data1 + data2

        embeds = await self.ppembed(ctx, data, pp)
        await menu(ctx, embeds, singlepage())

    @commands.command(aliases=["tco", "tcstd", "tcosu", "topcompareosu", "topcomparestd", "topcompareo"], usage="[user] [args]")
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
            return await del_message(ctx, f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`")

        userid, rank = await self.topcompare(ctx, user_or_args)

        if rank:
            params = {"cursor[page]": ceil(rank / 50)}

            data = await self.fetch_api(f'rankings/osu/performance', params=params)
            userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

        if not userid:
            return

        params = {"mode": "osu", "limit": "50"}

        udata1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if udata1:
            return await del_message(ctx, "That user doesn't seem to have any top plays in this mode.")

        adata1 = await self.fetch_api(f"users/{author}/scores/best", params=params)

        if not adata1:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        params["offset"] =  "50"
        adata2 = await self.fetch_api(f"users/{author}/scores/best", params=params)
        adata = adata1 + adata2

        udata2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)
        udata = udata1 + udata2

        embeds = await self.topcompareembed(ctx, adata, udata)

        if embeds:
            return await menu(ctx, embeds, multipage(embeds))

        await del_message(ctx, "Your top plays are surprisingly identical.")

    @commands.command(aliases=["tct", "tctaiko", "topcomparet"], hidden=True, usage="[user] [args]")
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
            return await del_message(ctx, f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`")

        userid, rank = await self.topcompare(ctx, user_or_args)

        if rank:
            params = {"cursor[page]": ceil(rank / 50)}

            data = await self.fetch_api(f'rankings/osu/performance', params=params)
            userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

        if not userid:
            return

        params = {"mode": "taiko", "limit": "50"}

        udata1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if udata1:
            return await del_message(ctx, "That user doesn't seem to have any top plays in this mode.")

        adata1 = await self.fetch_api(f"users/{author}/scores/best", params=params)

        if not adata1:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        params["offset"] =  "50"
        adata2 = await self.fetch_api(f"users/{author}/scores/best", params=params)
        adata = adata1 + adata2

        udata2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)
        udata = udata1 + udata2

        embeds = await self.topcompareembed(ctx, adata, udata)
        
        if embeds:
            return await menu(ctx, embeds, multipage(embeds))

        await del_message(ctx, "Your top plays are surprisingly identical.")

    @commands.command(aliases=["tcf", "tcctb", "topcomparecatch", "topcomparectb", "tcfruits", "tccatch", "topcomparef"], hidden=True, usage="[user] [args]")
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
            return await del_message(ctx, f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`")

        userid, rank = await self.topcompare(ctx, user_or_args)

        if rank:
            params = {"cursor[page]": ceil(rank / 50)}

            data = await self.fetch_api(f'rankings/osu/performance', params=params)
            userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

        if not userid:
            return

        params = {"mode": "fruits", "limit": "50"}

        udata1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if udata1:
            return await del_message(ctx, "That user doesn't seem to have any top plays in this mode.")

        adata1 = await self.fetch_api(f"users/{author}/scores/best", params=params)

        if not adata1:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        params["offset"] =  "50"
        adata2 = await self.fetch_api(f"users/{author}/scores/best", params=params)
        adata = adata1 + adata2

        udata2 = await self.fetch_api(f"users/{userid}/scores/best", params=params)
        udata = udata1 + udata2

        embeds = await self.topcompareembed(ctx, adata, udata)
        
        if embeds:
            return await menu(ctx, embeds, multipage(embeds))

        await del_message(ctx, "Your top plays are surprisingly identical.")

    @commands.command(aliases=["tcm", "tcmania", "topcomparem"], hidden=True, usage="[user] [args]")
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
            return await del_message(ctx, f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`")

        userid, rank = await self.topcompare(ctx, user_or_args)

        if rank:
            params = {"cursor[page]": ceil(rank / 50)}

            data = await self.fetch_api(f'rankings/osu/performance', params=params)
            userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

        if not userid:
            return

        params = {"mode": "mania", "limit": "50"}

        udata1 = await self.fetch_api(f"users/{userid}/scores/best", ctx=ctx, params=params)

        if udata1:
            return await del_message(ctx, "That user doesn't seem to have any top plays in this mode.")

        adata1 = await self.fetch_api(f"users/{author}/scores/best", params=params)

        if not adata1:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        params["offset"] =  "50"
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

    async def update_tracking(self, a = True):
        """Checks for new top plays based on list of tracked users.
        """

        await self.bot.wait_until_ready()
        log.error("Tracking waited until ready")

        while True:
            try:
                await asyncio.sleep(60)
                async with self.osuconfig.tracking() as t:
                    modes = t

                path = bundled_data_path(self)
                for mode, users in modes.items():
                    for user, channels in users.items():
                        userdata = ""
                        userpath = f'{path}/{user}{mode}.json'

                        params = {"mode": mode, "limit": "50"}
                        newdata = await self.fetch_api(f'users/{user}/scores/best', params=params)
                        if newdata:
                            params["offset"] =  "50"
                            await asyncio.sleep(1)
                            newdata2 = await self.fetch_api(f'users/{user}/scores/best', params=params)
                            newdata = newdata + newdata2

                            newdata = self.topdata(newdata)

                            if not os.path.exists(userpath):
                                f =  open(userpath, "x")
                                f.close()
                                with open(userpath, "w") as data:
                                    json.dump(newdata, data, indent=4)
                            elif a:
                                with open(userpath, "w") as data:
                                    json.dump(newdata, data, indent=4)

                                await asyncio.sleep(15)
                            else:
                                with open(userpath) as data:
                                    userdata = json.load(data)

                                if not userdata == newdata:
                                    with open(userpath, "w") as data:
                                        json.dump(newdata, data, indent=4)

                                    badchannels = await self.trackingembed(channels, userdata, newdata)
                                    if len(badchannels) > 0:
                                        for bch in badchannels:
                                            await self.removetracking(channel=bch)
                                    await asyncio.sleep(15)

                            await asyncio.sleep(5)
                        else:
                            await self.removetracking(user=user, mode=mode)
                a = False
            except CancelledError:
                break
            except:
                log.error("Loop broke", exc_info=1)
                break
