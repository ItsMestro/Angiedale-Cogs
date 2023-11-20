import logging
from typing import Any, Awaitable, Callable, List, Optional, Union

import discord
from redbot.core.utils.chat_formatting import error, inline
from redbot.core.utils.predicates import ReactionPredicate

log = logging.getLogger("red.angiedale.utility")


class EmbedEditorBaseView(discord.ui.View):
    """Base class for embed editor views.

    Parameters
    ----------
    embed: :class:`discord.Embed`
        The embed object that is being edited.
    embed_message: :class:`discord.Message`
        The message the embed object object is on.
    timeout: Optional[:class:`float`]
        Timeout in seconds from last interaction with the UI before no longer accepting input.
        If ``None`` then there is no timeout.
    """

    def __init__(self, embed: discord.Embed, embed_message: discord.Message, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.embed = embed
        self.embed_message = embed_message

    async def update_view(self, interaction: discord.Interaction) -> None:
        await interaction.message.edit(view=self)


class SelectViewItem:
    """A selection view item to be used with :class:`ItemSelectView`.

    Parameters
    ----------
    label: :class:`str`
        The label for the selection item.
    value: Optional[:class:`str`]
        The value of the option. Will default to :code:`label` if not provided.
    emoji: Optional[Union[:class:`discord.Emoji`, :class:`str`]]
        A custom or default emoji to put on the option. Will default to number and
        letter emojis if not provided.
    """

    def __init__(
        self,
        label: str,
        value: Optional[str] = None,
        emoji: Optional[Union[discord.Emoji, str]] = None,
    ):
        self.label = label
        self.value = value
        self.emoji = emoji


class ItemSelectView(discord.ui.View):
    """A simple selection view.

    Parameters
    ----------
    items: List[:class:`SelectViewItem`]
        The items to populate the selection with.
    use_cancel: Optional[:class:`bool`]
        Whether or not to have a cancel button for the view.
        Defaults to :code:`True`.
    default_label: Optional[:class:`str`]
        An optional label to put on the selection before it's interacted with.
    """

    def __init__(
        self,
        items: List[SelectViewItem],
        use_cancel: bool = True,
        default_label: str = None,
    ):
        super().__init__(timeout=30)

        self.result: bool = False
        self.value: Optional[str] = None

        self.add_item(ItemSelect(items, default_label))

        if use_cancel:
            self.add_item(CancelButton(row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await interaction.client.is_owner(interaction.user):
            return False
        return True


class ItemSelect(discord.ui.Select):
    def __init__(self, items: List[SelectViewItem], placeholder: Optional[str]):
        options: List[discord.SelectOption] = []
        for i, item in enumerate(items):
            emoji = item.emoji
            if emoji is None:
                if len(items) > 9:
                    emoji = ReactionPredicate.ALPHABET_EMOJIS[i]
                else:
                    emoji = ReactionPredicate.NUMBER_EMOJIS[i + 1]

            options.append(
                discord.SelectOption(
                    label=item.label,
                    value=item.value,
                    emoji=emoji,
                )
            )

        super().__init__(options=options, row=0, placeholder=placeholder)

    async def callback(self, interaction: discord.Interaction) -> Any:
        self.view.result = True
        self.view.value = self.values[0]
        await interaction.response.defer()
        self.view.stop()


class CancelButton(discord.ui.Button):
    """A cancel button.

    Parameters
    ----------
    row: Optional[:class:`int`]
        The row this button should appear on. Defaults to `0`.
        Has to be between `0` and `4`
    """

    def __init__(self, row: int = 0):
        super().__init__(style=discord.ButtonStyle.red, label="Cancel", row=max(0, min(row, 4)))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.defer()
        self.view.stop()


class SimpleModal(discord.ui.Modal):
    def __init__(
        self,
        title: str,
        inputs: List[discord.ui.TextInput],
        callback: Callable[[discord.Interaction, List[discord.ui.TextInput]], Awaitable[None]],
    ):
        super().__init__(title=title)
        self.callback = callback
        self.text_inputs = inputs

        for text_input in self.text_inputs:
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await interaction.client.is_owner(interaction.user):
            return

        await self.callback(interaction, self.text_inputs)

    async def on_error(self, interaction: discord.Interaction, exception: Exception) -> None:
        if type(exception) is discord.NotFound:
            return await interaction.response.send_message(
                error("Failed to submit response. Message has likely timed out."),
                ephemeral=True,
                delete_after=10,
            )

        await interaction.response.send_message(
            error(
                "A unknown error has occured and has been logged. "
                "If you'd like to help out resolving it. Post a bug report in the support server "
                f"which you can join with {inline('-support')}"
            ),
            ephemeral=True,
            delete_after=20,
        )
        log.exception("Unhandled exception in raffle setup modal.", exc_info=exception)
