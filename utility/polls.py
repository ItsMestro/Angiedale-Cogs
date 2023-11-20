import asyncio
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import ceil
from typing import Any, Dict, List, Optional, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.converter import TimedeltaConverter
from redbot.core.utils.chat_formatting import bold, inline, success
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate

from .abc import MixinMeta
from .converters import RawMessageIds, TrueEmojiConverter
from .ui import EmbedEditorBaseView, ItemSelectView, SelectViewItem, SimpleModal

log = logging.getLogger("red.angiedale.utility")


# region Item classes
class VoteType(Enum):
    single_vote = "Single Vote"
    multi_vote = "Multi Vote"


class PollOption:
    """Represents a poll voting option.

    Parameters
    ----------
    index: :class:`int`
        The order this option shows up in the list.
    name: :class:`str`
        The name of the poll voting option.
    emoji: :class:`discord.Emoji` or :class:`str`
        The emoji associated with the voting option. Can either be a custom emoji or built-in one.
    """

    def __init__(self, **kwargs: Any):
        # required
        self.name: str = kwargs.get("name")
        self.emoji: Union[discord.Emoji, int, str] = kwargs.get("emoji")

        # pre-defined
        self.index: int = kwargs.get("index", 0)
        self.votes: List[int] = kwargs.get("votes", [])

    @property
    def vote_count(self) -> int:
        return len(self.votes)

    def to_string(self) -> str:
        return f"{ReactionPredicate.ALPHABET_EMOJIS[self.index] if isinstance(self.emoji, int) else str(self.emoji)} {self.name}"

    def to_dict(self) -> Dict[str, Union[int, str]]:
        return {
            "name": self.name,
            "index": self.index,
            "emoji": self.emoji.id if isinstance(self.emoji, discord.Emoji) else self.emoji,
            "votes": self.votes,
        }

    def __repr__(self) -> str:
        return f"<PollOption index={self.index} name={self.name} emoji={str(self.emoji)} votes={self.vote_count}>"


class Poll:
    """A poll object."""

    def __init__(self, **kwargs):
        # Required on init
        self._end_time: Union[datetime, int] = kwargs.get("end_time")

        # Has defaults
        self.use_buttons: bool = kwargs.get("use_buttons", True)

        self._options: Union[List[PollOption], dict] = kwargs.get("options", [])
        self._vote_type: Union[VoteType, str] = kwargs.get("vote_type", VoteType.single_vote)
        self._init_roles: List[Union[discord.Role, int]] = kwargs.get("roles", [])
        self._roles: List[discord.Role] = []

        # Extra
        self.question: str = kwargs.get("question", None)

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

        if isinstance(self._vote_type, str):
            self._vote_type = VoteType(self._vote_type)

        if len(self._options) > 0 and isinstance(self._options, dict):
            self._options = [PollOption(**option) for option in self._options.values()]

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

    def set_emojis(self, bot: Red):
        for i, option in enumerate(self._options):
            if isinstance(option.emoji, int):
                emoji = bot.get_emoji(option.emoji)
                if emoji is not None:
                    self._options[i].emoji = emoji

    @property
    def roles(self) -> List[discord.Role]:
        if len(self._init_roles) > 0 and len(self.roles) == 0 and self.guild is not None:
            self._set_role_list()
            return self._roles

        return self._roles

    @roles.setter
    def roles(self, value: List[Union[discord.Role, int]]) -> None:
        self._init_roles = value

        if self.guild is not None:
            self._set_role_list()

    @property
    def timestamp(self) -> int:
        if isinstance(self._end_time, int):
            self._parse_timestamp()

        return int(self._end_time.timestamp())

    @property
    def timedelta(self) -> timedelta:
        return self.end_time - datetime.now(timezone.utc)

    @property
    def total_votes(self) -> int:
        return sum([option.vote_count for option in self._options])

    @property
    def options(self) -> List[PollOption]:
        return self._options

    @property
    def end_time(self) -> datetime:
        return self._end_time

    @property
    def vote_type(self) -> VoteType:
        return self._vote_type

    @vote_type.setter
    def vote_type(self, value: Union[VoteType, str]) -> None:
        if isinstance(value, str):
            value = VoteType(value)
        self._vote_type = value

    @property
    def embed(self) -> Optional[discord.Embed]:
        return self._embed

    @embed.setter
    def embed(self, value: discord.Embed) -> None:
        self._embed = value

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

    def get_channel(
        self, guild: discord.Guild
    ) -> Optional[
        Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
    ]:
        return guild.get_channel_or_thread(self._channel_id)

    def roles_to_string(self) -> str:
        if len(self.roles) == 0:
            return "@everyone"

        return " ".join([role.mention for role in self.roles])

    def to_role_ids(self) -> List[int]:
        return [role.id for role in self.roles]

    def options_to_emojis(self, return_as_string: bool = False) -> List[Union[discord.Emoji, str]]:
        if return_as_string:
            return [str(o.emoji) for o in self.options]
        return [o.emoji for o in self.options]

    def options_to_dict(self) -> Dict[str, Dict[str, Union[int, str]]]:
        output = {}
        for option in self.options:
            output.update({str(option.index): option.to_dict()})
        return output

    def to_dict(self) -> dict:
        return {
            "end_time": self.timestamp,
            "question": self.question,
            "channel_id": self._channel_id,
            "message_id": self._message_id,
            "vote_type": self.vote_type.value,
            "roles": self.to_role_ids(),
            "use_buttons": self.use_buttons,
            "options": self.options_to_dict(),
        }


