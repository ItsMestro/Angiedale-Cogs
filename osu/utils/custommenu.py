# Original source of reaction-based menu idea from
# https://github.com/Lunar-Dust/Dusty-Cogs/blob/master/menu/menu.py
#
# Ported to Red V3 by Palm\_\_ (https://github.com/palmtree5)
# Modified by Mestro
import asyncio
import contextlib
import functools
from typing import Iterable, List, Union

import discord
from redbot.core import commands
from redbot.core.utils.menus import close_menu
from redbot.core.utils.predicates import ReactionPredicate


async def custom_menu(
    ctx: commands.Context,
    pages: Union[List[str], List[discord.Embed]],
    controls: dict,
    message: discord.Message = None,
    page: int = 0,
    timeout: float = 30.0,
    data: list = None,
    func=None,
):
    if not isinstance(pages[0], (discord.Embed, str)):
        raise RuntimeError("Pages must be of type discord.Embed or str")
    if not all(isinstance(x, discord.Embed) for x in pages) and not all(
        isinstance(x, str) for x in pages
    ):
        raise RuntimeError("All pages must be of the same type")
    for key, value in controls.items():
        maybe_coro = value
        if isinstance(value, functools.partial):
            maybe_coro = value.func
        if not asyncio.iscoroutinefunction(maybe_coro):
            raise RuntimeError("Function must be a coroutine")
    current_page = pages[0]

    if not message:
        if isinstance(current_page, discord.Embed):
            message = await ctx.send(embed=current_page)
        else:
            message = await ctx.send(current_page)
        # Don't wait for reactions to be added (GH-1797)
        # noinspection PyAsyncCall
        start_adding_reactions(message, controls.keys())
    else:
        try:
            if isinstance(current_page, discord.Embed):
                await message.edit(embed=current_page)
            else:
                await message.edit(content=current_page)
        except discord.NotFound:
            return

    try:
        react, user = await ctx.bot.wait_for(
            "reaction_add",
            check=ReactionPredicate.with_emojis(tuple(controls.keys()), message, ctx.author),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        if not ctx.me:
            return
        try:
            if message.channel.permissions_for(ctx.me).manage_messages:
                await message.clear_reactions()
            else:
                raise RuntimeError
        except (discord.Forbidden, RuntimeError):  # cannot remove all reactions
            for key in controls.keys():
                try:
                    await message.remove_reaction(key, ctx.bot.user)
                except discord.Forbidden:
                    return
                except discord.HTTPException:
                    pass
        except discord.NotFound:
            return
    else:
        return await controls[react.emoji](
            ctx, pages, controls, message, page, timeout, react.emoji, data, func
        )


async def custom_next_page(
    ctx: commands.Context,
    pages: list,
    controls: dict,
    message: discord.Message,
    page: int,
    timeout: float,
    emoji: str,
    data: list,
    func,
):
    perms = message.channel.permissions_for(ctx.me)
    if perms.manage_messages:  # Can manage messages, so remove react
        with contextlib.suppress(discord.NotFound):
            await message.remove_reaction(emoji, ctx.author)

    if page == len(data) - 1:
        page = 0
    else:
        page += 1

    embed = await func(ctx, data, page)

    return await custom_menu(
        ctx, embed, controls, message=message, page=page, timeout=timeout, data=data, func=func
    )


async def custom_previous_page(
    ctx: commands.Context,
    pages: list,
    controls: dict,
    message: discord.Message,
    page: int,
    timeout: float,
    emoji: str,
    data: list,
    func,
):
    perms = message.channel.permissions_for(ctx.me)
    if perms.manage_messages:  # Can manage messages, so remove react
        with contextlib.suppress(discord.NotFound):
            await message.remove_reaction(emoji, ctx.author)

    if page == 0:
        page = len(data) - 1
    else:
        page -= 1

    embed = await func(ctx, data, page)

    return await custom_menu(
        ctx, embed, controls, message=message, page=page, timeout=timeout, data=data, func=func
    )


async def custom_close_menu(
    ctx: commands.Context,
    pages: list,
    controls: dict,
    message: discord.Message,
    page: int,
    timeout: float,
    emoji: str,
    data: list,
    func,
):
    with contextlib.suppress(discord.NotFound):
        await message.delete()


def start_adding_reactions(
    message: discord.Message, emojis: Iterable[Union[str, discord.Emoji]]
) -> asyncio.Task:
    async def task():
        # The task should exit silently if the message is deleted
        with contextlib.suppress(discord.NotFound):
            for emoji in emojis:
                await message.add_reaction(emoji)

    return asyncio.create_task(task())


def custompage(bot, embeds):
    """And here's another one just for good measure."""

    if len(embeds) > 1:
        return {
            bot.get_emoji(755808378432913558): custom_previous_page,
            "\N{CROSS MARK}": custom_close_menu,
            bot.get_emoji(755808379170979971): custom_next_page,
        }
    else:
        return {"\N{CROSS MARK}": close_menu}
