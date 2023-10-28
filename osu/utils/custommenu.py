# Original source of reaction-based menu idea from
# https://github.com/Lunar-Dust/Dusty-Cogs/blob/master/menu/menu.py
#
# Ported to Red V3 by Palm\_\_ (https://github.com/palmtree5)
# Modified by Mestro
import asyncio
import contextlib
import functools
import logging
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, TypeVar, Union

import discord
from ossapi import Score as OsuScore
from redbot.core import commands
from redbot.core.utils.predicates import ReactionPredicate

log = logging.getLogger("red.angiedale.osu")

_T = TypeVar("_T")
_PageList = TypeVar("_PageList", List[str], List[discord.Embed])
_ReactableEmoji = Union[str, discord.Emoji]
_ControlCallable = Callable[
    [
        commands.Context,
        _PageList,
        discord.Message,
        int,
        int,
        float,
        str,
        Optional[OsuScore],
        Optional[functools.partial],
        bool,
    ],
    _T,
]


class CustomButton(discord.ui.Button):
    def __init__(self, emoji: discord.PartialEmoji, func: _ControlCallable):
        if emoji == "\N{CROSS MARK}":
            style = discord.ButtonStyle.red
            emoji = "\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}"
        else:
            style = discord.ButtonStyle.grey
        super().__init__(emoji=emoji, style=style)
        self.func = func

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            await self.func(
                self.view.ctx,
                self.view.pages,
                self.view.controls,
                self.view.message,
                self.view.page,
                self.view.chapter,
                self.view.timeout,
                self.emoji,
                self.view.data,
                self.view.funct,
                self.view.run_funct,
            )
        except Exception as e:
            log.info(e)
            pass


