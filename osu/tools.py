import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Union

import aiohttp
import discord
from redbot.core import Config, commands
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.menus import DEFAULT_CONTROLS, close_menu, next_page

log = logging.getLogger("red.angiedale.osu")

MODE_STANDARD = ["standard", "std", "osu", "o", "s", "0"]
MODE_TAIKO = ["taiko", "t", "1"]
MODE_CATCH = ["catch", "fruits", "ctb", "c", "f", "2"]
MODE_MANIA = ["mania", "m", "3"]

MODS = ["FM", "NM", "NF", "EZ", "HD", "HR", "DT", "HT", "NC", "FL", "SO", "FI", "MR"]
MODS_PRETTY = {
    "FM": "Free Mod",
    "NM": "No Mod",
    "NF": "No Fail",
    "EZ": "Easy",
    "HD": "Hidden",
    "HR": "Hard Rock",
    "DT": "Double Time",
    "HT": "Half Time",
    "NC": "Nightcore",
    "FL": "Flashlight",
    "SO": "Spun Out",
    "FI": "Fade In",
    "MR": "Mirror",
}

TIME_RE_STRING = r"|".join(
    [
        r"((?P<weeks>\d+?)\s?(weeks?|w))",
        r"((?P<days>\d+?)\s?(days?|d))",
        r"((?P<hours>\d+?)\s?(hours?|hrs|hr?))",
        r"((?P<minutes>\d+?)\s?(minutes?|mins?|m(?!o)))",  # prevent matching "months"
    ]
)
TIME_RE = re.compile(TIME_RE_STRING, re.I)
TIME_SPLIT = re.compile(r"t(?:ime)?=")


class API:
    """Class for handling OAuth."""

    def __init__(self):
        self.osu_bearer_cache: dict = {}

    async def maybe_renew_osu_bearer_token(self) -> None:
        if self.osu_bearer_cache:
            if self.osu_bearer_cache["expires_at"] - datetime.now().timestamp() <= 60:
                await self.get_osu_bearer_token()

    async def get_osu_bearer_token(self, api_tokens: Optional[Dict] = None) -> None:
        tokens = await self.bot.get_shared_api_tokens("osu") if api_tokens is None else api_tokens
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
                    "scope": "public",
                },
                headers={"Accept": "application/json", "Content-Type": "application/json"},
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

    async def fetch_api(self, url, ctx: commands.Context = None, params=None):
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
                            message = await ctx.send(
                                "API is either slow or unavaliable atm. I will keep trying to process your command."
                            )
                        await asyncio.sleep(10)
                    elif r.status == 502:
                        log.error("osu! api fetch 502 Error")
                        if ctx and not message:
                            message = await ctx.send(
                                "API is either slow or unavaliable atm. I will keep trying to process your command."
                            )
                        await asyncio.sleep(10)
                    elif r.status == 503:
                        log.error("osu! api fetch 503 Error")
                        if ctx and not message:
                            message = await ctx.send(
                                "API is either slow or unavaliable atm. I will keep trying to process your command."
                            )
                        await asyncio.sleep(10)
                    elif r.status == 200:
                        try:
                            await message.delete()
                        except:
                            pass
                        try:
                            data = await r.json(encoding="utf-8")
                            return data
                        except:
                            return
                    else:
                        log.error(f"API fetch error: {r.status}")
                        return


