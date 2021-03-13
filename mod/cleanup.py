import logging
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Set, Union

import discord
from redbot.core import checks, commands
from redbot.core.commands import Context, permissions_check
from redbot.core.utils.chat_formatting import humanize_number
from redbot.core.utils.mod import (
    check_permissions, is_mod_or_superior, mass_purge, slow_deletion
)
from redbot.core.utils.predicates import MessagePredicate

from .abc import MixinMeta
from .converters import PositiveInt, RawMessageIds, positive_int

log = logging.getLogger("red.angiedale.mod.cleanup")

def check_self_permissions():
    async def predicate(ctx: Context):
        if not ctx.guild:
            return True
        if await check_permissions(ctx, {"manage_messages": True}) or await is_mod_or_superior(
            ctx.bot, ctx.author
        ):
            return True
        return False

    return permissions_check(predicate)


class Cleanup(MixinMeta):
    """This cog contains commands used for "cleaning up" (deleting) messages.

    This is designed as a moderator tool and offers many convenient use cases.
    All cleanup commands only apply to the channel the command is executed in.

    Messages older than two weeks cannot be mass deleted.
    This is a limitation of the API.
    """

    @staticmethod
    async def check_100_plus(ctx: Context, number: int) -> bool:
        """
        Called when trying to delete more than 100 messages at once.

        Prompts the user to choose whether they want to continue or not.

        Tries its best to cleanup after itself if the response is positive.
        """

        if ctx.assume_yes:
            return True

        prompt = await ctx.send(
            ("Are you sure you want to delete {number} messages? (y/n)").format(
                number=humanize_number(number)
            )
        )
        response = await ctx.bot.wait_for("message", check=MessagePredicate.same_context(ctx))

        if response.content.lower().startswith("y"):
            await prompt.delete()
            try:
                await response.delete()
            except discord.HTTPException:
                pass
            return True
        else:
            await ctx.send(("Cancelled."))
            return False

    @staticmethod
    async def get_messages_for_deletion(
        *,
        channel: discord.TextChannel,
        number: Optional[PositiveInt] = None,
        check: Callable[[discord.Message], bool] = lambda x: True,
        limit: Optional[PositiveInt] = None,
        before: Union[discord.Message, datetime] = None,
        after: Union[discord.Message, datetime] = None,
        delete_pinned: bool = False,
    ) -> List[discord.Message]:
        """
        Gets a list of messages meeting the requirements to be deleted.
        Generally, the requirements are:
        - We don't have the number of messages to be deleted already
        - The message passes a provided check (if no check is provided,
          this is automatically true)
        - The message is less than 14 days old
        - The message is not pinned

        Warning: Due to the way the API hands messages back in chunks,
        passing after and a number together is not advisable.
        If you need to accomplish this, you should filter messages on
        the entire applicable range, rather than use this utility.
        """

        # This isn't actually two weeks ago to allow some wiggle room on API limits
        two_weeks_ago = datetime.utcnow() - timedelta(days=14, minutes=-5)

        def message_filter(message):
            return (
                check(message)
                and message.created_at > two_weeks_ago
                and (delete_pinned or not message.pinned)
            )

        if after:
            if isinstance(after, discord.Message):
                after = after.created_at
            after = max(after, two_weeks_ago)

        collected = []
        async for message in channel.history(
            limit=limit, before=before, after=after, oldest_first=False
        ):
            if message.created_at < two_weeks_ago:
                break
            if message_filter(message):
                collected.append(message)
                if number is not None and number <= len(collected):
                    break

        return collected

    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    @commands.group()
    async def cleanup(self, ctx: Context):
        """Base command for deleting messages."""
        pass

    @cleanup.command()
    @commands.bot_has_permissions(manage_messages=True)
    async def text(
        self, ctx: Context, text: str, number: positive_int, delete_pinned: bool = False
    ):
        """Delete the last X messages matching the specified text.

        Example:
            - `[p]cleanup text "test" 5`

        Remember to use double quotes.

        **Arguments:**

        - `<number>` The max number of messages to cleanup. Must be a positive integer.
        - `<delete_pinned>` Whether to delete pinned messages or not. Defaults to False
        """

        channel = ctx.channel

        author = ctx.author

        if number > 100:
            cont = await self.check_100_plus(ctx, number)
            if not cont:
                return

        def check(m):
            if text in m.content:
                return True
            else:
                return False

        to_delete = await self.get_messages_for_deletion(
            channel=channel,
            number=number,
            check=check,
            before=ctx.message,
            delete_pinned=delete_pinned,
        )
        to_delete.append(ctx.message)

        reason = "{}({}) deleted {} messages containing '{}' in channel {}.".format(
            author.name,
            author.id,
            humanize_number(len(to_delete), override_locale="en_us"),
            text,
            channel.id,
        )
        log.info(reason)

        await mass_purge(to_delete, channel)

    @cleanup.command()
    @commands.bot_has_permissions(manage_messages=True)
    async def user(
        self, ctx: Context, user: str, number: positive_int, delete_pinned: bool = False
    ):
        """Delete the last X messages from a specified user.

        Examples:
            - `[p]cleanup user @Twentysix 2`
            - `[p]cleanup user Red 6`

        **Arguments:**

        - `<user>` The user whose messages are to be cleaned up.
        - `<number>` The max number of messages to cleanup. Must be a positive integer.
        - `<delete_pinned>` Whether to delete pinned messages or not. Defaults to False
        """
        channel = ctx.channel

        member = None
        try:
            member = await commands.MemberConverter().convert(ctx, user)
        except commands.BadArgument:
            try:
                _id = int(user)
            except ValueError:
                raise commands.BadArgument()
        else:
            _id = member.id

        author = ctx.author

        if number > 100:
            cont = await self.check_100_plus(ctx, number)
            if not cont:
                return

        def check(m):
            if m.author.id == _id:
                return True
            else:
                return False

        to_delete = await self.get_messages_for_deletion(
            channel=channel,
            number=number,
            check=check,
            before=ctx.message,
            delete_pinned=delete_pinned,
        )
        to_delete.append(ctx.message)

        reason = (
            "{}({}) deleted {} messages "
            " made by {}({}) in channel {}."
            "".format(
                author.name,
                author.id,
                humanize_number(len(to_delete), override_locale="en_US"),
                member or "???",
                _id,
                channel.name,
            )
        )
        log.info(reason)

        await mass_purge(to_delete, channel)

    @cleanup.command()
    @commands.bot_has_permissions(manage_messages=True)
    async def after(
        self, ctx: Context, message_id: RawMessageIds, delete_pinned: bool = False
    ):
        """Delete all messages after a specified message.

        To get a message id, enable developer mode in Discord's
        settings, 'appearance' tab. Then right click a message
        and copy its id.

        **Arguments:**

        - `<message_id>` The id of the message to cleanup after. This message won't be deleted.
        - `<delete_pinned>` Whether to delete pinned messages or not. Defaults to False
        """

        channel = ctx.channel
        author = ctx.author

        try:
            after = await channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send(("Message not found."))

        to_delete = await self.get_messages_for_deletion(
            channel=channel, number=None, after=after, delete_pinned=delete_pinned
        )

        reason = "{}({}) deleted {} messages in channel {}.".format(
            author.name,
            author.id,
            humanize_number(len(to_delete), override_locale="en_US"),
            channel.name,
        )
        log.info(reason)

        await mass_purge(to_delete, channel)

    @cleanup.command()
    @commands.bot_has_permissions(manage_messages=True)
    async def before(
        self,
        ctx: Context,
        message_id: RawMessageIds,
        number: positive_int,
        delete_pinned: bool = False,
    ):
        """Deletes X messages before the specified message.

        To get a message id, enable developer mode in Discord's
        settings, 'appearance' tab. Then right click a message
        and copy its id.

        **Arguments:**

        - `<message_id>` The id of the message to cleanup before. This message won't be deleted.
        - `<number>` The max number of messages to cleanup. Must be a positive integer.
        - `<delete_pinned>` Whether to delete pinned messages or not. Defaults to False
        """

        channel = ctx.channel
        author = ctx.author

        try:
            before = await channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send(("Message not found."))

        to_delete = await self.get_messages_for_deletion(
            channel=channel, number=number, before=before, delete_pinned=delete_pinned
        )
        to_delete.append(ctx.message)

        reason = "{}({}) deleted {} messages in channel {}.".format(
            author.name,
            author.id,
            humanize_number(len(to_delete), override_locale="en_US"),
            channel.name,
        )
        log.info(reason)

        await mass_purge(to_delete, channel)

    @cleanup.command()
    @commands.bot_has_permissions(manage_messages=True)
    async def between(
        self,
        ctx: Context,
        one: RawMessageIds,
        two: RawMessageIds,
        delete_pinned: bool = False,
    ):
        """Delete the messages between Message One and Message Two, providing the messages IDs.

        The first message ID should be the older message and the second one the newer.

        Example:
            - `[p]cleanup between 123456789123456789 987654321987654321`

        **Arguments:**

        - `<one>` The id of the message to cleanup after. This message won't be deleted.
        - `<two>` The id of the message to cleanup before. This message won't be deleted.
        - `<delete_pinned>` Whether to delete pinned messages or not. Defaults to False
        """
        channel = ctx.channel
        author = ctx.author
        try:
            mone = await channel.fetch_message(one)
        except discord.errors.NotFound:
            return await ctx.send(
                ("Could not find a message with the ID of {id}.".format(id=one))
            )
        try:
            mtwo = await channel.fetch_message(two)
        except discord.errors.NotFound:
            return await ctx.send(
                ("Could not find a message with the ID of {id}.".format(id=two))
            )
        to_delete = await self.get_messages_for_deletion(
            channel=channel, before=mtwo, after=mone, delete_pinned=delete_pinned
        )
        to_delete.append(ctx.message)
        reason = "{}({}) deleted {} messages in channel {}.".format(
            author.name,
            author.id,
            humanize_number(len(to_delete), override_locale="en_US"),
            channel.name,
        )
        log.info(reason)

        await mass_purge(to_delete, channel)

    @cleanup.command()
    @commands.bot_has_permissions(manage_messages=True)
    async def messages(
        self, ctx: Context, number: positive_int, delete_pinned: bool = False
    ):
        """Delete the last X messages.

        Example:
            - `[p]cleanup messages 26`

        **Arguments:**

        - `<number>` The max number of messages to cleanup. Must be a positive integer.
        - `<delete_pinned>` Whether to delete pinned messages or not. Defaults to False
        """

        channel = ctx.channel
        author = ctx.author

        if number > 100:
            cont = await self.check_100_plus(ctx, number)
            if not cont:
                return

        to_delete = await self.get_messages_for_deletion(
            channel=channel, number=number, before=ctx.message, delete_pinned=delete_pinned
        )
        to_delete.append(ctx.message)

        reason = "{}({}) deleted {} messages in channel {}.".format(
            author.name, author.id, len(to_delete), channel.name
        )
        log.info(reason)

        await mass_purge(to_delete, channel)

    @cleanup.command(name="bot")
    @commands.bot_has_permissions(manage_messages=True)
    async def cleanup_bot(
        self, ctx: Context, number: positive_int, delete_pinned: bool = False
    ):
        """Clean up command messages and messages from the bot.

        Can only cleanup custom commands and alias commands if those cogs are loaded.

        **Arguments:**

        - `<number>` The max number of messages to cleanup. Must be a positive integer.
        - `<delete_pinned>` Whether to delete pinned messages or not. Defaults to False
        """

        channel = ctx.channel
        author = ctx.message.author

        if number > 100:
            cont = await self.check_100_plus(ctx, number)
            if not cont:
                return

        prefixes = await self.bot.get_prefix(ctx.message)  # This returns all server prefixes
        if isinstance(prefixes, str):
            prefixes = [prefixes]

        # In case some idiot sets a null prefix
        if "" in prefixes:
            prefixes.remove("")

        cc_cog = self.bot.get_cog("CustomCommands")
        if cc_cog is not None:
            command_names: Set[str] = await cc_cog.get_command_names(ctx.guild)
            is_cc = lambda name: name in command_names
        else:
            is_cc = lambda name: False
        alias_cog = self.bot.get_cog("Alias")
        if alias_cog is not None:
            alias_names: Set[str] = set(
                a.name for a in await alias_cog._aliases.get_global_aliases()
            ) | set(a.name for a in await alias_cog._aliases.get_guild_aliases(ctx.guild))
            is_alias = lambda name: name in alias_names
        else:
            is_alias = lambda name: False

        bot_id = self.bot.user.id

        def check(m):
            if m.author.id == bot_id:
                return True
            elif m == ctx.message:
                return True
            p = discord.utils.find(m.content.startswith, prefixes)
            if p and len(p) > 0:
                cmd_name = m.content[len(p) :].split(" ")[0]
                return (
                    bool(self.bot.get_command(cmd_name)) or is_alias(cmd_name) or is_cc(cmd_name)
                )
            return False

        to_delete = await self.get_messages_for_deletion(
            channel=channel,
            number=number,
            check=check,
            before=ctx.message,
            delete_pinned=delete_pinned,
        )
        to_delete.append(ctx.message)

        reason = (
            "{}({}) deleted {} "
            " command messages in channel {}."
            "".format(
                author.name,
                author.id,
                humanize_number(len(to_delete), override_locale="en_US"),
                channel.name,
            )
        )
        log.info(reason)

        await mass_purge(to_delete, channel)

    @cleanup.command(name="self")
    @check_self_permissions()
    async def cleanup_self(
        self,
        ctx: Context,
        number: positive_int,
        match_pattern: str = None,
        delete_pinned: bool = False,
    ):
        """Clean up messages owned by the bot.

        By default, all messages are cleaned. If a second argument is specified,
        it is used for pattern matching - only messages containing the given text will be deleted.

        Examples:
            - `[p]cleanup self 6`
            - `[p]cleanup self 10 Pong`
            - `[p]cleanup self 7 "" True`

        **Arguments:**

        - `<number>` The max number of messages to cleanup. Must be a positive integer.
        - `<match_pattern>` The text that messages must contain to be deleted. Use "" to skip this.
        - `<delete_pinned>` Whether to delete pinned messages or not. Defaults to False
        """
        channel = ctx.channel
        author = ctx.message.author

        if number > 100:
            cont = await self.check_100_plus(ctx, number)
            if not cont:
                return

        # You can always delete your own messages, this is needed to purge
        can_mass_purge = False
        if type(author) is discord.Member:
            me = ctx.guild.me
            can_mass_purge = channel.permissions_for(me).manage_messages

        if match_pattern:

            def content_match(c):
                return match_pattern in c

        else:

            def content_match(_):
                return True

        def check(m):
            if m.author.id != self.bot.user.id:
                return False
            elif content_match(m.content):
                return True
            return False

        to_delete = await self.get_messages_for_deletion(
            channel=channel,
            number=number,
            check=check,
            before=ctx.message,
            delete_pinned=delete_pinned,
        )

        if ctx.guild:
            channel_name = "channel " + channel.name
        else:
            channel_name = str(channel)

        reason = (
            "{}({}) deleted {} messages "
            "sent by the bot in {}."
            "".format(
                author.name,
                author.id,
                humanize_number(len(to_delete), override_locale="en_US"),
                channel_name,
            )
        )
        log.info(reason)

        if can_mass_purge:
            await mass_purge(to_delete, channel)
        else:
            await slow_deletion(to_delete)

    @cleanup.command(name="duplicates", aliases=["spam"])
    @commands.bot_has_permissions(manage_messages=True)
    async def cleanup_duplicates(
        self, ctx: Context, number: positive_int = PositiveInt(50)
    ):
        """Deletes duplicate messages in the channel from the last X messages and keeps only one copy.

        Defaults to 50.

        **Arguments:**

        - `<number>` The number of messages to check for duplicates. Must be a positive integer.
        """
        msgs = []
        spam = []

        def check(m):
            if m.attachments:
                return False
            c = (m.author.id, m.content, [e.to_dict() for e in m.embeds])
            if c in msgs:
                spam.append(m)
                return True
            else:
                msgs.append(c)
                return False

        to_delete = await self.get_messages_for_deletion(
            channel=ctx.channel, limit=number, check=check, before=ctx.message
        )

        if len(to_delete) > 100:
            cont = await self.check_100_plus(ctx, len(to_delete))
            if not cont:
                return

        log.info(
            "%s (%s) deleted %s spam messages in channel %s (%s).",
            ctx.author,
            ctx.author.id,
            len(to_delete),
            ctx.channel,
            ctx.channel.id,
        )

        to_delete.append(ctx.message)
        await mass_purge(to_delete, ctx.channel)