# endregion


class Polls(MixinMeta):
    """Interactive polling system."""

    def __init__(self):
        self.poll_cache: Dict[int, Dict[int, Poll]] = {}
        self.polls: Dict[int, Dict[int, Poll]] = {}
        self.loop_delay: bool = True

    # region Commands
    @commands.mod_or_permissions(administrator=True)
    @commands.group()
    @commands.guild_only()
    async def poll(self, ctx: commands.Context):
        """Create and manage polls."""

    @poll.command(name="clearguild", alises=["resetguild"], hidden=True)
    @commands.is_owner()
    async def _poll_clear_guild(self, ctx: commands.Context):
        await self.poll_config.guild(ctx.guild).polls.clear()
        await self.poll_config.guild(ctx.guild).polls_history.clear()
        await ctx.send("Poll data cleared out.")

    @commands.max_concurrency(1, commands.BucketType.guild)
    @poll.command(name="start", aliases=["create", "make", "new"])
    async def _poll_start(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        *,
        time: Optional[TimedeltaConverter] = None,
    ):
        """Start a poll.

        Time should be a string that doesn't exceed 8 weeks.

        Each poll is able to have up to 10 different answers.

        Examples:
        - `[p]poll start #polls 2w` (Channel Mention)
        - `[p]poll start channel-name 1week12days` (Channel Name)
        - `[p]poll start 661358360188289054 20 hours 30 min` (Channel ID)

        Up to 4 polls can be active per server.
        """
        if (
            not ctx.channel.permissions_for(ctx.guild.me).embed_links
            or not channel.permissions_for(ctx.guild.me).embed_links
        ):
            return await ctx.send(
                f"I need the {inline('Embed Links')} permission to be able to start polls."
            )
        if (
            not ctx.channel.permissions_for(ctx.guild.me).add_reactions
            or not channel.permissions_for(ctx.guild.me).add_reactions
        ):
            return await ctx.send(
                f"I need the {inline('Add Reactions')} permission to be able to start polls."
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
            return await ctx.send("You need to provide a valid time for the poll to last.")

        active_polls: dict = await self.poll_config.guild(ctx.guild).polls()

        if len(active_polls) >= 4:
            return await ctx.send(
                "You already have 4 polls running in the server. "
                "Wait for one of them to finish first before starting another one."
            )

        if time > timedelta(weeks=8):
            return await ctx.send(f"The time can't be longer than {inline('8 weeks')}.")
        if time < timedelta(minutes=5):
            return await ctx.send(f"The poll can't be shorter than {inline('5 minutes')}.")

        poll = await self.poll_setup(
            ctx, Poll(end_time=datetime.now(timezone.utc) + time), channel
        )

        if poll is None:
            return

        poll_message: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ] = await channel.send(
            embed=poll.embed,
        )

        if poll.use_buttons:
            view = PollView(config=self.poll_config, poll=poll, cog=self)

            await poll_message.edit(
                view=view,
            )
        else:
            try:
                self.polls[ctx.guild.id]
            except KeyError:
                self.polls.update({ctx.guild.id: {}})

            self.polls[ctx.guild.id][poll_message.id] = poll

            start_adding_reactions(poll_message, poll.options_to_emojis())

        await ctx.send(success(f"Poll sent! {poll_message.jump_url}"))

        poll.guild_id = ctx.guild.id
        poll.channel_id = channel.id
        poll.message_id = poll_message.id

        async with self.poll_config.guild(ctx.guild).polls() as polls:
            polls[poll.message_id] = poll.to_dict()

        self.active_poll_tasks.append(asyncio.create_task(self.poll_timer(poll)))

    @commands.max_concurrency(1, commands.BucketType.guild)
    @poll.command(name="end", aliases=["stop"])
    async def _poll_end(self, ctx: commands.Context, message_id: Optional[RawMessageIds] = None):
        """Ends a poll early.

        If `message_id` is left empty you're given a menu
        where you can pick which to one to end.
        """
        polls: dict = await self.poll_config.polls(ctx.guild).polls()
        if len(polls) == 0:
            return await ctx.send("There are no active polls running in the server.")

        new_polls: List[Poll] = []
        for poll in polls.values():
            new_poll = Poll(**poll)
            new_poll.guild = ctx.guild
            new_polls.append(new_poll)

        poll = await self.poll_selection(ctx, new_polls, message_id)
        if poll is None:
            return

        await self.end_poll(poll)

    @commands.cooldown(1, 30, commands.BucketType.member)
    @poll.command(name="list")
    async def _poll_list(self, ctx: commands.Context):
        """List current and past polls.

        Also allows you to see the results for them.
        The last 5 polls ran in the server are saved.
        """
        guild_data: dict = await self.poll_config.guild(ctx.guild).all()
        if len(guild_data["polls"]) == 0 and len(guild_data["polls_history"]) == 0:
            return await ctx.send("There are no current or past raffles in this server.")

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f"List of polls in {ctx.guild.name}", icon_url=ctx.guild.me.display_avatar.url
        )
        embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.set_footer(text="Name ◈ Message Link ◈ Ends/Ended ◈ Votes")

        view: Optional[ItemSelectView] = None
        if len(guild_data["polls"]) > 0:
            output = []
            for poll_data in guild_data["raffles"].values():
                poll = Poll(**poll_data)
                poll.guild = ctx.guild
                jump_url = ""
                if poll.channel is not None:
                    message = await poll.fetch_message()
                    if message is not None:
                        jump_url = f" ◈ {message.jump_url}"
                output.append(
                    "\n".join(
                        [
                            f"{poll.question}" f"{jump_url}",
                            f"<t:{poll.timestamp}:D> <t:{poll.timestamp}:R>"
                            f" ◈ Votes: {poll.total_votes}",
                        ]
                    )
                )

            embed.add_field(name="Active Polls", value="\n\n".join(output), inline=False)

        if len(guild_data["polls_history"]) > 0:
            output = []
            polls: List[SelectViewItem] = []
            past_polls: List[Poll] = []
            for poll_data in guild_data["polls_history"]:
                poll = Poll(**poll_data)
                poll.guild = ctx.guild
                poll.set_emojis(self.bot)
                past_polls.append(poll)
                jump_url = ""
                if poll.channel is not None:
                    message = await poll.fetch_message()
                    if message is not None:
                        jump_url = f" ◈ {message.jump_url}"
                output.append(
                    "\n".join(
                        [
                            f"{poll.question}" f"{jump_url}",
                            f"<t:{poll.timestamp}:D> <t:{poll.timestamp}:R>"
                            f" ◈ Votes: {poll.total_votes}",
                        ]
                    )
                )
                polls.append(SelectViewItem(label=poll.question, value=poll.message_id))

            embed.add_field(name="Past Polls", value="\n\n".join(output), inline=False)

            view = ItemSelectView(
                polls,
                use_cancel=False,
                default_label="Select a past poll to show it's results",
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

            poll = next(filter(lambda p: p.message_id == int(view.value), past_polls))  # type: ignore

            if poll is None:
                return await ctx.send("An unknown error occured.")

            description_dicts: List[Dict[str, Union[int, str]]] = []

            unique_votes = set()
            longest_vote = len(str(max([option.vote_count for option in poll.options])))
            for option in poll.options:
                unique_votes.update(option.votes)
                option_votes_string = str(option.vote_count)
                while len(option_votes_string) < longest_vote:
                    option_votes_string += " "

                description_dicts.append(
                    {
                        "votes": option.vote_count,
                        "string": f"{inline(option_votes_string)} - {option.to_string()}",
                        "index": option.index,
                    }
                )

            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

            embed.title = poll.question
            embed.set_thumbnail(url=poll.guild.icon.url)
            embed.set_author(
                name=f"{poll.guild.name} Poll Results!", icon_url=self.bot.user.display_avatar.url
            )

            embed.add_field(
                name="Ended",
                value=f"<t:{poll.timestamp}:D> ◈ <t:{poll.timestamp}:R>",
                inline=False,
            )

            footer_text = f"A total of {poll.total_votes} votes were submitted!"
            if poll.vote_type == VoteType.multi_vote:
                footer_text = f"A total of {poll.total_votes} votes were submitted by {len(unique_votes)} user{'s' if len(unique_votes) > 1 else ''}"

            embed.set_footer(text=footer_text)

            embed.description = "\n".join(
                [
                    x["string"]
                    for x in sorted(description_dicts, key=lambda y: y["votes"], reverse=True)
                ]
            )

            if response_message is None:
                response_message = await ctx.send(embed=embed)
            else:
                await response_message.edit(embed=embed)

            view = ItemSelectView(
                polls,  # type: ignore
                use_cancel=False,
                default_label="Select a past poll to show it's results",
            )
            await original_response.edit(view=view)

    # endregion
    # region Command Functions
    async def load_polls(self) -> None:
        await self.bot.wait_until_red_ready()
        try:
            coroutines = []
            guilds = await self.poll_config.all_guilds()
            time_now = datetime.now(timezone.utc)
            for g_id, g_data in guilds.items():
                if len(g_data["polls"]) > 0:
                    guild = self.bot.get_guild(g_id)
                    if guild is None:
                        continue

                    for m_id, poll_data in g_data["polls"].items():
                        poll = Poll(**poll_data)
                        poll.set_emojis(self.bot)
                        poll.guild = guild
                        if time_now > poll.end_time:
                            await self.end_poll(poll)
                            continue

                        if poll.channel is None:
                            continue

                        message = await poll.fetch_message()

                        if message is None:
                            continue

                        if poll.use_buttons:
                            view = PollView(config=self.poll_config, poll=poll, cog=self)
                            self.bot.add_view(
                                view,
                                message_id=int(m_id),
                            )
                            await message.edit(view=view)
                        else:
                            try:
                                self.polls[g_id]
                            except KeyError:
                                self.polls.update({g_id: {}})

                            self.polls[g_id][message.id] = poll

                            await message.clear_reactions()
                            start_adding_reactions(message, poll.options_to_emojis())

                        coroutines.append(self.poll_timer(poll))

            await asyncio.gather(*coroutines)
        except Exception:
            log.error("Error during poll initialization", exc_info=True)

    async def poll_timer(self, poll: Poll) -> None:
        await asyncio.sleep(poll.timedelta.total_seconds())

        guild = self.bot.get_guild(poll.guild_id)
        if guild is None:
            return

        if self.poll_cache_task is not None:
            self.poll_cache_task.cancel()
            await asyncio.wait_for(self.poll_cache_task, timeout=30)

        async with self.poll_config.guild(guild).polls() as polls:
            fresh_poll_data: Dict[str, Union[List[int], int]] = polls.get(str(poll.message_id))
        if fresh_poll_data:
            fresh_poll = Poll(**fresh_poll_data)
            fresh_poll.guild = guild

            await self.end_poll(fresh_poll)

    async def end_poll(self, poll: Poll) -> None:
        if not poll.use_buttons:
            try:
                self.polls[poll.guild_id].pop(poll.message_id)
            except:
                pass

        if (
            poll.channel is None
            or not poll.channel.permissions_for(poll.guild.me).read_messages
            or not poll.channel.permissions_for(poll.guild.me).send_messages
        ):
            return await self.clear_poll_entry(poll)

        try:
            message = await poll.fetch_message()
        except discord.NotFound:
            return await self.clear_poll_entry(poll)
        except discord.Forbidden:
            return

        await self.send_results(poll, message)
        await self.move_poll_to_history(message)

    async def send_results(self, poll: Poll, message: discord.Message) -> None:
        poll_embed = message.embeds[0]
        poll_embed.set_field_at(
            index=1, name="Ended", value=poll_embed.fields[1].value, inline=False
        )
        poll_embed.set_footer(text=f"Guild polls brought to you by {poll.guild.me.display_name}!")

        description = poll_embed.description.splitlines()
        description_dicts: List[Dict[str, Union[int, str]]] = []

        unique_votes = set()
        longest_vote = len(str(max([option.vote_count for option in poll.options])))
        for option in poll.options:
            unique_votes.update(option.votes)
            option_votes_string = str(option.vote_count)
            while len(option_votes_string) < longest_vote:
                option_votes_string += " "

            description_dicts.append(
                {
                    "votes": option.vote_count,
                    "string": f"{inline(option_votes_string)} - {description[option.index]}",
                    "index": option.index,
                }
            )

        poll_embed.description = "\n".join([x["string"] for x in description_dicts])

        await message.edit(embed=poll_embed, view=None)
        if not poll.use_buttons:
            try:
                await message.clear_reactions()
            except:
                pass

        embed = discord.Embed(color=await self.bot.get_embed_color(poll.channel))

        embed.set_thumbnail(url=poll.guild.icon.url)
        embed.set_author(
            name=f"{poll.guild.name} Poll Results!", icon_url=self.bot.user.display_avatar.url
        )

        embed.add_field(
            name="Ended", value=f"<t:{poll.timestamp}:D> ◈ <t:{poll.timestamp}:R>", inline=False
        )

        footer_text = f"A total of {poll.total_votes} votes were submitted!"
        if poll.vote_type == VoteType.multi_vote:
            footer_text = f"A total of {poll.total_votes} votes were submitted by {len(unique_votes)} user{'s' if len(unique_votes) > 1 else ''}"

        embed.set_footer(text=footer_text)

        embed.description = "\n".join(
            [
                x["string"]
                for x in sorted(description_dicts, key=lambda y: y["votes"], reverse=True)
            ]
        )

        await poll.channel.send(embed=embed, reference=message)

    async def poll_selection(
        self, ctx: commands.Context, polls: List[Poll], message_id: Optional[int] = None
    ) -> Optional[Poll]:
        if message_id is None:
            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
            embed.set_author(
                name="List of currently active polls.", icon_url=ctx.me.display_avatar.url
            )
            embed.title = "Select which poll you'd like to end"
            embed.set_thumbnail(url=ctx.guild.icon.url)

            output: List[str] = []
            messages: List[discord.Message] = []
            for i, poll in enumerate(polls):
                if poll.channel is None:
                    continue
                message = await poll.fetch_message()
                if message is None:
                    continue

                output.append(
                    f"{ReactionPredicate.NUMBER_EMOJIS[i + 1]} {bold(message.embeds[0].title)} ◈ {message.jump_url}"
                )
                messages.append(message)

            if len(messages) == 0:
                await ctx.send("There are no active polls running in the server.")
                return

            embed.description = "\n".join(output)

            poll_items: List[SelectViewItem] = []
            for message in messages:
                poll_items.append(SelectViewItem(label=message.embeds[0].title, value=message.id))
            view = ItemSelectView(items=poll_items)
            select_message = await ctx.send(embed=embed, view=view)
            timed_out = await view.wait()
            if timed_out:
                await select_message.edit(content="Selection timed out!", embed=None, view=None)
                return

            if not view.result:
                await select_message.edit(content="Selection cancelled!", embed=None, view=None)
                return

            await select_message.delete()

            result = next(filter(lambda p: p.message_id == int(view.value), polls), None)
        else:
            poll = next(filter(lambda p: p.message_id == message_id, polls), None)

            if poll is None:
                await ctx.send("I couldn't find a poll with that message ID.")
                return

            if poll.channel is None:
                await ctx.send("I couldn't find the channel that the poll is supposed to be in.")
                return

            if await poll.fetch_message() is None:
                await ctx.send("I couldn't find the poll message.")
                return

            result = poll

        return result

    async def poll_setup(
        self,
        ctx: commands.Context,
        poll: Poll,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
    ) -> Optional[Poll]:
        ctx.channel.fetch_message
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.set_author(name=f"{ctx.guild.name} Poll!", icon_url=self.bot.user.display_avatar.url)

        embed.add_field(
            name="Ends", value=f"<t:{poll.timestamp}:D> ◈ <t:{poll.timestamp}:R>", inline=False
        )

        embed.add_field(name="Hosted By", value=ctx.author.mention, inline=True)
        embed.add_field(name="Mode", value=poll.vote_type.value, inline=True)
        embed.add_field(name="Allowed Roles", value=poll.roles_to_string(), inline=True)

        embed.set_footer(
            text="Click the buttons below to vote. If interaction fails, try again later. Bot might be down."
        )

        embed_message = await ctx.send(embed=embed, view=PollView(poll=poll, preview=True))

        view = PollSetupView(self.bot, ctx, embed, embed_message, poll)
        message = await ctx.send(
            "\n\n".join(
                [
                    f"This is a preview of how the embed will look when sent in {channel.mention}",
                    "\n".join(
                        [
                            f"Use the buttons below to customize it and click {inline('Finished')} when done. ",
                            "Keep in mind that you can search in the list of roles.",
                        ]
                    ),
                ]
            ),
            view=view,
        )
        timed_out = await view.wait()
        if timed_out:
            await message.edit(content="Poll creation timed out!", view=None)
            return

        if not view.result:
            return

        poll.embed = view.embed

        await message.delete()
        return view.poll

    async def clear_poll_entry(self, poll: Poll) -> None:
        async with self.poll_config.guild(poll.guild).polls() as polls:
            try:
                del polls[str(poll.message_id)]
            except KeyError:
                pass

    async def move_poll_to_history(self, message: discord.Message) -> None:
        async with self.poll_config.guild(message.guild).all() as guild_data:
            poll_data: Optional[
                Dict[str, Union[Dict[str, Union[int, str]], bool, List[int], int, str]]
            ] = guild_data["polls"].pop(str(message.id), None)
            if poll_data is not None:
                guild_data["polls_history"].insert(0, poll_data)
                if len(guild_data["polls_history"]) > 5:
                    guild_data["polls_history"].pop()

    async def schedule_poll_cache_dump(self):
        self.loop_delay = True
        if self.poll_cache_task is None or self.poll_cache_task.done():
            self.poll_cache_task = asyncio.create_task(self.dump_poll_cache())

    async def dump_poll_cache(self):
        try:
            while self.loop_delay:
                self.loop_delay = False
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

        poll_cache = self.poll_cache
        self.poll_cache = {}

        for g_id, messages in poll_cache.items():
            async with self.poll_config.guild_from_id(g_id).polls() as polls:
                for m_id, poll in messages.items():
                    try:
                        polls.get(str(m_id))
                    except KeyError:
                        continue

                    polls[str(m_id)]["options"] = poll.options_to_dict()

    async def update_cache(self, guild_id: int, message_id: int, poll: Poll) -> None:
        try:
            self.poll_cache[guild_id]
        except KeyError:
            self.poll_cache.update({guild_id: {}})

        self.poll_cache[guild_id][message_id] = poll

        await self.schedule_poll_cache_dump()

    # endregion


# region ui
# region Views
class PollView(discord.ui.View):
    def __init__(
        self, config: Config = None, poll: Poll = None, cog: Polls = None, preview: bool = False
    ):
        super().__init__(timeout=None)
        self.config = config
        self.poll = poll
        self.cog = cog
        self.preview = preview

        self.update_options()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.preview:
            await interaction.response.defer()
            return False

        if len(self.poll.roles) > 0:
            if not any(role in interaction.user.roles for role in self.poll.roles):
                await interaction.response.send_message(
                    "You don't have any of the required roles to interact with this poll!",
                    ephemeral=True,
                    delete_after=20,
                )
                return False

        return True

    async def update_view(self, interaction: discord.Interaction) -> None:
        self.update_options()
        await interaction.message.edit(view=self)

    def update_options(self) -> None:
        self.clear_items()

        row_split = ceil(len(self.poll.options) / 2) if len(self.poll.options) > 5 else 5
        for option in self.poll.options:
            self.add_item(
                PollOptionButton(option, row=0 if option.index < row_split else 1),
            )


class PollSetupView(EmbedEditorBaseView):
    def __init__(
        self,
        bot: Red,
        ctx: commands.Context,
        embed: discord.Embed,
        embed_message: discord.Message,
        poll: Poll,
    ):
        super().__init__(embed=embed, embed_message=embed_message, timeout=60 * 5)
        self.bot = bot
        self.ctx = ctx
        self.poll = poll

        self.result: bool = False
        self._poll_option_select: Optional[PollOptionSelect] = None
        self.custom_emojis: List[Union[discord.Emoji, str]] = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await interaction.client.is_owner(interaction.user):
            return False
        return True

    async def on_timeout(self) -> None:
        await self.embed_message.delete()

    async def update_reactions(self, clear_first: bool = False) -> None:
        if clear_first:
            await self.embed_message.clear_reactions()
        start_adding_reactions(self.embed_message, self.poll.options_to_emojis())

    @discord.ui.button(label="Finished", style=discord.ButtonStyle.green, row=0, disabled=True)
    async def finished_button(self, interaction: discord.Interaction, button: discord.Button):
        await self.embed_message.delete()
        if datetime.now(timezone.utc) > self.poll.end_time:
            await interaction.message.edit(
                content="The time this poll was supposed to end has already passed. Try again!",
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
        await interaction.message.edit(content="Cancelled poll creation!", view=None)
        self.stop()

    @discord.ui.button(
        label="Switch to reaction based entry",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def switch_type_button(self, interaction: discord.Interaction, button: discord.Button):
        self.poll.use_buttons = not self.poll.use_buttons
        if self.poll.use_buttons:
            self.switch_type_button.label = "Switch to reaction based entry"
            text = "Successfully swapped to using buttons for poll entries!"

            self.embed.set_footer(
                text="Click the button(s) below to vote in this poll. If interaction fails, try again later. Bot might be down."
            )
            await self.embed_message.clear_reactions()
            await self.embed_message.edit(
                embed=self.embed, view=PollView(poll=self.poll, preview=True)
            )
        else:
            self.switch_type_button.label = "Switch to button based entry"
            text = "Successfully swapped to using reactions for poll entries! "
            f"Keep in mind that reactions to the poll while the bot is down will {bold('not')} count."

            self.embed.set_footer(text="React to one of the emojis below to vote in this poll.")
            await self.embed_message.edit(embed=self.embed, view=None)
            start_adding_reactions(self.embed_message, self.poll.options_to_emojis())

        await self.update_view(interaction)
        await interaction.response.send_message(
            success(text),
            ephemeral=True,
            delete_after=10,
        )

    @discord.ui.button(label="Add Question", style=discord.ButtonStyle.primary, row=1)
    async def question_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.send_modal(
            SimpleModal(
                "Question",
                [
                    discord.ui.TextInput(
                        label="Poll Question",
                        style=discord.TextStyle.short,
                        required=True,
                        default=self.embed.title,
                        max_length=256,
                    )
                ],
                callback=self.question_button_callback,
            )
        )

    async def question_button_callback(
        self, interaction: discord.Interaction, values: List[discord.ui.TextInput]
    ) -> None:
        await interaction.response.defer()
        self.poll.question = values[0].value

        if self.poll.question == self.embed.title:
            return

        self.question_button.label = "Change Question"
        self.question_button.style = discord.ButtonStyle.grey
        if self.poll.question is not None and len(self.poll.options) > 0:
            self.finished_button.disabled = False

        self.embed.title = self.poll.question

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed)

        message: discord.WebhookMessage = await interaction.followup.send(
            success("Successfully changed the poll question."), ephemeral=True
        )

        async def delete_message():
            await asyncio.sleep(10)
            try:
                await message.delete()
            except:
                pass

        asyncio.create_task(delete_message())

    @discord.ui.button(label="Switch to Multi-vote", style=discord.ButtonStyle.secondary, row=1)
    async def poll_type_button(self, interaction: discord.Interaction, button: discord.Button):
        if self.poll.vote_type == VoteType.single_vote:
            self.poll.vote_type = VoteType.multi_vote
            self.poll_type_button.label = "Switch to Single-vote"
            self.embed.set_field_at(
                index=2, name="Mode", value=self.poll.vote_type.value, inline=True
            )
        else:
            self.poll.vote_type = VoteType.single_vote
            self.poll_type_button.label = "Switch to Multi-vote"
            self.embed.set_field_at(
                index=2, name="Mode", value=self.poll.vote_type.value, inline=True
            )

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed)

        await interaction.response.send_message(
            success(f"Successfully swapped poll mode to {inline(self.poll.vote_type.value)}"),
            ephemeral=True,
            delete_after=10,
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Set Allowed Roles (Leave empty for @everyone)",
        min_values=0,
        max_values=5,
        row=2,
    )
    async def roles_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if select.values == self.poll.roles:
            return

        self.poll.roles = select.values

        roles_string = (
            " ".join([role.mention for role in self.poll.roles])
            if len(self.poll.roles) > 0
            else "@everyone"
        )
        self.embed.set_field_at(index=3, name="Allowed Roles", value=roles_string, inline=True)

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed)
        await interaction.response.send_message(
            content=success("Successfully changed the allowed roles for the poll!"),
            ephemeral=True,
            delete_after=10,
        )

    @discord.ui.button(label="Add Vote Option", style=discord.ButtonStyle.primary, row=3)
    async def vote_option_button(self, interaction: discord.Interaction, button: discord.Button):
        index = len(self.poll.options)
        await interaction.response.send_modal(
            SimpleModal(
                "Vote Option",
                [
                    discord.ui.TextInput(
                        label=f"Vote Option {index + 1} / 10",
                        style=discord.TextStyle.short,
                        required=True,
                        max_length=256,
                    )
                ],
                callback=self.vote_option_button_callback,
            )
        )

    async def vote_option_button_callback(
        self, interaction: discord.Interaction, values: List[discord.ui.TextInput]
    ) -> None:
        await interaction.response.defer()
        index = len(self.poll.options)
        value = values[0].value

        if index > len(self.custom_emojis) - 1:
            emoji = ReactionPredicate.ALPHABET_EMOJIS[index]
        else:
            emoji = self.custom_emojis[index]

        poll_option = PollOption(index=index, name=value, emoji=emoji)

        self.poll.options.append(poll_option)

        if self.poll.question is not None and len(self.poll.options) > 0:
            self.finished_button.disabled = False

        if len(self.poll.options) == 10:
            self.vote_option_button.disabled = True

        self.vote_option_button.style = discord.ButtonStyle.secondary

        self.embed.description = (
            poll_option.to_string()
            if self.embed.description is None
            else "\n".join([self.embed.description, poll_option.to_string()])
        )

        if self._poll_option_select is not None:
            self.remove_item(self._poll_option_select)
        self._poll_option_select = PollOptionSelect(self, row=4)
        self.add_item(self._poll_option_select)

        if self.poll.use_buttons:
            view = PollView(poll=self.poll, preview=True)
        else:
            view = None
            await self.update_reactions()

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed, view=view)
        followup_message: discord.WebhookMessage = await interaction.followup.send(
            success("\n\n".join(["Successfully added poll option:", poll_option.to_string()])),
            ephemeral=True,
        )

        async def delete_message():
            await asyncio.sleep(10)
            try:
                await followup_message.delete()
            except:
                pass

        asyncio.create_task(delete_message())

    @discord.ui.button(label="Set Custom Emojis", style=discord.ButtonStyle.secondary, row=3)
    async def custom_emojis_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.defer(ephemeral=True)

        if len(self.custom_emojis) > 0:
            self.custom_emojis = []

            for option in self.poll.options:
                option.emoji = ReactionPredicate.ALPHABET_EMOJIS[option.index]

            self.embed.description = "\n".join(
                [option.to_string() for option in self.poll.options]
            )

            self.custom_emojis_button.style = discord.ButtonStyle.secondary
            self.custom_emojis_button.label = "Set Custom Emojis"

            if self._poll_option_select is not None:
                self.remove_item(self._poll_option_select)
            if len(self.poll.options) > 0:
                self._poll_option_select = PollOptionSelect(self, row=4)
                self.add_item(self._poll_option_select)

            if self.poll.use_buttons:
                view = PollView(poll=self.poll, preview=True)
            else:
                view = None
                await self.update_reactions(clear_first=True)

            await self.update_view(interaction)
            await self.embed_message.edit(embed=self.embed, view=view)
            await interaction.followup.send(
                content="Successfully reset the poll option emojis to default!", ephemeral=True
            )
            return

        message_string = "\n\n".join(
            [
                "To set custom emojis to use for the poll, send me a list of them "
                "as a single message with space in-between each like this:",
                f"{ReactionPredicate.NUMBER_EMOJIS[1]} {ReactionPredicate.NUMBER_EMOJIS[2]} {ReactionPredicate.NUMBER_EMOJIS[3]}",
                "I will fill the poll options with these in the same order you set the poll options "
                "and fill with letter emojis in case there's more options than custom emojis like this:",
                "\n".join(
                    [
                        f"{ReactionPredicate.NUMBER_EMOJIS[1]} Option 1",
                        f"{ReactionPredicate.NUMBER_EMOJIS[2]} Option 2",
                        f"{ReactionPredicate.NUMBER_EMOJIS[3]} Option 3",
                        f"{ReactionPredicate.ALPHABET_EMOJIS[3]} Option 4 (This got auto-filled)",
                    ]
                ),
            ]
        )

        message: discord.WebhookMessage = await interaction.followup.send(
            content=message_string, ephemeral=True
        )

        while True:
            end_early = False
            fresh_message_string = "\n\n".join(
                [
                    message_string,
                    f"To cancel just type {inline('cancel')} ◈ Auto-Timeout: <t:{int((datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp())}:R>",
                ]
            )
            await message.edit(content=fresh_message_string)

            try:
                pred_result: discord.Message = await self.bot.wait_for(
                    "message", check=MessagePredicate.same_context(self.ctx), timeout=60
                )
            except asyncio.TimeoutError:
                await message.edit(content="Custom emoji submission timed out!")
                return
            if pred_result.content.lower() == "cancel" or pred_result.content.lower() == "stop":
                await pred_result.delete()
                await message.edit(content="Cancelled custom emoji submission!")
                return

            actual_emojis: List[Union[discord.Emoji, str]] = []
            emoji_strings = pred_result.content.split(" ")
            for emoji_string in emoji_strings:
                if "><" in emoji_string:
                    await pred_result.delete()
                    await self.ctx.send(
                        "Make sure you have spaces between the emojis!", delete_after=10
                    )
                    end_early = True
                    break
                try:
                    emoji = await TrueEmojiConverter().convert(self.ctx, emoji_string)
                except:
                    await pred_result.delete()
                    await self.ctx.send(
                        "\n".join(
                            [
                                f"I couldn't identify {inline(emoji_string)} as an emoji.",
                                "Reason could be one of:",
                                "- I don't have access to it.",
                                "- You forgot to put spaces between the emojis",
                                "- It's not an emoji",
                            ]
                        ),
                        delete_after=10,
                    )
                    end_early = True
                    break

                actual_emojis.append(emoji)

            if end_early:
                continue
            break

        self.custom_emojis = actual_emojis

        for option in self.poll.options:
            if option.index > len(self.custom_emojis) - 1:
                option.emoji = ReactionPredicate.ALPHABET_EMOJIS[option.index]
            else:
                option.emoji = self.custom_emojis[option.index]

        self.embed.description = "\n".join([option.to_string() for option in self.poll.options])

        self.custom_emojis_button.style = discord.ButtonStyle.red
        self.custom_emojis_button.label = "Remove Custom Emojis"

        if self._poll_option_select is not None:
            self.remove_item(self._poll_option_select)
        if len(self.poll.options) > 0:
            self._poll_option_select = PollOptionSelect(self, row=4)
            self.add_item(self._poll_option_select)

        if self.poll.use_buttons:
            view = PollView(poll=self.poll, preview=True)
        else:
            view = None
            await self.update_reactions(clear_first=True)

        await self.update_view(interaction)
        await self.embed_message.edit(embed=self.embed, view=view)
        await pred_result.delete()

        await message.edit(content="Successfully changed the poll option emojis!")


