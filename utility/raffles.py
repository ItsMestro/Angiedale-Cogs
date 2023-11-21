import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.converter import TimedeltaConverter
from redbot.core.utils.chat_formatting import (
    bold,
    error,
    humanize_timedelta,
    inline,
    success,
    warning,
)
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate

from .abc import MixinMeta
from .converters import RawMessageIds
from .ui import EmbedEditorBaseView, ItemSelectView, SelectViewItem, SimpleModal

log = logging.getLogger("red.angiedale.utility")

raffle_entry_emoji = "\N{ADMISSION TICKETS}\N{VARIATION SELECTOR-16}"

numbered_emojis = [ReactionPredicate.NUMBER_EMOJIS[i] for i in range(1, 10)] + ["\N{KEYCAP TEN}"]


# region Item Classes
class Raffle:
    def __init__(self, **kwargs):
        # Required on init
        self._end_time: Union[datetime, int] = kwargs.get("end_time")

        # Has defaults
        self.total_winners: int = kwargs.get("winners", 1)
        self.days_on_server: int = kwargs.get("dos", 0)
        self.use_buttons: bool = kwargs.get("use_buttons", True)

        self._init_roles: List[Union[discord.Role, int]] = kwargs.get("roles", [])
        self._roles: List[discord.Role] = []
        self._entries: List[int] = kwargs.get("entries", [])
        self._winners: List[int] = kwargs.get("winners", [])

        # Extra
        self.title: str = kwargs.get("title", None)

        self._embed: Optional[discord.Embed] = None
        self._guild_id: Optional[int] = None
        self._guild: Optional[discord.Guild] = None
        self._channel_id: Optional[int] = kwargs.get("channel_id", None)
        self._channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        ] = None
        self._message_id: Optional[int] = kwargs.get("message_id", None)
        self._message: Optional[discord.Message] = None

        if isinstance(self._end_time, int):
            self._parse_timestamp()

        if len(self._init_roles) > 0 and self.guild is not None:
            self._set_role_list()

    def _parse_timestamp(self):
        self._end_time = datetime.fromtimestamp(self._end_time, timezone.utc)

    def _set_role_list(self):
        for item in self._init_roles:
            if isinstance(item, int):
                role = self.guild.get_role(item)
                if role is not None:
                    self._roles.append(role)
            elif isinstance(item, discord.Role):
                self._roles.append(item)

    @property
    def timestamp(self) -> int:
        if isinstance(self._end_time, int):
            self._parse_timestamp()

        return int(self._end_time.timestamp())

    @property
    def timedelta(self) -> timedelta:
        return self.end_time - datetime.now(timezone.utc)

    @property
    def end_time(self) -> datetime:
        return self._end_time

    @property
    def entries(self) -> List[int]:
        return self._entries

    @entries.setter
    def entries(self, value: List[int]) -> None:
        self._entries = value

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    @property
    def winners(self) -> List[int]:
        return self._winners

    @entries.setter
    def winners(self, value: List[int]) -> None:
        self._winners = value

    @property
    def guild_id(self) -> Optional[int]:
        return self._guild_id

    @guild_id.setter
    def guild_id(self, value: int) -> None:
        self._guild_id = value

    @property
    def guild(self) -> Optional[discord.Guild]:
        return self._guild

    @guild.setter
    def guild(self, value: discord.Guild) -> None:
        self._guild = value

    @property
    def channel_id(self) -> Optional[int]:
        return self._channel_id

    @channel_id.setter
    def channel_id(
        self,
        value: int,
    ) -> None:
        self._channel_id = value

    @property
    def channel(
        self,
    ) -> Optional[
        Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
    ]:
        if self._channel is None and self._guild is not None and self._channel_id is not None:
            self._channel = self._guild.get_channel_or_thread(self._channel_id)

        return self._channel

    @channel.setter
    def channel(
        self,
        value: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
    ) -> None:
        self._channel = value

    @property
    def message_id(self) -> Optional[int]:
        return self._message_id

    @message_id.setter
    def message_id(self, value: int) -> None:
        self._message_id = value

    @property
    def embed(self) -> Optional[discord.Embed]:
        return self._embed

    @embed.setter
    def embed(self, value: discord.Embed) -> None:
        self._embed = value

    @property
    def roles(self) -> List[discord.Role]:
        if len(self._init_roles) > 0 and len(self._roles) == 0 and self.guild is not None:
            self._set_role_list()
            return self._roles

        return self._roles

    @roles.setter
    def roles(self, value: List[Union[discord.Role, int]]) -> None:
        self._init_roles = value

        if self.guild is not None:
            self._set_role_list()

    async def fetch_message(
        self,
    ) -> Optional[discord.Message]:
        """|coro|

        Retrieves a single :class:`~discord.Message` from stored `message_id`.

        Raises
        --------
        ~discord.NotFound
            The specified message was not found.
        ~discord.Forbidden
            You do not have the permissions required to get a message.
        ~discord.HTTPException
            Retrieving the message failed.

        Returns
        --------
        Optional[:class:`~discord.Message`]
            The message asked for or `None` if `channel` and `message_id` is missing.
        """
        if self.channel is None or self._message_id is None:
            return

        return await self.channel.fetch_message(self._message_id)

    def roles_to_string(self) -> str:
        if len(self.roles) == 0:
            return "@everyone"

        roles = []
        for role in self.roles:
            if isinstance(role, discord.Role):
                roles.append(role.mention)

        return " ".join(roles)

    def to_role_ids(self) -> List[int]:
        return [role.id for role in self.roles]

    def to_dict(self) -> dict:
        return {
            "end_time": self.timestamp,
            "title": self.title,
            "channel_id": self._channel_id,
            "message_id": self._message_id,
            "winners": self.total_winners,
            "dos": self.days_on_server,
            "roles": self.to_role_ids(),
            "use_buttons": self.use_buttons,
            "entries": self._entries,
        }


