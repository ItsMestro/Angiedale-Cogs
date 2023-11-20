import logging
from typing import Union

import discord
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument
from redbot.core import commands

log = logging.getLogger("red.angiedale.utility")

SNOWFLAKE_THRESHOLD = 2**63


class RawMessageIds(Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> int:
        if argument.isnumeric() and len(argument) >= 17 and int(argument) < SNOWFLAKE_THRESHOLD:
            return int(argument)

        raise BadArgument(("{} doesn't look like a valid message ID.").format(argument))


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
