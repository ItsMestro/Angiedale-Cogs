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
from redbot.core.bot import Red
from redbot.core import commands
from redbot.core.utils.predicates import ReactionPredicate


async def custom_menu(
    ctx: commands.Context,
    pages: Union[List[str], List[discord.Embed]],
    bot: Red,
    controls: dict = None,
    message: discord.Message = None,
    page: int = 0,
    chapter: int = 0,
    timeout: float = 30.0,
    data: list = None,
    func = None,
    has_chapters = False,
):
    if not isinstance(pages[0], (discord.Embed, str)):
        raise RuntimeError("Pages must be of type discord.Embed or str")
    if not all(isinstance(x, discord.Embed) for x in pages) and not all(
        isinstance(x, str) for x in pages
    ):
        raise RuntimeError("All pages must be of the same type")
    
    new_controls = check_controls(bot, pages, data, has_chapters)
    if new_controls != controls:
        if message:
            with contextlib.suppress(discord.Forbidden):
                await message.clear_reactions()
        controls = new_controls

    for key, value in controls.items():
        maybe_coro = value
        if isinstance(value, functools.partial):
            maybe_coro = value.func
        if not asyncio.iscoroutinefunction(maybe_coro):
            raise RuntimeError("Function must be a coroutine")
    if has_chapters:
        current_page = pages[page]
    else:
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
            ctx, pages, bot, controls, message, page, chapter, timeout, react.emoji, data, func, has_chapters
        )


async def custom_next_page(
    ctx: commands.Context,
    pages: list,
    bot: Red,
    controls: dict,
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: list,
    func,
    has_chapters: bool
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
        ctx, embed, bot, controls, message=message, page=page, chapter=chapter, timeout=timeout, data=data, func=func, has_chapters=has_chapters
    )


async def custom_previous_page(
    ctx: commands.Context,
    pages: list,
    bot: Red,
    controls: dict,
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: list,
    func,
    has_chapters: bool,
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
        ctx, embed, bot, controls, message=message, page=page, chapter=chapter, timeout=timeout, data=data, func=func, has_chapters=has_chapters
    )

async def custom_next_chapter(
    ctx: commands.Context,
    pages: list,
    bot: Red,
    controls: dict,
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: list,
    func,
    has_chapters: bool
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
        ctx, embed, bot, controls, message=message, page=page, chapter=chapter, timeout=timeout, data=data, func=func, has_chapters=has_chapters
    )

async def custom_previous_chapter(
    ctx: commands.Context,
    pages: list,
    bot: Red,
    controls: dict,
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: list,
    func,
    has_chapters: bool,
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
        ctx, embed, bot, controls, message=message, page=page, chapter=chapter, timeout=timeout, data=data, func=func, has_chapters=has_chapters
    )


async def custom_close_menu(
    ctx: commands.Context,
    pages: list,
    bot: Red,
    controls: dict,
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: list,
    func,
    has_chapters: bool,
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


def check_controls(bot: Red, embeds: Union[List[str], List[discord.Embed]], data: list, has_chapters: bool):
    """And here's another one just for good measure."""
    if len(embeds) > 1:
         output = {
            bot.get_emoji(755808378432913558): custom_previous_page,
            "\N{CROSS MARK}": custom_close_menu,
            bot.get_emoji(755808379170979971): custom_next_page,
        }
    else:
        output = {"\N{CROSS MARK}": custom_close_menu}
    
    if has_chapters:
        if len(data) > 1:
            output["\N{UP ARROW}"] = custom_previous_chapter
            output["\N{DOWN ARROW}"] = custom_next_chapter

    return output