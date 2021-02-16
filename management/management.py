import asyncio
import logging
import re
from math import ceil
from datetime import datetime, timedelta, date
from dateutil.easter import easter
from copy import copy
from re import search
from string import Formatter
from typing import List, Literal, Callable, Optional, Set, Union
from random import choice as rndchoice

import discord
from redbot.core import checks, bank, commands, Config
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS, close_menu
from redbot.core.utils.mod import slow_deletion, mass_purge
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.chat_formatting import box, humanize_number, pagify, bold, humanize_timedelta
from redbot.core.utils.tunnel import Tunnel
from redbot.core.bot import Red
from .alias_entry import AliasEntry, AliasCache, ArgParseError
from .checks import check_self_permissions
from .converters import PositiveInt, RawMessageIds, positive_int

log = logging.getLogger("red.angiedale.management")


def is_owner_if_bank_global():
    """
    Command decorator. If the bank is global, it checks if the author is
    bot owner, otherwise it only checks
    if command was used in guild - it DOES NOT check any permissions.

    When used on the command, this should be combined
    with permissions check like `guildowner_or_permissions()`.
    """

    async def pred(ctx: commands.Context):
        author = ctx.author
        if not await bank.is_global():
            if not ctx.guild:
                return False
            return True
        else:
            return await ctx.bot.is_owner(author)

    return commands.check(pred)

class _TrackingFormatter(Formatter):
    def __init__(self):
        super().__init__()
        self.max = -1

    def get_value(self, key, args, kwargs):
        if isinstance(key, int):
            self.max = max((key, self.max))
        return super().get_value(key, args, kwargs)


