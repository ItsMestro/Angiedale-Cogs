import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Dict, Optional

import aiohttp
import discord
from discord.errors import HTTPException
from redbot.core import Config, commands
from redbot.core.data_manager import bundled_data_path
from redbot.core.utils.menus import DEFAULT_CONTROLS, close_menu, next_page

log = logging.getLogger("red.angiedale.osu")

class API():
    """Class for handling OAuth.
    """

    def __init__(self):
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

    async def fetch_api(self, url, ctx: commands.Context = None, params = None):
        await self.maybe_renew_osu_bearer_token()

        endpoint = f"https://osu.ppy.sh/api/v2/{url}"
        token = (await self.bot.get_shared_api_tokens("osu")).get("client_id")
        bearer = self.osu_bearer_cache.get("access_token", None)
        header = {"client_id": str(token)}
        if bearer is not None:
            header = {**header, "Authorization": f"Bearer {bearer}"}

        message = None

        while True:
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=header, params=params) as r:
                    if r.status == 404:
                        return
                    elif r.status == 525:
                        log.error("osu! api fetch 525 Error")
                        if ctx and not message:
                            message = await ctx.send("API is either slow or unavaliable atm. I will keep trying to process your command.")
                        await asyncio.sleep(10)
                    elif r.status == 502:
                        log.error("osu! api fetch 502 Error")
                        if ctx and not message:
                            message = await ctx.send("API is either slow or unavaliable atm. I will keep trying to process your command.")
                        await asyncio.sleep(10)
                    elif r.status == 503:
                        log.error("osu! api fetch 503 Error")
                        if ctx and not message:
                            message = await ctx.send("API is either slow or unavaliable atm. I will keep trying to process your command.")
                        await asyncio.sleep(10)
                    else:
                        try:
                            await message.delete()
                        except:
                            pass
                        try:
                            data = await r.json(encoding="utf-8")
                            return data
                        except:
                            return

