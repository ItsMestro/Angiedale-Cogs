import discord
import logging
import json
import aiohttp
import asyncio
import re
from datetime import datetime
from typing import Optional, List, Dict

from redbot.core import checks, commands, Config
from redbot.core.utils._internal_utils import send_to_owners_with_prefix_replaced

from redbot.core.bot import Red

log = logging.getLogger("red.angiedale.osu")


class Osu(commands.Cog):
    """osu! commands.
    
    Has the ability to fetch profile info.
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
            message = (
                "You need a client secret key if you want to use the osu API on this cog.\n"
                "Acquire one from here: https://osu.ppy.sh/home/account/edit.\n"
                "Then copy your client ID and your client secret into:\n"
                "{command}"
                "\n\n"
                "Note: These tokens are sensitive and should only be used in a private channel "
                "or in DM with the bot."
            ).format(
                command="`[p]set api osu client_id {} client_secret {}`".format(
                    ("<your_client_id_here>"), ("<your_client_secret_here>")
                )
            )
            await send_to_owners_with_prefix_replaced(self.bot, message)
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

    @commands.command(aliases=["standard", "std"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu(self, ctx: commands.Context, player_id: str = None):
        """Get profile info of a player."""
        user_id = await self.osuconfig.user(ctx.author).userid()
        if player_id is None and user_id is None:
            await self.profilelinking(ctx)
        else:
            if player_id is None:
                player_id = user_id

            await self.maybe_renew_osu_bearer_token()

            token = (await self.bot.get_shared_api_tokens("osu")).get("client_id")
            endpoint = "https://osu.ppy.sh/api/v2/users/"
            bearer = self.osu_bearer_cache.get("access_token", None)
            args = f"{player_id}/osu"

            data = await self.fetch_api(ctx, bearer, token, endpoint, args)
            await self.profile_embed(ctx, data, player_id, "Standard")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def taiko(self, ctx: commands.Context, player_id: str):
        """Get profile info of a player."""
        user_id = await self.osuconfig.user(ctx.author).userid()
        if player_id is None and user_id is None:
            await self.profilelinking(ctx)
        else:
            if player_id is None:
                player_id = user_id

            await self.maybe_renew_osu_bearer_token()

            token = (await self.bot.get_shared_api_tokens("osu")).get("client_id")
            endpoint = "https://osu.ppy.sh/api/v2/users/"
            bearer = self.osu_bearer_cache.get("access_token", None)
            args = f"{player_id}/taiko"

            data = await self.fetch_api(ctx, bearer, token, endpoint, args)
            await self.profile_embed(ctx, data, player_id, "Taiko")

    @commands.command(aliases=["catch", "ctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def fruits(self, ctx: commands.Context, player_id: str):
        """Get profile info of a player."""
        user_id = await self.osuconfig.user(ctx.author).userid()
        if player_id is None and user_id is None:
            await self.profilelinking(ctx)
        else:
            if player_id is None:
                player_id = user_id

            await self.maybe_renew_osu_bearer_token()

            token = (await self.bot.get_shared_api_tokens("osu")).get("client_id")
            endpoint = "https://osu.ppy.sh/api/v2/users/"
            bearer = self.osu_bearer_cache.get("access_token", None)
            args = f"{player_id}/fruits"

            data = await self.fetch_api(ctx, bearer, token, endpoint, args)
            await self.profile_embed(ctx, data, player_id, "Catch")

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def mania(self, ctx: commands.Context, player_id: str):
        """Get profile info of a player."""
        user_id = await self.osuconfig.user(ctx.author).userid()
        if player_id is None and user_id is None:
            await self.profilelinking(ctx)
        else:
            if player_id is None:
                player_id = user_id

            await self.maybe_renew_osu_bearer_token()

            token = (await self.bot.get_shared_api_tokens("osu")).get("client_id")
            endpoint = "https://osu.ppy.sh/api/v2/users/"
            bearer = self.osu_bearer_cache.get("access_token", None)
            args = f"{player_id}/mania"

            data = await self.fetch_api(ctx, bearer, token, endpoint, args)
            await self.profile_embed(ctx, data, player_id, "Mania")

    async def fetch_api(self, ctx, bearer, token, endpoint, args):
        url = f"{endpoint}{args}"
        header = {"client_id": str(token)}
        if bearer is not None:
            header = {**header, "Authorization": f"Bearer {bearer}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=header) as r:
                if r.status == 404:
                    data = 404
                else:
                    data = await r.json(encoding="utf-8")
            return data

    async def profile_embed(self, ctx, data, player_id, mode):
        if data == 404:
            message = await ctx.send(f"Could not find the user {player_id}")
            await asyncio.sleep(10)
            try:
                await message.delete()
            except (discord.errors.NotFound, discord.errors.Forbidden):
                pass
        else:
            statistics = data["statistics"]
            rank = statistics["rank"]
            user_id = data["id"]
            country = data["country_code"]
            username = data["username"]
            ranking = rank["global"]
            country_ranking = rank["country"]
            accuracy = round(float(statistics["hit_accuracy"]),2)
            playcount = statistics["play_count"]
            last_online = list(map(int, re.split(r"-|T|:|\+", data["last_visit"])))
            max_combo = statistics["maximum_combo"]
            level = statistics["level"]
            level_current = level["current"]
            level_progress = level["progress"]
            performance = statistics["pp"]
            grades = statistics["grade_counts"]
            grade_ss = grades["ss"]
            grade_ssh = grades["ssh"]
            grade_s = grades["s"]
            grade_sh = grades["sh"]
            grade_a = grades["a"]
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
                    performancevalue = f"{performance}pp\n{performance_7k}pp | **7k**"
                elif performance_7k == 0:
                    performancevalue = f"{performance}pp\n{performance_4k}pp | **4k**"
                else:
                    performancevalue = f"{performance}pp\n{performance_4k}pp | **4k**\n{performance_7k}pp | **7k**"
                
                if ranking_4k == None and ranking_7k == None:
                    rankingvalue = f"#{ranking} ({country} #{country_ranking})"
                elif ranking_4k == None:
                    rankingvalue = f"#{ranking} ({country} #{country_ranking})\n#{ranking_7k} ({country} #{country_ranking_7k}) | **7k**"
                elif ranking_7k == None:
                    rankingvalue = f"#{ranking} ({country} #{country_ranking})\n#{ranking_4k} ({country} #{country_ranking_4k}) | **4k**"
                else:
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
                value=f"<:SSHRank:794603347498762240> {grade_ssh} <:SSRank:794603347247890443> {grade_ss} <:SHRank:794603346870796340> {grade_sh} <:SRank:794603347289833472> {grade_s} <:ARank:794603347537297448> {grade_a}",
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
                embed.timestamp = datetime(last_online[0], last_online[1], last_online[2], hour=last_online[3], minute=last_online[4], second=last_online[5])
                
            await ctx.send(embed=embed)

    @commands.command(hidden=True)
    @commands.cooldown(5, 10, commands.BucketType.user)
    async def et(self, ctx, user: discord.Member=None):
        """Marcinho is ET"""

        author = ctx.message.author
        if not user:
            message = f"{author.mention} thinks <@253588524652036096> is ET"
        else:
            message = f"{author.mention} thinks {user.mention} is ET"

        await ctx.send(message)

    async def profilelinking(self, ctx):
        prefix = ctx.clean_prefix
        message = await ctx.maybe_send_embed(f"Looks like you haven't linked an account.\nYou can do so using `{prefix}osulink <username>`"
            "\n\nAlternatively you can use the command\nwith a username or id after it")
        await asyncio.sleep(10)
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass

    @commands.command()
    async def osulink(self, ctx, username: str):
        """Link your account with an osu! user"""

        await self.maybe_renew_osu_bearer_token()

        token = (await self.bot.get_shared_api_tokens("osu")).get("client_id")
        endpoint = f"https://osu.ppy.sh/api/v2/users/"
        bearer = self.osu_bearer_cache.get("access_token", None)
        args = username

        data = await self.fetch_api(ctx, bearer, token, endpoint, args)

        if data == 404:
            message = await ctx.send(f"Could not find a user matching {username}")
            await asyncio.sleep(10)
            try:
                await message.delete()
            except (discord.errors.NotFound, discord.errors.Forbidden):
                pass
        else:
            username = data["username"]
            user_id = data["id"]
            await self.osuconfig.user(ctx.author).username.set(username)
            await self.osuconfig.user(ctx.author).userid.set(user_id)
            await ctx.send(f"{username} is successfully linked to your account!")