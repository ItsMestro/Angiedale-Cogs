import discord
import re
from redbot.core import commands
from typing import Dict, Union
from datetime import timedelta
from redbot.core.commands import Converter

TIME_RE_STRING = r"|".join(
    [
        r"((?P<weeks>\d+?)\s?(weeks?|w))",
        r"((?P<days>\d+?)\s?(days?|d))",
        r"((?P<hours>\d+?)\s?(hours?|hrs|hr?))",
        r"((?P<minutes>\d+?)\s?(minutes?|mins?|m(?!o)))",  # prevent matching "months"
        r"((?P<seconds>\d+?)\s?(seconds?|secs?|s))",
    ]
)
TIME_RE = re.compile(TIME_RE_STRING, re.I)
TIME_SPLIT = re.compile(r"t(?:ime)?=")


class ReminderTime(Converter):
    """
    This will parse my defined multi response pattern and provide usable formats
    to be used in multiple reponses

    Taken from mutes
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
        result["reminder"] = argument.strip()
        return result


class SelfRole(commands.Converter):
    async def convert(self, ctx: commands.Context, arg: str) -> discord.Role:
        admin = ctx.command.cog
        if admin is None:
            raise commands.BadArgument(("The Admin cog is not loaded."))

        role_converter = commands.RoleConverter()
        role = await role_converter.convert(ctx, arg)

        selfroles = await admin.adminconfig.guild(ctx.guild).selfroles()

        if role.id not in selfroles:
            raise commands.BadArgument(("The provided role is not a valid selfrole."))
        return role