class Management(commands.Cog):
    """Management commands"""

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.interaction = []
        self.last_change = None
        self.config = Config.get_conf(self, 1387003, cog_name="ManagementAlias")
        self.config.register_global(entries=[], handled_string_creator=False)
        self.config.register_guild(entries=[])
        self._aliases: AliasCache = AliasCache(config=self.config, cache_enabled=True)
        self._ready_event = asyncio.Event()

        self.presence_task = asyncio.create_task(self.maybe_update_presence())

    def cog_unload(self):
        self.presence_task.cancel()
        for user in self.interaction:
            self.bot.loop.create_task(self.stop_interaction(user))

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        if requester != "discord_deleted_user":
            return

        await self._ready_event.wait()
        await self._aliases.anonymize_aliases(user_id)

    async def cog_before_invoke(self, ctx):
        await self._ready_event.wait()

    async def _maybe_handle_string_keys(self):
        # This isn't a normal schema migration because it's being added
        # after the fact for GH-3788
        if await self.config.handled_string_creator():
            return

        async with self.config.entries() as alias_list:
            bad_aliases = []
            for a in alias_list:
                for keyname in ("creator", "guild"):
                    if isinstance((val := a.get(keyname)), str):
                        try:
                            a[keyname] = int(val)
                        except ValueError:
                            # Because migrations weren't created as changes were made,
                            # and the prior form was a string of an ID,
                            # if this fails, there's nothing to go back to
                            bad_aliases.append(a)
                            break

            for a in bad_aliases:
                alias_list.remove(a)

        # if this was using a custom group of (guild_id, aliasname) it would be better but...
        all_guild_aliases = await self.config.all_guilds()

        for guild_id, guild_data in all_guild_aliases.items():

            to_set = []
            modified = False

            for a in guild_data.get("entries", []):

                for keyname in ("creator", "guild"):
                    if isinstance((val := a.get(keyname)), str):
                        try:
                            a[keyname] = int(val)
                        except ValueError:
                            break
                        finally:
                            modified = True
                else:
                    to_set.append(a)

            if modified:
                await self.config.guild_from_id(guild_id).entries.set(to_set)

            await asyncio.sleep(0)
            # control yielded per loop since this is most likely to happen
            # at bot startup, where this is most likely to have a performance
            # hit.

        await self.config.handled_string_creator.set(True)

    @staticmethod
    async def check_100_plus(ctx: commands.Context, number: int) -> bool:
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

    def sync_init(self):
        t = asyncio.create_task(self._initialize())

        def done_callback(fut: asyncio.Future):
            try:
                t.result()
            except Exception as exc:
                log.exception("Failed to load alias cog", exc_info=exc)
                # Maybe schedule extension unloading with message to owner in future

        t.add_done_callback(done_callback)

    async def _initialize(self):
        """ Should only ever be a task """

        await self._maybe_handle_string_keys()

        if not self._aliases._loaded:
            await self._aliases.load_aliases()

        self._ready_event.set()

    def is_command(self, alias_name: str) -> bool:
        """
        The logic here is that if this returns true, the name should not be used for an alias
        The function name can be changed when alias is reworked
        """
        command = self.bot.get_command(alias_name)
        return command is not None or alias_name in commands.RESERVED_COMMAND_NAMES

    @staticmethod
    def is_valid_alias_name(alias_name: str) -> bool:
        return not bool(search(r"\s", alias_name)) and alias_name.isprintable()

    async def get_prefix(self, message: discord.Message) -> str:
        """
        Tries to determine what prefix is used in a message object.
            Looks to identify from longest prefix to smallest.

            Will raise ValueError if no prefix is found.
        :param message: Message object
        :return:
        """
        content = message.content
        prefix_list = await self.bot.command_prefix(self.bot, message)
        prefixes = sorted(prefix_list, key=lambda pfx: len(pfx), reverse=True)
        for p in prefixes:
            if content.startswith(p):
                return p
        raise ValueError("No prefix found.")

    async def call_alias(self, message: discord.Message, prefix: str, alias: AliasEntry):
        new_message = copy(message)
        try:
            args = alias.get_extra_args_from_alias(message, prefix)
        except commands.BadArgument:
            return

        trackform = _TrackingFormatter()
        command = trackform.format(alias.command, *args)

        # noinspection PyDunderSlots
        new_message.content = "{}{} {}".format(
            prefix, command, " ".join(args[trackform.max + 1 :])
        )
        await self.bot.process_commands(new_message)

    async def paginate_alias_list(
        self, ctx: commands.Context, alias_list: List[AliasEntry]
    ) -> None:
        names = sorted(["+ " + a.name for a in alias_list])
        message = "\n".join(names)
        temp = list(pagify(message, delims=["\n"], page_length=1850))
        alias_list = []
        count = 0
        for page in temp:
            count += 1
            page = page.lstrip("\n")
            page = (
                ("Aliases:\n")
                + page
                + ("\n\nPage {page}/{total}").format(page=count, total=len(temp))
            )
            alias_list.append(box("".join(page), "diff"))
        if len(alias_list) == 1:
            await ctx.send(alias_list[0])
            return
        await menu(ctx, alias_list, DEFAULT_CONTROLS)

    @checks.mod_or_permissions(manage_guild=True)
    @commands.group()
    async def alias(self, ctx: commands.Context):
        """Manage command aliases."""
        pass

    @alias.group(name="global")
    async def global_(self, ctx: commands.Context):
        """Manage global aliases."""
        pass

    @checks.admin_or_permissions(administrator=True)
    @alias.command(name="add")
    @commands.guild_only()
    async def _add_alias(self, ctx: commands.Context, alias_name: str, *, command):
        """Add an alias for a command."""
        # region Alias Add Validity Checking
        is_command = self.is_command(alias_name)
        if is_command:
            await ctx.send(
                (
                    "You attempted to create a new alias"
                    " with the name {name} but that"
                    " name is already a command on this bot."
                ).format(name=alias_name)
            )
            return

        alias = await self._aliases.get_alias(ctx.guild, alias_name)
        if alias:
            await ctx.send(
                (
                    "You attempted to create a new alias"
                    " with the name {name} but that"
                    " alias already exists."
                ).format(name=alias_name)
            )
            return

        is_valid_name = self.is_valid_alias_name(alias_name)
        if not is_valid_name:
            await ctx.send(
                (
                    "You attempted to create a new alias"
                    " with the name {name} but that"
                    " name is an invalid alias name. Alias"
                    " names may not contain spaces."
                ).format(name=alias_name)
            )
            return

        given_command_exists = self.bot.get_command(command.split(maxsplit=1)[0]) is not None
        if not given_command_exists:
            await ctx.send(
                ("You attempted to create a new alias for a command that doesn't exist.")
            )
            return
        # endregion

        # At this point we know we need to make a new alias
        #   and that the alias name is valid.

        try:
            await self._aliases.add_alias(ctx, alias_name, command)
        except ArgParseError as e:
            return await ctx.send(" ".join(e.args))

        await ctx.send(
            ("A new alias with the trigger `{name}` has been created.").format(name=alias_name)
        )

    @checks.is_owner()
    @global_.command(name="add")
    async def _add_global_alias(self, ctx: commands.Context, alias_name: str, *, command):
        """Add a global alias for a command."""
        # region Alias Add Validity Checking
        is_command = self.is_command(alias_name)
        if is_command:
            await ctx.send(
                (
                    "You attempted to create a new global alias"
                    " with the name {name} but that"
                    " name is already a command on this bot."
                ).format(name=alias_name)
            )
            return

        alias = await self._aliases.get_alias(None, alias_name)
        if alias:
            await ctx.send(
                (
                    "You attempted to create a new global alias"
                    " with the name {name} but that"
                    " alias already exists."
                ).format(name=alias_name)
            )
            return

        is_valid_name = self.is_valid_alias_name(alias_name)
        if not is_valid_name:
            await ctx.send(
                (
                    "You attempted to create a new global alias"
                    " with the name {name} but that"
                    " name is an invalid alias name. Alias"
                    " names may not contain spaces."
                ).format(name=alias_name)
            )
            return

        given_command_exists = self.bot.get_command(command.split(maxsplit=1)[0]) is not None
        if not given_command_exists:
            await ctx.send(
                ("You attempted to create a new alias for a command that doesn't exist.")
            )
            return
        # endregion

        try:
            await self._aliases.add_alias(ctx, alias_name, command, global_=True)
        except ArgParseError as e:
            return await ctx.send(" ".join(e.args))

        await ctx.send(
            ("A new global alias with the trigger `{name}` has been created.").format(
                name=alias_name
            )
        )

    @checks.mod_or_permissions(manage_guild=True)
    @alias.command(name="help")
    async def _help_alias(self, ctx: commands.Context, alias_name: str):
        """Try to execute help for the base command of the alias."""
        alias = await self._aliases.get_alias(ctx.guild, alias_name=alias_name)
        if alias:
            await self.bot.send_help_for(ctx, alias.command)
        else:
            await ctx.send(("No such alias exists."))

    @checks.mod_or_permissions(manage_guild=True)
    @alias.command(name="show")
    async def _show_alias(self, ctx: commands.Context, alias_name: str):
        """Show what command the alias executes."""
        alias = await self._aliases.get_alias(ctx.guild, alias_name)

        if alias:
            await ctx.send(
                ("The `{alias_name}` alias will execute the command `{command}`").format(
                    alias_name=alias_name, command=alias.command
                )
            )
        else:
            await ctx.send(("There is no alias with the name `{name}`").format(name=alias_name))

    @checks.admin_or_permissions(administrator=True)
    @alias.command(name="delete", aliases=["del", "remove"])
    @commands.guild_only()
    async def _del_alias(self, ctx: commands.Context, alias_name: str):
        """Delete an existing alias on this server."""
        if not await self._aliases.get_guild_aliases(ctx.guild):
            await ctx.send(("There are no aliases on this server."))
            return

        if await self._aliases.delete_alias(ctx, alias_name):
            await ctx.send(
                ("Alias with the name `{name}` was successfully deleted.").format(name=alias_name)
            )
        else:
            await ctx.send(("Alias with name `{name}` was not found.").format(name=alias_name))

    @checks.is_owner()
    @global_.command(name="delete", aliases=["del", "remove"])
    async def _del_global_alias(self, ctx: commands.Context, alias_name: str):
        """Delete an existing global alias."""
        if not await self._aliases.get_global_aliases():
            await ctx.send(("There are no global aliases on this bot."))
            return

        if await self._aliases.delete_alias(ctx, alias_name, global_=True):
            await ctx.send(
                ("Alias with the name `{name}` was successfully deleted.").format(name=alias_name)
            )
        else:
            await ctx.send(("Alias with name `{name}` was not found.").format(name=alias_name))

    @alias.command(name="list")
    @commands.guild_only()
    @checks.bot_has_permissions(add_reactions=True)
    @checks.mod_or_permissions(manage_guild=True)
    async def _list_alias(self, ctx: commands.Context):
        """List the available aliases on this server."""
        guild_aliases = await self._aliases.get_guild_aliases(ctx.guild)
        if not guild_aliases:
            return await ctx.send(("There are no aliases on this server."))
        await self.paginate_alias_list(ctx, guild_aliases)

    @global_.command(name="list")
    @checks.bot_has_permissions(add_reactions=True)
    @checks.mod_or_permissions(manage_guild=True)
    async def _list_global_alias(self, ctx: commands.Context):
        """List the available global aliases on this bot."""
        global_aliases = await self._aliases.get_global_aliases()
        if not global_aliases:
            return await ctx.send(("There are no global aliases."))
        await self.paginate_alias_list(ctx, global_aliases)

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):

        await self._ready_event.wait()

        if message.guild is not None:
            if await self.bot.cog_disabled_in_guild(self, message.guild):
                return

        try:
            prefix = await self.get_prefix(message)
        except ValueError:
            return

        try:
            potential_alias = message.content[len(prefix) :].split(" ")[0]
        except IndexError:
            return

        alias = await self._aliases.get_alias(message.guild, potential_alias)

        if alias:
            await self.call_alias(message, prefix, alias)

    @is_owner_if_bank_global()
    @checks.guildowner_or_permissions(administrator=True)
    @commands.group()
    async def bankset(self, ctx: commands.Context):
        """Base command for bank settings."""

    @bankset.command(name="showsettings")
    async def bankset_showsettings(self, ctx: commands.Context):
        """Show the current bank settings."""
        cur_setting = await bank.is_global()
        if cur_setting:
            group = bank._config
        else:
            if not ctx.guild:
                return
            group = bank._config.guild(ctx.guild)
        group_data = await group.all()
        bank_name = group_data["bank_name"]
        bank_scope = ("Global") if cur_setting else ("Server")
        currency_name = group_data["currency"]
        default_balance = group_data["default_balance"]
        max_balance = group_data["max_balance"]

        settings = (
            "Bank settings:\n\nBank name: {bank_name}\nBank scope: {bank_scope}\n"
            "Currency: {currency_name}\nDefault balance: {default_balance}\n"
            "Maximum allowed balance: {maximum_bal}\n"
        ).format(
            bank_name=bank_name,
            bank_scope=bank_scope,
            currency_name=currency_name,
            default_balance=humanize_number(default_balance),
            maximum_bal=humanize_number(max_balance),
        )
        await ctx.send(box(settings))

    @bankset.command(name="toggleglobal")
    @checks.is_owner()
    async def bankset_toggleglobal(self, ctx: commands.Context, confirm: bool = False):
        """Toggle whether the bank is global or not.

        If the bank is global, it will become per-server.
        If the bank is per-server, it will become global.
        """
        cur_setting = await bank.is_global()

        word = ("per-server") if cur_setting else ("global")
        if confirm is False:
            await ctx.send(
                (
                    "This will toggle the bank to be {banktype}, deleting all accounts "
                    "in the process! If you're sure, type `{command}`"
                ).format(banktype=word, command=f"{ctx.clean_prefix}bankset toggleglobal yes")
            )
        else:
            await bank.set_global(not cur_setting)
            await ctx.send(("The bank is now {banktype}.").format(banktype=word))

    @is_owner_if_bank_global()
    @checks.guildowner_or_permissions(administrator=True)
    @bankset.command(name="bankname")
    async def bankset_bankname(self, ctx: commands.Context, *, name: str):
        """Set the bank's name."""
        await bank.set_bank_name(name, ctx.guild)
        await ctx.send(("Bank name has been set to: {name}").format(name=name))

    @is_owner_if_bank_global()
    @checks.guildowner_or_permissions(administrator=True)
    @bankset.command(name="creditsname")
    async def bankset_creditsname(self, ctx: commands.Context, *, name: str):
        """Set the name for the bank's currency."""
        await bank.set_currency_name(name, ctx.guild)
        await ctx.send(("Currency name has been set to: {name}").format(name=name))

    @is_owner_if_bank_global()
    @checks.guildowner_or_permissions(administrator=True)
    @bankset.command(name="maxbal")
    async def bankset_maxbal(self, ctx: commands.Context, *, amount: int):
        """Set the maximum balance a user can get."""
        try:
            await bank.set_max_balance(amount, ctx.guild)
        except ValueError:
            # noinspection PyProtectedMember
            return await ctx.send(
                ("Amount must be greater than zero and less than {max}.").format(
                    max=humanize_number(bank._MAX_BALANCE)
                )
            )
        await ctx.send(
            ("Maximum balance has been set to: {amount}").format(amount=humanize_number(amount))
        )

    @checks.mod_or_permissions(manage_messages=True)
    @commands.group()
    async def cleanup(self, ctx: commands.Context):
        """Base command for deleting messages."""
        pass

    @cleanup.command()
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def text(
        self, ctx: commands.Context, text: str, number: positive_int, delete_pinned: bool = False
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
        if can_mass_purge:
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
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def user(
        self, ctx: commands.Context, user: str, number: positive_int, delete_pinned: bool = False
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
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def after(
        self, ctx: commands.Context, message_id: RawMessageIds, delete_pinned: bool = False
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
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def before(
        self,
        ctx: commands.Context,
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
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def between(
        self,
        ctx: commands.Context,
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
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def messages(
        self, ctx: commands.Context, number: positive_int, delete_pinned: bool = False
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
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def cleanup_bot(
        self, ctx: commands.Context, number: positive_int, delete_pinned: bool = False
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
        alias_cog = self.bot.get_cog("Management")
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
        ctx: commands.Context,
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

    @cleanup.command(name="spam")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def cleanup_spam(self, ctx: commands.Context, number: positive_int = PositiveInt(50)):
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

    async def maybe_update_presence(self):
        await self.bot.wait_until_red_ready()
        delay = 90
        while True:
            try:
                await self.presence_updater()
                await asyncio.sleep(int(delay))
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(e, exc_info=e)

    async def presence_updater(self):
        pattern = re.compile(rf"<@!?{self.bot.user.id}>")
        guilds = self.bot.guilds
        guild = next(g for g in guilds if not g.unavailable)
        try:
            current_game = str(guild.me.activity.name)
        except AttributeError:
            current_game = None
        _type = 0

        url = f"https://www.twitch.tv/itsmestro"
        prefix = await self.bot.get_valid_prefixes()
        status = discord.Status.online

        me = self.bot.user
        clean_prefix = pattern.sub(f"@{me.name}", prefix[0])
        total_users = len(self.bot.users)
        servers = str(len(self.bot.guilds))
        helpaddon = f"{clean_prefix}help"
        usersstatus = f"with {total_users} users"
        serversstatus = f"in {servers} servers"
        datetoday = date.today()
        wheneaster = easter(datetoday.year)
        if datetoday >= wheneaster and datetoday <= wheneaster + timedelta(days=7):
            statuses = ["with you <3", "with things", "with ink", "Splatoon", "in the bot channel", "with my owner", "Happy Easter", "Happy Easter", "with colored eggs", "with bunnies", "egghunt", usersstatus, serversstatus,]
        elif datetoday.month == 2 and datetoday.day >= 14 and datetoday.day <= 15:
            statuses = ["with you <3", "with things", "with ink", "Splatoon", "in the bot channel", "with my owner", "Happy Valentine", "Happy Valentine", "cupid", "with love", "with a box of heart chocolate", "with my lover", "with my valentine", usersstatus, serversstatus,]
        elif datetoday.month == 12 and datetoday.day >= 24 and datetoday.day < 31:
            statuses = ["with you <3", "with things", "with ink", "Splatoon", "in the bot channel", "with my owner", "Merry Christmas", "Happy Holidays" "Merry Squidmas", "the christmas tree", "with santa", "with gifts", "in the snow", usersstatus, serversstatus,]
        elif datetoday.month == 12 and datetoday.day == 31 or datetoday.month == 1 and datetoday.day <= 7:
            statuses = ["with you <3", "with things", "with ink", "Splatoon", "in the bot channel", "with my owner", "Happy New Year", "Happy New Year", "with fireworks", usersstatus, serversstatus,]
        elif datetoday.month == 11 and datetoday.day == 31 or datetoday.month == 11 and datetoday.day <= 7:
            statuses = ["with you <3", "with things", "with ink", "Splatoon", "in the bot channel", "with my owner", "Happy Halloween", "Happy Splatoween", "trick or treat", "with candy", "spooky", "with pumpkins", usersstatus, serversstatus,]
        else:
            statuses = ["with you <3", "with things", "with ink", "Splatoon", "in the bot channel", "with my owner", "with Pearl", "with Marina", "with Callie", "with Marie", "with Agent 3", "with Agent 4", usersstatus, serversstatus,]
        new_status = self.random_status(guild, statuses)
        if (current_game != new_status) or (current_game is None):
            new_status = " | ".join((new_status, helpaddon))
            await self.bot.change_presence(
                activity=discord.Activity(name=new_status, type=_type), status=status
            )

    def random_status(self, guild, statuses):
        try:
            current = str(guild.me.activity.name)
        except AttributeError:
            current = None
        new_statuses = [s for s in statuses if s != current]
        if len(new_statuses) > 1:
            return rndchoice(new_statuses)
        elif len(new_statuses) == 1:
            return new_statuses[0]
        return current

    async def say(self, ctx: commands.Context, channel: Optional[discord.TextChannel], text: str, files: list):
        if not channel:
            channel = ctx.channel
        if not text and not files:
            await ctx.send_help()
            return

        # preparing context info in case of an error
        if files != []:
            error_message = (
                "Has files: yes\n"
                f"Number of files: {len(files)}\n"
                f"Files URL: " + ", ".join([x.url for x in ctx.message.attachments])
            )
        else:
            error_message = "Has files: no"

        # sending the message
        try:
            await channel.send(text, files=files)
        except discord.errors.HTTPException as e:
            if not ctx.guild.me.permissions_in(channel).send_messages:
                author = ctx.author
                try:
                    await ctx.send(
                        ("I am not allowed to send messages in ") + channel.mention,
                        delete_after=2,
                    )
                except discord.errors.Forbidden:
                    await author.send(
                        ("I am not allowed to send messages in ") + channel.mention,
                        delete_after=15,
                    )
                    # If this fails then fuck the command author
            elif not ctx.guild.me.permissions_in(channel).attach_files:
                try:
                    await ctx.send(
                        ("I am not allowed to upload files in ") + channel.mention, delete_after=2
                    )
                except discord.errors.Forbidden:
                    await author.send(
                        ("I am not allowed to upload files in ") + channel.mention,
                        delete_after=15,
                    )
            else:
                log.error(
                    f"Unknown permissions error when sending a message.\n{error_message}",
                    exc_info=e,
                )

    @commands.command(name="say")
    @checks.guildowner()
    async def _say(self, ctx, channel: Optional[discord.TextChannel], *, text: str = ""):
        """
        Make the bot say what you want in the desired channel.

        If no channel is specified, the message will be send in the current channel.
        You can attach some files to upload them to Discord.

        Example usage :
        - `!say #general hello there`
        - `!say owo I have a file` (a file is attached to the command message)
        """

        files = await Tunnel.files_from_attatch(ctx.message)
        await self.say(ctx, channel, text, files)

    @commands.command(name="sayd", aliases=["sd"])
    @checks.guildowner()
    async def _saydelete(self, ctx, channel: Optional[discord.TextChannel], *, text: str = ""):
        """
        Same as say command, except it deletes your message.

        If the message wasn't removed, then I don't have enough permissions.
        """

        # download the files BEFORE deleting the message
        author = ctx.author
        files = await Tunnel.files_from_attatch(ctx.message)

        try:
            await ctx.message.delete()
        except discord.errors.Forbidden:
            try:
                await ctx.send(("Not enough permissions to delete messages."), delete_after=2)
            except discord.errors.Forbidden:
                await author.send(("Not enough permissions to delete messages."), delete_after=15)

        await self.say(ctx, channel, text, files)

    @commands.command(name="interact")
    @checks.is_owner()
    async def _interact(self, ctx, channel: discord.TextChannel = None):
        """Start receiving and sending messages as the bot through DM"""

        u = ctx.author
        if channel is None:
            if isinstance(ctx.channel, discord.DMChannel):
                await ctx.send(
                    _(
                        "You need to give a channel to enable this in DM. You can "
                        "give the channel ID too."
                    )
                )
                return
            else:
                channel = ctx.channel

        if u in self.interaction:
            await ctx.send(("A session is already running."))
            return

        message = await u.send(
            _(
                "I will start sending you messages from {0}.\n"
                "Just send me any message and I will send it in that channel.\n"
                "React with ❌ on this message to end the session.\n"
                "If no message was send or received in the last 5 minutes, "
                "the request will time out and stop."
            ).format(channel.mention)
        )
        await message.add_reaction("❌")
        self.interaction.append(u)

        while True:

            if u not in self.interaction:
                return

            try:
                message = await self.bot.wait_for("message", timeout=300)
            except asyncio.TimeoutError:
                await u.send(("Request timed out. Session closed"))
                self.interaction.remove(u)
                return

            if message.author == u and isinstance(message.channel, discord.DMChannel):
                files = await Tunnel.files_from_attatch(message)
                if message.content.startswith(tuple(await self.bot.get_valid_prefixes())):
                    return
                await channel.send(message.content, files=files)
            elif (
                message.channel != channel
                or message.author == channel.guild.me
                or message.author == u
            ):
                pass

            else:
                embed = discord.Embed()
                embed.set_author(
                    name="{} | {}".format(str(message.author), message.author.id),
                    icon_url=message.author.avatar_url,
                )
                embed.set_footer(text=message.created_at.strftime("%d %b %Y %H:%M"))
                embed.description = message.content
                embed.colour = message.author.color

                if message.attachments != []:
                    embed.set_image(url=message.attachments[0].url)

                await u.send(embed=embed)

    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    @commands.group()
    async def access(self, ctx):
        """Check channel access"""

    @access.command()
    async def compare(self, ctx, user: discord.Member, guild: int = None):
        """Compare channel access with [user]."""
        if user is None:
            return
        if guild is None:
            guild = ctx.guild
        else:
            guild = self.bot.get_guild(guild)

        try:
            tcs = guild.text_channels
            vcs = guild.voice_channels
        except AttributeError:
            return await ctx.send("User is not in that guild or I do not have access to that guild.")

        author_text_channels = [c for c in tcs if c.permissions_for(ctx.author).read_messages is True]
        author_voice_channels = [c for c in vcs if c.permissions_for(ctx.author).connect is True]

        user_text_channels = [c for c in tcs if c.permissions_for(user).read_messages is True]
        user_voice_channels = [c for c in vcs if c.permissions_for(user).connect is True]

        author_only_t = set(author_text_channels) - set(
            user_text_channels
        )  # text channels only the author has access to
        author_only_v = set(author_voice_channels) - set(
            user_voice_channels
        )  # voice channels only the author has access to

        user_only_t = set(user_text_channels) - set(author_text_channels)  # text channels only the user has access to
        user_only_v = set(user_voice_channels) - set(
            author_voice_channels
        )  # voice channels only the user has access to

        common_t = list(
            set([c for c in tcs]) - author_only_t - user_only_t
        )  # text channels that author and user have in common
        common_v = list(
            set([c for c in vcs]) - author_only_v - user_only_v
        )  # voice channels that author and user have in common

        theembed = []

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f"Comparing {ctx.author} with {user}",
            icon_url=user.avatar_url
        )
        embed.add_field(
            name=f"Text channels in common ◈ {len(common_t)}",
            value=(f"{' ◈ '.join([c.name for c in common_t])}" if common_t else "~"),
            inline=False
        )
        embed.add_field(
            name=f"Text channels {user} can exclusively access ◈ {len(user_only_t)}",
            value=(f"{' ◈ '.join([c.name for c in user_only_t])}" if user_only_t else "~"),
            inline=False
        )
        embed.add_field(
            name=f"Text channels you can exclusively access ◈ {len(author_only_t)}",
            value=(f"{' ◈ '.join([c.name for c in author_only_t])}" if author_only_t else "~"),
            inline=False
        )
        embed.add_field(
            name=f"Voice channels in common ◈ {len(common_v)}",
            value=(f"{' ◈ '.join([c.name for c in common_v])}" if common_v else "~"),
            inline=False
        )
        embed.add_field(
            name=f"Voice channels {user} can exclusively access ◈ {len(user_only_v)}",
            value=(f"{' ◈ '.join([c.name for c in user_only_v])}" if user_only_v else "~"),
            inline=False
        )
        embed.add_field(
            name=f"Voice channels you can exclusively access ◈ {len(author_only_v)}",
            value=(f"{' ◈ '.join([c.name for c in author_only_v])}" if author_only_v else "~"),
            inline=False
        )

        theembed.append(embed)

        await menu(ctx, theembed, {"\N{CROSS MARK}": close_menu})

    @access.command()
    async def text(self, ctx, user: discord.Member = None, guild: int = None):
        """Check text channel access."""
        if user is None:
            user = ctx.author
        if guild is None:
            guild = ctx.guild
        else:
            guild = self.bot.get_guild(guild)

        try:
            can_access = [c.name for c in guild.text_channels if c.permissions_for(user).read_messages == True]
            text_channels = [c.name for c in guild.text_channels]
        except AttributeError:
            return await ctx.send("User is not in that guild or I do not have access to that guild.")

        theembed = []

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f'{("You have" if user.id == ctx.author.id else str(user) + " has")} access to {len(can_access)} out of {len(text_channels)} text channels',
            icon_url=user.avatar_url
        )
        embed.add_field(
            name="Can Access",
            value=(f"{' ◈ '.join(can_access)}" if can_access else "~"),
            inline=False
        )
        embed.add_field(
            name="Can Not Access",
            value=(f"{' ◈ '.join(list(set(text_channels) - set(can_access)))}" if not len(list(set(text_channels) - set(can_access))) == 0 else "~"),
            inline=False
        )

        theembed.append(embed)

        await menu(ctx, theembed, {"\N{CROSS MARK}": close_menu})

    @access.command()
    async def voice(self, ctx, user: discord.Member = None, guild: int = None):
        """Check voice channel access."""
        if user is None:
            user = ctx.author
        if guild is None:
            guild = ctx.guild
        else:
            guild = self.bot.get_guild(guild)

        try:
            can_access = [c.name for c in guild.voice_channels if c.permissions_for(user).connect is True]
            voice_channels = [c.name for c in guild.voice_channels]
        except AttributeError:
            return await ctx.send("User is not in that guild or I do not have access to that guild.")

        theembed = []

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f'{("You have" if user.id == ctx.author.id else str(user) + " has")} access to {len(can_access)} out of {len(voice_channels)} voice channels',
            icon_url=user.avatar_url
        )
        embed.add_field(
            name="Can Access",
            value=(f"{' ◈ '.join(can_access)}" if can_access else "~"),
            inline=False
        )
        embed.add_field(
            name="Can Not Access",
            value=(f"{' ◈ '.join(list(set(voice_channels) - set(can_access)))}" if not len(list(set(voice_channels) - set(can_access))) == 0 else "~"),
            inline=False
        )

        theembed.append(embed)

        await menu(ctx, theembed, {"\N{CROSS MARK}": close_menu})

    @commands.group()
    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    async def user(self, ctx):
        """Check user information."""

    @user.command()
    async def inrole(self, ctx, *, role: discord.Role):
        """Check members in the role specified."""
        guild = ctx.guild

        users_in_role = "\n".join(sorted(m.display_name for m in guild.members if role in m.roles))
        embed_list = []
        base_embed = discord.Embed(colour=await self.bot.get_embed_color(ctx))

        if len(users_in_role) == 0:
            embed = base_embed.copy()
            embed.description=bold(f"0 users found with the {role.mention} role")
            embed_list.append(embed)
            await menu(ctx, embed_list, {"\N{CROSS MARK}": close_menu})
        else:
            base_embed.description=bold(f"{len([m for m in guild.members if role in m.roles])} users found with the {role.mention} role\n")
            for page in pagify(users_in_role, delims=["\n"], page_length=200):
                embed = base_embed.copy()
                embed.add_field(name="Users", value=page)
                embed_list.append(embed)
            final_embed_list = []
            for i, embed in enumerate(embed_list):
                embed.set_footer(text=f"Page {i + 1}/{len(embed_list)}")
                final_embed_list.append(embed)

            await menu(ctx, final_embed_list, DEFAULT_CONTROLS if len(final_embed_list) > 1 else {"\N{CROSS MARK}": close_menu})

    @user.command()
    async def joined(self, ctx, user: discord.Member = None):
        """Show when a user joined the guild."""
        if not user:
            user = ctx.author
        if user.joined_at:
            user_joined = user.joined_at.strftime("%d %b %Y %H:%M")
            since_joined = (ctx.message.created_at - user.joined_at).days
            joined_on = f"{user_joined} ({since_joined} days ago)"
        else:
            joined_on = "a mysterious date that not even Discord knows."

        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed(
                description=f"{user.mention} joined this guild on {joined_on}.", color=await ctx.embed_colour(),
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{user.display_name} joined this guild on {joined_on}.")

    @user.command()
    async def perms(self, ctx, user: Optional[discord.Member] = None, channel: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel] = None):
        """Fetch a specific user's permissions."""
        if user is None:
            user = ctx.author
        if channel is None:
            channel = ctx.channel

        perms = iter(channel.permissions_for(user))
        perms_we_have = ""
        perms_we_dont = ""
        for x in sorted(perms):
            if "True" in str(x):
                perms_we_have += "+\t{0}\n".format(str(x).split("'")[1])
            else:
                perms_we_dont += "-\t{0}\n".format(str(x).split("'")[1])

        if not perms_we_have:
            perms_we_have = "+\tNothing"
        if not perms_we_dont:
            perms_we_dont = "-\tNothing"

        page = []
        embed = discord.Embed(color=await ctx.embed_colour())
        embed.set_author(
            name=f"Permissions for {user.name} in {channel.name}",
            icon_url=user.avatar_url
        )
        embed.add_field(
            name="\N{WHITE HEAVY CHECK MARK}", value=perms_we_have, inline=True
        )
        embed.add_field(
            name="\N{CROSS MARK}", value=perms_we_dont, inline=True
        )
        page.append(embed)

        await menu(ctx, page, {"\N{CROSS MARK}": close_menu})

    @user.command()
    async def new(self, ctx, count: int = 5):
        """Lists the newest 5 members."""
        guild = ctx.guild
        count = max(min(count, 25), 5)
        members = sorted(guild.members, key=lambda m: m.joined_at, reverse=True)[:count]

        base_embed = discord.Embed(color=await ctx.embed_colour())
        base_embed.set_author(
            name=f"{count} newest members in {ctx.guild.name}",
            icon_url=self.bot.user.avatar_url
        )
        base_embed.set_thumbnail(url=ctx.guild.icon_url)

        n = 0
        p = 0
        embed_list = []
        timenow = datetime.utcnow()
        for m in members:
            jlist = humanize_timedelta(timedelta=timenow - m.joined_at).split(", ")
            clist = humanize_timedelta(timedelta=timenow - m.created_at).split(", ")
            joined = (f"{jlist[0]}, {jlist[1]}" if len(jlist) > 1 else jlist[0])
            created = (f"{clist[0]}, {clist[1]}" if len(clist) > 1 else clist[0])
            if n == 0:
                embed = base_embed.copy()
                p += 1
                embed.set_footer(text=f"Page {p}/{ceil(len(members) / 5)}")
            if n < 4:
                embed.add_field(name=f"{m.name} ({m.id})", value=f"Joined Server: {joined} ago\nJoined Discord: {created}\n\u200B", inline=False)
                n += 1
            else:
                embed.add_field(name=f"{m.name} ({m.id})", value=f"Joined Server: {joined} ago\nJoined Discord: {created}", inline=False)
                embed_list.append(embed)
                n = 0

        await menu(ctx, embed_list, DEFAULT_CONTROLS if len(embed_list) > 1 else {"\N{CROSS MARK}": close_menu})

    @commands.command(name="listguilds", aliases=["listservers", "guildlist", "serverlist"])
    @checks.is_owner()
    async def listguilds(self, ctx):
        """List the servers the bot is in."""
        guilds = sorted(self.bot.guilds, key=lambda g: -g.member_count)

        base_embed = discord.Embed(color=await ctx.embed_colour())

        base_embed.set_author(
            name=f"{self.bot.user.name} is in {len(guilds)} servers",
            icon_url=self.bot.user.avatar_url
        )

        guild_list = []
        for g in guilds:
            entry = f"**{g.name}** ◈ {humanize_number(g.member_count)} Users ◈ {g.id}"
            guild_list.append(entry)

        final = "\n".join(guild_list)

        page_list = []
        pages = list(pagify(final, delims=["\n"], page_length=1000))

        i = 1
        for page in pages:
            embed = base_embed.copy()
            embed.description = page
            embed.set_footer(text=f"Page {i}/{len(pages)}")

            page_list.append(embed)
            i += 1

        await menu(ctx, page_list, DEFAULT_CONTROLS if len(page_list) > 1 else {"\N{CROSS MARK}": close_menu})

    @commands.group(aliases=["server"])
    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    async def guild(self, ctx):
        """Check guild information."""

    @guild.command()
    async def banlist(self, ctx):
        """Displays the server's banlist."""
        try:
            banlist = await ctx.guild.bans()
        except discord.errors.Forbidden:
            await ctx.send("I do not have the `Ban Members` permission.")
            return
        bancount = len(banlist)
        ban_list = []
        if bancount == 0:
            msg = "No users are banned from this server."
        else:
            msg = ""
            for user_obj in banlist:
                user_name = f"{user_obj.user.name}#{user_obj.user.discriminator}"
                msg += f"`{user_obj.user.id} - {user_name}`\n"

        banlist = sorted(msg)
        embed_list = []
        for page in pagify(msg, shorten_by=1400):
            embed = discord.Embed(
                description="**Total bans:** {}\n\n{}".format(bancount, page), color=await self.bot.get_embed_color(ctx),
            )
            embed_list.append(embed)
        await menu(ctx, embed_list, DEFAULT_CONTROLS if len(embed_list) > 1 else {"\N{CROSS MARK}": close_menu})

    @guild.command(aliases=["rinfo"])
    async def roleinfo(self, ctx, role: discord.Role):
        """Shows role info."""
        page = []
        embed = discord.Embed(color=(role.color if role.color else await ctx.embed_colour()))
        embed.set_author(
            name=f"Role info for role ◈ {role.name}",
            icon_url=self.bot.user.avatar_url
        )
        embed.set_thumbnail(url=role.guild.icon_url)

        perms = iter(role.permissions)
        perms_we_have = ""
        perms_we_dont = ""
        for x in sorted(perms):
            if "True" in str(x):
                perms_we_have += "+\t{0}\n".format(str(x).split("'")[1])
            else:
                perms_we_dont += "-\t{0}\n".format(str(x).split("'")[1])
        if not perms_we_have:
            perms_we_have = "+\tNothing"
        if not perms_we_dont:
            perms_we_dont = "-\tNothing"

        # Add after discord.py 1.6.0
        # if role.managed:
        #     if role.is_integration():
        #         embed.description = "This role is managed by an integration."
        #     elif role.is_premium_subscriber():
        #         embed.description = "This is the <:Nitro:806680038179078164> nitro booster role."
        #     elif role.is_bot_managed():
        #         embed.description = "This role is related to a bot."

        embed.add_field(
            name="ID", value=role.id, inline=True,
        )
        embed.add_field(
            name="Color", value=role.color, inline=True,
        )
        embed.add_field(
            name="Users", value=len(role.members), inline=True,
        )
        embed.add_field(
            name="Permissions \N{WHITE HEAVY CHECK MARK}", value=perms_we_have, inline=True,
        )
        embed.add_field(
            name="Permissions \N{CROSS MARK}", value=perms_we_dont, inline=True,
        )
        embed.set_footer(text=f"Position in role list: {int(role.position) + 1} ◈ Created")
        embed.timestamp = role.created_at

        page.append(embed)

        await menu(ctx, page, {"\N{CROSS MARK}": close_menu})

    @guild.group()
    async def list(self, ctx):
        """List out different things."""

    @list.command()
    async def channels(self, ctx):
        """
        List the channels of the current server
        """
        channels = ctx.guild.channels

        temp = dict()
        channels = sorted(channels, key=lambda c: c.position)
        for c in channels[:]:
            if isinstance(c, discord.CategoryChannel):
                channels.pop(channels.index(c))
                temp[c] = list()
        for c in channels[:]:
            if c.category:
                channels.pop(channels.index(c))
                temp[c.category].append(c)
        category_channels = sorted(
            [(cat, sorted(chans, key=lambda c: c.position)) for cat, chans in temp.items()],
            key=lambda t: t[0].position,
        )

        no_category_list = []
        for n in channels:
            no_category_list.append(n.name)

        channels_desc = "\n".join(no_category_list)

        thing = []
        for t in category_channels:
            newlinelist = ""
            for f in t[1]:
                newlinelist += f"\n{f.name} ◈ **{f.type}** ◈ {f.id}"
            thing.append(f"{t[0].name} ◈ {t[0].id}\n" + newlinelist)
        categories_formed = "\a\a\a".join(thing)
        if channels_desc:
            final_string = f"{channels_desc}\t{categories_formed}"
        else:
            final_string = categories_formed

        embed_list = []

        base_embed = discord.Embed(color=await ctx.embed_colour())
        base_embed.set_author(
            name=f"{ctx.guild.name} has {len(ctx.guild.channels)} channel{'s' if len(ctx.guild.channels) > 1 else ''}",
            icon_url=self.bot.user.avatar_url
        )
        base_embed.set_thumbnail(url=ctx.guild.icon_url)

        i = 1
        pages = list(pagify(final_string, delims=["\a\a\a"], page_length=1000))
        for page in pages:
            embed = base_embed.copy()
            if i == 1:
                if channels_desc:
                    page = page.split("\t")
                    embed.description = page[0]
                    page = page[1]
            if page.startswith("\a\a\a"):
                page = page[3:]
            entries = page.split("\a\a\a")
            for c in entries:
                if "\n\n" in c:
                    data = c.split("\n\n")
                    embed.add_field(name=data[0], value=data[1], inline=False)
                else:
                    embed.description = c
            embed.set_footer(text=f"Page {i}/{len(pages)}")
            embed_list.append(embed)
            i += 1

        await menu(ctx, embed_list, DEFAULT_CONTROLS if len(embed_list) > 1 else {"\N{CROSS MARK}": close_menu})

    @list.command()
    async def roles(self, ctx):
        """Displays the server's roles."""
        form = "`{rpos:0{zpadding}}` ◈ `{rid}` ◈ `{rcolor}` ◈ {rment}"
        max_zpadding = max([len(str(r.position)) for r in ctx.guild.roles])
        rolelist = [
            form.format(rpos=r.position, zpadding=max_zpadding, rid=r.id, rment=r.mention, rcolor=r.color)
            for r in ctx.guild.roles
        ]

        rolelist = sorted(rolelist, reverse=True)
        rolelist = "\n".join(rolelist)
        embed_list = []
        pages = list(pagify(rolelist, shorten_by=1200))
        i = 1
        for page in pages:
            if page.startswith("\n"):
                page = page[1:]
            embed = discord.Embed(
                description=f"**Total roles:** {len(ctx.guild.roles)}\n\n{page}", colour=await ctx.embed_colour(),
            )
            embed.set_footer(text=f"Page {i}/{len(pages)}")
            embed_list.append(embed)
            i +=1

        await menu(ctx, embed_list, DEFAULT_CONTROLS if len(embed_list) > 1 else {"\N{CROSS MARK}": close_menu})
            
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user in self.interaction:
            channel = reaction.message.channel
            if isinstance(channel, discord.DMChannel):
                await self.stop_interaction(user)

    async def stop_interaction(self, user):
        self.interaction.remove(user)
        await user.send(("Session closed"))

    def __unload(self):
        self.cog_unload()