class CustomView(discord.ui.View):
    def __init__(
        self,
        ctx: commands.Context,
        pages: list,
        controls: Mapping[str, _ControlCallable],
        page: int,
        chapter: int,
        timeout: float,
        data: List[OsuScore],
        funct: Optional[functools.partial],
        run_funct: bool,
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.pages = pages
        self.controls = controls
        self.page = page
        self.chapter = chapter
        self.data = data
        self.funct = funct
        self.run_funct = run_funct

        for emoji, func in controls.items():
            self.add_item(CustomButton(emoji=emoji, func=func))

    async def start(self, ctx: commands.Context):
        self.message = await ctx.send(**{"view": self, "embeds": self.pages})

    async def on_timeout(self):
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            # message could no longer be there or we may not be able to edit/delete it anymore
            pass

    async def get_view(self) -> Dict[str, Optional[Any]]:
        return {"view": self}

    @property
    def source(self):
        return self._source


_active_menus: Dict[int, CustomView] = {}


async def custom_menu(
    ctx: commands.Context,
    pages: _PageList,
    controls: Optional[Mapping[str, _ControlCallable]] = None,
    message: discord.Message = None,
    page: int = 0,
    chapter: int = 0,
    timeout: float = 30.0,
    data: List[OsuScore] = None,
    funct: Optional[functools.partial] = None,
    run_funct: bool = True,
) -> _T:
    if message is not None and message.id in _active_menus:
        # prevents the expected callback from going any further
        # our custom button will always pass the message the view is
        # attached to, allowing one to send multiple menus on the same
        # context.
        view = _active_menus[message.id]
        view.page = page
        view.chapter = chapter
        new_page = await view.get_view()
        new_page.update({"embeds": pages})
        await view.message.edit(**new_page)
        return
    if not isinstance(pages[0], (discord.Embed, str)):
        raise RuntimeError("Pages must be of type discord.Embed or str")
    if not all(isinstance(x, discord.Embed) for x in pages) and not all(
        isinstance(x, str) for x in pages
    ):
        raise RuntimeError("All pages must be of the same type")

    # new_controls = check_controls(pages, data, has_chapters)
    # if new_controls != controls:
    #     if message:
    #         with contextlib.suppress(discord.Forbidden):
    #             await message.clear_reactions()
    #     controls = new_controls

    for key, value in controls.items():
        maybe_coro = value
        if isinstance(value, functools.partial):
            maybe_coro = value.func
        if not asyncio.iscoroutinefunction(maybe_coro):
            raise RuntimeError("Function must be a coroutine")

    if await ctx.bot.use_buttons() and message is None:
        # Only send the button version if `message` is None
        # This is because help deals with this menu in weird ways
        # where the original message is already sent prior to starting.
        # This is not normally the way we recommend sending this because
        # internally we already include the emojis we expect.

        view = CustomView(ctx, pages, controls, page, chapter, timeout, data, funct, run_funct)
        await view.start(ctx)
        _active_menus[view.message.id] = view
        await view.wait()
        del _active_menus[view.message.id]
        return

        # if controls == None:  # Deal with this another time
        #     view = SimpleMenu(pages, timeout=timeout)
        #     await view.start(ctx)
        #     await view.wait()
        #     return
        # else:
        #     view = SimpleMenu(
        #         pages=pages,
        #         timeout=timeout,
        #         chapter=chapter,
        #         data=data,
        #         funct=funct,
        #         has_chapters=has_chapters,
        #     )
        #     view.remove_item(view.last_button)
        #     view.remove_item(view.first_button)
        #     has_next = False
        #     has_prev = False
        #     has_close = False
        #     to_add = {}
        #     for emoji, func in controls.items():
        #         part_emoji = discord.PartialEmoji.from_str(str(emoji))
        #         if func == check_controls:  # Temporary
        #             has_next = True
        #             if part_emoji != view.forward_button.emoji:
        #                 view.forward_button.emoji = part_emoji
        #         elif func == check_controls:  # Temporary
        #             has_prev = True
        #             if part_emoji != view.backward_button.emoji:
        #                 view.backward_button.emoji = part_emoji
        #         elif func == check_controls:  # Temporary
        #             has_close = True
        #         else:
        #             to_add[part_emoji] = func
        #     if not has_next:
        #         view.remove_item(view.forward_button)
        #     if not has_prev:
        #         view.remove_item(view.backward_button)
        #     if not has_close:
        #         view.remove_item(view.stop_button)
        #     for emoji, func in to_add.items():
        #         view.add_item(_GenericButton(emoji, func))
        #     await view.start(ctx)
        #     _active_menus[view.message.id] = view
        #     await view.wait()
        #     del _active_menus[view.message.id]
        #     return
        """"""

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
        predicates = ReactionPredicate.with_emojis(tuple(controls.keys()), message, ctx.author)
        tasks = [
            asyncio.create_task(ctx.bot.wait_for("reaction_add", check=predicates)),
            asyncio.create_task(ctx.bot.wait_for("reaction_remove", check=predicates)),
        ]
        done, pending = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

        if len(done) == 0:
            raise asyncio.TimeoutError()
        react, user = done.pop().result()
    except asyncio.TimeoutError:
        if not ctx.me:
            return
        try:
            if (
                isinstance(message.channel, discord.PartialMessageable)
                or message.channel.permissions_for(ctx.me).manage_messages
            ):
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
            ctx,
            pages,
            controls,
            message,
            page,
            chapter,
            timeout,
            react.emoji,
            data,
            funct,
            run_funct,
        )


async def custom_next_page(
    ctx: commands.Context,
    pages: list,
    controls: Mapping[str, _ControlCallable],
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: Optional[List[OsuScore]],
    funct: Optional[functools.partial],
    run_funct: bool,
) -> _T:
    if page >= len(data) - 1:
        page = 0
    else:
        page += 1

    if run_funct:
        embeds = await funct(ctx, data, page)

    return await custom_menu(
        ctx,
        embeds,
        controls,
        message=message,
        page=page,
        chapter=chapter,
        timeout=timeout,
        data=data,
        funct=funct,
        run_funct=run_funct,
    )


async def custom_prev_page(
    ctx: commands.Context,
    pages: list,
    controls: Mapping[str, _ControlCallable],
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: Optional[List[OsuScore]],
    funct: Optional[functools.partial],
    run_funct: bool,
) -> _T:
    if page <= 0:
        page = len(data) - 1
    else:
        page -= 1

    if run_funct:
        embeds = await funct(ctx, data, page)

    return await custom_menu(
        ctx,
        embeds,
        controls,
        message=message,
        page=page,
        chapter=chapter,
        timeout=timeout,
        data=data,
        funct=funct,
        run_funct=run_funct,
    )


