from re import S
import discord
import logging
import aiohttp
import asyncio
from math import ceil
from typing import Optional, Union

from redbot.core import commands, Config
from redbot.core.utils.menus import menu

from redbot.core.bot import Red

from .tools import del_message, multipage, singlepage, togglepage, API, Helper
from .embeds import Embed

log = logging.getLogger("red.angiedale.osu")

class Osu(commands.Cog):
    """osu! commands.

    Link your account with `[p]osulink <username>`

    Any command with `standard` in their name can be
    replaced with any mode.

    These versions of modes also work:
    `std` `osu` `o` `s`
    `t`
    `ctb` `fruits` `f` `c`
    `m`
    """

    default_user_settings = {"username": None, "userid": None}

    def __init__(self, bot: Red):
        self.bot = bot
        super().__init__()
        self.api = API(bot)
        self.embed = Embed(bot)
        self.helper = Helper(bot)
        self.osuconfig: Config = Config.get_conf(self, 1387002, cog_name="Osu")
        self.osuconfig.register_user(**self.default_user_settings)

        self.bot: Red = bot

        self.task: Optional[asyncio.Task] = None
        self._ready_event: asyncio.Event = asyncio.Event()
        self._init_task: asyncio.Task = self.bot.loop.create_task(self.initialize())

    async def initialize(self) -> None:
        """Should be called straight after cog instantiation."""
        await self.bot.wait_until_ready()

        try:
            await self.api.get_osu_bearer_token()
        except Exception as error:
            log.exception("Failed to initialize osu cog:", exc_info=error)

        self._ready_event.set()

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name, api_tokens):
        if service_name == "osu":
            await self.api.get_osu_bearer_token(api_tokens)

    async def cog_before_invoke(self, ctx: commands.Context):
        await self._ready_event.wait()

    @commands.command()
    async def osulink(self, ctx, username: str):
        """Link your account with an osu! user profile."""

        data = await self.api.fetch_api(ctx, f"users/{username}")

        if data:
            username = data["username"]
            user_id = data["id"]
            await self.osuconfig.user(ctx.author).username.set(username)
            await self.osuconfig.user(ctx.author).userid.set(user_id)
            await ctx.send(f"{username} is successfully linked to your account!")
        else:
            await del_message(ctx, f"Could not find the user {username}.")

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def map(self, ctx, beatmap: str):
        """Get info about a osu! map."""

        mapid = self.helper.map(beatmap)

        if mapid:
            data = await self.api.fetch_api(ctx, f'beatmaps/{mapid}')

            if data:
                embeds = await self.embed.map(ctx, data)
                await menu(ctx, embeds, singlepage())
            else:
                await del_message(ctx, "Cant find the map specified")
        else:
            await del_message(ctx, f"That doesn't seem to be a valid map.")

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osunews(self, ctx):
        """Shows the news from the osu! front page."""

        data = await self.api.fetch_api(ctx, "news")
        if data:
            embeds = await self.embed.news(ctx, data)
            await menu(ctx, embeds, multipage(embeds))

    @commands.command(aliases=["osucl"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osuchangelog(self, ctx, release_stream = "stable"):
        """Gets the changelog for different parts of osu!.
        
        Supported Release Streams:
        `stable`
        `fallback`
        `beta`
        `cuttingedge`
        `lazer`
        `web`"""

        stream = self.helper.stream(release_stream)
        
        if stream:
            params = {"stream": stream}
            data = await self.api.fetch_api(ctx, "changelog", params=params)

            if data:
                embeds = await self.embed.changelog(ctx, data)
                await menu(ctx, embeds, multipage(embeds))
        else:
            await del_message(ctx, f"Please provide a valid release stream.")

    @commands.command(aliases=["osur"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osurankings(self, ctx, *arguments):
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

        mode, type, country, variant = self.helper.ranking(arguments)
        
        if mode:
            if country:
                country = (country if len(country) == 2 else False)

            if type == "score" and country or type == "score" and variant:
                await del_message(ctx, "Score can not be used with the `<variant>` or `<country>` arguments.")
            elif country == False:
                await del_message(ctx, f"Please use the 2 letter ISO code for countries.")
            else:
                params = {}
                if country:
                    params["country"] = country
                if variant:
                    params["variant"] = variant

                data = await self.api.fetch_api(ctx, f'rankings/{"/".join([mode, type])}', params=params)

                embeds = await self.embed.rankings(ctx, data, mode, type, country, variant)
                await menu(ctx, embeds, multipage(embeds))
        else:
            await del_message(ctx, "You seem to have used too many arguments.")

    @commands.command(aliases=["osuc", "oc"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osucompare(self, ctx, user = None):
        """Compare your or someone elses score with the last one sent in the channel.
        """

        userid = await self.helper.user(ctx, self.api, user)

        if userid:
            mapid, params = await self.helper.history(ctx)

            if mapid:
                data = await self.api.fetch_api(ctx, f"beatmaps/{mapid}/scores/users/{userid}", params=params)
                await asyncio.sleep(0.5)

                if not data:
                    data = await self.api.fetch_api(ctx, f"beatmaps/{mapid}/scores/users/{userid}")
                    await asyncio.sleep(0.5)

                mapdata = await self.api.fetch_api(ctx, f"beatmaps/{mapid}")

                if data and mapdata:
                    embeds = await self.embed.recent(ctx, [data["score"]], mapdata)
                    await menu(ctx, embeds, multipage(embeds))
                else:
                    if user:
                        await del_message(ctx, f"I cant find a play from that user on this map")
                    else:
                        await del_message(ctx, f"Looks like you don't have a score on that map.")
            else:
                await del_message(ctx, "Could not find any recently displayed maps in this channel.")

    @commands.command(aliases=["osus", "os"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osuscore(self, ctx, beatmap, user = None):
        """Get your or another users score for a specified map."""
        
        userid = await self.helper.user(ctx, self.api, user)

        if userid:
            mapid = self.helper.map(beatmap)

            if mapid:
                mapdata = await self.api.fetch_api(ctx, f"beatmaps/{mapid}")
                if mapdata:
                    await asyncio.sleep(0.5)
                    data = await self.api.fetch_api(ctx, f"beatmaps/{mapid}/scores/users/{userid}")

                    if data:
                        embeds = await self.embed.recent(ctx, [data["score"]], mapdata)
                        await menu(ctx, embeds, multipage(embeds))
                    else:
                        if user:
                            await del_message(ctx, f"Can't find any plays on that map by {user}.")
                        else:
                            await del_message(ctx, "Can't find any plays on that map by you.")
                else:
                    await del_message(ctx, "I can't find the map specified.")
            else:
                await del_message(ctx, f"That doesn't seem to be a valid map.")

    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.cooldown(10, 10, commands.BucketType.user)
    async def et(self, ctx, user: discord.Member = None):
        """Marcinho is ET"""

        author = ctx.message.author
        if not user:
            if ctx.guild.id == 571000112688660501:
                message = f"{author.mention} thinks <@243350573850558464> is ET"
            else:
                try:
                    marcinho = await ctx.guild.fetch_member(253588524652036096)
                    message = f"{author.mention} thinks {marcinho.mention} is ET"
                except discord.HTTPException:
                    message = "I dont know who you're trying to call ET"
        else:
            if author == user:
                message = "You cant call yourself et dum dum. <:KannaMad:755808378344701953>"
            elif self.bot.user == user:
                message = "Awww thank you <:KannaHeart:755808377946243213>"
            else:
                message = f"{author.mention} thinks {user.mention} is ET"

        await ctx.send(message)

    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.cooldown(10, 10, commands.BucketType.user)
    async def birthday(self, ctx, user: discord.Member = None):
        """It's Zayyken's birthday today"""

        author = ctx.message.author
        if not user:
            if ctx.guild.id == 571000112688660501:
                message = f"{author.mention} wishes <@201671692647399424> Happy Birthday!"
            else:
                message = "I dont know who you're trying to wish happy birthday"
        else:
            message = f"{author.mention} wishes {user.mention} Happy Birthday!"

        await ctx.send(message)

    @commands.command(aliases=["osu", "std"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def standard(self, ctx, user = None):
        """Get a players osu! profile."""

        userid = await self.helper.user(ctx, self.api, user)
            
        if userid:
            data = await self.api.fetch_api(ctx, f"users/{userid}/osu")
            

            if data:
                embeds = await self.embed.profile(ctx, data)
                await menu(ctx, embeds, togglepage(self.bot))
            elif user:
                await del_message(ctx, f"I can't seem to get {user}'s profile.")
            else:
                await del_message(ctx, "I can't seem to get your profile.")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def taiko(self, ctx, user = None):
        """Get a players osu! profile."""

        userid = await self.helper.user(ctx, self.api, user)
            
        if userid:
            data = await self.api.fetch_api(ctx, f"users/{userid}/taiko")

            if data:
                embeds = await self.embed.profile(ctx, data)
                await menu(ctx, embeds, togglepage(self.bot))
            elif user:
                await del_message(ctx, f"I can't seem to get {user}'s profile.")
            else:
                await del_message(ctx, "I can't seem to get your profile.")

    @commands.command(aliases=["catch", "ctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def fruits(self, ctx, user = None):
        """Get a players osu! profile."""

        userid = await self.helper.user(ctx, self.api, user)
            
        if userid:
            data = await self.api.fetch_api(ctx, f"users/{userid}/fruits")

            if data:
                embeds = await self.embed.profile(ctx, data)
                await menu(ctx, embeds, togglepage(self.bot))
            elif user:
                await del_message(ctx, f"I can't seem to get {user}'s profile.")
            else:
                await del_message(ctx, "I can't seem to get your profile.")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def mania(self, ctx, user = None):
        """Get a players osu! profile."""

        userid = await self.helper.user(ctx, self.api, user)
            
        if userid:
            data = await self.api.fetch_api(ctx, f"users/{userid}/mania")

            if data:
                embeds = await self.embed.profile(ctx, data)
                await menu(ctx, embeds, togglepage(self.bot))
            elif user:
                await del_message(ctx, f"I can't seem to get {user}'s profile.")
            else:
                await del_message(ctx, "I can't seem to get your profile.")

    @commands.command(aliases=["rsstd", "recentosu", "rsosu", "rsstandard", "recentstd", "rso"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentstandard(self, ctx, user = None):
        """Get a players recent osu! plays.
        
        Includes failed plays.
        """

        userid = await self.helper.user(ctx, self.api, user)

        if userid:
            params = {"include_fails": "1", "mode": "osu", "limit": "10"}

            data = await self.api.fetch_api(ctx, f"users/{userid}/scores/recent", params=params)

            if data:
                embeds = await self.embed.recent(ctx, data)
                await menu(ctx, embeds, multipage(embeds))
            elif user:
                await del_message(ctx, f"Looks like {user} don't have any recent plays in that mode.")
            else:
                await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["rst", "rstaiko", "recentt"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recenttaiko(self, ctx, user = None):
        """Get a players recent osu! plays.
        
        Includes failed plays.
        """

        userid = await self.helper.user(ctx, self.api, user)

        if userid:
            params = {"include_fails": "1", "mode": "taiko", "limit": "10"}

            data = await self.api.fetch_api(ctx, f"users/{userid}/scores/recent", params=params)

            if data:
                embeds = await self.embed.recent(ctx, data)
                await menu(ctx, embeds, multipage(embeds))
            elif user:
                await del_message(ctx, f"Looks like {user} don't have any recent plays in that mode.")
            else:
                await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["rsctb", "recentcatch", "recentctb", "rscatch", "rsfruits"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentfruits(self, ctx, user = None):
        """Get a players recent osu! plays.
        
        Includes failed plays.
        """

        userid = await self.helper.user(ctx, self.api, user)

        if userid:
            params = {"include_fails": "1", "mode": "fruits", "limit": "10"}

            data = await self.api.fetch_api(ctx, f"users/{userid}/scores/recent", params=params)

            if data:
                embeds = await self.embed.recent(ctx, data)
                await menu(ctx, embeds, multipage(embeds))
            elif user:
                await del_message(ctx, f"Looks like {user} don't have any recent plays in that mode.")
            else:
                await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["rsm", "recentm", "rsmania"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentmania(self, ctx, user = None):
        """Get a players recent osu! plays.
        
        Includes failed plays.
        """

        userid = await self.helper.user(ctx, self.api, user)

        if userid:
            params = {"include_fails": "1", "mode": "mania", "limit": "10"}

            data = await self.api.fetch_api(ctx, f"users/{userid}/scores/recent", params=params)

            if data:
                embeds = await self.embed.recent(ctx, data)
                await menu(ctx, embeds, multipage(embeds))
            elif user:
                await del_message(ctx, f"Looks like {user} don't have any recent plays in that mode.")
            else:
                await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    @commands.command(aliases=["topstd", "toposu"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topstandard(self, ctx, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        userid, recent, pos = await self.helper.top(ctx, self.api, user_or_args)

        if userid:
            params = {"mode": "osu", "limit": "50"}

            data1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

            if data1:
                params["offset"] =  "50"
                await asyncio.sleep(0.5)

                data2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                data = data1 + data2

                embeds = await self.embed.top(ctx, data, recent, pos)
                await menu(ctx, embeds, multipage(embeds))
            else:
                await del_message(ctx, f"I can't find any top plays for that user in this mode.")

    @commands.command(aliases=["topt"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def toptaiko(self, ctx, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        userid, recent, pos = await self.helper.top(ctx, self.api, user_or_args)

        if userid:
            params = {"mode": "taiko", "limit": "50"}

            data1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

            if data1:
                params["offset"] =  "50"
                await asyncio.sleep(0.5)

                data2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                data = data1 + data2

                embeds = await self.embed.top(ctx, data, recent, pos)
                await menu(ctx, embeds, multipage(embeds))
            else:
                await del_message(ctx, f"I can't find any top plays for that user in this mode.")

    @commands.command(aliases=["topcatch", "topctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topfruits(self, ctx, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        userid, recent, pos = await self.helper.top(ctx, self.api, user_or_args)

        if userid:
            params = {"mode": "fruits", "limit": "50"}

            data1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

            if data1:
                params["offset"] =  "50"
                await asyncio.sleep(0.5)

                data2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                data = data1 + data2

                embeds = await self.embed.top(ctx, data, recent, pos)
                await menu(ctx, embeds, multipage(embeds))
            else:
                await del_message(ctx, f"I can't find any top plays for that user in this mode.")

    @commands.command(aliases=["topm"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topmania(self, ctx, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        userid, recent, pos = await self.helper.top(ctx, self.api, user_or_args)

        if userid:
            params = {"mode": "mania", "limit": "50"}

            data1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

            if data1:
                params["offset"] =  "50"
                await asyncio.sleep(0.5)

                data2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                data = data1 + data2

                embeds = await self.embed.top(ctx, data, recent, pos)
                await menu(ctx, embeds, multipage(embeds))
            else:
                await del_message(ctx, f"I can't find any top plays for that user in this mode.")

    @commands.command(aliases=["ppstd", "pposu"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ppstandard(self, ctx, *user_or_args):
        """Shows pp info for osu!.
        
        **Arguments:**
        
        - `-pp <number>` will display how many scores you have above `<number>`"""

        userid, pp = await self.helper.pp(ctx, self.api, user_or_args)

        if userid:
            params = {"mode": "osu", "limit": "50"}

            data1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

            if data1:
                params["offset"] =  "50"
                await asyncio.sleep(0.5)

                data2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                data = data1 + data2

                embeds = await self.embed.pp(ctx, data, pp)
                await menu(ctx, embeds, singlepage())
            else:
                await del_message(ctx, f"There isn't enough plays by this user to use this command.")

    @commands.command(aliases=["ppt"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pptaiko(self, ctx, *user_or_args):
        """Shows pp info for osu!.
        
        **Arguments:**
        
        - `-pp <number>` will display how many scores you have above `<number>`"""
        
        userid, pp = await self.helper.pp(ctx, self.api, user_or_args)

        if userid:
            params = {"mode": "taiko", "limit": "50"}

            data1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

            if data1:
                params["offset"] =  "50"
                await asyncio.sleep(0.5)

                data2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                data = data1 + data2

                embeds = await self.embed.pp(ctx, data, pp)
                await menu(ctx, embeds, singlepage())
            else:
                await del_message(ctx, f"There isn't enough plays by this user to use this command.")

    @commands.command(aliases=["ppf", "ppc", "ppcatch", "ppctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ppfruits(self, ctx, *user_or_args):
        """Shows pp info for osu!.
        
        **Arguments:**
        
        - `-pp <number>` will display how many scores you have above `<number>`"""
        
        userid, pp = await self.helper.pp(ctx, self.api, user_or_args)

        if userid:
            params = {"mode": "fruits", "limit": "50"}

            data1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

            if data1:
                params["offset"] =  "50"
                await asyncio.sleep(0.5)

                data2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                data = data1 + data2

                embeds = await self.embed.pp(ctx, data, pp)
                await menu(ctx, embeds, singlepage())
            else:
                await del_message(ctx, f"There isn't enough plays by this user to use this command.")

    @commands.command(aliases=["ppm"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ppmania(self, ctx, *user_or_args):
        """Shows pp info for osu!.
        
        **Arguments:**
        
        - `-pp <number>` will display how many scores you have above `<number>`"""
        
        userid, pp = await self.helper.pp(ctx, self.api, user_or_args)

        if userid:
            params = {"mode": "mania", "limit": "50"}

            data1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

            if data1:
                params["offset"] =  "50"
                await asyncio.sleep(0.5)

                data2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                data = data1 + data2

                embeds = await self.embed.pp(ctx, data, pp)
                await menu(ctx, embeds, singlepage())
            else:
                await del_message(ctx, f"There isn't enough plays by this user to use this command.")

    @commands.command(aliases=["tcs", "tco", "tcstd", "tcosu", "topcompareosu", "topcomparestd"])
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def topcomparestandard(self, ctx, *user_or_args):
        """Returns a list of unique maps between you and another user.

        Requires to have your account linked with the bot.

        **Arguments:**
        
        - `-p <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        author = await self.osuconfig.user(ctx.author).userid()

        if author:
            userid, rank = await self.helper.topcompare(ctx, self.api, user_or_args)

            if rank:
                params = {"cursor[page]": ceil(rank / 50)}

                data = await self.api.fetch_api(ctx, f'rankings/osu/performance', params=params)
                userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

            if userid:
                params = {"mode": "osu", "limit": "50"}

                udata1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                if udata1:
                    adata1 = await self.api.fetch_api(ctx, f"users/{author}/scores/best", params=params)

                    if adata1:
                        params["offset"] =  "50"
                        adata2 = await self.api.fetch_api(ctx, f"users/{author}/scores/best", params=params)
                        adata = adata1 + adata2

                        udata2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)
                        udata = udata1 + udata2

                        embeds = await self.embed.topcompare(ctx, adata, udata)
                        if embeds:
                            await menu(ctx, embeds, multipage(embeds))
                        else:
                            await del_message(ctx, "Your top plays are surprisingly identical.")
                    else:
                        await del_message(ctx, "You don't seem to have any top plays in this mode.")
                else:
                    await del_message(ctx, "That user doesn't seem to have any top plays in this mode.")
        else:
            await del_message(ctx, f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`")

    @commands.command(aliases=["tct", "tct"], hidden=True)
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def topcomparetaiko(self, ctx, *user_or_args):
        """Returns a list of unique maps between you and another user.

        Requires to have your account linked with the bot.

        **Arguments:**
        
        - `-p <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        author = await self.osuconfig.user(ctx.author).userid()

        if author:
            userid, rank = await self.helper.topcompare(ctx, self.api, user_or_args)

            if rank:
                params = {"cursor[page]": ceil(rank / 50)}

                data = await self.api.fetch_api(ctx, f'rankings/taiko/performance', params=params)
                userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

            if userid:
                params = {"mode": "taiko", "limit": "50"}

                udata1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                if udata1:
                    adata1 = await self.api.fetch_api(ctx, f"users/{author}/scores/best", params=params)

                    if adata1:
                        params["offset"] =  "50"
                        adata2 = await self.api.fetch_api(ctx, f"users/{author}/scores/best", params=params)
                        adata = adata1 + adata2

                        udata2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)
                        udata = udata1 + udata2

                        embeds = await self.embed.topcompare(ctx, adata, udata)
                        if embeds:
                            await menu(ctx, embeds, multipage(embeds))
                        else:
                            await del_message(ctx, "Your top plays are surprisingly identical.")
                    else:
                        await del_message(ctx, "You don't seem to have any top plays in this mode.")
                else:
                    await del_message(ctx, "That user doesn't seem to have any top plays in this mode.")
        else:
            await del_message(ctx, f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`")

    @commands.command(aliases=["tcf", "tcc", "tcctb", "topcomparecatch", "topcomparectb", "tcfruits", "tccatch"], hidden=True)
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def topcomparefruits(self, ctx, *user_or_args):
        """Returns a list of unique maps between you and another user.

        Requires to have your account linked with the bot.

        **Arguments:**
        
        - `-p <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        author = await self.osuconfig.user(ctx.author).userid()

        if author:
            userid, rank = await self.helper.topcompare(ctx, self.api, user_or_args)

            if rank:
                params = {"cursor[page]": ceil(rank / 50)}

                data = await self.api.fetch_api(ctx, f'rankings/fruits/performance', params=params)
                userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

            if userid:
                params = {"mode": "fruits", "limit": "50"}

                udata1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                if udata1:
                    adata1 = await self.api.fetch_api(ctx, f"users/{author}/scores/best", params=params)

                    if adata1:
                        params["offset"] =  "50"
                        adata2 = await self.api.fetch_api(ctx, f"users/{author}/scores/best", params=params)
                        adata = adata1 + adata2

                        udata2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)
                        udata = udata1 + udata2

                        embeds = await self.embed.topcompare(ctx, adata, udata)
                        if embeds:
                            await menu(ctx, embeds, multipage(embeds))
                        else:
                            await del_message(ctx, "Your top plays are surprisingly identical.")
                    else:
                        await del_message(ctx, "You don't seem to have any top plays in this mode.")
                else:
                    await del_message(ctx, "That user doesn't seem to have any top plays in this mode.")
        else:
            await del_message(ctx, f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`")

    @commands.command(aliases=["tcm", "tcmania"], hidden=True)
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def topcomparemania(self, ctx, *user_or_args):
        """Returns a list of unique maps between you and another user.

        Requires to have your account linked with the bot.

        **Arguments:**
        
        - `-p <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        author = await self.osuconfig.user(ctx.author).userid()

        if author:
            userid, rank = await self.helper.topcompare(ctx, self.api, user_or_args)

            if rank:
                params = {"cursor[page]": ceil(rank / 50)}

                data = await self.api.fetch_api(ctx, f'rankings/mania/performance', params=params)
                userid = data["ranking"][(rank % 50) - 1]["user"]["id"]

            if userid:
                params = {"mode": "mania", "limit": "50"}

                udata1 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)

                if udata1:
                    adata1 = await self.api.fetch_api(ctx, f"users/{author}/scores/best", params=params)

                    if adata1:
                        params["offset"] =  "50"
                        adata2 = await self.api.fetch_api(ctx, f"users/{author}/scores/best", params=params)
                        adata = adata1 + adata2

                        udata2 = await self.api.fetch_api(ctx, f"users/{userid}/scores/best", params=params)
                        udata = udata1 + udata2

                        embeds = await self.embed.topcompare(ctx, adata, udata)
                        if embeds:
                            await menu(ctx, embeds, multipage(embeds))
                        else:
                            await del_message(ctx, "Your top plays are surprisingly identical.")
                    else:
                        await del_message(ctx, "You don't seem to have any top plays in this mode.")
                else:
                    await del_message(ctx, "That user doesn't seem to have any top plays in this mode.")
        else:
            await del_message(ctx, f"You need to have your account linked before using this command.\nYou can do so using `{ctx.clean_prefix}osulink <username>`")