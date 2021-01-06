import discord
import logging
import json
import aiohttp
import asyncio
import re
from math import ceil
from datetime import datetime
from typing import Optional, List, Dict

from redbot.core import checks, commands, Config
from redbot.core.utils.chat_formatting import inline, humanize_timedelta, humanize_number
from redbot.core.utils.menus import close_menu, menu, DEFAULT_CONTROLS

from redbot.core.bot import Red

log = logging.getLogger("red.angiedale.osu")

EMOJI_SSH = "<:SSH_Rank:794823890873483305>"
EMOJI_SS = "<:SS_Rank:794823687807172608>"
EMOJI_SH = "<:SH_Rank:794823687311720450>"
EMOJI_S = "<:S_Rank:794823687492337714>"
EMOJI_A = "<:A_Rank:794823687470710815>"
EMOJI_B = "<:B_Rank:794823687446593557>"
EMOJI_C = "<:C_Rank:794823687488012308>"
EMOJI_F = "<:F_Rank:794823687781613609>"


class Osu(commands.Cog):
    """osu! commands.

    Link your account with `[p]osulink <username>`
    
    `[p]<mode>` Gets a players profile.
    `[p]recent<mode>` Gets a players most recent play.
    `[p]top<mode>` Gets a players top plays.

    Most commands also have shortened aliases and
    all modes can be shortened.
    """

    default_user_settings = {"username": None, "userid": None}

    def __init__(self, bot: Red):
        self.bot = bot
        super().__init__()
        self.osuconfig: Config = Config.get_conf(self, 1387002, cog_name="Osu")
        self.osu_bearer_cache: dict = {}
        self.osuconfig.register_user(**self.default_user_settings)

        self.bot: Red = bot

        self.task: Optional[asyncio.Task] = None
        self._ready_event: asyncio.Event = asyncio.Event()
        self._init_task: asyncio.Task = self.bot.loop.create_task(self.initialize())

    async def initialize(self) -> None:
        """Should be called straight after cog instantiation."""
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

    async def get_osu_bearer_token(self, api_tokens: Optional[Dict] = None) -> None:
        tokens = (
            await self.bot.get_shared_api_tokens("osu") if api_tokens is None else api_tokens
        )
        try:
            tokens.get("client_id")
        except KeyError:
            pass
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://osu.ppy.sh/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": tokens.get("client_id"),
                    "client_secret": tokens.get("client_secret"),
                    "scope": "public"
                },
                headers={
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
            ) as req:
                try:
                    data = await req.json()
                except aiohttp.ContentTypeError:
                    data = {}

                if req.status == 200:
                    pass
                elif req.status == 400:
                    log.error(
                        "osu! OAuth2 API request failed with status code %s"
                        " and error message: %s",
                        req.status,
                        data["message"],
                    )
                else:
                    log.error("osu! OAuth2 API request failed with status code %s", req.status)

                if req.status != 200:
                    return

        self.osu_bearer_cache = data
        self.osu_bearer_cache["expires_at"] = datetime.now().timestamp() + data.get("expires_in")

    async def maybe_renew_osu_bearer_token(self) -> None:
        if self.osu_bearer_cache:
            if self.osu_bearer_cache["expires_at"] - datetime.now().timestamp() <= 60:
                await self.get_osu_bearer_token()

    @commands.command()
    async def osulink(self, ctx, username: str):
        """Link your account with an osu! user profile"""

        data = await self.fetch_api(ctx, f"/users/{username}", username)

        if data:
            username = data["username"]
            user_id = data["id"]
            await self.osuconfig.user(ctx.author).username.set(username)
            await self.osuconfig.user(ctx.author).userid.set(user_id)
            await ctx.send(f"{username} is successfully linked to your account!")

    @commands.command(aliases=["standard", "std"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu(self, ctx, username: str = None):
        """Get a players osu!Standard profile.
        
        Works with any `[p]<mode>`
        """
        user = await self.check_context(ctx, username, True)
            
        if user:
            data = await self.fetch_api(ctx, f"users/{user}/osu", user)
            await self.profile_embed(ctx, data, user, "Standard")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def taiko(self, ctx, username: str = None):
        """Get a players osu!Taiko profile."""
        
        user = await self.check_context(ctx, username, True)
            
        if user:
            data = await self.fetch_api(ctx, f"users/{user}/taiko", user)
            await self.profile_embed(ctx, data, user, "Taiko")

    @commands.command(aliases=["catch", "ctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def fruits(self, ctx, username: str = None):
        """Get a players osu!Catch profile."""
        
        user = await self.check_context(ctx, username, True)
            
        if user:
            data = await self.fetch_api(ctx, f"users/{user}/fruits", user)
            await self.profile_embed(ctx, data, user, "Catch")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def mania(self, ctx, username: str = None):
        """Get a players osu!Mania profile."""
        
        user = await self.check_context(ctx, username, True)
            
        if user:
            data = await self.fetch_api(ctx, f"users/{user}/mania", user)
            await self.profile_embed(ctx, data, user, "Mania")

    @commands.command(aliases=["rso","recentstandard"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentosu(self, ctx, username: str = None):
        """Get a players recent osu!Standard play.
        
        Works with any `[p]recent<mode>`
        or shortened to `[p]rso`
        """

        user = await self.check_context(ctx, username)

        if user:
            params = {"include_fails": "1", "mode": "osu",}

            data = await self.fetch_api(ctx, f"users/{user}/scores/recent", user, params)
            await self.recent_embed(ctx, data, user)

    @commands.command(aliases=["rst"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recenttaiko(self, ctx, username: str = None):
        """Get a players recent osu!Taiko play."""

        user = await self.check_context(ctx, username)

        if user:
            params = {"include_fails": "1", "mode": "taiko",}

            data = await self.fetch_api(ctx, f"users/{user}/scores/recent", user, params)
            await self.recent_embed(ctx, data, user)

    @commands.command(aliases=["rsf","recentcatch","rsc"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentfruits(self, ctx, username: str = None):
        """Get a players recent osu!Catch play."""

        user = await self.check_context(ctx, username)

        if user:
            params = {"include_fails": "1", "mode": "fruits",}

            data = await self.fetch_api(ctx, f"users/{user}/scores/recent", user, params)
            await self.recent_embed(ctx, data, user)

    @commands.command(aliases=["rsm"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentmania(self, ctx, username: str = None):
        """Get a players recent osu!Mania play."""

        user = await self.check_context(ctx, username)

        if user:
            params = {"include_fails": "1", "mode": "mania",}

            data = await self.fetch_api(ctx, f"users/{user}/scores/recent", user, params)
            await self.recent_embed(ctx, data, user)

    @commands.command(aliases=["topo", "topstandard"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def toposu(self, ctx, username: str = None):
        """Get a players osu!Standard top plays.
        
        Works with any `[p]top<mode>`
        or shortened to `[p]topo`
        """

        user = await self.check_context(ctx, username)

        if user:
            params = {"mode": "osu", "limit": "50",}

            data = await self.fetch_api(ctx, f"users/{user}/scores/best", user, params)
            await self.top_embed(ctx, data, user, "Standard")

    @commands.command(aliases=["topt"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def toptaiko(self, ctx, username: str = None):
        """Get a players osu!Taiko top plays."""

        user = await self.check_context(ctx, username)
        
        if user:
            params = {"mode": "taiko", "limit": "50",}

            data = await self.fetch_api(ctx, f"users/{user}/scores/best", user, params)
            await self.top_embed(ctx, data, user, "Taiko")

    @commands.command(aliases=["topf","topcatch", "topc"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topfruits(self, ctx, username: str = None):
        """Get a players osu!Catch top plays."""

        user = await self.check_context(ctx, username)
        
        if user:
            params = {"mode": "fruits", "limit": "50",}

            data = await self.fetch_api(ctx, f"users/{user}/scores/best", user, params)
            await self.top_embed(ctx, data, user, "Catch")

    @commands.command(aliases=["topm"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topmania(self, ctx, username: str = None):
        """Get a players osu!Mania top plays."""

        user = await self.check_context(ctx, username)
        
        if user:
            params = {"mode": "mania", "limit": "50",}

            data = await self.fetch_api(ctx, f"users/{user}/scores/best", user, params)
            await self.top_embed(ctx, data, user, "Mania")

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osunews(self, ctx):
        """Show the news from the osu! front page."""

        data = await self.fetch_api(ctx, f"news")
        await self.news_embed(ctx, data)

    @commands.command(hidden=True)
    @commands.cooldown(10, 10, commands.BucketType.user)
    async def et(self, ctx, user: discord.Member=None):
        """Marcinho is ET"""

        author = ctx.message.author
        if not user:
            message = f"{author.mention} thinks <@253588524652036096> is ET"
        else:
            message = f"{author.mention} thinks {user.mention} is ET"

        await ctx.send(message)

    async def fetch_api(self, ctx, url, user = None, params = None):
        await self.maybe_renew_osu_bearer_token()

        endpoint = f"https://osu.ppy.sh/api/v2/{url}"
        token = (await self.bot.get_shared_api_tokens("osu")).get("client_id")
        bearer = self.osu_bearer_cache.get("access_token", None)
        header = {"client_id": str(token)}
        if bearer is not None:
            header = {**header, "Authorization": f"Bearer {bearer}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=header, params=params) as r:
                if r.status == 404 and user is not None:
                    message = await ctx.send(f"Could not find the user {user}")
                    await asyncio.sleep(10)
                    try:
                        await message.delete()
                    except (discord.errors.NotFound, discord.errors.Forbidden):
                        pass
                else:
                    data = await r.json(encoding="utf-8")
            return data

    async def profile_embed(self, ctx, data, player_id, mode):
        if data:
            statistics = data["statistics"]
            rank = statistics["rank"]
            user_id = data["id"]
            country = data["country_code"]
            username = data["username"]
            ranking = humanize_number(rank["global"])
            country_ranking = humanize_number(rank["country"])
            accuracy = round(float(statistics["hit_accuracy"]),2)
            playcount = humanize_number(statistics["play_count"])
            last_online = data["last_visit"]
            max_combo = humanize_number(statistics["maximum_combo"])
            level = statistics["level"]
            level_current = level["current"]
            level_progress = level["progress"]
            performance = humanize_number(statistics["pp"])
            grades = statistics["grade_counts"]
            grade_ss = humanize_number(grades["ss"])
            grade_ssh = humanize_number(grades["ssh"])
            grade_s = humanize_number(grades["s"])
            grade_sh = humanize_number(grades["sh"])
            grade_a = humanize_number(grades["a"])
            if mode == "Mania":
                variant = statistics["variants"]
                performance_4k = variant[0]["pp"]
                performance_7k = variant[1]["pp"]
                ranking_4k = variant[0]["global_rank"]
                ranking_7k = variant[1]["global_rank"]
                country_ranking_4k = variant[0]["country_rank"]
                country_ranking_7k = variant[1]["country_rank"]

                if performance_4k == 0 and performance_7k == 0:
                    performancevalue = f"{performance}pp"
                elif performance_4k == 0:
                    performance_7k = humanize_number(performance_7k)
                    performancevalue = f"{performance}pp\n{performance_7k}pp | **7k**"
                elif performance_7k == 0:
                    performance_4k = humanize_number(performance_4k)
                    performancevalue = f"{performance}pp\n{performance_4k}pp | **4k**"
                else:
                    performance_4k = humanize_number(performance_4k)
                    performance_7k = humanize_number(performance_7k)
                    performancevalue = f"{performance}pp\n{performance_4k}pp | **4k**\n{performance_7k}pp | **7k**"
                
                if ranking_4k == None and ranking_7k == None:
                    rankingvalue = f"#{ranking} ({country} #{country_ranking})"
                elif ranking_4k == None:
                    ranking_7k = humanize_number(ranking_7k)
                    rankingvalue = f"#{ranking} ({country} #{country_ranking})\n#{ranking_7k} ({country} #{country_ranking_7k}) | **7k**"
                elif ranking_7k == None:
                    ranking_4k = humanize_number(ranking_4k)
                    rankingvalue = f"#{ranking} ({country} #{country_ranking})\n#{ranking_4k} ({country} #{country_ranking_4k}) | **4k**"
                else:
                    ranking_4k = humanize_number(ranking_4k)
                    ranking_7k = humanize_number(ranking_7k)
                    rankingvalue = f"#{ranking} ({country} #{country_ranking})\n#{ranking_4k} ({country} #{country_ranking_4k}) | **4k**\n#{ranking_7k} ({country} #{country_ranking_7k}) | **7k**"
            else:
                performancevalue = f"{performance}pp"
                rankingvalue = f"#{ranking} ({country} #{country_ranking})"

            embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )
            embed.set_author(
                name=f"{username} | osu!{mode}",
                url=f"https://osu.ppy.sh/users/{user_id}",
                icon_url=f"https://osu.ppy.sh/images/flags/{country}.png"
            )
            embed.set_thumbnail(
                url=f"https://a.ppy.sh/{user_id}"
            )
            embed.add_field(
                name="Ranking",
                value=rankingvalue,
                inline=True
            )
            embed.add_field(
                name="Performance",
                value=performancevalue,
                inline=True
            )
            embed.add_field(
                name="Accuracy",
                value=f"{accuracy}%",
                inline=True
            )
            embed.add_field(
                name="Level",
                value=f"{level_current} ({level_progress}%)",
                inline=True
            )
            embed.add_field(
                name="Max Combo",
                value=max_combo,
                inline=True
            )
            embed.add_field(
                name="Playcount",
                value=playcount,
                inline=True
            )
            embed.add_field(
                name="Grades",
                value=f"{EMOJI_SSH} {grade_ssh} {EMOJI_SS} {grade_ss} {EMOJI_SH} {grade_sh} {EMOJI_S} {grade_s} {EMOJI_A} {grade_a}",
                inline=False
            )
            if data["is_online"] == True:
                embed.set_footer(
                    text="Currently Online"
                )
            else:
                embed.set_footer(
                    text="Last Online"
                )
                embed.timestamp = datetime.strptime(last_online, "%Y-%m-%dT%H:%M:%S%z")
                
            await ctx.send(embed=embed)

    async def profilelinking(self, ctx):
        prefix = ctx.clean_prefix
        message = await ctx.maybe_send_embed(f"Looks like you haven't linked an account.\nYou can do so using `{prefix}osulink <username>`"
            "\n\nAlternatively you can use the command\nwith a username or id after it")
        await asyncio.sleep(10)
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass

    def translatemode(self, mode):
        if mode == 0:
            mode = "standard"
        elif mode == 1:
            mode = "taiko"
        elif mode == 2:
            mode = "fruits"
        elif mode == 3:
            mode = "mania"
        return mode

    def translateemote(self, grade):
        if grade == "XH":
            emote = EMOJI_SSH
        elif grade == "X":
            emote = EMOJI_SS
        elif grade == "SH":
            emote = EMOJI_SH
        elif grade == "S":
            emote = EMOJI_S
        elif grade == "A":
            emote = EMOJI_A
        elif grade == "B":
            emote = EMOJI_B
        elif grade == "C":
            emote = EMOJI_C
        elif grade == "D":
            emote = EMOJI_F
        else:
            emote = EMOJI_F
        return emote

    async def recent_embed(self, ctx, data, player_id):
        if data:
            index = 0
            try:
                beatmapset = data[index]["beatmapset"]
                statistics = data[index]["statistics"]
                user = data[index]["user"]
                played = data[index]["created_at"]
                username = user["username"]
                count_miss = humanize_number(statistics["count_miss"])
                count_50 = humanize_number(statistics["count_50"])
                count_100 = humanize_number(statistics["count_100"])
                count_300 = statistics["count_300"]
                count_geki = statistics["count_geki"]
                count_katu = humanize_number(statistics["count_katu"])
                rank = data[index]["rank"]
                emoji = self.translateemote(rank)
                artist = beatmapset["artist"]
                beatmapsetid = beatmapset["id"]
                title = beatmapset["title"]
                beatmap = data[index]["beatmap"]
                version = beatmap["version"]
                beatmapmode = beatmap["mode_int"]
                starrating = beatmap["difficulty_rating"]
                comboraw = data[index]["max_combo"]
                beatmapurl = beatmap["url"]
                user_id = data[index]["user_id"]
                score = humanize_number(data[index]["score"])
                creator = beatmapset["creator"]
                creator_id = beatmapset["user_id"]
                mapstatus = beatmapset["status"]
                accuracy = "{:.2%}".format(data[index]["accuracy"])

                if beatmapmode == 3:
                    comboratio = "Combo / Ratio"
                    version = re.sub(r"^\S*\s", "", beatmap["version"])
                    ratio = round(count_geki / count_300,2)
                    combo = f"**{comboraw:,}x** / {ratio}"
                    hits = f"{humanize_number(count_geki)}/{humanize_number(count_300)}/{count_katu}/{count_100}/{count_50}/{count_miss}"
                else:
                    comboratio = "Combo"
                    combo = f"**{comboraw}x**"
                    hits = f"{humanize_number(count_300)}/{count_100}/{count_50}/{count_miss}"

                mods = ""
                if data[0]["mods"]:
                    mods = mods.join(data[index]["mods"])
                    mods = f" +{mods}"

                try:
                    performance = humanize_number(round(data[index]["pp"],2))
                except TypeError:
                    performance = 0


                embed = discord.Embed(
                    color=await self.bot.get_embed_color(ctx)
                )
                embed.set_author(
                    name=f"{artist} - {title} [{version}]",
                    url=beatmapurl,
                    icon_url=f"https://a.ppy.sh/{user_id}"
                )
                embed.set_image(
                    url=f"https://assets.ppy.sh/beatmaps/{beatmapsetid}/covers/cover.jpg"
                )
                embed.add_field(
                    name="Grade",
                    value=f"{emoji}{mods}",
                    inline=True
                )
                embed.add_field(
                    name="Score",
                    value=f"{score}",
                    inline=True
                )
                embed.add_field(
                    name="Acc",
                    value=f"{accuracy}",
                    inline=True
                )
                embed.add_field(
                    name="PP",
                    value=f"**{performance}pp**",
                    inline=True
                )
                embed.add_field(
                    name=comboratio,
                    value=combo,
                    inline=True
                )
                embed.add_field(
                    name="Hits",
                    value=hits,
                    inline=True
                )
                embed.add_field(
                    name="Map Info",
                    value=f"Mapper: [{creator}](https://osu.ppy.sh/users/{creator_id})\nStatus: {inline(mapstatus.capitalize())} | SR: {inline(str(starrating))}",
                    inline=False
                )
                embed.set_footer(
                    text=f"{username} | osu!{self.translatemode(beatmapmode).capitalize()} | Played"
                )
                embed.timestamp = datetime.strptime(played, "%Y-%m-%dT%H:%M:%S%z")
            
                await ctx.send(embed=embed)
            except IndexError:
                message = await ctx.send(f"Looks like you don't have any recent plays in that mode")
                await asyncio.sleep(10)
                try:
                    await message.delete()
                except (discord.errors.NotFound, discord.errors.Forbidden):
                    pass

    async def top_embed(self, ctx, data, player_id, mode):
        if data:
            user = data[0]["user"]
            username = user["username"]
            user_id = user["id"]
            country_code = user["country_code"]
            page_num = 1
            scores = []

            base_embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )
            base_embed.set_author(
                name=f"Top plays for {username} | osu!{mode}",
                url=f"https://osu.ppy.sh/users/{user_id}",
                icon_url=f"https://osu.ppy.sh/images/flags/{country_code}.png"
            )
            base_embed.set_thumbnail(
                url=f"https://a.ppy.sh/{user_id}"
            )

            while page_num <= ceil(len(data) / 5):
                i = (page_num - 1) * 5
                maps = ""
                while i < (page_num * 5):
                    maps = self.fetch_top(data, i, maps)
                    i += 1
                
                embed = base_embed.copy()
                embed.description = maps
                embed.set_footer(text=f"Page {page_num}/{ceil(len(data) / 5)}")

                scores.append(embed)
                page_num += 1
            
            await  menu(ctx, scores, DEFAULT_CONTROLS if ceil(len(data)) > 1 else {"\N{CROSS MARK}": close_menu})

    async def news_embed(self, ctx, data):
        if data:
            news_posts = data["news_posts"]
            post_count = len(news_posts)
            posts = []

            base_embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )

            for i in range(post_count):
                post_image = news_posts[i]["first_image"]
                post_author = news_posts[i]["author"]
                post_url = news_posts[i]["slug"]
                published_at = news_posts[i]["published_at"]
                title = news_posts[i]["title"]
                preview = news_posts[i]["preview"]

                embed = base_embed.copy()
                embed.set_image(url=f"https://osu.ppy.sh/{post_image}")
                embed.set_author(name=post_author, icon_url=f"https://osu.ppy.sh/favicon-32x32.png")
                embed.url = f"https://osu.ppy.sh/home/news/{post_url}"
                embed.timestamp = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%S%z")
                embed.title = title
                embed.description = preview
                embed.set_footer(text=f"Post # {i + 1}/{len(news_posts)}")

                posts.append(embed)
            
            await  menu(ctx, posts, DEFAULT_CONTROLS if len(data) > 1 else {"\N{CROSS MARK}": close_menu})

    def fetch_top(self, data, i, maps):
        current_date = datetime.now()
        beatmap = data[i]["beatmap"]
        beatmapset = data[i]["beatmapset"]
        statistics = data[i]["statistics"]
        beatmapmode = beatmap["mode_int"]
        version = beatmap["version"]
        title = beatmapset["title"]
        beatmapurl = beatmap["url"]
        starrating = beatmap["difficulty_rating"]
        performance = humanize_number(round(data[i]["pp"],2))
        rank = data[i]["rank"]
        score = humanize_number(data[i]["score"])
        combo = humanize_number(data[i]["max_combo"])
        count_miss = humanize_number(statistics["count_miss"])
        count_50 = humanize_number(statistics["count_50"])
        count_100 = humanize_number(statistics["count_100"])
        count_300 = humanize_number(statistics["count_300"])
        count_geki = humanize_number(statistics["count_geki"])
        count_katu = humanize_number(statistics["count_katu"])
        emoji = self.translateemote(rank)
        accuracy = "{:.2%}".format(data[i]["accuracy"])
        hits = f"{count_300}/{count_100}/{count_50}/{count_miss}"
        played = data[i]["created_at"]

        date = current_date - datetime.strptime(played, "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
        time = re.split(r",\s", humanize_timedelta(timedelta=date))
        try:
            time = f"{time[0]} {time[1]}"
        except ValueError:
            pass

        if beatmapmode == 3:
            version = re.sub(r"^\S*\s", "", beatmap["version"])
            hits = f"{count_geki}/{count_300}/{count_katu}/{count_100}/{count_50}/{count_miss}"

        mods = ""
        if data[i]["mods"]:
            mods = mods.join(data[i]["mods"])
            mods = f" +{mods}"

        maps = f"{maps}\n**{i+1}. [{title} - [{version}]]({beatmapurl}){mods}** [{starrating}★]\n{emoji} **{performance}pp** ◈ ({accuracy}) ◈ {score}\n**{combo}x** ◈ [{hits}] ◈ {time} ago\n"

        return maps

    async def no_user(self, ctx, player_id):
        message = await ctx.send(f"Could not find the user {player_id}")
        await asyncio.sleep(10)
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass

    async def check_context(self, ctx, username, no_id = False):
        if username is None:
            user_id = await self.osuconfig.user(ctx.author).userid()
            if user_id is None:
                await self.profilelinking(ctx)
            else:
                username = user_id
        elif "@" in username:
            try:
                member = await commands.MemberConverter().convert(ctx, username)
                username = await self.osuconfig.user(member).userid()
            except:
                pass

        if username is not None:
            if str(username).isnumeric() == False and no_id == False:
                data = await self.fetch_api(ctx, f"users/{username}/osu", username)
                username = data["id"]

        return username