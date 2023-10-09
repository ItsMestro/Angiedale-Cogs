import re
from datetime import timedelta
from typing import Dict, Union

import discord
from redbot.core import commands
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import inline

# the following regex is slightly modified from Red
# it's changed to be slightly more strict on matching with finditer
# this is to prevent "empty" matches when parsing the full reason
# This is also designed more to allow time interval at the beginning or the end of the mute
# to account for those times when you think of adding time *after* already typing out the reason
# https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/redbot/core/commands/converter.py#L55
TIME_RE_STRING = r"|".join(
    [
        r"((?P<years>\d+?)\s?(years?|y))?",
        r"((?P<months>\d+?)\s?(months?|mo))?",
        r"((?P<weeks>\d+?)\s?(weeks?|w))",
        r"((?P<days>\d+?)\s?(days?|d))",
        r"((?P<hours>\d+?)\s?(hours?|hrs|hr?))",
        r"((?P<minutes>\d+?)\s?(minutes?|mins?|m(?!o)))",  # prevent matching "months"
        r"((?P<seconds>\d+?)\s?(seconds?|secs?|s))",
    ]
)
TIME_RE = re.compile(TIME_RE_STRING, re.I)
TIME_SPLIT = re.compile(r"t(?:ime)?=")


class SelfRole(commands.Converter):
    async def convert(self, ctx: commands.Context, arg: str) -> discord.Role:
        admin = ctx.command.cog
        if admin is None:
            raise commands.BadArgument(("The Admin cog is not loaded."))

        selfroles = await admin.config.guild(ctx.guild).selfroles()
        role_converter = commands.RoleConverter()

        pool = set()
        async for role_id in AsyncIter(selfroles, steps=100):
            role = ctx.guild.get_role(role_id)
            if role is None:
                continue
            if role.name.casefold() == arg.casefold():
                pool.add(role)

        if not pool:
            role = await role_converter.convert(ctx, arg)
            if role.id not in selfroles:
                raise commands.BadArgument(
                    ('The role "{role_name}" is not a valid selfrole.').format(role_name=role.name)
                )
        elif len(pool) > 1:
            raise commands.BadArgument(
                (
                    "This selfrole has more than one case insensitive match. "
                    "Please ask a moderator to resolve the ambiguity, or "
                    "use the role ID to reference the role."
                )
            )
        else:
            role = pool.pop()
        return role


class MuteTime(commands.Converter):
    """
    This will parse my defined multi response pattern and provide usable formats
    to be used in multiple responses
    """

    async def convert(
        self, ctx: commands.Context, argument: str
    ) -> Dict[str, Union[timedelta, str, None]]:
        time_split = TIME_SPLIT.split(argument)
        result: Dict[str, Union[timedelta, str, None]] = {}
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
            result["duration"] = timedelta(**time_data)
        result["reason"] = argument.strip()
        return result


class RRoleType:
    NORMAL = 1
    ONCE = 2
    REMOVE = 3
    TOGGLE = 4


def rroletype_solver(type: str):
    if type == "NORMAL" or type == "1" or type == 1:
        return RRoleType.NORMAL
    elif type == "ONCE" or type == "2" or type == 2:
        return RRoleType.ONCE
    elif type == "REMOVE" or type == "3" or type == 3:
        return RRoleType.REMOVE
    elif type == "TOGGLE" or type == "4" or type == 4:
        return RRoleType.TOGGLE
    else:
        return None


class RRoleTypeConverter(commands.Converter):
    """Converter for reaction role types"""

    async def convert(self, ctx: commands.Context, arg: str) -> RRoleType:
        type = rroletype_solver(arg.upper())
        if not type:
            typestr = ""
            for t in [
                attr
                for attr in dir(RRoleType)
                if not callable(getattr(RRoleType, attr)) and not attr.startswith("__")
            ]:
                typestr += f"{inline(t)}, "
            raise commands.BadArgument(f"The type has to be one of {typestr[:len(typestr) - 2]}")

        return type


class TrueEmojiConverter(commands.EmojiConverter):
    async def convert(self, ctx: commands.Context, argument: str) -> Union[discord.Emoji, str]:
        try:
            emoji = await super().convert(ctx, argument)
        except commands.BadArgument:
            try:
                await ctx.message.add_reaction(argument)
                await ctx.message.remove_reaction(argument, ctx.guild.me)
            except discord.HTTPException:
                raise commands.EmojiNotFound(argument)
            else:
                emoji = argument
        return emoji
