import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Dict, NewType, Union

from redbot.core import commands
from redbot.core.commands import BadArgument, Context, Converter
from redbot.core.utils.chat_formatting import inline

log = logging.getLogger("red.angiedale.mod.converter")

SNOWFLAKE_THRESHOLD = 2**63


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


class MuteTime(Converter):
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


class RawMessageIds(Converter):
    async def convert(self, ctx: Context, argument: str) -> int:
        if argument.isnumeric() and len(argument) >= 17 and int(argument) < SNOWFLAKE_THRESHOLD:
            return int(argument)

        raise BadArgument(("{} doesn't look like a valid message ID.").format(argument))
