import discord
import logging
import asyncio
import aiohttp
import re
from datetime import datetime
from typing import Optional, Dict

from redbot.core.utils.menus import close_menu, DEFAULT_CONTROLS, next_page
from redbot.core import commands, Config

log = logging.getLogger("red.angiedale.osu")

class API():
    """Class for handling OAuth."""

    def __init__(self, bot):
        self.bot = bot
        self.osu_bearer_cache: dict = {}

    async def maybe_renew_osu_bearer_token(self) -> None:
        if self.osu_bearer_cache:
            if self.osu_bearer_cache["expires_at"] - datetime.now().timestamp() <= 60:
                await self.get_osu_bearer_token()

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

    async def fetch_api(self, ctx, url, params = None, isfrom = None):
        await self.maybe_renew_osu_bearer_token()

        endpoint = f"https://osu.ppy.sh/api/v2/{url}"
        token = (await self.bot.get_shared_api_tokens("osu")).get("client_id")
        bearer = self.osu_bearer_cache.get("access_token", None)
        header = {"client_id": str(token)}
        if bearer is not None:
            header = {**header, "Authorization": f"Bearer {bearer}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=header, params=params) as r:
                if r.status == 404:
                    return
                else:
                    data = await r.json(encoding="utf-8")
                    return data

class Helper():
    """Helper class to find arguments."""

    def __init__(self, bot):
        self.bot = bot
        self.osuconfig: Config = Config.get_conf(self, 1387002, cog_name="Osu")

    def map(self, map):
        if map.startswith("https://osu.ppy.sh/") or map.startswith("http://osu.ppy.sh/"):
            return map.rsplit('/', 1)[-1]
        elif map.isdigit():
            return map
        else:
            return None

    def stream(self, stream):
        if stream == "stable":
            return "stable40"
        elif stream == "fallback":
            return "stable"
        elif stream == "beta":
            return "beta40"
        elif stream == "cuttingedge":
            return "cuttingedge"
        elif stream == "lazer":
            return "lazer"
        elif stream == "web":
            return "web"
        else:
            return None

    def ranking(self, args):
        args = self.ttol(args)

        modes = {"osu": ["osu", "standard", "std"], "taiko": ["taiko"], "fruits": ["catch", "fruits", "ctb"], "mania": ["mania"]}
        mode = None
        variant = None
        type = "performance"
        country = None

        for modelist in modes.items():
            if mode:
                break
            for m in modelist[1]:
                if m in args:
                    mode = modelist[0]
                    args.remove(m)
                    break
        
        if not mode:
            mode = "osu"
        elif mode == "mania":
            if "4k" in args:
                variant = "4k"
                args.remove("4k")
            elif "7k" in args:
                variant = "7k"
                args.remove("7k")

        if "score" in args:
            type = "score"
            args.remove("score")

        lcheck = len(args)

        if lcheck == 1:
            country = args[0].upper()
        elif lcheck > 1:
            mode = None

        return mode, type, country, variant

    def ttol(self, args):
        return [(x.lower()) for x in args]

    async def user(self, ctx, api, user):
        userid = None
        if not user:
            userid = await self.osuconfig.user(ctx.author).userid()
            if not userid:
                await self.profilelinking(ctx)
                return
        else:
            if isinstance(user, discord.Member):
                userid = await self.osuconfig.user(user).userid()

            if not userid:
                if not str(user).isnumeric():
                    data = await api.fetch_api(ctx, f"users/{user}/osu")
                    await asyncio.sleep(0.5)
                    if data:
                        userid = data["id"]
                else:
                    userid = user
        
            if not userid:
                try:
                    member = await commands.MemberConverter().convert(ctx, str(user))
                    userid = await self.osuconfig.user(member).userid()
                except:
                    await del_message(ctx, f"Could not find the user {user}.")

        return userid

    async def top(self, ctx, api, args):
        args = self.ttol(args)

        recent = False
        pos = None

        if "-r" in args:
            if "-p" in args:
                await del_message(ctx, "You can't use `-r` and `-p` at the same time.")
                return
            else:
                recent = True
                args.remove("-r")
        elif "-p" in args:
            l = args.index("-p")
            try:
                if int(args[l + 1]) <= 0 or int(args[l + 1]) > 100:
                    await del_message(ctx, "Please use a number between 1-100 for `-p`")
                    return
                else:
                    pos = int(args[l + 1])
                    args.pop(l + 1)
                    args.pop(l)
            except ValueError:
                await del_message(ctx, "Please provide a number for `-p`")
                return

        userid = await self.user(ctx, api, (args[0] if len(args) > 0 else None))
        
        if userid:
            return userid, recent, pos
        else:
            await del_message(ctx, f"Could not find the user {args[0]}.")
            return

    async def pp(self, ctx, api, args):
        args = self.ttol(args)

        pp = None

        if "-pp" in args:
            l = args.index("-pp")
            num = args[l + 1].replace(",", ".")
            try:
                pp = float(num)
                args.pop(l + 1)
                args.pop(l)
            except ValueError:
                await del_message(ctx, "Please provide a number for `-p`")
                return

        userid = await self.user(ctx, api, (args[0] if len(args) > 0 else None))
        
        if userid:
            return userid, pp
        else:
            await del_message(ctx, f"Could not find the user {args[0]}.")
            return

    async def history(self, ctx):
        params = None
        messages = []
        mapid = None

        async for m in ctx.channel.history(limit=50):
            if m.author.id == self.bot.user.id and m.type:
                try:
                    messages.append(m.embeds[0])
                except:
                    pass

        if messages:
            for e in messages:
                author_url = e.author.url
                title_url = e.url
                description = None

                if e.description:
                    description = re.search(r"beatmaps/(.*?)\)", e.description)
                if author_url:
                    if "beatmaps" in author_url:
                        mapid = author_url.rsplit('/', 1)[-1]
                        if "+" in e.fields[0].value:
                            mods = e.fields[0].value.split("+")[1]
                            params = {"mods": [mods[i:i+2] for i in range(0, len(mods), 2)]}
                        break
                if title_url:
                    if "beatmaps" in title_url:
                        mapid = title_url.rsplit('/', 1)[-1]
                        break
                if description:
                    mapid = description.group(1)
                    firstrow = e.description.split("\n")[0]
                    if "**+" in firstrow:
                        mods = firstrow.split("**+")[1].split("** [")[0]
                        params = {"mods": [mods[i:i+2] for i in range(0, len(mods), 2)]}
                    break

        return mapid, params

async def profilelinking(ctx):
    prefix = ctx.clean_prefix
    await ctx.maybe_send_embed(f"Looks like you haven't linked an account.\nYou can do so using `{prefix}osulink <username>`"
        "\n\nAlternatively you can use the command\nwith a username or id after it.")

async def del_message(ctx, message_text):
    """Simple function to sends a small embed that auto-deletes"""

    message = await ctx.maybe_send_embed(message_text)
    await asyncio.sleep(10)
    try:
        await message.delete()
    except (discord.errors.NotFound, discord.errors.Forbidden):
        pass

def multipage(embeds):
    """Dumb mini function for checking what emojis to use."""

    if len(embeds) > 1:
        return DEFAULT_CONTROLS
    else:
        return {"\N{CROSS MARK}": close_menu}

def singlepage():
    """Even dumber function that just returns the single page version."""

    return {"\N{CROSS MARK}": close_menu}

def togglepage(bot):
    """Another one for two page embeds"""

    return {bot.get_emoji(755808377959088159): next_page, "\N{CROSS MARK}": close_menu}