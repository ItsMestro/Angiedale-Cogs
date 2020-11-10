import asyncio
import logging
from copy import copy
from re import search
from string import Formatter
from typing import Dict, List, Literal

import discord
from redbot.core.utils.chat_formatting import box, humanize_number, pagify

from redbot.core import checks, bank, commands, Config

from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

from redbot.core.bot import Red
from .alias_entry import AliasEntry, AliasCache, ArgParseError

log = logging.getLogger("red.cogs.alias")


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
        self.config = Config.get_conf(self, 1387003)
        self.config.register_global(entries=[], handled_string_creator=False)
        self.config.register_guild(entries=[])
        self._aliases: AliasCache = AliasCache(config=self.config, cache_enabled=True)
        self._ready_event = asyncio.Event()

    # SECTION commands

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
                _("Aliases:\n")
                + page
                + _("\n\nPage {page}/{total}").format(page=count, total=len(temp))
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
                _(
                    "You attempted to create a new alias"
                    " with the name {name} but that"
                    " name is already a command on this bot."
                ).format(name=alias_name)
            )
            return

        alias = await self._aliases.get_alias(ctx.guild, alias_name)
        if alias:
            await ctx.send(
                _(
                    "You attempted to create a new alias"
                    " with the name {name} but that"
                    " alias already exists."
                ).format(name=alias_name)
            )
            return

        is_valid_name = self.is_valid_alias_name(alias_name)
        if not is_valid_name:
            await ctx.send(
                _(
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
                _("You attempted to create a new alias for a command that doesn't exist.")
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
            _("A new alias with the trigger `{name}` has been created.").format(name=alias_name)
        )

    @checks.is_owner()
    @global_.command(name="add")
    async def _add_global_alias(self, ctx: commands.Context, alias_name: str, *, command):
        """Add a global alias for a command."""
        # region Alias Add Validity Checking
        is_command = self.is_command(alias_name)
        if is_command:
            await ctx.send(
                _(
                    "You attempted to create a new global alias"
                    " with the name {name} but that"
                    " name is already a command on this bot."
                ).format(name=alias_name)
            )
            return

        alias = await self._aliases.get_alias(None, alias_name)
        if alias:
            await ctx.send(
                _(
                    "You attempted to create a new global alias"
                    " with the name {name} but that"
                    " alias already exists."
                ).format(name=alias_name)
            )
            return

        is_valid_name = self.is_valid_alias_name(alias_name)
        if not is_valid_name:
            await ctx.send(
                _(
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
                _("You attempted to create a new alias for a command that doesn't exist.")
            )
            return
        # endregion

        try:
            await self._aliases.add_alias(ctx, alias_name, command, global_=True)
        except ArgParseError as e:
            return await ctx.send(" ".join(e.args))

        await ctx.send(
            _("A new global alias with the trigger `{name}` has been created.").format(
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
            await ctx.send(_("No such alias exists."))

    @alias.command(name="show")
    async def _show_alias(self, ctx: commands.Context, alias_name: str):
        """Show what command the alias executes."""
        alias = await self._aliases.get_alias(ctx.guild, alias_name)

        if alias:
            await ctx.send(
                _("The `{alias_name}` alias will execute the command `{command}`").format(
                    alias_name=alias_name, command=alias.command
                )
            )
        else:
            await ctx.send(_("There is no alias with the name `{name}`").format(name=alias_name))

    @checks.mod_or_permissions(manage_guild=True)
    @alias.command(name="delete", aliases=["del", "remove"])
    @commands.guild_only()
    async def _del_alias(self, ctx: commands.Context, alias_name: str):
        """Delete an existing alias on this server."""
        if not await self._aliases.get_guild_aliases(ctx.guild):
            await ctx.send(_("There are no aliases on this server."))
            return

        if await self._aliases.delete_alias(ctx, alias_name):
            await ctx.send(
                _("Alias with the name `{name}` was successfully deleted.").format(name=alias_name)
            )
        else:
            await ctx.send(_("Alias with name `{name}` was not found.").format(name=alias_name))

    @checks.is_owner()
    @global_.command(name="delete", aliases=["del", "remove"])
    async def _del_global_alias(self, ctx: commands.Context, alias_name: str):
        """Delete an existing global alias."""
        if not await self._aliases.get_global_aliases():
            await ctx.send(_("There are no global aliases on this bot."))
            return

        if await self._aliases.delete_alias(ctx, alias_name, global_=True):
            await ctx.send(
                _("Alias with the name `{name}` was successfully deleted.").format(name=alias_name)
            )
        else:
            await ctx.send(_("Alias with name `{name}` was not found.").format(name=alias_name))

    @alias.command(name="list")
    @commands.guild_only()
    @checks.bot_has_permissions(add_reactions=True)
    async def _list_alias(self, ctx: commands.Context):
        """List the available aliases on this server."""
        guild_aliases = await self._aliases.get_guild_aliases(ctx.guild)
        if not guild_aliases:
            return await ctx.send(_("There are no aliases on this server."))
        await self.paginate_alias_list(ctx, guild_aliases)

    @global_.command(name="list")
    @checks.bot_has_permissions(add_reactions=True)
    async def _list_global_alias(self, ctx: commands.Context):
        """List the available global aliases on this bot."""
        global_aliases = await self._aliases.get_global_aliases()
        if not global_aliases:
            return await ctx.send(_("There are no global aliases."))
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
        bank_scope = _("Global") if cur_setting else _("Server")
        currency_name = group_data["currency"]
        default_balance = group_data["default_balance"]
        max_balance = group_data["max_balance"]

        settings = _(
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

        word = _("per-server") if cur_setting else _("global")
        if confirm is False:
            await ctx.send(
                _(
                    "This will toggle the bank to be {banktype}, deleting all accounts "
                    "in the process! If you're sure, type `{command}`"
                ).format(banktype=word, command=f"{ctx.clean_prefix}bankset toggleglobal yes")
            )
        else:
            await bank.set_global(not cur_setting)
            await ctx.send(_("The bank is now {banktype}.").format(banktype=word))

    @is_owner_if_bank_global()
    @checks.guildowner_or_permissions(administrator=True)
    @bankset.command(name="bankname")
    async def bankset_bankname(self, ctx: commands.Context, *, name: str):
        """Set the bank's name."""
        await bank.set_bank_name(name, ctx.guild)
        await ctx.send(_("Bank name has been set to: {name}").format(name=name))

    @is_owner_if_bank_global()
    @checks.guildowner_or_permissions(administrator=True)
    @bankset.command(name="creditsname")
    async def bankset_creditsname(self, ctx: commands.Context, *, name: str):
        """Set the name for the bank's currency."""
        await bank.set_currency_name(name, ctx.guild)
        await ctx.send(_("Currency name has been set to: {name}").format(name=name))

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
                _("Amount must be greater than zero and less than {max}.").format(
                    max=humanize_number(bank._MAX_BALANCE)
                )
            )
        await ctx.send(
            _("Maximum balance has been set to: {amount}").format(amount=humanize_number(amount))
        )