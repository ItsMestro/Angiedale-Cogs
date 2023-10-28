import re
from datetime import timedelta
from enum import Enum
from typing import Union

from redbot.core import commands

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


class BeatMode(Enum):
    NORMAL = 1
    TUNNELVISION = 2
    SECRET = 3


class BeatModeConverter(commands.Converter):
    """
    Tries to convert given string to a osubeat mode.
    """

    async def convert(self, ctx: commands.Context, arg: str) -> BeatMode:
        if arg.upper() == BeatMode.NORMAL.name or arg == "1" or arg == 1:
            return BeatMode.NORMAL
        elif arg.upper() == BeatMode.TUNNELVISION.name or arg == "2" or arg == 2:
            return BeatMode.TUNNELVISION
        elif arg.upper() == BeatMode.SECRET.name or arg == "3" or arg == 3:
            return BeatMode.SECRET
        else:
            raise commands.BadArgument(
                f"The mode has to be one of `Normal`, `Tunnelvision` or `Secret`."
            )


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
