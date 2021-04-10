import re
from typing import TYPE_CHECKING, NewType

from redbot.core.commands import BadArgument, Converter
from redbot.core.utils.chat_formatting import inline

_id_regex = re.compile(r"([0-9]{15,20})$")
_mention_regex = re.compile(r"<@!?([0-9]{15,20})>$")


class RawUserIds(Converter):
    async def convert(self, ctx, argument):
        # This is for the hackban and unban commands, where we receive IDs that
        # are most likely not in the guild.
        # Mentions are supported, but most likely won't ever be in cache.

        if match := _id_regex.match(argument) or _mention_regex.match(argument):
            return int(match.group(1))

        raise BadArgument(("{} doesn't look like a valid user ID.").format(argument))


# Duplicate of redbot.cogs.cleanup.converters.PositiveInt
PositiveInt = NewType("PositiveInt", int)
if TYPE_CHECKING:
    positive_int = PositiveInt
else:

    def positive_int(arg: str) -> int:
        try:
            ret = int(arg)
        except ValueError:
            raise BadArgument(_("{arg} is not an integer.").format(arg=inline(arg)))
        if ret <= 0:
            raise BadArgument(_("{arg} is not a positive integer.").format(arg=inline(arg)))
        return ret