class Helper():
    """Helper class to find arguments.
    """

    def __init__(self):
        self.osuconfig: Config = Config.get_conf(self, identifier=1387000, cog_name="Osu")

    def findmap(self, map):
        if map.startswith("https://osu.ppy.sh/b/") or map.startswith("http://osu.ppy.sh/b/") or map.startswith("https://osu.ppy.sh/beatmap") or map.startswith("http://osu.ppy.sh/beatmap"):
            return re.sub("[^0-9]", "", map.rsplit('/', 1)[-1])
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

    async def user(self, ctx: commands.Context, user):
        userid = None
        if not user:
            userid = await self.osuconfig.user(ctx.author).userid()
            if not userid:
                await profilelinking(ctx)
                return None
        else:
            if isinstance(user, discord.Member):
                userid = await self.osuconfig.user(user).userid()

            if not userid:
                if not str(user).isnumeric():
                    data = await self.fetch_api(f"users/{user}/osu", ctx)
                    await asyncio.sleep(0.5)
                    if data:
                        userid = data["id"]
                elif user.startswith("https://osu.ppy.sh/users") or user.startswith("http://osu.ppy.sh/users") or user.startswith("https://osu.ppy.sh/u/") or user.startswith("http://osu.ppy.sh/u/"):
                    data = await self.fetch_api(f'users/{re.sub("[^0-9]", "", user.rsplit("/", 1)[-1])}/osu', ctx)
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

    async def top(self, ctx: commands.Context, args):
        args = self.ttol(args)

        recent = False
        pos = None

        if "-r" in args:
            if "-p" in args:
                await del_message(ctx, "You can't use `-r` and `-p` at the same time.")
                return None, None, None
            else:
                recent = True
                args.remove("-r")
        elif "-p" in args:
            l = args.index("-p")
            try:
                if int(args[l + 1]) <= 0 or int(args[l + 1]) > 100:
                    await del_message(ctx, "Please use a number between 1-100 for `-p`")
                    return None, None, None
                else:
                    pos = int(args[l + 1])
                    args.pop(l + 1)
                    args.pop(l)
            except ValueError:
                await del_message(ctx, "Please provide a number for `-p`")
                return None, None, None

        userid = await self.user(ctx, (args[0] if len(args) > 0 else None))
        
        if userid:
            return userid, recent, pos
        else:
            return None, None, None

    async def pp(self, ctx: commands.Context, args):
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
                return await del_message(ctx, "Please provide a number for `-p`")

        if len(args) > 1:
            await del_message(ctx, "You seem to have used too many arguments.")
        else:
            log.error(len(args))
            log.error(args)
            userid = await self.user(ctx, (args[0] if len(args) > 0 else None))
            
            if not userid and len(args) > 0:
                await del_message(ctx, f"Could not find the user {args[0]}.")

        return userid, pp

    async def history(self, ctx: commands.Context):
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

    async def topcompare(self, ctx: commands.Context, args):
        args = self.ttol(args)

        userid = None
        rank = None

        if "-p" in args:
            if len(args) > 2:
                await del_message(ctx, "Please use only one of the available arguments.")
                return
            elif len(args) < 2:
                await del_message(ctx, "Please provide a rank for `-p`")
                return
            else:
                l = args.index("-p")
                if not args[l + 1].isdigit():
                    await del_message(ctx, "Please user a number for `-p`")
                    return
                elif int(args[l + 1]) > 10000 or int(args[l + 1]) < 1:
                    await del_message(ctx, "Please provide a rank between 1-10000 for `-p`")
                    return
                else:
                    rank = int(args[l + 1])
                    return userid, rank
        else:
            userid = await self.user(ctx, args[0])
            return userid, rank

    async def removetracking(self, user = None, channel = None, mode = None, dev = False):
        """Finds unnecessary tracking entries
        """

        if dev == True:
            async with self.osuconfig.tracking() as modes:
                try:
                    modes[mode][user].remove(channel.id)
                except (KeyError, ValueError):
                    pass
                try:
                    modes[mode][user].append(channel.id)
                except KeyError:
                    modes[mode][user] = [channel.id]
        elif user and channel:
            async with self.osuconfig.tracking() as modes:
                done = False
                for m, us in modes.items():
                    for id, ch in us.items():
                        if user == id:
                            for c in ch:
                                if channel.guild.id == self.bot.get_channel(c).guild.id:
                                    ch.remove(c)
                                    done = True
                                    break
                        if len(ch) < 1:
                            us.pop(id)
                            try:
                                os.remove(f'{bundled_data_path(self)}/{id}{m}.json')
                            except:
                                pass
                        if done == True:
                            break

                    if mode == m:
                        try:
                            us[user].append(channel.id)
                        except KeyError:
                            us[user] = [channel.id]
        elif channel:
            async with self.osuconfig.tracking() as modes:
                for m, us in modes.items():
                    for id, ch in us.items():
                        if channel in ch:
                            ch.remove(channel)
        elif user:
            async with self.osuconfig.tracking() as modes:
                for m, us in modes.items():
                    if mode == m:
                        try:
                            us.pop(user)
                            os.remove(f'{bundled_data_path(self)}/{user}{m}.json')
                        except KeyError:
                            pass

    async def counttracking(self, channel = None, user = None, guild = None):
        """Helper for getting tracked users for guilds.
        """

        if channel:
            count = 0
            async with self.osuconfig.tracking() as modes:
                for us in modes.values():
                    for ch in us.values():
                        for c in ch:
                            if channel.guild.id == self.bot.get_channel(c).guild.id:
                                count += 1
        elif user:
            count = 0
            async with self.osuconfig.tracking() as modes:
                for us in modes.values():
                    for u in us.keys():
                        if u == user:
                            count += 1
        elif guild:
            count = []
            async with self.osuconfig.tracking() as modes:
                for m, us in modes.items():
                    for u, ch in us.items():
                        for c in ch:
                            i = self.bot.get_channel(c)
                            if guild == i.guild.id:
                                count.append({"id": u, "channel": i, "mode": m})
        return count

async def profilelinking(ctx: commands.Context):
    await ctx.maybe_send_embed(f"Looks like you haven't linked an account.\nYou can do so using `{ctx.clean_prefix}osulink <username>`"
        "\n\nAlternatively you can use the command\nwith a username or id after it.")

async def del_message(ctx, message_text: str):
    """Simple function to sends a small embed that auto-deletes.
    """

    message = await ctx.maybe_send_embed(message_text)
    await asyncio.sleep(10)
    try:
        await message.delete()
    except (discord.errors.NotFound, discord.errors.Forbidden):
        pass

def multipage(embeds):
    """Dumb mini function for checking what emojis to use.
    """

    if len(embeds) > 1:
        return DEFAULT_CONTROLS
    else:
        return {"\N{CROSS MARK}": close_menu}

def singlepage():
    """Even dumber function that just returns the single page version.
    """

    return {"\N{CROSS MARK}": close_menu}

def togglepage(bot):
    """Another one for two page embeds.
    """

    return {bot.get_emoji(755808377959088159): next_page, "\N{CROSS MARK}": close_menu}