# endregion
# region Buttons
class PollOptionButton(discord.ui.Button):
    def __init__(self, poll_option: PollOption, row: int = 0):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=f"[{poll_option.vote_count}]",
            emoji=poll_option.emoji
            if not isinstance(poll_option.emoji, int)
            else ReactionPredicate.NUMBER_EMOJIS(poll_option.index + 1),
            row=row,
            custom_id=f"PollOptionButton:{poll_option.index}",
        )

        self.option = poll_option

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.defer(ephemeral=True)

        removed_vote = None
        text = ""
        for i, option in enumerate(self.view.poll.options):
            if (
                self.view.poll.vote_type == VoteType.single_vote
                and option.index != self.option.index
            ):
                try:
                    self.view.poll.options[i].votes.remove(interaction.user.id)
                    removed_vote = option.to_string()
                except ValueError:
                    pass

            if option.index == self.option.index:
                if interaction.user.id not in self.view.poll.options[i].votes:
                    text = f"Successfully counted your vote for: {self.option.to_string()}"
                    self.view.poll.options[i].votes.append(interaction.user.id)
                else:
                    text = f"Removed your vote for: {self.option.to_string()}"
                    self.view.poll.options[i].votes.remove(interaction.user.id)

        if self.view.poll.vote_type == VoteType.single_vote and removed_vote is not None:
            text += f"\n\nAnd removed your previous vote for: {removed_vote}"

        await self.view.update_view(interaction)
        await interaction.followup.send(content=text, ephemeral=True)

        await self.view.cog.update_cache(
            interaction.guild.id, interaction.message.id, self.view.poll
        )


