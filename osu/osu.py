import discord
import logging
import json
import aiohttp
import asyncio
import re
import operator
from math import ceil
from datetime import datetime, timedelta
import time
from typing import Optional, List, Dict

from redbot.core import checks, commands, Config
from redbot.core.utils.chat_formatting import inline, humanize_timedelta, humanize_number
from redbot.core.utils.menus import close_menu, next_page, menu, DEFAULT_CONTROLS

from redbot.core.bot import Red

log = logging.getLogger("red.angiedale.osu")

EMOJI = {
    "XH": "<:SSH_Rank:794823890873483305>",
    "X": "<:SS_Rank:794823687807172608>",
    "SH": "<:SH_Rank:794823687311720450>",
    "S": "<:S_Rank:794823687492337714>",
    "A": "<:A_Rank:794823687470710815>",
    "B": "<:B_Rank:794823687446593557>",
    "C": "<:C_Rank:794823687488012308>",
    "F": "<:F_Rank:794823687781613609>"
}

MODE = {
    0: "standard",
    1: "taiko",
    2: "catch",
    3: "mania"
}


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

        data = await self.fetch_api(ctx, f"users/{username}", username)

        if data:
            username = data["username"]
            user_id = data["id"]
            await self.osuconfig.user(ctx.author).username.set(username)
            await self.osuconfig.user(ctx.author).userid.set(user_id)
            await ctx.send(f"{username} is successfully linked to your account!")

    @commands.command(aliases=["standard", "std"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu(self, ctx, *username):
        """Get a players osu!Standard profile.
        
        Works with any `[p]<mode>`
        """
        url, *extra = await self.check_context(ctx, username, "profile")
            
        if url:
            data = await self.fetch_api(ctx, f"{url}/osu", extra[1])
            await self.profile_embed(ctx, data, extra[1], "Standard")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def taiko(self, ctx, *username):
        """Get a players osu!Taiko profile."""
        url, *extra = await self.check_context(ctx, username, "profile")
            
        if url:
            data = await self.fetch_api(ctx, f"{url}/taiko", extra[1])
            await self.profile_embed(ctx, data, extra[1], "Taiko")

    @commands.command(aliases=["catch", "ctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def fruits(self, ctx, *username):
        """Get a players osu!Catch profile.
        
        Aliases:
        - `catch`
        - `ctb`"""
        url, *extra = await self.check_context(ctx, username, "profile")
            
        if url:
            data = await self.fetch_api(ctx, f"{url}/fruits", extra[1])
            await self.profile_embed(ctx, data, extra[1], "Catch")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def mania(self, ctx, *username):
        """Get a players osu!Mania profile."""
        url, *extra = await self.check_context(ctx, username, "profile")
            
        if url:
            data = await self.fetch_api(ctx, f"{url}/mania", extra[1])
            await self.profile_embed(ctx, data, extra[1], "Mania")

    @commands.command(aliases=["rso","recentstandard"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentosu(self, ctx, *username):
        """Get a players recent osu!Standard play.
        
        Works with any `[p]recent<mode>`
        or shortened to `[p]rso`
        """
        url, *extra = await self.check_context(ctx, username, "recent")

        if url:
            params = {"include_fails": "1", "mode": "osu"}

            data = await self.fetch_api(ctx, url, extra[1], params)
            await self.recent_embed(ctx, data, extra[2]["user_name"])

    @commands.command(aliases=["rst"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recenttaiko(self, ctx, *username):
        """Get a players recent osu!Taiko play.
        
        Aliases:
        - `rst`"""
        url, *extra = await self.check_context(ctx, username, "recent")

        if url:
            params = {"include_fails": "1", "mode": "taiko"}

            data = await self.fetch_api(ctx, url, extra[1], params)
            await self.recent_embed(ctx, data, extra[2]["user_name"])

    @commands.command(aliases=["rsf","recentcatch","rsc", "rsctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentfruits(self, ctx, *username):
        """Get a players recent osu!Catch play.
        
        Aliases:
        - `recentcatch`
        - `rsctb`
        - `rsf`
        - `rsc`"""
        url, *extra = await self.check_context(ctx, username, "recent")

        if url:
            params = {"include_fails": "1", "mode": "fruits"}

            data = await self.fetch_api(ctx, url, extra[1], params)
            await self.recent_embed(ctx, data, extra[2]["user_name"])

    @commands.command(aliases=["rsm"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recentmania(self, ctx, *username):
        """Get a players recent osu!Mania play.
        
        Aliases:
        - `rsm`"""
        url, *extra = await self.check_context(ctx, username, "recent")

        if url:
            params = {"include_fails": "1", "mode": "mania"}

            data = await self.fetch_api(ctx, url, extra[1], params)
            await self.recent_embed(ctx, data, extra[2]["user_name"])

    @commands.command(aliases=["topo", "topstandard"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def toposu(self, ctx, *username):
        """Get a players osu!Standard top plays.
        
        Works with any `[p]top<mode>`
        or shortened to `[p]topo`

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """
        url, *extra = await self.check_context(ctx, username, "top")

        if url:
            params = {"mode": "osu", "limit": "50"}

            data1 = await self.fetch_api(ctx, url, extra[1], params)
            params["offset"] =  "50"
            data2 = await self.fetch_api(ctx, url, extra[1], params)
            data = data1 + data2
            await self.top_embed(ctx, data, extra[1], "Standard", extra[2])

    @commands.command(aliases=["topt"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def toptaiko(self, ctx, *username):
        """Get a players osu!Taiko top plays.
        
        Aliases:
        - `topt`"""
        url, *extra = await self.check_context(ctx, username, "top")

        if url:
            params = {"mode": "taiko", "limit": "50"}

            data1 = await self.fetch_api(ctx, url, extra[1], params)
            params["offset"] =  "50"
            data2 = await self.fetch_api(ctx, url, extra[1], params)
            data = data1 + data2
            await self.top_embed(ctx, data, extra[1], "Taiko", extra[2])

    @commands.command(aliases=["topf","topcatch", "topc", "topctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topfruits(self, ctx, *username):
        """Get a players osu!Catch top plays.
        
        Aliases:
        - `topcatch`
        - `topctb`
        - `topc`
        - `topf`"""
        url, *extra = await self.check_context(ctx, username, "top")

        if url:
            params = {"mode": "fruits", "limit": "50"}

            data1 = await self.fetch_api(ctx, url, extra[1], params)
            params["offset"] =  "50"
            data2 = await self.fetch_api(ctx, url, extra[1], params)
            data = data1 + data2
            await self.top_embed(ctx, data, extra[1], "Catch", extra[2])

    @commands.command(aliases=["topm"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def topmania(self, ctx, *username):
        """Get a players osu!Mania top plays.
        
        Aliases:
        - `topm`"""
        url, *extra = await self.check_context(ctx, username, "top")

        if url:
            params = {"mode": "mania", "limit": "50"}

            data1 = await self.fetch_api(ctx, url, extra[1], params)
            params["offset"] =  "50"
            data2 = await self.fetch_api(ctx, url, extra[1], params)
            data = data1 + data2
            await self.top_embed(ctx, data, extra[1], "Mania", extra[2])

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def map(self, ctx, beatmap):
        """Get info about a map."""
        url = await self.check_context(ctx, beatmap, "map")

        if url:
            data = await self.fetch_api(ctx, url, isfrom="map")
            await self.map_embed(ctx, data)

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osunews(self, ctx):
        """Show the news from the osu! front page."""

        data = await self.fetch_api(ctx, f"news")
        await self.news_embed(ctx, data)

    @commands.command(aliases=["osucl"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osuchangelog(self, ctx, release_stream = "stable"):
        """Gets the changelog for the game.
        
        Supported Release Streams:
        `stable`
        `fallback`
        `beta`
        `cuttingedge`
        `lazer`
        `web`"""

        url, params = await self.check_context(ctx, release_stream, "changelog")
        
        if url:
            data = await self.fetch_api(ctx, url, params=params)
            await self.changelog_embed(ctx, data)

    @commands.command(aliases=["osur"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osurankings(self, ctx, *arguments):
        """Show the top players from each leaderboard.

        Examples:
            - `[p]osurankings catch SE`
            - `[p]osur mania 4k`
        
        **Arguments:**

        - `<mode>` one of the 4 gamemodes. Only full names.
        - `<type>` to sort by score use score. Else nothing.
        - `<country>` a 2 digit ISO country code to get that countries leaderboard. Does not work with `<type>`.
        - `<variant>` either 4k or 7k when `<mode>` is mania. Leave blank for global. Does not work with `<type>`.
        """
        params, rtype, mode, country, variant = await self.check_context(ctx, arguments, "rankings")
        
        if rtype:
            data = await self.fetch_api(ctx, f"rankings/{mode}/{rtype}", params=params)
            await self.rankings_embed(ctx, data, rtype, mode, country, variant)

    @commands.command(aliases=["osuc", "oc"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osucompare(self, ctx, *username):
        """Compare your score with the last one sent in the channel
        """
        url, *extra = await self.check_context(ctx, username, "compare")

        messages = []
        map_id = None
        async for m in ctx.channel.history(limit=50):
            if m.author.id == self.bot.user.id and m.type:
                try:
                    messages.append(m.embeds[0])
                except:
                    pass
        if messages:
            for e in messages:
                try:
                    author_url = e.author.url
                    title_url = e.url
                    if "beatmaps" in author_url:
                        map_id = author_url.rsplit('/', 1)[-1]
                        break
                    elif "beatmaps" in title_url:
                        map_id = title_url.rsplit('/', 1)[-1]
                        break
                except:
                    pass
        else:
            await self.del_message(ctx, "Could not find any recently displayed maps in this channel")

        if map_id and extra[0]:
            await self.legacycompare(ctx, url, extra[0], map_id)

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

    async def fetch_api(self, ctx, url, user = None, params = None, isfrom = None):
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
                    await self.del_message(ctx, f"Could not find the user {user}")
                elif r.status == 404 and isfrom == "map":
                    await self.del_message(ctx, f"Could not find that map")
                elif r.status == 404:
                    await self.del_message(ctx, f"Something went wrong with the api")
                else:
                    data = await r.json(encoding="utf-8")
                    return data

    async def profile_embed(self, ctx, data, player_id, mode):
        if data:
            user_id = data["id"]
            country = data["country_code"]
            username = data["username"]
            try:
                ranking = humanize_number(data["statistics"]["rank"]["global"])
                country_ranking = humanize_number(data["statistics"]["rank"]["country"])
            except:
                ranking = 0
                country_ranking = 0
            accuracy = round(float(data["statistics"]["hit_accuracy"]),2)
            playcount = humanize_number(data["statistics"]["play_count"])
            last_online = data["last_visit"]
            max_combo = humanize_number(data["statistics"]["maximum_combo"])
            level_current = data["statistics"]["level"]["current"]
            level_progress = data["statistics"]["level"]["progress"]
            performance = humanize_number(data["statistics"]["pp"])
            grade_ss = humanize_number(data["statistics"]["grade_counts"]["ss"])
            grade_ssh = humanize_number(data["statistics"]["grade_counts"]["ssh"])
            grade_s = humanize_number(data["statistics"]["grade_counts"]["s"])
            grade_sh = humanize_number(data["statistics"]["grade_counts"]["sh"])
            grade_a = humanize_number(data["statistics"]["grade_counts"]["a"])
            ranked_score = humanize_number(data["statistics"]["ranked_score"])
            total_score = humanize_number(data["statistics"]["total_score"])
            mapping_follower_count = data["mapping_follower_count"]
            scores_first_count = data["scores_first_count"]
            kudosu_total = data["kudosu"]["total"]
            graveyard_beatmapset_count = data["graveyard_beatmapset_count"]
            replays_watched_by_others = data["statistics"]["replays_watched_by_others"]
            play_time = re.split(r",\s", humanize_timedelta(timedelta=timedelta(seconds=data["statistics"]["play_time"])))
            try:
                play_time = f"{play_time[0]}, {play_time[1]}, {play_time[2]}"
            except IndexError:
                try:
                    play_time = f"{play_time[0]}, {play_time[1]}"
                except IndexError:
                    try:
                        play_time = f"{play_time[0]}"
                    except IndexError:
                        play_time = "0"
            total_hits = humanize_number(data["statistics"]["total_hits"])
            user_achievements = len(data["user_achievements"])
            follower_count = data["follower_count"]
            join_date = datetime.strptime(data["join_date"], "%Y-%m-%dT%H:%M:%S%z")
            join_date = join_date.strftime("%B %-d, %Y")
            ranked_and_approved_beatmapset_count = data["ranked_and_approved_beatmapset_count"]
            loved_beatmapset_count = data["loved_beatmapset_count"]
            unranked_beatmapset_count = data["unranked_beatmapset_count"]
            favourite_beatmapset_count = data["favourite_beatmapset_count"]
            if mode == "Mania":
                performance_4k = data["statistics"]["variants"][0]["pp"]
                performance_7k = data["statistics"]["variants"][1]["pp"]
                ranking_4k = data["statistics"]["variants"][0]["global_rank"]
                ranking_7k = data["statistics"]["variants"][1]["global_rank"]
                country_ranking_4k = data["statistics"]["variants"][0]["country_rank"]
                country_ranking_7k = data["statistics"]["variants"][1]["country_rank"]

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

            try:
                rank_history = list(map(int, data["rank_history"]["data"]))
                rank_history = ( f'``` Delta |   Rank   | Date\n'
                f'-----------------------\n'
                f'   -   |{"{0:^10}".format(humanize_number(rank_history[0]))}| -90d\n'
                f'{"{0:^7}".format(humanize_number(rank_history[0] - rank_history[14]))}|{"{0:^10}".format(humanize_number(rank_history[14]))}| -75d\n'
                f'{"{0:^7}".format(humanize_number(rank_history[14] - rank_history[29]))}|{"{0:^10}".format(humanize_number(rank_history[29]))}| -60d\n'
                f'{"{0:^7}".format(humanize_number(rank_history[29] - rank_history[44]))}|{"{0:^10}".format(humanize_number(rank_history[44]))}| -45d\n'
                f'{"{0:^7}".format(humanize_number(rank_history[44] - rank_history[59]))}|{"{0:^10}".format(humanize_number(rank_history[59]))}| -30d\n'
                f'{"{0:^7}".format(humanize_number(rank_history[59] - rank_history[74]))}|{"{0:^10}".format(humanize_number(rank_history[74]))}| -15d\n'
                f'{"{0:^7}".format(humanize_number(rank_history[74] - rank_history[89]))}|{"{0:^10}".format(humanize_number(rank_history[89]))}|  Now```' )
            except TypeError:
                rank_history = "This user doesn't have any rank history"

            profiles = []

            base_embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )
            base_embed.set_author(
                name=f"{username} | osu!{mode}",
                url=f"https://osu.ppy.sh/users/{user_id}",
                icon_url=f"https://osu.ppy.sh/images/flags/{country}.png"
            )
            base_embed.set_thumbnail(
                url=f"https://a.ppy.sh/{user_id}"
            )
            base_embed.add_field(
                name="Ranking",
                value=rankingvalue,
                inline=True
            )
            base_embed.add_field(
                name="Performance",
                value=performancevalue,
                inline=True
            )
            base_embed.add_field(
                name="Accuracy",
                value=f"{accuracy}%",
                inline=True
            )
            base_embed.add_field(
                name="Level",
                value=f"{level_current} ({level_progress}%)",
                inline=True
            )
            base_embed.add_field(
                name="Max Combo",
                value=max_combo,
                inline=True
            )
            base_embed.add_field(
                name="Playcount",
                value=playcount,
                inline=True
            )
            base_embed.add_field(
                name="Grades",
                value=f'{EMOJI["XH"]} {grade_ssh} {EMOJI["X"]} {grade_ss} {EMOJI["SH"]} {grade_sh} {EMOJI["S"]} {grade_s} {EMOJI["A"]} {grade_a}',
                inline=False
            )
            if data["is_online"] == True:
                base_embed.set_footer(
                    text="Currently Online"
                )
            elif not last_online:
                base_embed.set_footer(text="Last Online | Unknown")
            else:
                base_embed.set_footer(
                    text="Last Online"
                )
                base_embed.timestamp = datetime.strptime(last_online, "%Y-%m-%dT%H:%M:%S%z")

            detailed_embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )
            detailed_embed.set_author(
                name=f"{username} | osu!{mode}",
                url=f"https://osu.ppy.sh/users/{user_id}",
                icon_url=f"https://osu.ppy.sh/images/flags/{country}.png"
            )
            detailed_embed.set_thumbnail(
                url=f"https://a.ppy.sh/{user_id}"
            )
            detailed_embed.add_field(
                name="Ranking",
                value=rankingvalue,
                inline=True
            )
            detailed_embed.add_field(
                name="Performance",
                value=performancevalue,
                inline=True
            )
            detailed_embed.add_field(
                name="Accuracy",
                value=f"{accuracy}%",
                inline=True
            )
            detailed_embed.add_field(
                name="Level",
                value=f"{level_current} ({level_progress}%)",
                inline=True
            )
            detailed_embed.add_field(
                name="Max Combo",
                value=max_combo,
                inline=True
            )
            detailed_embed.add_field(
                name="Playcount",
                value=playcount,
                inline=True
            )
            detailed_embed.add_field(
                name="Grades",
                value=f'{EMOJI["XH"]} {grade_ssh} {EMOJI["X"]} {grade_ss} {EMOJI["SH"]} {grade_sh} {EMOJI["S"]} {grade_s} {EMOJI["A"]} {grade_a}',
                inline=False
            )
            if data["is_online"] == True:
                detailed_embed.set_footer(
                    text="Currently Online"
                )
            elif not last_online:
                detailed_embed.set_footer(text="Last Online | Unknown")
            else:
                detailed_embed.set_footer(
                    text="Last Online"
                )
                detailed_embed.timestamp = datetime.strptime(last_online, "%Y-%m-%dT%H:%M:%S%z")
            detailed_embed.add_field(
                name="Ranked Score",
                value=ranked_score,
                inline=True
            )
            detailed_embed.add_field(
                name="#1 Scores",
                value=scores_first_count,
                inline=True
            )
            detailed_embed.add_field(
                name="Play Time",
                value=play_time,
                inline=True
            )
            detailed_embed.add_field(
                name="Total Score",
                value=total_score,
                inline=True
            )
            detailed_embed.add_field(
                name="Replays Watched",
                value=replays_watched_by_others,
                inline=True
            )
            detailed_embed.add_field(
                name="Joined osu!",
                value=join_date,
                inline=True
            )
            detailed_embed.add_field(
                name="Rank Change",
                value=rank_history,
                inline=False
            )
            detailed_embed.add_field(
                name="Total Hits",
                value=total_hits,
                inline=True
            )
            detailed_embed.add_field(
                name="Medals",
                value=user_achievements,
                inline=True
            )
            detailed_embed.add_field(
                name="Favorite Beatmaps",
                value=favourite_beatmapset_count,
                inline=True
            )
            detailed_embed.add_field(
                name="Followers",
                value=follower_count,
                inline=True
            )
            detailed_embed.add_field(
                name="Mapping Followers",
                value=mapping_follower_count,
                inline=True
            )
            detailed_embed.add_field(
                name="Kudoso Total",
                value=kudosu_total,
                inline=True
            )
            detailed_embed.add_field(
                name="Uploaded Beatmaps",
                value=f"Ranked: **{ranked_and_approved_beatmapset_count}** ◈ Loved: **{loved_beatmapset_count}** ◈ Unranked: **{unranked_beatmapset_count}** ◈ Graveyarded: **{graveyard_beatmapset_count}**",
                inline=False
            )

            profiles.append(base_embed)
            profiles.append(detailed_embed)
                
            await menu(ctx, profiles, {self.bot.get_emoji(755808377959088159): next_page, "\N{CROSS MARK}": close_menu})

    async def profilelinking(self, ctx):
        prefix = ctx.clean_prefix
        await self.del_message(ctx, f"Looks like you haven't linked an account.\nYou can do so using `{prefix}osulink <username>`"
            "\n\nAlternatively you can use the command\nwith a username or id after it")

    async def del_message(self, ctx, message_text):
        message = await ctx.maybe_send_embed(message_text)
        await asyncio.sleep(10)
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass

    async def recent_embed(self, ctx, data, player_id):
        if data:
            beatmapset = data[0]["beatmapset"]
            statistics = data[0]["statistics"]
            user = data[0]["user"]
            played = data[0]["created_at"]
            username = user["username"]
            count_miss = humanize_number(statistics["count_miss"])
            count_50 = humanize_number(statistics["count_50"])
            count_100 = humanize_number(statistics["count_100"])
            count_300 = statistics["count_300"]
            count_geki = statistics["count_geki"]
            count_katu = humanize_number(statistics["count_katu"])
            rank = data[0]["rank"]
            artist = beatmapset["artist"]
            beatmapsetid = beatmapset["id"]
            title = beatmapset["title"]
            beatmap = data[0]["beatmap"]
            version = beatmap["version"]
            beatmapmode = beatmap["mode_int"]
            starrating = beatmap["difficulty_rating"]
            comboraw = data[0]["max_combo"]
            beatmapurl = beatmap["url"]
            bpm = beatmap["bpm"]
            bmaccuracy = beatmap["accuracy"]
            drain = beatmap["drain"]
            approach = beatmap["ar"]
            circle_size = beatmap["cs"]
            objects_count = beatmap["count_circles"] + beatmap["count_sliders"] + beatmap["count_spinners"]
            user_id = data[0]["user_id"]
            score = humanize_number(data[0]["score"])
            creator = beatmapset["creator"]
            creator_id = beatmapset["user_id"]
            mapstatus = beatmapset["status"]
            accuracy = "{:.2%}".format(data[0]["accuracy"])

            if beatmapmode == 3:
                comboratio = "Combo / Ratio"
                version = re.sub(r"^\S*\s", "", beatmap["version"])
                ratio = round(count_geki / count_300,2)
                combo = f"**{comboraw:,}x** / {ratio}"
                hits = f"{humanize_number(count_geki)}/{humanize_number(count_300)}/{count_katu}/{count_100}/{count_50}/{count_miss}"
                stats = f"OD: `{bmaccuracy}` | HP: `{drain}`"
            else:
                comboratio = "Combo"
                combo = f"**{comboraw}x**"
                hits = f"{humanize_number(count_300)}/{count_100}/{count_50}/{count_miss}"
                stats = f"CS: `{circle_size}` | AR: `{approach}` | OD: `{bmaccuracy}` | HP: `{drain}`"

            mods = ""
            if data[0]["mods"]:
                mods = mods.join(data[0]["mods"])
                mods = f" +{mods}"

            try:
                performance = humanize_number(round(data[0]["pp"],2))
            except TypeError:
                performance = 0


            embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )
            embed.set_author(
                name=f"{artist} - {title} [{version}] [{str(starrating)}★]",
                url=beatmapurl,
                icon_url=f"https://a.ppy.sh/{user_id}"
            )
            embed.set_image(
                url=f"https://assets.ppy.sh/beatmaps/{beatmapsetid}/covers/cover.jpg"
            )
            embed.add_field(
                name="Grade",
                value=f"{EMOJI[rank]}{mods}",
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
                value=f"Mapper: [{creator}](https://osu.ppy.sh/users/{creator_id}) | BPM: `{bpm}` | Objects: `{objects_count}` \n"
                f"Status: {inline(mapstatus.capitalize())} | {stats}",
                inline=False
            )
            embed.set_footer(
                text=f"{username} | osu!{MODE[beatmapmode].capitalize()} | Played"
            )
            embed.timestamp = datetime.strptime(played, "%Y-%m-%dT%H:%M:%S%z")
        
            await ctx.send(embed=embed)
        else:
            await self.del_message(ctx, f"Looks like {player_id} don't have any recent plays in that mode")

    async def top_embed(self, ctx, data, player_id, mode, bonus):
        if data:
            for i, scores in enumerate(data):
                data[i]["index"] = i
            recent_text = "Top"
            if bonus["sort_recent"] == True:
                data = sorted(data, key=operator.itemgetter("created_at"), reverse=True)
                recent_text = "Most recent top"

            user = data[0]["user"]
            username = user["username"]
            user_id = user["id"]
            country_code = user["country_code"]
            page_num = 1
            scores = []
            author_text = "plays"
            if bonus["score_num"] >= 1:
                author_text = "#" + str(bonus["score_num"])

            base_embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )
            base_embed.set_author(
                name=f"{recent_text} {author_text} for {username} | osu!{mode}",
                url=f"https://osu.ppy.sh/users/{user_id}",
                icon_url=f"https://osu.ppy.sh/images/flags/{country_code}.png"
            )
            base_embed.set_thumbnail(
                url=f"https://a.ppy.sh/{user_id}"
            )

            if bonus["score_num"] >= 1:
                maps = ""
                single_index = bonus["score_num"] - 1
                maps = self.fetch_top(data[single_index], maps, True)

                embed = base_embed.copy()
                embed.description = maps
                percent = "{:.2%}".format(data[bonus["score_num"] - 1]["weight"]["percentage"])
                pp = round(data[bonus["score_num"] - 1]["weight"]["pp"],1)
                embed.set_footer(text=f"Weighted pp | {pp}pp ({percent})")

                scores.append(embed)
            else:
                while page_num <= ceil(len(data) / 5):
                    start_index = (page_num - 1) * 5
                    end_index = (page_num - 1 ) * 5 + 5
                    maps = ""
                    for s in data[start_index:end_index]:
                        maps = self.fetch_top(s, maps)
                    
                    embed = base_embed.copy()
                    embed.description = maps
                    embed.set_footer(text=f"Page {page_num}/{ceil(len(data) / 5)}")

                    scores.append(embed)
                    page_num += 1
            
            await menu(ctx, scores, DEFAULT_CONTROLS if page_num > 1 else {"\N{CROSS MARK}": close_menu})
        else:
            await self.del_message(ctx, f"Looks like {player_id} doesn't have any top plays in that mode")

    async def rankings_embed(self, ctx, data, rtype, mode, country = None, variant = None):
        if data:
            page_num = 1
            users = []

            if mode == "osu":
                mode = "standard"
            elif mode == "fruits":
                mode = "catch"
            mode = mode.capitalize()

            if variant:
                variant = f"{variant} "
            else:
                variant = ""

            base_embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )

            if country:
                rtype = data["ranking"][0]["user"]["country"]["name"]
                base_embed.set_thumbnail(
                    url=f"https://osu.ppy.sh/images/flags/{country}.png"
                )

            base_embed.set_author(
                name=f"{rtype.capitalize()} {variant}ranking | osu!{mode}",
                icon_url="https://osu.ppy.sh/favicon-32x32.png"
            )

            while page_num <= 5:
                i = (page_num - 1) * 10
                user = ""
                while i < (page_num * 10):
                    user = self.fetch_rankings(data, i, user, country, rtype)
                    i += 1
                
                embed = base_embed.copy()
                embed.description = user
                if rtype == "score":
                    embed.set_footer(text=f"Page {page_num}/5 | Username ◈ Score ◈ Accuracy ◈ pp")
                else:
                    embed.set_footer(text=f"Page {page_num}/5 | Username ◈ pp ◈ Accuracy ◈ Play Count")

                users.append(embed)
                page_num += 1
            
            await menu(ctx, users, DEFAULT_CONTROLS)

    async def changelog_embed(self, ctx, data):
        if data:
            base_embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )
            active_users = ""
            if not data["builds"][0]["update_stream"]["name"] == "lazer" and not data["builds"][0]["update_stream"]["name"] == "web":
                active_users = f' ◈ Active users on branch: {humanize_number(data["builds"][0]["users"])}'
            base_embed.set_author(
                name=f'Changelog | {data["builds"][0]["update_stream"]["display_name"]}{active_users}',
                icon_url="https://osu.ppy.sh/favicon-32x32.png"
            )

            page_num = 1
            changelogs = []

            for build in data["builds"]:
                embed = base_embed.copy()
                embed.title = build["display_version"]
                embed.set_footer(
                    text=f'Page {page_num}/{len(data["builds"])}'
                )
                embed.timestamp = datetime.strptime(build["created_at"], "%Y-%m-%dT%H:%M:%S%z")

                categories = {}
                categories2 = {}
                categories3 = {}

                for entry in build["changelog_entries"]:
                    github_link = ""
                    dev = ""
                    if entry["github_pull_request_id"]:
                        github_link = f' ([{entry["repository"].replace("ppy/","")}#{entry["github_pull_request_id"]}]({entry["github_url"]}))'
                    if entry["github_user"]["user_url"]:
                        dev = f' [{entry["github_user"]["display_name"]}]({entry["github_user"]["user_url"]})'
                    elif entry["github_user"]["github_url"]:
                        dev = f' [{entry["github_user"]["display_name"]}]({entry["github_user"]["github_url"]})'
                    if entry["major"] == True:
                        the_title = f'**{entry["title"]}{github_link}{dev}**'
                        the_title2 = f'**{entry["title"]}{dev}**'
                        the_title3 = f'**{entry["title"]}**'
                    else:
                        the_title = f'{entry["title"]}{github_link}{dev}'
                        the_title2 = f'{entry["title"]}{dev}'
                        the_title3 = f'{entry["title"]}'

                    if entry["category"] in categories:
                        categories[entry["category"]].append(the_title)
                    else:
                        categories[entry["category"]] = [the_title]
                    if entry["category"] in categories2:
                        categories2[entry["category"]].append(the_title2)
                    else:
                        categories2[entry["category"]] = [the_title2]
                    if entry["category"] in categories3:
                        categories3[entry["category"]].append(the_title3)
                    else:
                        categories3[entry["category"]] = [the_title3]

                for category in categories.items():
                    entries = ""
                    for item in category[1]:
                        entries = entries + f"◈ {item}\n"
                    if len(entries) >= 1024:
                        entries = ""
                        for item in categories2[category[0]]:
                            entries = entries + f"◈ {item}\n"
                        if len(entries) >= 1024:
                            entries = ""
                            for item in categories3[category[0]]:
                                entries = entries + f"◈ {item}\n"
                            if len(entries) >= 1024:
                                entries = f'◈ Too big for embed. Category has: {len(category[1])} changes. [Read on the site](https://osu.ppy.sh/home/changelog/{data["builds"][0]["update_stream"]["name"]}/{build["version"]})'

                    embed.add_field(
                        name=category[0],
                        value=entries,
                        inline=False
                        )
                fields = [embed.title, embed.description, embed.footer.text, embed.author.name]

                fields.extend([field.name for field in embed.fields])
                fields.extend([field.value for field in embed.fields])

                total = ""
                for item in fields:
                    total += str(item) if str(item) != 'Embed.Empty' else ''

                if len(total) >= 6000:
                    embed = base_embed.copy()
                    embed.title = build["display_version"]
                    embed.set_footer(
                        text=f'Page {page_num}/{len(data["builds"])}'
                    )
                    embed.timestamp = datetime.strptime(build["created_at"], "%Y-%m-%dT%H:%M:%S%z")
                    for category in categories2.items():
                        entries = ""
                        for item in category[1]:
                            entries = entries + f"◈ {item}\n"
                        if len(entries) >= 1024:
                            entries = ""
                            for item in categories3[category[0]]:
                                entries = entries + f"◈ {item}\n"
                            if len(entries) >= 1024:
                                entries = f'◈ Too big for embed. Category has: {len(category[1])} changes. [Read on the site](https://osu.ppy.sh/home/changelog/{data["builds"][0]["update_stream"]["name"]}/{build["version"]})'

                        embed.add_field(
                        name=category[0],
                        value=entries,
                        inline=False
                        )

                    fields = [embed.title, embed.description, embed.footer.text, embed.author.name]

                    fields.extend([field.name for field in embed.fields])
                    fields.extend([field.value for field in embed.fields])

                    total = ""
                    for item in fields:
                        total += str(item) if str(item) != 'Embed.Empty' else ''

                    if len(total) >= 6000:
                        embed = base_embed.copy()
                        embed.title = build["display_version"]
                        embed.description = f'Too big to display in discord. [Read on the site](https://osu.ppy.sh/home/changelog/{data["builds"][0]["update_stream"]["name"]}/{build["version"]})'
                        embed.timestamp = datetime.strptime(build["created_at"], "%Y-%m-%dT%H:%M:%S%z")

                changelogs.append(embed)
                page_num += 1



            await menu(ctx, changelogs, DEFAULT_CONTROLS)

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

                if post_image.startswith("/"):
                    post_image = f"https://osu.ppy.sh/{post_image}"

                embed = base_embed.copy()
                embed.set_image(url=post_image)
                embed.set_author(name=post_author, icon_url=f"https://osu.ppy.sh/favicon-32x32.png")
                embed.url = f"https://osu.ppy.sh/home/news/{post_url}"
                embed.timestamp = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%S%z")
                embed.title = title
                embed.description = preview
                embed.set_footer(text=f"Post # {i + 1}/{len(news_posts)}")

                posts.append(embed)
            
            await menu(ctx, posts, DEFAULT_CONTROLS if len(data) > 1 else {"\N{CROSS MARK}": close_menu})

    async def map_embed(self, ctx, data):
        if data:
            mode_int = data["mode_int"]
            creator = data["beatmapset"]["creator"]
            creator_id = data["beatmapset"]["user_id"]
            title = data["beatmapset"]["title"]
            version = data["version"]
            artist = data["beatmapset"]["artist"]
            beatmapset_id = data["beatmapset"]["id"]
            approach = data["ar"]
            favorite_count = humanize_number(data["beatmapset"]["favourite_count"])
            source = data["beatmapset"]["source"]
            tags = data["beatmapset"]["tags"]
            status = data["beatmapset"]["status"]
            submitted_date = datetime.strptime(data["beatmapset"]["submitted_date"], "%Y-%m-%dT%H:%M:%S%z").strftime("%B %-d, %Y")
            last_updated = datetime.strptime(data["beatmapset"]["last_updated"], "%Y-%m-%dT%H:%M:%S%z").strftime("%B %-d, %Y")
            bpm = data["bpm"]
            ratings = list(data["beatmapset"]["ratings"])
            playcount = humanize_number(data["beatmapset"]["play_count"])
            count_circles = data["count_circles"]
            count_spinners = data["count_spinners"]
            count_sliders = data["count_sliders"]
            accuracy = data["accuracy"]
            drain = data["drain"]
            if not mode_int == 3:
                max_combo = humanize_number(data["max_combo"])
                max_combo_text = "Max Combo"
                stats = f"Circles: `{humanize_number(count_circles)}` | Sliders: `{humanize_number(count_sliders)}` | Spinners: `{humanize_number(count_spinners)}`"
                stats2 = f"CS: `{circle_size}` | AR: `{approach}` | OD: `{accuracy}` | HP: `{drain}`"
            else:
                max_combo = "{:.2%}".format(count_sliders / (count_sliders + count_circles))
                max_combo_text = "LN Ratio"
                stats = f"Notes: `{humanize_number(count_circles)}` | Long Notes: `{humanize_number(count_sliders)}`"
                stats2 = f"OD: `{accuracy}` | HP: `{drain}`"
            hit_length = time.gmtime(data["hit_length"])
            if not time.strftime("%H", hit_length) == "00":
                hit_length = time.strftime("%-H:%M:%S", hit_length)
            else:
                hit_length = time.strftime("%-M:%S", hit_length)
            difficulty_rating = data["difficulty_rating"]
            circle_size = data["cs"]
            total_length = time.gmtime(data["total_length"])
            if not time.strftime("%H", total_length) == "00":
                total_length = time.strftime("%-H:%M:%S", total_length)
            else:
                total_length = time.strftime("%-M:%S", total_length)
            url = data["url"]

            if mode_int == 3:
                version = re.sub(r"^\S*\s", "", data["version"])

            maps = []
            
            embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx),
                title=f'{artist} - {title} [{version}]',
                url=url
            )
            embed.set_author(
                name=f"Mapped by {creator} | osu!{MODE[mode_int].capitalize()}",
                url=f"https://osu.ppy.sh/users/{creator_id}",
                icon_url=f"https://a.ppy.sh/{creator_id}"
            )
            if status == "ranked":
                status = "Ranked on"
                embed.timestamp = datetime.strptime(data["beatmapset"]["ranked_date"], "%Y-%m-%dT%H:%M:%S%z")
            else:
                status = status.capitalize()

            embed.set_footer(
                text=f'Status: {status}'
            )
            embed.set_image(
                url=f"https://assets.ppy.sh/beatmaps/{beatmapset_id}/covers/cover.jpg"
            )
            embed.add_field(
                name="Stats",
                value=f"SR: `{difficulty_rating}★` | {stats2}\n"
                f"{stats} | Total: `{count_circles+count_sliders+count_spinners}`",
                inline=False
            )
            embed.add_field(
                name="Length / Drain",
                value=f'{total_length} / {hit_length}',
                inline=True
            )
            embed.add_field(
                name="BPM",
                value=f"{bpm}",
                inline=True
            )
            embed.add_field(
                name=max_combo_text,
                value=max_combo,
                inline=True
            )
            embed.add_field(
                name="Playcount",
                value=playcount,
                inline=True
            )
            embed.add_field(
                name="Favorites",
                value=favorite_count,
                inline=True
            )
            embed.add_field(
                name="Download",
                value=f'[Link](https://osu.ppy.sh/beatmapsets/{beatmapset_id}/download) ([No Video](https://osu.ppy.sh/beatmapsets/{beatmapset_id}/download?noVideo=1))',
                inline=True
            )
            if not sum(ratings) == 0:
                rating = 0
                p = 0
                s = 0
                star_emojis = ""

                for i in ratings:
                    rating = rating + p * i
                    p += 1
                final_rating = int(rating / sum(ratings))

                while s < final_rating:
                    star_emojis = star_emojis + ":star:"
                    s += 1
                embed.add_field(
                    name="Rating",
                    value=f"{star_emojis} {round(rating / sum(ratings), 1)} / 10",
                    inline=False
                )
            embed.add_field(
                name="Submitted",
                value=submitted_date,
                inline=True
            )
            embed.add_field(
                name="Last Update",
                value=last_updated,
                inline=True
            )
            if source:
                embed.add_field(
                    name="Source",
                    value=source,
                    inline=True
                )
            else:
                embed.add_field(
                    name="Source",
                    value="None",
                    inline=True
                )
            if tags:
                embed.add_field(
                    name="Tags",
                    value=f'`{tags.replace(" ", "` `")}`',
                    inline=False
                )

            maps.append(embed)
            
            await menu(ctx, maps, {"\N{CROSS MARK}": close_menu})

    def fetch_rankings(self, data, i, user, country, rtype):
        country_code = data["ranking"][i]["user"]["country"]["code"]
        username = data["ranking"][i]["user"]["username"]
        performance = humanize_number(data["ranking"][i]["pp"])
        accuracy = "{:.2%}".format(data["ranking"][i]["hit_accuracy"])
        score = humanize_number(data["ranking"][i]["ranked_score"])
        playcount = humanize_number(data["ranking"][i]["play_count"])
        if country:
            user = f"{user}\n**{i+1}.** | **{username}** ◈ {performance}pp ◈ {accuracy}% ◈ {playcount}\n"
        elif rtype == "score":
            user = f"{user}\n**{i+1}.** | :flag_{country_code.lower()}: **{username}** ◈ {score} ◈ {accuracy}% ◈ {performance}pp\n"
        else:
            user = f"{user}\n**{i+1}.** | :flag_{country_code.lower()}: **{username}** ◈ {performance}pp ◈ {accuracy} ◈ {playcount}\n"

        return user

    def fetch_top(self, data, maps, specific_score = False):
        current_date = datetime.now()
        beatmap = data["beatmap"]
        beatmapset = data["beatmapset"]
        statistics = data["statistics"]
        beatmapmode = beatmap["mode_int"]
        version = beatmap["version"]
        title = beatmapset["title"]
        beatmapurl = beatmap["url"]
        starrating = beatmap["difficulty_rating"]
        performance = humanize_number(round(data["pp"],2))
        rank = data["rank"]
        score = humanize_number(data["score"])
        combo = humanize_number(data["max_combo"])
        count_miss = humanize_number(statistics["count_miss"])
        count_50 = humanize_number(statistics["count_50"])
        count_100 = humanize_number(statistics["count_100"])
        count_300 = humanize_number(statistics["count_300"])
        count_geki = humanize_number(statistics["count_geki"])
        count_katu = humanize_number(statistics["count_katu"])
        accuracy = "{:.2%}".format(data["accuracy"])
        hits = f"{count_300}/{count_100}/{count_50}/{count_miss}"
        played = data["created_at"]

        date = current_date - datetime.strptime(played, "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
        time = re.split(r",\s", humanize_timedelta(timedelta=date))
        try:
            time = f"{time[0]} {time[1]}"
        except ValueError:
            pass
        except IndexError:
            time = time[0]

        if beatmapmode == 3:
            version = re.sub(r"^\S*\s", "", beatmap["version"])
            hits = f"{count_geki}/{count_300}/{count_katu}/{count_100}/{count_50}/{count_miss}"

        mods = ""
        if data["mods"]:
            mods = mods.join(data["mods"])
            mods = f" +{mods}"

        if specific_score == True:
            index = ""
        else:
            index = str(int(data["index"]) + 1) + ". "

        maps = f"{maps}\n**{index}[{title} - [{version}]]({beatmapurl}){mods}** [{starrating}★]\n{EMOJI[rank]} **{performance}pp** ◈ ({accuracy}) ◈ {score}\n**{combo}x** ◈ [{hits}] ◈ {time} ago\n"

        return maps

    async def no_user(self, ctx, player_id):
        await self.del_message(ctx, f"Could not find the user {player_id}")

    async def check_context(self, ctx, args, isfrom = None):
        if not isfrom == "map" and not isfrom == "changelog":
            args = [(x.lower()) for x in args]
            args = list(dict.fromkeys(args))
        params = None
        user = None
        url = None
        bonus = {"sort_recent": False, "score_num": 0, "user_name": None}
        
        if isfrom == "rankings":
            variant = None
            country = None
            mode = "osu"
            if "osu" in args or "standard" in args:
                await ctx.send(args)
                mode = "osu"
                try:
                    args.remove("osu")
                except:
                    args.remove("standard")
            elif "taiko" in args:
                mode = "taiko"
                args.remove("taiko")
            elif "catch" in args or "fruits" in args:
                mode = "fruits"
                try:
                    args.remove("catch")
                except:
                    args.remove("fruits")
            elif "mania" in args:
                mode = "mania"
                args.remove("mania")
                if "4k" in args:
                    variant = "4k"
                    args.remove("4k")
                elif "7k" in args:
                    variant = "7k"
                    args.remove("7k")
            if "score" in args and variant:
                await self.del_message(ctx, f"Can not keymodes with score rankings")
            elif "score" in args and len(args) > 1:
                await self.del_message(ctx, f"Score can not be used for country rankings")
            elif "score" in args:
                rtype = "score"
                args.remove("score")
            else:
                rtype = "performance"
            if len(args) > 0 and len(args) < 2:
                countrylen = args[0]
                if len(countrylen) > 2:
                    await self.del_message(ctx, f"Please use the 2 letter ISO code for countries")
                else:
                    country = countrylen.upper()
            elif len(args) >= 2:
                await self.del_message(ctx, f"There seems to be too many arguments or something went wrong")
            params = {}
            if country:
                params["country"] = country
            if variant:
                params["variant"] = variant
            return params, rtype, mode, country, variant
        elif isfrom == "map":
            if args.startswith("https://osu.ppy.sh/") or args.startswith("http://osu.ppy.sh/"):
                map_id = args.rsplit('/', 1)[-1]
                url = f"beatmaps/{map_id}"
            elif args.isdigit():
                map_id = args
                url = f"beatmaps/{map_id}"
            else:
                await self.del_message(ctx, f"That doesn't seem to be a valid map")
            return url
        elif isfrom == "changelog":
            params = {}
            if args == "stable":
                url = "changelog"
                params["stream"] = "stable40"
            elif args == "fallback":
                url = "changelog"
                params["stream"] = "stable"
            elif args == "beta":
                url = "changelog"
                params["stream"] = "beta40"
            elif args == "cuttingedge":
                url = "changelog"
                params["stream"] = "cuttingedge"
            elif args == "lazer":
                url = "changelog"
                params["stream"] = "lazer"
            elif args == "web":
                url = "changelog"
                params["stream"] = "web"
            else:
                await self.del_message(ctx, f"Please provide a valid release stream")
            return url, params
        else:
            if "-r" in args:
                if "-p" in args:
                    await self.del_message(ctx, "You can't use `-r` and `-p` at the same time")
                    return
                else:
                    bonus["sort_recent"] = True
                    args.remove("-r")
            elif "-p" in args:
                loc = args.index("-p")
                if not args[loc + 1].isdigit():
                    await self.del_message(ctx, "Please provide a number for `-p`")
                    return
                elif int(args[loc + 1]) <= 0 or int(args[loc + 1]) > 100:
                    await self.del_message(ctx, "Please use a number between 1-100 for `-p`")
                    return
                else:
                    bonus["score_num"] = int(args[loc + 1])
                    args.pop(loc + 1)
                    args.pop(loc)

            if len(args) < 1:
                user_id = await self.osuconfig.user(ctx.author).userid()
                bonus["user_name"] = await self.osuconfig.user(ctx.author).username()
                if user_id is None:
                    await self.profilelinking(ctx)
                else:
                    user = user_id
            elif "@" in args[0]:
                try:
                    member = await commands.MemberConverter().convert(ctx, args[0])
                    user = await self.osuconfig.user(member).userid()
                    bonus["user_name"] = await self.osuconfig.user(ctx.author).username()
                except:
                    pass
            else:
                user = args[0]
                bonus["user_name"] = args[0]

            if user is not None and len(args) > 0:
                if str(user).isnumeric() == False and isfrom != "profile":
                    data = await self.fetch_api(ctx, f"users/{user}/osu", user)
                    user = data["id"]

            if isfrom == "profile" and user:
                url = f"users/{user}"
            elif isfrom == "recent" and user:
                url = f"users/{user}/scores/recent"
            elif isfrom == "top" and user:
                url = f"users/{user}/scores/best"
            elif isfrom == "compare" and user:
                url = f"get_scores"
                params = {}
                params["u"] = user
            elif not user:
                try:
                    await self.del_message(ctx, f"{args[0]} does not have an account linked")
                except:
                    pass

            return url, params, user, bonus

    async def legacycompare(self, ctx, url, params, map_id):
        token = (await self.bot.get_shared_api_tokens("legacyosu")).get("token")

        data = None

        if token:
            data2 = await self.fetch_api(ctx, f"beatmaps/{map_id}")
            mode_int = data2["mode_int"]
            params["m"] = mode_int
            params["b"] = map_id
            params["type"] = "id"
            params["k"] = token
            endpoint = f"https://osu.ppy.sh/api/{url}"

            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, params=params) as r:
                    if not r.status == 404:
                        log.error(r)
                        data = await r.json(encoding="utf-8")
                        log.error(data)
        else:
            await self.del_message(ctx, "No api token")

        if data:
            rank = data[0]["rank"]
            score = humanize_number(data[0]["score"])
            if data[0]["pp"]:
                performance = humanize_number(round(float(data[0]["pp"]),2))
            else:
                performance = 0
            comboraw = int(data[0]["maxcombo"])
            count_miss = humanize_number(data[0]["countmiss"])
            count_50 = humanize_number(data[0]["count50"])
            count_100 = humanize_number(data[0]["count100"])
            count_300 = int(data[0]["count300"])
            count_geki = int(data[0]["countgeki"])
            count_katu = humanize_number(data[0]["countkatu"])
            username = data[0]["username"]
            played = data[0]["date"]

            artist = data2["beatmapset"]["artist"]
            title = data2["beatmapset"]["title"]
            version = data2["version"]
            difficulty_rating = data2["difficulty_rating"]
            beatmapurl = data2["url"]
            beatmapset_id = data2["beatmapset"]["id"]
            bmaccuracy = data2["accuracy"]
            drain = data2["drain"]
            circle_size = data2["cs"]
            approach = data2["ar"]
            creator = data2["beatmapset"]["creator"]
            creator_id = data2["beatmapset"]["user_id"]
            bpm = data2["bpm"]
            objects_count = data2["count_circles"] + data2["count_sliders"] + data2["count_spinners"]
            mapstatus = data2["beatmapset"]["status"]

            if mode_int == 3:
                version = re.sub(r"^\S*\s", "", data2["version"])
                comboratio = "Combo / Ratio"
                ratio = round(count_geki / count_300,2)
                combo = f"**{comboraw:,}x** / {ratio}"
                hits = f"{humanize_number(count_geki)}/{humanize_number(count_300)}/{count_katu}/{count_100}/{count_50}/{count_miss}"
                stats = f"OD: `{bmaccuracy}` | HP: `{drain}`"
            else:
                comboratio = "Combo"
                combo = f"**{comboraw}x**"
                hits = f"{humanize_number(count_300)}/{count_100}/{count_50}/{count_miss}"
                stats = f"CS: `{circle_size}` | AR: `{approach}` | OD: `{bmaccuracy}` | HP: `{drain}`"

            embed = discord.Embed(
                color=await self.bot.get_embed_color(ctx)
            )
            embed.set_author(
                name=f"{artist} - {title} [{version}] [{str(difficulty_rating)}★]",
                url=beatmapurl,
                icon_url=f'https://a.ppy.sh/{data[0]["user_id"]}'
            )
            embed.set_image(
                url=f"https://assets.ppy.sh/beatmaps/{beatmapset_id}/covers/cover.jpg"
            )
            embed.add_field(
                name="Grade",
                value=f"{EMOJI[rank]}",
                inline=True
            )
            embed.add_field(
                name="Score",
                value=f"{score}",
                inline=True
            )
            embed.add_field(
                name="Acc",
                value=f"N/A",
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
                value=f"Mapper: [{creator}](https://osu.ppy.sh/users/{creator_id}) | BPM: `{bpm}` | Objects: `{objects_count}` \n"
                f"Status: {inline(mapstatus.capitalize())} | {stats}",
                inline=False
            )
            embed.set_footer(
                text=f"{username} | osu!{MODE[mode_int].capitalize()} | Played"
            )
            embed.timestamp = datetime.strptime(played, "%Y-%m-%d %H:%M:%S")

            await ctx.send(embed=embed)
        else:
            await self.del_message(ctx, f"Looks like you don't have a score on that map")