from contextlib import suppress
from typing import Any

import discord
from dateutil.relativedelta import relativedelta
from redbot.core import commands
from redbot.core.utils.chat_formatting import humanize_list

MAX_EMBED_SIZE = 5900
MAX_EMBED_FIELDS = 20
MAX_EMBED_FIELD_SIZE = 1024


async def reply(
    ctx: commands.Context, content: str | None = None, **kwargs: Any  # noqa: ANN401
) -> None:
    """Safely reply to a command message.

    If the command is in a guild, will reply, otherwise will send a message like normal.
    Pre discord.py 1.6, replies are just messages sent with the users mention prepended.
    """
    if ctx.guild:
        if (
            hasattr(ctx, "reply")
            and ctx.channel.permissions_for(ctx.guild.me).read_message_history
        ):
            mention_author = kwargs.pop("mention_author", False)
            kwargs.update(mention_author=mention_author)
            with suppress(discord.HTTPException):
                await ctx.reply(content=content, **kwargs)
                return
        allowed_mentions = kwargs.pop(
            "allowed_mentions",
            discord.AllowedMentions(users=False),
        )
        kwargs.update(allowed_mentions=allowed_mentions)
        await ctx.send(content=f"{ctx.message.author.mention} {content}", **kwargs)
    else:
        await ctx.send(content=content, **kwargs)


async def embed_splitter(
    embed: discord.Embed, destination: discord.abc.Messageable | None = None
) -> list[discord.Embed]:
    """Take an embed and split it so that each embed has at most 20 fields and a length of 5900.

    Each field value will also be checked to have a length no greater than 1024.

    If supplied with a destination, will also send those embeds to the destination.
    """
    embed_dict = embed.to_dict()

    # Check and fix field value lengths
    modified = False
    if "fields" in embed_dict:
        for field in embed_dict["fields"]:
            if len(field["value"]) > MAX_EMBED_FIELD_SIZE:
                field["value"] = field["value"][: MAX_EMBED_FIELD_SIZE - 3] + "..."
                modified = True
    if modified:
        embed = discord.Embed.from_dict(embed_dict)

    # Short circuit
    if len(embed) <= MAX_EMBED_SIZE and (
        "fields" not in embed_dict or len(embed_dict["fields"]) <= MAX_EMBED_FIELDS
    ):
        if destination:
            await destination.send(embed=embed)
        return [embed]

    # Nah, we're really doing this
    split_embeds: list[discord.Embed] = []
    fields = embed_dict["fields"] if "fields" in embed_dict else []
    embed_dict["fields"] = []

    for field in fields:
        embed_dict["fields"].append(field)
        current_embed = discord.Embed.from_dict(embed_dict)
        if len(current_embed) > MAX_EMBED_SIZE or len(embed_dict["fields"]) > MAX_EMBED_FIELDS:
            embed_dict["fields"].pop()
            current_embed = discord.Embed.from_dict(embed_dict)
            split_embeds.append(current_embed.copy())
            embed_dict["fields"] = [field]

    current_embed = discord.Embed.from_dict(embed_dict)
    split_embeds.append(current_embed.copy())

    if destination:
        for split_embed in split_embeds:
            await destination.send(embed=split_embed)
    return split_embeds


@staticmethod
def humanize_relativedelta(relative_delta: relativedelta | dict) -> str:
    """Convert relativedelta (or a dict of its keyword arguments) into a humanized string."""
    if isinstance(relative_delta, dict):
        relative_delta = relativedelta(**relative_delta)
    periods = [
        ("year", "years", relative_delta.years),
        ("month", "months", relative_delta.months),
        ("week", "weeks", relative_delta.weeks),
        ("day", "days", relative_delta.days % 7),
        ("hour", "hours", relative_delta.hours),
        ("minute", "minutes", relative_delta.minutes),
        ("second", "seconds", relative_delta.seconds),
    ]

    strings = []
    for period_name, plural_period_name, time_unit in periods:
        if time_unit == 0:
            continue
        unit = plural_period_name if time_unit not in (1, -1) else period_name
        strings.append(f"{time_unit} {unit}")

    if not strings:
        strings.append("0 seconds")
    return humanize_list(strings)