class Helper:
    """Helper class to find arguments."""

    def __init__(self):
        self.osuconfig: Config = Config.get_conf(self, identifier=1387000, cog_name="Osu")

    @staticmethod
    def findmap(map):
        if (
            map.startswith("https://osu.ppy.sh/b/")
            or map.startswith("http://osu.ppy.sh/b/")
            or map.startswith("https://osu.ppy.sh/beatmap")
            or map.startswith("http://osu.ppy.sh/beatmap")
        ):
            return re.sub("[^0-9]", "", map.rsplit("/", 1)[-1])
        elif map.isdigit():
            return map
        else:
            return None

    async def leaderboard(self, ctx, args):
        args = self.ttol(args)

        findself = False
        guildonly = False
        mode = None
        if "-me" in args:
            findself = True
            args.remove("-me")
        if "-g" in args:
            guildonly = True
            args.remove("-g")
        if "-m" in args:
            l = args.index("-m")
            try:
                if not int(args[l + 1]) in [0, 1, 2, 3]:
                    await del_message(
                        ctx, "Please use one of the mode numbers between 0-3 for `-m`"
                    )
                    return None, False, False, None
                else:
                    mode = int(args[l + 1])
                    args.pop(l + 1)
                    args.pop(l)
                    if mode == 0:
                        mode = "osu"
                    elif mode == 1:
                        mode = "taiko"
                    elif mode == 2:
                        mode = "fruits"
                    elif mode == 3:
                        mode = "mania"
            except IndexError:
                await del_message(ctx, "Please provide a number for `-m`")
                return None, False, False, None

        if len(args) > 1:
            await del_message(ctx, "Seems like you used too many arguments.")
            return None, False, False, None

        return args[0], guildonly, findself, mode

    @staticmethod
    def stream(stream):
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

        modes = {
            "osu": ["osu", "standard", "std"],
            "taiko": ["taiko"],
            "fruits": ["catch", "fruits", "ctb"],
            "mania": ["mania"],
        }
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

    @staticmethod
    def ttol(args):
        return [(x.lower()) for x in args]

    async def user(self, ctx: commands.Context, user, withleaderboard=False):
        userid = None
        if not user:
            userid = await self.osuconfig.user(ctx.author).userid()
            if not userid:
                return await profilelinking(ctx)
            elif withleaderboard:
                return userid, True
        else:
            if isinstance(user, discord.Member):
                userid = await self.osuconfig.user(user).userid()

            if not userid:
                tempuser = user
                if (
                    user.startswith("https://osu.ppy.sh/users/")
                    or user.startswith("http://osu.ppy.sh/users/")
                    or user.startswith("https://osu.ppy.sh/u/")
                    or user.startswith("http://osu.ppy.sh/u/")
                ):
                    cleanuser = (
                        user.replace("/osu", "")
                        .replace("/taiko", "")
                        .replace("/fruits", "")
                        .replace("/mania", "")
                    )
                    tempuser = cleanuser.rsplit("/", 1)[-1]

                if not str(tempuser).isnumeric():
                    data = await self.fetch_api(f"users/{tempuser}/osu", ctx)
                    if data:
                        return data["id"]
                else:
                    userid = tempuser

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
                        mapid = author_url.rsplit("/", 1)[-1]
                        if "+" in e.fields[0].value:
                            mods = e.fields[0].value.split("+")[1]
                            params = {"mods": [mods[i : i + 2] for i in range(0, len(mods), 2)]}
                        break
                if title_url:
                    if "beatmaps" in title_url:
                        mapid = title_url.rsplit("/", 1)[-1]
                        break
                if description:
                    mapid = description.group(1)
                    firstrow = e.description.split("\n")[0]
                    if "**+" in firstrow:
                        mods = firstrow.split("**+")[1].split("** [")[0]
                        params = {"mods": [mods[i : i + 2] for i in range(0, len(mods), 2)]}
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

    async def removetracking(self, user=None, channel=None, mode=None, dev=False):
        """Finds unnecessary tracking entries"""

        log.error(f"Removing {user} from tracking. mode: {mode}, channel: {channel}")

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
                                os.remove(f"{cog_data_path(self)}/tracking/{id}_{m}.json")
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
                            os.remove(f"{cog_data_path(self)}/tracking/{user}{m}.json")
                        except KeyError:
                            pass

    async def counttracking(self, channel=None, user=None, guild=None):
        """Helper for getting tracked users for guilds."""

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

    @staticmethod
    def mode_prettify(mode):
        """Turns any known mode identifier into a user friendly version for that mode."""

        mode = str(mode.lower())
        clean_mode = None

        if mode in MODE_STANDARD:
            clean_mode = "standard"
        elif mode in MODE_TAIKO:
            clean_mode = "taiko"
        elif mode in MODE_CATCH:
            clean_mode = "catch"
        elif mode in MODE_MANIA:
            clean_mode = "mania"

        return clean_mode

    @staticmethod
    def mode_api(mode):
        """Turns any known mode identifier into the api version for that mode."""

        mode = str(mode.lower())
        clean_mode = None

        if mode in MODE_STANDARD:
            clean_mode = "osu"
        elif mode in MODE_TAIKO:
            clean_mode = "taiko"
        elif mode in MODE_CATCH:
            clean_mode = "fruits"
        elif mode in MODE_MANIA:
            clean_mode = "mania"

        return clean_mode

    @staticmethod
    async def mod_parser(ctx: commands.Context, mods: tuple):
        """Return list of mods from mod tuple."""

        return_list = []
        freemod = False
        for entry in mods:
            entrylist = [entry[i : i + 2].upper() for i in range(0, len(entry), 2)]
            modlist = []
            for mod in entrylist:
                if mod in MODS:
                    modlist.append(mod)
                if mod == "FM":
                    freemod = True
            if not modlist == entrylist or len(modlist) == 0:
                return await del_message(
                    ctx,
                    f"The specified mod(s) are incorrect. Check valid mods with `{ctx.clean_prefix}osuweekly mods`.",
                )
            return_list.append(modlist)

        if freemod and len(return_list) > 1 or freemod and len(return_list[0]) > 1:
            return await del_message(ctx, "Nice try but freemod can only be used by iteself.")

        return return_list


class TimeConverter(commands.Converter):
    """
    This will parse my defined multi response pattern and provide usable formats
    to be used in multiple reponses
    """

    async def convert(self, ctx: commands.Context, argument: str) -> Union[timedelta, None]:
        time_split = TIME_SPLIT.split(argument)
        result: Union[timedelta, None] = None
        if time_split:
            maybe_time = time_split[-1]
        else:
            maybe_time = argument

        time_data = {}
        for time in TIME_RE.finditer(maybe_time):
            argument = argument.replace(time[0], "")
            for k, v in time.groupdict().items():
                if v:
                    time_data[k] = int(v)
        if time_data:
            result = timedelta(**time_data)
        return result


async def profilelinking(ctx: commands.Context):
    await ctx.maybe_send_embed(
        f"Looks like you haven't linked an account.\nYou can do so using `{ctx.clean_prefix}osulink <username>`"
        "\n\nAlternatively you can use the command\nwith a username or id after it."
    )


async def del_message(ctx, message_text: str, timeout: int = 10):
    """Simple function to sends a small embed that auto-deletes."""

    message = await ctx.maybe_send_embed(message_text)
    await asyncio.sleep(timeout)
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
    """Another one for two page embeds."""

    return {bot.get_emoji(755808377959088159): next_page, "\N{CROSS MARK}": close_menu}