async def custom_close_menu(
    ctx: commands.Context,
    pages: list,
    controls: Mapping[str, _ControlCallable],
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: Optional[List[OsuScore]],
    funct: Optional[functools.partial],
    run_funct: bool,
) -> None:
    with contextlib.suppress(discord.NotFound):
        await message.delete()


async def chapter_menu(
    ctx: commands.Context,
    data: List[dict],
    funct: functools.partial,
    chapter: int = 0,
    message: discord.Message = None,
):
    embeds = await funct(ctx, data, chapter)

    await custom_menu(
        ctx,
        embeds,
        check_controls(data, chapter=chapter),
        message=message,
        funct=funct,
        chapter=chapter,
        data=data,
        run_funct=False,
    )


async def custom_next_chapter(
    ctx: commands.Context,
    pages: list,
    controls: Mapping[str, _ControlCallable],
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: Optional[List[OsuScore]],
    funct: Optional[functools.partial],
    run_funct: bool,
) -> _T:
    if chapter >= len(data) - 1:
        chapter = 0
    else:
        chapter += 1

    return await chapter_menu(ctx, data, funct, chapter, message)


async def custom_prev_chapter(
    ctx: commands.Context,
    pages: list,
    controls: Mapping[str, _ControlCallable],
    message: discord.Message,
    page: int,
    chapter: int,
    timeout: float,
    emoji: str,
    data: Optional[List[OsuScore]],
    funct: Optional[functools.partial],
    run_funct: bool,
) -> _T:
    if chapter <= 0:
        chapter = len(data) - 1
    else:
        chapter -= 1

    return await chapter_menu(ctx, data, funct, chapter, message)


def start_adding_reactions(
    message: discord.Message, emojis: Iterable[_ReactableEmoji]
) -> asyncio.Task:
    async def task():
        # The task should exit silently if the message is deleted
        with contextlib.suppress(discord.NotFound):
            for emoji in emojis:
                await message.add_reaction(emoji)

    return asyncio.create_task(task())


def check_controls(data: Union[List[OsuScore], List[dict]], chapter: Optional[int] = None):
    """Checks which types of controls to use for the menu."""
    if chapter is not None:
        if len(data) > 1:
            if len(data[chapter]["members"]) > 5:
                return CHAPTER_CONTROLS
            return CHAPTER_PAGE_CONTROLS

        if len(data[chapter]["members"]) > 5:
            return DEFAULT_CONTROLS
        return PAGE_CONTROLS

    if len(data) > 1:
        return DEFAULT_CONTROLS

    return PAGE_CONTROLS


#: Default controls for `menu()` that contain controls for
#: previous page, closing menu, and next page.
DEFAULT_CONTROLS: Mapping[str, _ControlCallable] = MappingProxyType(
    {
        "\N{LEFTWARDS BLACK ARROW}\N{VARIATION SELECTOR-16}": custom_prev_page,
        "\N{CROSS MARK}": custom_close_menu,
        "\N{BLACK RIGHTWARDS ARROW}\N{VARIATION SELECTOR-16}": custom_next_page,
    }
)

PAGE_CONTROLS: Mapping[str, _ControlCallable] = MappingProxyType(
    {"\N{CROSS MARK}": custom_close_menu}
)

CHAPTER_CONTROLS: Mapping[str, _ControlCallable] = MappingProxyType(
    {
        "\N{LEFTWARDS BLACK ARROW}\N{VARIATION SELECTOR-16}": custom_prev_page,
        "\N{CROSS MARK}": custom_close_menu,
        "\N{BLACK RIGHTWARDS ARROW}\N{VARIATION SELECTOR-16}": custom_next_page,
        "\N{UPWARDS BLACK ARROW}\N{VARIATION SELECTOR-16}": custom_prev_chapter,
        "\N{DOWNWARDS BLACK ARROW}\N{VARIATION SELECTOR-16}": custom_next_chapter,
    }
)

CHAPTER_PAGE_CONTROLS: Mapping[str, _ControlCallable] = MappingProxyType(
    {
        "\N{CROSS MARK}": custom_close_menu,
        "\N{UPWARDS BLACK ARROW}\N{VARIATION SELECTOR-16}": custom_prev_chapter,
        "\N{DOWNWARDS BLACK ARROW}\N{VARIATION SELECTOR-16}": custom_next_chapter,
    }
)