# endregion
# region Selects
class PollOptionSelect(discord.ui.Select):
    def __init__(self, view: PollSetupView, row: Optional[int] = None):
        self.parent_view = view
        self._row = row

        options: List[discord.SelectOption] = []
        for option in self.parent_view.poll.options:
            options.append(
                discord.SelectOption(
                    label=option.name, value=str(option.index), emoji=option.emoji
                )
            )

        super().__init__(
            placeholder="Select to Remove Vote Option",
            min_values=0,
            max_values=len(options),
            options=options,
            row=self._row,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        if len(self.values) == 0:
            return await interaction.response.defer()

        options_for_removal = sorted([int(x) for x in self.values], reverse=True)

        option_strings = self.parent_view.embed.description.splitlines()

        for i in options_for_removal:
            option_strings.pop(i)

        for i in options_for_removal:
            self.parent_view.poll.options.pop(i)

        for i in range(len(self.parent_view.poll.options)):
            self.parent_view.poll.options[i].index = i
            if i > len(self.parent_view.custom_emojis) - 1:
                self.parent_view.poll.options[i].emoji = ReactionPredicate.ALPHABET_EMOJIS[i]
            else:
                self.parent_view.poll.options[i].emoji = self.parent_view.custom_emojis[i]

        self.parent_view.embed.description = (
            "\n".join([poll_option.to_string() for poll_option in self.parent_view.poll.options])
            if len(self.parent_view.poll.options) > 0
            else None
        )

        self.parent_view.vote_option_button.disabled = False

        if self.parent_view._poll_option_select is not None:
            self.parent_view.remove_item(self.parent_view._poll_option_select)

        if len(self.parent_view.poll.options) == 0:
            self.parent_view.finished_button.disabled = True
            self.parent_view.vote_option_button.style = discord.ButtonStyle.primary
            self.parent_view._poll_option_select = None
        else:
            self.parent_view._poll_option_select = PollOptionSelect(self.parent_view, self._row)
            self.parent_view.add_item(self.parent_view._poll_option_select)

        if self.parent_view.poll.use_buttons:
            view = PollView(poll=self.parent_view.poll, preview=True)
        else:
            view = None
            await self.parent_view.update_reactions(clear_first=True)

        await self.parent_view.update_view(interaction)
        await self.parent_view.embed_message.edit(embed=self.parent_view.embed, view=view)

        return await interaction.response.send_message(
            success(
                f"Successfully removed the selected voting options{'s' if len(self.values) > 1 else ''}!"
            ),
            ephemeral=True,
            delete_after=10,
        )


# endregion
# endregion
