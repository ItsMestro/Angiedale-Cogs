import asyncio
import logging
import re
import contextlib
from datetime import datetime, timedelta
from copy import copy
from re import search
from string import Formatter
from typing import Dict, List, Literal, Callable, Optional, Set, Union
from random import choice as rndchoice
from collections import defaultdict

import discord
from redbot.core import checks, bank, commands, Config
from redbot.core.utils import AsyncIter
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from redbot.core.utils.mod import slow_deletion, mass_purge
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.chat_formatting import box, humanize_number, pagify
from redbot.core.bot import Red
from .alias_entry import AliasEntry, AliasCache, ArgParseError
from .checks import check_self_permissions
from .converters import PositiveInt, RawMessageIds, positive_int

log = logging.getLogger("red.cogs.alias")
log = logging.getLogger("red.cleanup")
log = logging.getLogger("red.aikaterna.rndstatus")


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
        self.last_change = None
        self.config = Config.get_conf(self, 1387003, cog_name="ManagementAlias")
        self.config.register_global(entries=[], handled_string_creator=False)
        self.config.register_guild(entries=[])
        self._aliases: AliasCache = AliasCache(config=self.config, cache_enabled=True)
        self._ready_event = asyncio.Event()
        self.rndstatusconfig = Config.get_conf(self, 1387010, cog_name="Managementrndstatus", force_registration=True)

        self.presence_task = asyncio.create_task(self.maybe_update_presence())

        default_global = {
            "botstats": False,
            "delay": 300,
            "statuses": ["her Turn()", "Tomb Raider II", "Transistor", "NEO Scavenger", "Python", "with your heart.",],
            "streamer": "rndstatusstreamer",
            "type": 0,
            "status": 0,
        }
        self.rndstatusconfig.register_global(**default_global)

    def cog_unload(self):
        self.presence_task.cancel()

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
            _("Are you sure you want to delete {number} messages? (y/n)").format(
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
            await ctx.send(_("Cancelled."))
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
                log1.exception("Failed to load alias cog", exc_info=exc)
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

    @commands.group()
    async def alias(self, ctx: commands.Context):
        """Manage command aliases."""
        pass

    @alias.group(name="global")
    async def global_(self, ctx: commands.Context):
        """Manage global aliases."""
        pass

    @checks.mod_or_permissions(manage_guild=True)
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

    @alias.command(name="help")
    async def _help_alias(self, ctx: commands.Context, alias_name: str):
        """Try to execute help for the base command of the alias."""
        alias = await self._aliases.get_alias(ctx.guild, alias_name=alias_name)
        if alias:
            await self.bot.send_help_for(ctx, alias.command)
        else:
            await ctx.send(("No such alias exists."))

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

    @checks.mod_or_permissions(manage_guild=True)
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
    async def _list_alias(self, ctx: commands.Context):
        """List the available aliases on this server."""
        guild_aliases = await self._aliases.get_guild_aliases(ctx.guild)
        if not guild_aliases:
            return await ctx.send(("There are no aliases on this server."))
        await self.paginate_alias_list(ctx, guild_aliases)

    @global_.command(name="list")
    @checks.bot_has_permissions(add_reactions=True)
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
            return await ctx.send(_("Message not found."))

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
            return await ctx.send(_("Message not found."))

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
                _("Could not find a message with the ID of {id}.".format(id=one))
            )
        try:
            mtwo = await channel.fetch_message(two)
        except discord.errors.NotFound:
            return await ctx.send(
                _("Could not find a message with the ID of {id}.".format(id=two))
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

    @commands.group(autohelp=True)
    @commands.guild_only()
    @checks.is_owner()
    async def rndstatus(self, ctx):
        """Rndstatus group commands."""
        pass

    @rndstatus.command(name="set")
    async def _set(self, ctx, *statuses: str):
        """Sets Red's random statuses.
        Accepts multiple statuses.
        Must be enclosed in double quotes in case of multiple words.
        Example:
        [p]rndstatus set \"Tomb Raider II\" \"Transistor\" \"with your heart.\"
        Shows current list if empty."""
        saved_status = await self.rndstatusconfig.statuses()
        if statuses == () or "" in statuses:
            msg = (
                f"Current statuses: {(' | ').join(saved_status)}\n"
                f"To set new statuses, use the instructions in `{ctx.prefix}help rndstatus set`."
            )
            return await ctx.send(msg)
        await self.rndstatusconfig.statuses.set(list(statuses))
        await self.presence_updater()
        await ctx.send("Done. Redo this command with no parameters to see the current list of statuses.")

    @rndstatus.command(name="streamer")
    async def _streamer(self, ctx: commands.Context, *, streamer=None):
        """Set the streamer name needed for streaming statuses."""
        saved_streamer = await self.rndstatusconfig.streamer()
        if streamer is None:
            return await ctx.send(f"Current Streamer: {saved_streamer}")
        await self.rndstatusconfig.streamer.set(streamer)
        await ctx.send("Done. Redo this command with no parameters to see the current streamer.")

    @rndstatus.command()
    async def botstats(self, ctx, *statuses: str):
        """Toggle for a bot stats status instead of random messages."""
        botstats = await self.rndstatusconfig.botstats()
        await self.rndstatusconfig.botstats.set(not botstats)
        await ctx.send(f"Botstats toggle: {not botstats}.")
        await self.presence_updater()

    @rndstatus.command()
    async def delay(self, ctx, seconds: int):
        """Sets interval of random status switch.
        Must be 20 or superior."""
        if seconds < 20:
            seconds = 20
        await self.rndstatusconfig.delay.set(seconds)
        await ctx.send(f"Interval set to {seconds} seconds.")

    @rndstatus.command(name="type")
    async def _rndstatus_type(self, ctx, status_type: int):
        """Define the rndstatus game type.

        Type list:
        0 = Playing
        1 = Streaming
        2 = Listening
        3 = Watching"""
        if 0 <= status_type <= 3:
            rnd_type = {0: "playing", 1: "streaming", 2: "listening", 3: "watching"}
            await self.rndstatusconfig.type.set(status_type)
            await self.presence_updater()
            await ctx.send(f"Rndstatus activity type set to {rnd_type[status_type]}.")
        else:
            await ctx.send(
                f"Status activity type must be between 0 and 3. "
                f"See `{ctx.prefix}help rndstatus type` for more information."
            )

    @rndstatus.command()
    async def status(self, ctx, status: int):
        """Define the rndstatus presence status.

        Status list:
        0 = Online
        1 = Idle
        2 = DND
        3 = Invisible"""
        if 0 <= status <= 3:
            rnd_status = {0: "online", 1: "idle", 2: "DND", 3: "invisible"}
            await self.rndstatusconfig.status.set(status)
            await self.presence_updater()
            await ctx.send(f"Rndstatus presence status set to {rnd_status[status]}.")
        else:
            await ctx.send(
                f"Status presence type must be between 0 and 3. "
                f"See `{ctx.prefix}help rndstatus status` for more information."
            )

    async def maybe_update_presence(self):
        await self.bot.wait_until_red_ready()
        delay = await self.rndstatusconfig.delay()
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
        cog_settings = await self.rndstatusconfig.all()
        guilds = self.bot.guilds
        guild = next(g for g in guilds if not g.unavailable)
        try:
            current_game = str(guild.me.activity.name)
        except AttributeError:
            current_game = None
        statuses = cog_settings["statuses"]
        botstats = cog_settings["botstats"]
        streamer = cog_settings["streamer"]
        _type = cog_settings["type"]
        _status = cog_settings["status"]

        url = f"https://www.twitch.tv/{streamer}"
        prefix = await self.bot.get_valid_prefixes()

        if _status == 0:
            status = discord.Status.online
        elif _status == 1:
            status = discord.Status.idle
        elif _status == 2:
            status = discord.Status.dnd
        elif _status == 3:
            status = discord.Status.offline

        if botstats:
            me = self.bot.user
            clean_prefix = pattern.sub(f"@{me.name}", prefix[0])
            total_users = len(self.bot.users)
            servers = str(len(self.bot.guilds))
            botstatus = f"{clean_prefix}help | {total_users} users | {servers} servers"
            if (current_game != str(botstatus)) or current_game is None:
                if _type == 1:
                    await self.bot.change_presence(activity=discord.Streaming(name=botstatus, url=url))
                else:
                    await self.bot.change_presence(activity=discord.Activity(name=botstatus, type=_type), status=status)
        else:
            if len(statuses) > 0:
                new_status = self.random_status(guild, statuses)
                if (current_game != new_status) or (current_game is None) or (len(statuses) == 1):
                    if _type == 1:
                        await self.bot.change_presence(activity=discord.Streaming(name=new_status, url=url))
                    else:
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