# endregion


class Raffles(MixinMeta):
    """Raffle/Giveaways."""

    @commands.group()
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def raffle(self, ctx: commands.Context):
        """Run Raffles/Giveaways."""

    @raffle.command(name="clearguild", alises=["resetguild"], hidden=True)
    @commands.is_owner()
    async def _raffle_clear_guild(self, ctx: commands.Context):
        await self.raffle_config.guild(ctx.guild).raffles.clear()
        await self.raffle_config.guild(ctx.guild).raffles_history.clear()
        await ctx.send("Raffle data cleared out.")

    @commands.max_concurrency(1, commands.BucketType.guild)
    @raffle.command(name="start", aliases=["create", "make", "new"])
    async def _raffle_start(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        *,
        time: Optional[TimedeltaConverter] = None,
    ):
        """Start a raffle/giveaway.

        Time should be a string that doesn't exceed 8 weeks.

        Examples:
        - `[p]raffle start #giveaways 2w` (Channel Mention)
        - `[p]raffle start channel-name 1week12days` (Channel Name)
        - `[p]raffle start 672816387625975808 20 hours 30 min` (Channel ID)

        Up to four raffles can be active per server.
        """
        if (
            not ctx.channel.permissions_for(ctx.guild.me).embed_links
            or not channel.permissions_for(ctx.guild.me).embed_links
        ):
            return await ctx.send("I need the Embed Links permission to be able to start raffles.")
        if (
            not ctx.channel.permissions_for(ctx.guild.me).add_reactions
            or not channel.permissions_for(ctx.guild.me).add_reactions
        ):
            return await ctx.send(
                "I need the Add Reactions permission to be able to start raffles."
            )
        if (
            isinstance(channel, discord.Thread)
            and not channel.permissions_for(ctx.guild.me).send_messages_in_threads
            or not isinstance(channel, discord.Thread)
            and not channel.permissions_for(ctx.guild.me).send_messages
        ):
            return await ctx.send("I'm not allowed to send messages in that location!")
        if (
            isinstance(channel, discord.Thread)
            and not channel.permissions_for(ctx.author).send_messages_in_threads
            or not isinstance(channel, discord.Thread)
            and not channel.permissions_for(ctx.author).send_messages
        ):
            return await ctx.send("You don't have permission to send messages in that location!")

        if time is None:
            return await ctx.send("You need to provide a valid time for the raffle to last.")

        guild_settings: dict = await self.raffle_config.guild(ctx.guild).all()

        if len(guild_settings["raffles"]) >= 4:
            return await ctx.send(
                "You already have 4 raffles running in the server. "
                "Wait for one of them to finish first before starting another one."
            )

        if time > timedelta(weeks=8):
            return await ctx.send(f"The time can't be longer than {inline('8 weeks')}.")
        if time < timedelta(minutes=5):
            return await ctx.send(f"The raffle can't be shorter than {inline('5 minutes')}.")

        raffle = await self.raffle_setup(
            ctx, Raffle(end_time=datetime.now(timezone.utc) + time), channel
        )

        if raffle is None:
            return

        notification_role = None
        if guild_settings["notification_role_id"]:
            notification_role = ctx.guild.get_role(guild_settings["notification_role_id"])

            if notification_role is None:
                await ctx.send(
                    "\n".join(
                        [
                            "I was unable to get the notification role that's set.",
                            "The raffle will still be sent but you should set a new notification role with "
                            f"{inline(f'{ctx.clean_prefix}{self.raffle.name} {self._raffle_set.name} {self._raffle_set_mention.name} <role>')}",
                        ]
                    )
                )

        raffle_message: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ] = await channel.send(
            content=notification_role.mention if notification_role else None,
            embed=raffle.embed,
        )

        if raffle.use_buttons:
            view = RaffleView(config=self.raffle_config, raffle=raffle)

            await raffle_message.edit(
                view=view,
            )
        else:
            start_adding_reactions(raffle_message, [raffle_entry_emoji])

        await ctx.send(success(f"Raffle sent! {raffle_message.jump_url}"))

        raffle.guild_id = ctx.guild.id
        raffle.channel_id = channel.id
        raffle.message_id = raffle_message.id

        async with self.raffle_config.guild(ctx.guild).raffles() as raffles:
            raffles[raffle_message.id] = raffle.to_dict()

        self.active_raffle_tasks.append(asyncio.create_task(self.raffle_timer(raffle)))

    @commands.max_concurrency(1, commands.BucketType.guild)
    @raffle.command(name="end", aliases=["stop"])
    async def _raffle_end(self, ctx: commands.Context, message_id: Optional[RawMessageIds] = None):
        """Ends a raffle early and picks winner(s).

        If `message_id` is left empty you're given a menu
        where you can pick which to one to end.
        """
        raffles: dict = await self.raffle_config.guild(ctx.guild).raffles()
        if len(raffles) == 0:
            return await ctx.send("There are no active raffles running in the server.")

        new_raffles: List[Raffle] = []
        for raffle in raffles.values():
            new_raffle = Raffle(**raffle)
            new_raffle.guild = ctx.guild
            new_raffles.append(new_raffle)

        raffle = await self.raffle_selection(ctx, new_raffles, message_id)
        if raffle is None:
            return

        await self.end_raffle(raffle)

    @commands.max_concurrency(1, commands.BucketType.guild)
    @raffle.command(name="cancel")
    async def _raffle_cancel(
        self, ctx: commands.Context, message_id: Optional[RawMessageIds] = None
    ):
        """Cancels an on-going raffle without picking a winner.

        If `message_id` is left empty you're given a menu
        where you can pick which to one to cancel.
        """
        raffles: dict = await self.raffle_config.guild(ctx.guild).raffles()
        if len(raffles) == 0:
            return await ctx.send("There are no active raffles running in the server.")

        new_raffles: List[Raffle] = []
        for raffle in raffles.values():
            new_raffle = Raffle(**raffle)
            new_raffle.guild = ctx.guild
            new_raffles.append(new_raffle)

        raffle = await self.raffle_selection(ctx, new_raffles, message_id)
        if raffle is None:
            return

        try:
            message = await raffle.fetch_message()
        except:
            return await ctx.send("An unknown error has occured.")

        finished_embed = await self.get_finished_raffle_embed(raffle.guild, message.embeds[0])
        await message.edit(embed=finished_embed, view=None)

        await self.clear_raffle_entry(raffle)
        await message.channel.send(
            f"The {bold(message.embeds[0].title)} raffle was cancelled. No winners will be pulled!",
            reference=message,
        )

    @commands.max_concurrency(1, commands.BucketType.guild)
    @raffle.command(name="reroll", aliases=["redraw"])
    async def _raffle_reroll(
        self, ctx: commands.Context, message_id: Optional[RawMessageIds] = None
    ):
        """Reroll the winner for a raffle.

        The last 5 raffles ran can be rerolled.

        If `message_id` is left empty you're given a menu
        where you can pick which to one to reroll.
        """
        raffles_data: list = await self.raffle_config.guild(ctx.guild).raffles_history()
        if len(raffles_data) == 0:
            return await ctx.send("You haven't ran any raffles yet!")

        raffles: List[Raffle] = []
        for raffle_data in raffles_data:
            new_raffle = Raffle(**raffle_data)
            new_raffle.guild = ctx.guild
            raffles.append(new_raffle)

        if message_id is None:
            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
            embed.set_author(name="List of past raffles.", icon_url=ctx.me.display_avatar.url)
            embed.title = "Select which raffle you'd like to reroll"
            embed.set_thumbnail(url=ctx.guild.icon.url)

            output: List[str] = []
            raffle_items: List[SelectViewItem] = []
            messages: Dict[int, discord.Message] = {}
            for i, raffle in enumerate(raffles):
                extension = ""
                if raffle.channel is None:
                    continue

                message = await raffle.fetch_message()
                if message is not None:
                    extension = f" ◈ {message.jump_url}"
                    messages[raffle.message_id] = message

                if len(raffles) > 9:
                    emoji = ReactionPredicate.ALPHABET_EMOJIS[i]
                else:
                    emoji = ReactionPredicate.NUMBER_EMOJIS[i + 1]

                output.append(f"{emoji} {bold(raffle.title)}{extension}")
                raffle_items.append(SelectViewItem(label=raffle.title, value=raffle.message_id))

            if len(raffle_items) == 0:
                return await ctx.send(
                    warning("All the channels your previous raffles have ran in are unavaliable!")
                )

            embed.description = "\n".join(output)

            view = ItemSelectView(items=raffle_items)
            select_message = await ctx.send(embed=embed, view=view)
            timed_out = await view.wait()
            if timed_out:
                await select_message.edit(content="Selection timed out!", embed=None, view=None)
                return

            if not view.result:
                await select_message.edit(content="Selection cancelled!", embed=None, view=None)
                return

            await select_message.delete()

            raffle = next(filter(lambda r: r.message_id == int(view.value), raffles), None)
            reference_message = messages.get(raffle.message_id, None)
        else:
            raffle = next(filter(lambda r: r.message_id == message_id, raffles), None)

            if raffle is None:
                return await ctx.send(
                    "I couldn't find any raffle by that message ID in my history!"
                )

            if raffle.channel is None:
                return await ctx.send("I couldn't find the channel the raffle was ran in!")

            reference_message: Optional[discord.Message] = None
            message = await raffle.fetch_message()
            if message is not None:
                reference_message = message

        if not raffle.channel.permissions_for(ctx.me).send_messages:
            return await ctx.send(
                warning("I'm not allowed to send messages in that raffles channel anymore.")
            )

        winners = await self.pick_winner(raffle, reference_message, reroll=True)
        if winners is None:
            return

        async with self.raffle_config.guild(ctx.guild).raffles_history() as raffles_history:
            for i, raf in enumerate(raffles_history):
                if raf["message_id"] == raffle.message_id:
                    raffles_history[i]["winner_list"] = winners
                    break

    @commands.cooldown(1, 30, commands.BucketType.member)
    @raffle.command(name="list")
    async def _raffle_list(self, ctx: commands.Context):
        """List current and past raffles."""
        guild_data: dict = await self.raffle_config.guild(ctx.guild).all()
        if len(guild_data["raffles"]) == 0 and len(guild_data["raffles_history"]) == 0:
            return await ctx.send("There are no current or past raffles in this server.")

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f"List of raffles in {ctx.guild.name}", icon_url=ctx.guild.me.display_avatar.url
        )
        embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.set_footer(text="Name ◈ Message Link ◈ Ends/Ended ◈ Entries")

        view: Optional[ItemSelectView] = None
        if len(guild_data["raffles"]) > 0:
            output = []
            for raffle_data in guild_data["raffles"].values():
                raffle = Raffle(**raffle_data)
                raffle.guild = ctx.guild
                jump_url = ""
                if raffle.channel is not None:
                    message = await raffle.fetch_message()
                    if message is not None:
                        jump_url = f" ◈ {message.jump_url}"
                output.append(
                    "\n".join(
                        [
                            f"{raffle.title}" f"{jump_url}",
                            f"<t:{raffle.timestamp}:D> <t:{raffle.timestamp}:R>",
                        ]
                    )
                )

            embed.add_field(name="Active Raffles", value="\n\n".join(output), inline=False)

        if len(guild_data["raffles_history"]) > 0:
            output = []
            raffles: List[SelectViewItem] = []
            past_raffles: List[Raffle] = []
            for raffle_data in guild_data["raffles_history"]:
                raffle = Raffle(**raffle_data)
                raffle.guild = ctx.guild
                past_raffles.append(raffle)
                jump_url = ""
                if raffle.channel is not None:
                    message = await raffle.fetch_message()
                    if message is not None:
                        jump_url = f" ◈ {message.jump_url}"
                output.append(
                    "\n".join(
                        [
                            f"{raffle.title}" f"{jump_url}",
                            f"<t:{raffle.timestamp}:D> <t:{raffle.timestamp}:R>"
                            f" ◈ Entries: {raffle.total_entries}",
                        ]
                    )
                )
                raffles.append(SelectViewItem(label=raffle.title, value=raffle.message_id))

            embed.add_field(name="Past Raffles", value="\n\n".join(output), inline=False)

            view = ItemSelectView(
                raffles,
                use_cancel=False,
                default_label="Select a past raffle to show it's results",
            )

        original_response = await ctx.send(embed=embed, view=view)
        if view is None:
            return

        timed_out = False
        response_message: Optional[discord.Message] = None
        while not timed_out:
            timed_out = await view.wait()
            if timed_out:
                await original_response.edit(view=None)
                if response_message is not None:
                    await response_message.delete()
                break

            raffle = next(filter(lambda r: r.message_id == int(view.value), past_raffles))  # type: ignore

            if raffle is None:
                return await ctx.send("An unknown error occured.")

            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

            embed.title = raffle.title
            embed.set_author(name=f"Raffle Results!", icon_url=ctx.guild.me.display_avatar.url)

            winners: List[Union[discord.Member, int]] = []
            for winner in raffle.winners:
                member = ctx.guild.get_member(winner)
                winners.append(member if member is not None else winner)

            if len(winners) == 1:
                output = (
                    winners[0].mention if isinstance(winners[0], discord.Member) else winners[0]
                )
            elif len(winners) == 0:
                output = "Nobody!"
            else:
                mentions = []
                for i, member in enumerate(winners):
                    mentions.append(
                        f"{bold(f'{i+1}.')} {member.mention if isinstance(member, discord.Member) else member}"
                    )

                output = "\n".join(mentions)

            embed.add_field(
                name=f"Winner{'s' if raffle.total_winners > 1 else ''}", value=output, inline=False
            )

            embed.add_field(name="Allowed Roles", value=raffle.roles_to_string(), inline=True)
            embed.add_field(name="Winners Pulled", value=raffle.total_winners, inline=True)
            if raffle.days_on_server > 0:
                embed.add_field(
                    name="Days on Server to Enter", value=raffle.days_on_server, inline=True
                )

            if response_message is None:
                response_message = await ctx.send(embed=embed)
            else:
                await response_message.edit(embed=embed)

            view = ItemSelectView(
                raffles,  # type: ignore
                use_cancel=False,
                default_label="Select a past raffle to show it's results",
            )
            await original_response.edit(view=view)

    @raffle.group(name="set")
    @commands.guildowner()
    async def _raffle_set(self, ctx: commands.Context):
        """Change raffle/giveaway settings."""

    @_raffle_set.command(name="mention", aliases=["role", "notification"])
    async def _raffle_set_mention(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set a role I should ping for raffles."""
        if role:
            await self.raffle_config.guild(ctx.guild).notification_role_id.set(role.id)
            return await ctx.send(
                f"I will now mention {bold(role if role.is_default() else role.name)} for new raffles."
            )

        await self.raffle_config.guild(ctx.guild).notification_role_id.clear()
        await ctx.send("I will no longer mention any role for new raffles.")

    async def load_raffles(self) -> None:
        await self.bot.wait_until_red_ready()
        try:
            coroutines = []
            guilds = await self.raffle_config.all_guilds()
            time_now = datetime.now(timezone.utc)
            for g_id, g_data in guilds.items():
                if len(g_data["raffles"]) > 0:
                    guild = self.bot.get_guild(g_id)
                    if guild is None:
                        continue

                    for m_id, raffle_data in g_data["raffles"].items():
                        raffle = Raffle(**raffle_data)
                        raffle.guild = guild
                        if time_now > raffle.end_time:
                            await self.end_raffle(raffle)
                            continue

                        if raffle.channel is None:
                            continue

                        message = await raffle.fetch_message()

                        if message is None:
                            continue

                        if raffle.use_buttons:
                            view = RaffleView(
                                config=self.raffle_config,
                                raffle=raffle,
                            )
                            self.bot.add_view(
                                view,
                                message_id=int(m_id),
                            )
                            await message.edit(view=view)

                        coroutines.append(self.raffle_timer(raffle))

            await asyncio.gather(*coroutines)
        except Exception:
            log.error("Error during raffle initialization", exc_info=True)

    async def raffle_selection(
        self, ctx: commands.Context, raffles: List[Raffle], message_id: Optional[int] = None
    ) -> Optional[Raffle]:
        if message_id is None:
            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
            embed.set_author(
                name="List of currently active raffles.", icon_url=ctx.me.display_avatar.url
            )
            embed.title = "Select which raffle you'd like to end"
            embed.set_thumbnail(url=ctx.guild.icon.url)

            output: List[str] = []
            messages: List[discord.Message] = []
            for i, raffle in enumerate(raffles):
                if raffle.channel is None:
                    continue
                message = await raffle.fetch_message()
                if message is None:
                    continue

                output.append(
                    f"{numbered_emojis[i]} {bold(message.embeds[0].title)} ◈ {message.jump_url}"
                )
                messages.append(message)

            if len(messages) == 0:
                await ctx.send("There are no active raffles running in the server.")
                return

            embed.description = "\n".join(output)

            raffle_items: List[SelectViewItem] = []
            for message in messages:
                raffle_items.append(
                    SelectViewItem(label=message.embeds[0].title, value=message.id)
                )
            view = ItemSelectView(items=raffle_items)
            select_message = await ctx.send(embed=embed, view=view)
            timed_out = await view.wait()
            if timed_out:
                await select_message.edit(content="Selection timed out!", embed=None, view=None)
                return

            if not view.result:
                await select_message.edit(content="Selection cancelled!", embed=None, view=None)
                return

            await select_message.delete()

            result = next(filter(lambda r: r.message_id == int(view.value), raffles), None)
        else:
            raffle = next(filter(lambda r: r.message_id == message_id, raffles), None)

            if raffle is None:
                await ctx.send("I couldn't find a raffle with that message ID.")
                return

            if raffle.channel is None:
                await ctx.send("I couldn't find the channel that the raffle is supposed to be in.")
                return

            if await raffle.fetch_message() is None:
                await ctx.send("I couldn't find the raffle message.")
                return

            result = raffle

        return result

    async def raffle_timer(self, raffle: Raffle) -> None:
        await asyncio.sleep(raffle.timedelta.total_seconds())

        guild = self.bot.get_guild(raffle.guild_id)
        if guild is None:
            return

        async with self.raffle_config.guild(guild).raffles() as raffles:
            fresh_raffle_data: Dict[str, Union[List[int], int]] = raffles.get(
                str(raffle.message_id)
            )
        if fresh_raffle_data:
            fresh_raffle = Raffle(**fresh_raffle_data)
            fresh_raffle.guild = guild

            await self.end_raffle(fresh_raffle)

    async def end_raffle(
        self,
        raffle: Raffle,
    ) -> None:
        if (
            raffle.channel is None
            or not raffle.channel.permissions_for(raffle.guild.me).read_messages
            or not raffle.channel.permissions_for(raffle.guild.me).send_messages
        ):
            return await self.clear_raffle_entry(raffle)

        try:
            message = await raffle.fetch_message()
        except discord.NotFound:
            return await self.clear_raffle_entry(raffle)
        except discord.Forbidden:
            return

        winners = await self.pick_winner(raffle, message)
        if winners is not None:
            raffle.winners = winners
        await self.move_raffle_to_history(raffle)

    async def clear_raffle_entry(self, raffle: Raffle) -> None:
        async with self.raffle_config.guild(raffle.guild).raffles() as raffles:
            try:
                del raffles[str(raffle.message_id)]
            except KeyError:
                pass

    async def move_raffle_to_history(self, raffle: Raffle) -> None:
        async with self.raffle_config.guild(raffle.guild).all() as guild_data:
            raffle_data = guild_data["raffles"].pop(str(raffle.message_id), None)
            if raffle_data is not None:
                raffle_data["winner_list"] = []
                if len(raffle.winners) > 0:
                    raffle_data["winner_list"] = raffle.winners
                guild_data["raffles_history"].insert(0, raffle_data)
                if len(guild_data["raffles_history"]) > 5:
                    guild_data["raffles_history"].pop()

    async def pick_winner(
        self,
        raffle: Raffle,
        message: Optional[discord.Message],
        reroll=False,
    ) -> Optional[List[int]]:
        raffle_embed = None
        if message is not None:
            raffle_embed = await self.get_finished_raffle_embed(raffle.guild, message.embeds[0])

        if len(raffle.entries) == 0 and not raffle.use_buttons:
            reaction = None
            if message is not None:
                reaction = next(
                    filter(lambda x: x.emoji == raffle_entry_emoji, message.reactions), None
                )

            if reaction is None:
                await raffle.channel.send(
                    "Was unable to find/read the reaction emoji for the "
                    f"{bold(raffle.title)} raffle so no winner could be picked."
                )
                return

            raffle.entries = [
                member.id
                for member in [user async for user in reaction.users()]
                if isinstance(member, discord.Member) and not member == raffle.guild.me
            ]

        if len(raffle.entries) == 0:
            if raffle_embed is not None:
                await message.edit(embed=raffle_embed, view=None)
            await raffle.channel.send(
                "Seems like nobody entered the raffle for "
                f"{bold(raffle.title)} so no winner could be picked.",
                reference=message,
            )
            return

        valid_entries = await self.get_valid_winners(raffle)

        if len(valid_entries) == 0:
            if raffle_embed is not None:
                await message.edit(embed=raffle_embed, view=None)
            await raffle.channel.send(
                "Couldn't find any valid entries for the "
                f"{bold(raffle.title)} raffle so no winner could be picked.",
                reference=message,
            )
            return

        if reroll:
            output = "Raffle has been rerolled!"
            output = "\n\n".join(
                [
                    output,
                    f"The winner{'s' if len(valid_entries) > 1 else ''} for the {bold(raffle.title)} raffle is:",
                ]
            )
        else:
            output = f"The winner{'s' if len(valid_entries) > 1 else ''} for the {bold(raffle.title)} raffle is:"

        if len(valid_entries) > raffle.total_winners:
            while True:
                winners = random.sample(valid_entries, raffle.total_winners)

                if not reroll:
                    break
                if winners != raffle.winners:
                    break
        else:
            winners = valid_entries

        if len(winners) == 1:
            winners_string = winners[0].mention
        else:
            mentions = []
            for i, member in enumerate(winners):
                mentions.append(f"{bold(f'{i+1}.')} {member.mention}")

            winners_string = "\n".join(mentions)

        output = "\n\n".join([output, winners_string])

        if len(valid_entries) < raffle.total_winners:
            output = "\n\n".join(
                [
                    output,
                    f"There was only {bold(str(len(valid_entries)))} valid entries "
                    f"out of the {bold(str(raffle.total_winners))} maximum allowed. So everyone is a winner!",
                ]
            )

        output = "\n\n".join([output, ":tada::tada: Congratulations! :tada::tada:"])

        raffle_embed.add_field(name="Winners", value=winners_string, inline=False)

        await message.edit(embed=raffle_embed, view=None)
        await raffle.channel.send(output, reference=message)

        return [winner.id for winner in winners]

    async def get_finished_raffle_embed(
        self, guild: discord.Guild, base_embed: discord.Embed
    ) -> discord.Embed:
        raffle_embed = base_embed

        while len(raffle_embed.fields) > 2:
            raffle_embed.remove_field(len(raffle_embed.fields) - 1)

        end_timestamp = int(datetime.now(timezone.utc).timestamp())

        raffle_embed.set_field_at(
            index=0,
            name="Ended on",
            value=f"<t:{end_timestamp}:D> ◈ <t:{end_timestamp}:R>",
            inline=False,
        )
        raffle_embed.set_footer(text=f"Guild raffles brought to you by {guild.me.display_name}!")

        return raffle_embed

    async def get_valid_winners(self, raffle: Raffle) -> List[discord.Member]:
        members: List[discord.Member] = []
        for user_id in raffle.entries:
            member = raffle.guild.get_member(user_id)
            if member is not None:
                members.append(member)

        if len(members) == 0:
            return []

        if raffle.days_on_server > 0:
            members = [
                member
                for member in members
                if raffle.days_on_server
                < (member.joined_at.now(timezone.utc) - member.joined_at).days
            ]

        if len(raffle.roles) > 0:
            members = [
                member for member in members if any(role in member.roles for role in raffle.roles)
            ]

        return members

    async def raffle_setup(
        self,
        ctx: commands.Context,
        raffle: Raffle,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
    ) -> Optional[Raffle]:
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.set_author(
            name=f"{ctx.guild.name} Raffle!", icon_url=self.bot.user.display_avatar.url
        )

        embed.add_field(
            name="Ends", value=f"<t:{raffle.timestamp}:D> ◈ <t:{raffle.timestamp}:R>", inline=False
        )

        embed.add_field(name="Hosted By", value=ctx.author.mention, inline=False)

        embed.add_field(name="Allowed Roles", value=raffle.roles_to_string(), inline=True)

        embed.add_field(name="Winners Pulled", value=raffle.total_winners)

        embed.set_footer(
            text="Click the button to enter the raffle. If interaction fails, try again later. Bot might be down."
        )

        embed_message = await ctx.send(embed=embed, view=RaffleView(preview=True))

        view = RaffleSetupView(self.bot, ctx, embed, embed_message, raffle)
        message = await ctx.send(
            "\n\n".join(
                [
                    f"This is a preview of how the embed will look when sent in {channel.mention}",
                    "\n".join(
                        [
                            f"Use the buttons below to customize it and click {inline('Finished')} when done.",
                            "Keep in mind that you can search in the list of roles.",
                        ]
                    ),
                ]
            ),
            view=view,
        )
        timed_out = await view.wait()
        if timed_out:
            await message.edit(content="Raffle creation timed out!", view=None)
            return

        if not view.result:
            return

        raffle.embed = view.embed

        await message.delete()
        return view.raffle


# region ui
# region Views
class RaffleView(discord.ui.View):
    def __init__(self, config: Config = None, raffle: Raffle = None, preview: bool = False):
        super().__init__(timeout=None)
        self.config = config
        self.raffle = raffle
        self.preview = preview

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.preview:
            await interaction.response.defer()
            return False

        if self.raffle.days_on_server > 0:
            if (
                self.raffle.days_on_server
                > (interaction.user.joined_at.now(timezone.utc) - interaction.user.joined_at).days
            ):
                delta: timedelta = (
                    datetime.now(timezone.utc) + timedelta(days=self.raffle.days_on_server)
                ) - interaction.user.joined_at
                delta.seconds = 0
                await interaction.response.send_message(
                    "\n\n".join(
                        [
                            f"You don't meet the {inline('Days on Server')} requirement of {bold(str(self.raffle.days_on_server))} days.",
                            "You'd have to continue being a member for "
                            f"{bold(humanize_timedelta(timedelta=delta))} to enter!",
                        ]
                    ),
                    ephemeral=True,
                    delete_after=20,
                )
                return False

        if len(self.raffle.roles) > 0:
            if not any(role in interaction.user.roles for role in self.raffle.roles):
                await interaction.response.send_message(
                    "You don't have any of the required roles to interact with this raffle!",
                    ephemeral=True,
                    delete_after=20,
                )
                return False

        return True

    @discord.ui.button(
        label="Enter Raffle!",
        style=discord.ButtonStyle.green,
        emoji=raffle_entry_emoji,
        custom_id="raffle_entry",
    )
    async def entry_button(self, interaction: discord.Interaction, button: discord.Button):
        async with self.config.guild(interaction.guild).raffles() as raffles:
            entries = raffles[str(interaction.message.id)]["entries"]
            if interaction.user.id in entries:
                raffles[str(interaction.message.id)]["entries"].remove(interaction.user.id)
                entered = False
            else:
                raffles[str(interaction.message.id)]["entries"].append(interaction.user.id)
                entered = True

            entrants = raffles[str(interaction.message.id)]["entries"]

        if len(entrants) == 0:
            button.label = "Enter Raffle!"
        else:
            button.label = f"Enter Raffle! ◈ {len(entrants)}"

        await interaction.message.edit(view=self)

        if entered:
            await interaction.response.send_message(
                "Successfully entered the raffle!", ephemeral=True, delete_after=30
            )
        else:
            await interaction.response.send_message(
                "Removed your entry from the raffle!", ephemeral=True, delete_after=30
            )


class RaffleSetupView(EmbedEditorBaseView):
    def __init__(
        self,
        bot: Red,
        ctx: commands.Context,
        embed: discord.Embed,
        embed_message: discord.Message,
        raffle: Raffle,
    ):
        super().__init__(embed=embed, embed_message=embed_message, timeout=60 * 5)
        self.bot = bot
        self.ctx = ctx
        self.raffle = raffle

        self.result: bool = False
        self._winners_select = WinnerSelect(view=self, row=3)

        self.add_item(self._winners_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await interaction.client.is_owner(interaction.user):
            return False
        return True

    async def on_timeout(self) -> None:
        await self.embed_message.delete()

    @discord.ui.button(label="Finished", style=discord.ButtonStyle.green, row=0, disabled=True)
    async def finished_button(self, interaction: discord.Interaction, button: discord.Button):
        await self.embed_message.delete()
        if datetime.now(timezone.utc) > self.raffle.end_time:
            await interaction.message.edit(
                content="The time this raffle was supposed to end has already passed. Try again!",
                view=None,
            )
        else:
            self.result = True

        self.stop()

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.red,
        row=0,
    )
    async def cancel_button(self, interaction: discord.Interaction, button: discord.Button):
        await self.embed_message.delete()
        await interaction.message.edit(content="Cancelled raffle creation!", view=None)
        self.stop()

    @discord.ui.button(
        label="Switch to reaction based entry",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def switch_type_button(self, interaction: discord.Interaction, button: discord.Button):
        self.raffle.use_buttons = not self.raffle.use_buttons
        if self.raffle.use_buttons:
            self.switch_type_button.label = "Switch to reaction based entry"
            text = "Successfully swapped to using buttons for raffle entries!"

            self.embed.set_footer(
                text="Click the button to enter the raffle. If interaction fails, try again later. Bot might be down."
            )
            await self.embed_message.clear_reactions()
            await self.embed_message.edit(embed=self.embed, view=RaffleView(preview=True))
        else:
            self.switch_type_button.label = "Switch to button based entry"
            text = "Successfully swapped to using reactions for raffle entries!"

            self.embed.set_footer(text="React with the ticket emoji below to enter the raffle.")
            await self.embed_message.edit(embed=self.embed, view=None)
            start_adding_reactions(self.embed_message, [raffle_entry_emoji])

        await self.update_view(interaction)
        await interaction.response.send_message(
            success(text),
            ephemeral=True,
            delete_after=10,
        )

    @discord.ui.button(label="Add Title", style=discord.ButtonStyle.primary, row=1)
    async def title_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.send_modal(
            SimpleModal(
                "Title",
                [
                    discord.ui.TextInput(
                        label="Raffle Title",
                        style=discord.TextStyle.short,
                        required=True,
                        default=self.embed.title,
                        max_length=256,
                    )
                ],
                callback=self.title_button_callback,
            )
        )

    async def title_button_callback(
        self, interaction: discord.Interaction, values: List[discord.ui.TextInput]
    ) -> None:
        await interaction.response.defer()
        self.raffle.title = values[0].value

        if self.raffle.title == self.embed.title:
            return

        self.title_button.label = "Change Title"
        self.title_button.style = discord.ButtonStyle.grey
        self.finished_button.disabled = False

        self.embed.title = self.raffle.title

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed)

        message: discord.WebhookMessage = await interaction.followup.send(
            success("Successfully changed the raffle title."), ephemeral=True
        )

        async def delete_message():
            await asyncio.sleep(10)
            try:
                await message.delete()
            except:
                pass

        asyncio.create_task(delete_message())

    @discord.ui.button(label="Add Description", style=discord.ButtonStyle.grey, row=1)
    async def description_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.send_modal(
            SimpleModal(
                "Description",
                [
                    discord.ui.TextInput(
                        label="Raffle Description",
                        style=discord.TextStyle.paragraph,
                        required=False,
                        default=self.embed.description,
                        max_length=4000,
                    )
                ],
                callback=self.description_button_callback,
            )
        )

    async def description_button_callback(
        self, interaction: discord.Interaction, values: List[discord.ui.TextInput]
    ) -> None:
        await interaction.response.defer()
        description = values[0].value

        if description == self.embed.description:
            return

        if description == "":
            self.description_button.label = "Add Description"
            text = "Cleared the raffle description."
        else:
            self.description_button.label = "Change Description"
            text = "Successfully changed the raffle description."

        self.embed.description = description

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed)

        message: discord.WebhookMessage = await interaction.followup.send(
            success(text),
            ephemeral=True,
        )

        async def delete_message():
            await asyncio.sleep(10)
            try:
                await message.delete()
            except:
                pass

        asyncio.create_task(delete_message())

    @discord.ui.button(label="Add Link", style=discord.ButtonStyle.grey, row=1)
    async def link_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.send_modal(
            SimpleModal(
                "Link",
                [
                    discord.ui.TextInput(
                        label="Raffle Website Link",
                        style=discord.TextStyle.short,
                        required=False,
                        default=self.embed.url,
                        min_length=10,
                    )
                ],
                callback=self.link_button_callback,
            )
        )

    async def link_button_callback(
        self, interaction: discord.Interaction, values: List[discord.ui.TextInput]
    ) -> None:
        await interaction.response.defer()
        link = values[0].value

        if link == self.embed.url:
            return

        if link == "":
            self.link_button.label = "Add Link"
            text = "Cleared the raffle url."
        else:
            self.link_button.label = "Change Link"
            text = "Successfully changed the raffle website url."

        self.embed.url = link
        try:
            await self.embed_message.edit(embed=self.embed)
        except discord.HTTPException:
            self.embed.url = ""
            self.link_button.label = "Add Link"
            await self.embed_message.edit(embed=self.embed)
            message: discord.WebhookMessage = await interaction.followup.send(
                error("Failed to set the url. Are you sure it's a proper link?"),
                ephemeral=True,
            )
        else:
            message: discord.WebhookMessage = await interaction.followup.send(
                success(text),
                ephemeral=True,
            )

        await self.update_view(interaction)

        async def delete_message():
            await asyncio.sleep(10)
            try:
                await message.delete()
            except:
                pass

        asyncio.create_task(delete_message())

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Set Allowed Roles (Leave empty for @everyone)",
        min_values=0,
        max_values=5,
        row=2,
    )
    async def roles_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if select.values == self.raffle.roles:
            return

        self.raffle.roles = select.values

        roles_string = (
            " ".join([role.mention for role in self.raffle.roles])
            if len(self.raffle.roles) > 0
            else "@everyone"
        )
        self.embed.set_field_at(index=2, name="Allowed Roles", value=roles_string, inline=True)

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed)
        return await interaction.response.send_message(
            content=success("Successfully changed the allowed roles for the raffle!"),
            ephemeral=True,
            delete_after=10,
        )

    @discord.ui.button(
        label="Add Days on Server requirement", style=discord.ButtonStyle.grey, row=4
    )
    async def dos_button(self, interaction: discord.Interaction, button: discord.Button):
        default_value = 0
        if len(self.embed.fields) > 4:
            default_value = self.embed.fields[4].value

        await interaction.response.send_modal(
            SimpleModal(
                "Days on Server",
                [
                    discord.ui.TextInput(
                        label="Days on Server requirement",
                        style=discord.TextStyle.short,
                        required=True,
                        default=default_value,
                    )
                ],
                callback=self.dos_button_callback,
            )
        )

    async def dos_button_callback(
        self, interaction: discord.Interaction, values: List[discord.ui.TextInput]
    ) -> None:
        try:
            self.raffle.days_on_server = int(values[0].value)
        except ValueError:
            return await interaction.response.send_message(
                warning(f"The value for {inline('Days on Server')} has to be a number."),
                ephemeral=True,
                delete_after=10,
            )

        if self.raffle.days_on_server < 0:
            return await interaction.response.send_message(
                warning(f"The value for {inline('Days on Server')} has to be a positive number."),
                ephemeral=True,
                delete_after=10,
            )

        if self.raffle.days_on_server == 0:
            if len(self.embed.fields) < 5:
                return

            text = f"Removed the {inline('Days on Server')} requirement."
            self.embed.remove_field(4)

            self.dos_button.label = "Add Days on Server requirement"
        else:
            text = f"Set the {inline('Days on Server')} requirement to {bold(str(self.raffle.days_on_server))}."

            if len(self.embed.fields) > 4:
                self.embed.set_field_at(
                    index=4,
                    name="Days on Server to Enter",
                    value=self.raffle.days_on_server,
                    inline=True,
                )
            else:
                self.embed.add_field(
                    name="Days On Server To Enter", value=self.raffle.days_on_server, inline=True
                )

                self.dos_button.label = "Change Days on Server requirement"

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed)
        await interaction.response.send_message(
            success(text),
            ephemeral=True,
            delete_after=10,
        )


# endregion
# region Selects
class WinnerSelect(discord.ui.Select):
    def __init__(self, view: RaffleSetupView, row: Optional[int] = None):
        self.embed = view.embed
        self.embed_message = view.embed_message
        self.parent_view = view
        options: List[discord.SelectOption] = []
        for i in range(1, 11):
            options.append(
                discord.SelectOption(
                    label=str(i),
                    value=str(i),
                    emoji=numbered_emojis[i - 1],
                )
            )

        super().__init__(
            placeholder="Change how many winners to pull",
            min_values=0,
            max_values=1,
            options=options,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        if len(self.values) == 0 or self.values[0] == self.embed.fields[3].value:
            return await interaction.response.defer()

        self.embed.set_field_at(index=3, name="Winners Pulled", value=self.values[0], inline=True)
        self.parent_view.embed = self.embed

        await self.parent_view.update_view(interaction)
        await self.embed_message.edit(embed=self.embed)
        return await interaction.response.send_message(
            success("Successfully changed the amount of winners that get picked for the raffle!"),
            ephemeral=True,
            delete_after=10,
        )


# endregion
# endregion
