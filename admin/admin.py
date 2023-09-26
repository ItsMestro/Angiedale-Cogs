import asyncio
import logging
import re
from datetime import datetime
from typing import List, Literal, Union

import discord
from redbot.core import Config, checks, commands, modlog
from redbot.core.bot import Red
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import inline
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate

from .converters import (
    RRoleType,
    RRoleTypeConverter,
    SelfRole,
    TrueEmojiConverter,
    rroletype_solver,
)
from .modlog import ModLog
from .modset import ModSettings
from .mutes import Mutes
from .warnings import Warnings

log = logging.getLogger("red.angiedale.admin")


GENERIC_FORBIDDEN = (
    "I attempted to do something that Discord denied me permissions for."
    " Your command failed to successfully complete."
)

HIERARCHY_ISSUE_ADD = (
    "I can not give {role.name} to {member.display_name}"
    " because that role is higher than or equal to my highest role"
    " in the Discord hierarchy."
)

HIERARCHY_ISSUE_REMOVE = (
    "I can not remove {role.name} from {member.display_name}"
    " because that role is higher than or equal to my highest role"
    " in the Discord hierarchy."
)

ROLE_HIERARCHY_ISSUE = (
    "I can not edit {role.name}"
    " because that role is higher than my or equal to highest role"
    " in the Discord hierarchy."
)

USER_HIERARCHY_ISSUE_ADD = (
    "I can not let you give {role.name} to {member.display_name}"
    " because that role is higher than or equal to your highest role"
    " in the Discord hierarchy."
)

USER_HIERARCHY_ISSUE_REMOVE = (
    "I can not let you remove {role.name} from {member.display_name}"
    " because that role is higher than or equal to your highest role"
    " in the Discord hierarchy."
)

ROLE_USER_HIERARCHY_ISSUE = (
    "I can not let you edit {role.name}"
    " because that role is higher than or equal to your highest role"
    " in the Discord hierarchy."
)

NEED_MANAGE_ROLES = 'I need the "Manage Roles" permission to do that.'

NEED_ADD_REACTIONS = (
    'I need the "Add Reactions" permission in the channel for that message to do that.'
)

NEED_MANAGE_MESSAGES = (
    'I need the "Manage Messages" permission in the channel for that message to do that.'
)

USER_HIERARCHY_ISSUE = (
    "I can not let you give {role.name} to users"
    " because that role is higher than or equal to your highest role"
    " in the Discord hierarchy."
)

ROLE_HIERARCHY_ISSUE_ADD = (
    "I can not give users {role.name}"
    " because that role is higher than my or equal to highest role"
    " in the Discord hierarchy."
)

EMOJI_RE = re.compile(r"<a?:[a-zA-Z0-9\_]+:([0-9]+)>")


def is_support_guild():
    async def pred(ctx: commands.Context):
        if not ctx.guild:
            return False
        if ctx.guild.id == 128856147162562560:
            return True
        else:
            for guild in ctx.bot.guilds:
                if ctx.author.id == guild.owner_id:
                    return True
            return False

    return commands.check(pred)


class Admin(ModLog, ModSettings, Mutes, Warnings, commands.Cog):
    """A collection of server administration utilities."""

    default_guild_warnings = {
        "actions": [],
        "reasons": {},
        "allow_custom_reasons": False,
        "toggle_dm": True,
        "show_mod": False,
        "warn_channel": None,
        "toggle_channel": False,
    }

    default_member_warnings = {
        "total_points": 0,
        "status": "",
        "warnings": {}
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.rrolecache = set()

        self.reportsconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Reports"
        )

        self.modconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Mod"
        )

        self.mutesconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Mutes"
        )

        self.leaverconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Leaver"
        )
        leaverdefault_guild = {"channel": None}
        self.leaverconfig.register_guild(**leaverdefault_guild)

        self.warnconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Warnings"
        )
        self.cleanupconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Cleanup"
        )
        self.rroleconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="ReactionRoles"
        )
        self.cleanupconfig.register_guild(notify=True)
        self.warnconfig.register_guild(**self.default_guild_warnings)
        self.warnconfig.register_member(**self.default_member_warnings)
        self.registration_task = self.bot.loop.create_task(self.register_warningtype())

        self.adminconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="OwnerAdmin"
        )
        self.adminconfig.register_guild(
            announce_channel=None,  # Integer ID
            selfroles=[],  # List of integer ID's
        )

        self.filterconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Filter"
        )
        default_guild_settings = {
            "filter": [],
            "filterban_count": 0,
            "filterban_time": 0,
            "filter_names": False,
            "filter_default_name": "Florida Man",
        }
        default_member_settings = {"filter_count": 0, "next_reset_time": 0}
        default_channel_settings = {"filter": []}
        self.filterconfig.register_guild(**default_guild_settings)
        self.filterconfig.register_member(**default_member_settings)
        self.filterconfig.register_channel(**default_channel_settings)

        default_rrole_settings = {"rroles": {}}
        self.rroleconfig.init_custom("RRole", 2)
        self.rroleconfig.register_custom("RRole", **default_rrole_settings)

        self.initialize_task: asyncio.Task = self.bot.loop.create_task(self.initialize())

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        if requester != "discord_deleted_user":
            return

        all_members = await self.filterconfig.all_members()

        async for guild_id, guild_data in AsyncIter(all_members.items(), steps=100):
            if user_id in guild_data:
                await self.filterconfig.member_from_ids(guild_id, user_id).clear()

        all_members = await self.warnconfig.all_members()

        c = 0

        for guild_id, guild_data in all_members.items():
            c += 1
            if not c % 100:
                await asyncio.sleep(0)

            if user_id in guild_data:
                await self.warnconfig.member_from_ids(guild_id, user_id).clear()

            for remaining_user, user_warns in guild_data.items():
                c += 1
                if not c % 100:
                    await asyncio.sleep(0)

                for warn_id, warning in user_warns.get("warnings", {}).items():
                    c += 1
                    if not c % 100:
                        await asyncio.sleep(0)

                    if warning.get("mod", 0) == user_id:
                        grp = self.warnconfig.member_from_ids(guild_id, remaining_user)
                        await grp.set_raw("warnings", warn_id, "mod", value=0xDE1)

    async def initialize(self):
        await self.bot.wait_until_ready()
        await self._update_cache()

    # We're not utilising modlog yet - no need to register a casetype
    @staticmethod
    async def register_warningtype():
        casetypes_to_register = [
            {
                "name": "warning",
                "default_setting": True,
                "image": "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}",
                "case_str": "Warning",
            },
            {
                "name": "unwarned",
                "default_setting": True,
                "image": "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}",
                "case_str": "Unwarned",
            },
        ]
        try:
            await modlog.register_casetypes(casetypes_to_register)
        except RuntimeError:
            pass

    @staticmethod
    def pass_hierarchy_check(ctx: commands.Context, role: discord.Role) -> bool:
        """
        Determines if the bot has a higher role than the given one.
        :param ctx:
        :param role: Role object.
        :return:
        """
        return ctx.guild.me.top_role > role

    @staticmethod
    def pass_user_hierarchy_check(ctx: commands.Context, role: discord.Role) -> bool:
        """
        Determines if a user is allowed to add/remove/edit the given role.
        :param ctx:
        :param role:
        :return:
        """
        return ctx.author.top_role > role or ctx.author == ctx.guild.owner

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def filterset(self, ctx: commands.Context):
        """Change filter settings."""
        pass

    @filterset.command(name="defaultname")
    async def filter_default_name(self, ctx: commands.Context, name: str):
        """Set the nickname for users with a filtered name.

        Note that this has no effect if filtering names is disabled
        (to toggle, run `[p]filter names`).

        The default name used is *John Doe*.

        Example:
            - `[p]filterset defaultname Missingno`

        **Arguments:**

        - `<name>` The new nickname to assign.
        """
        guild = ctx.guild
        await self.filterconfig.guild(guild).filter_default_name.set(name)
        await ctx.send(("The name to use on filtered names has been set."))

    @filterset.command(name="ban")
    async def filter_ban(self, ctx: commands.Context, count: int, timeframe: int):
        """Set the filter's autoban conditions.

        Users will be banned if they send `<count>` filtered words in
        `<timeframe>` seconds.

        Set both to zero to disable autoban.

        Examples:
            - `[p]filterset ban 5 5` - Ban users who say 5 filtered words in 5 seconds.
            - `[p]filterset ban 2 20` - Ban users who say 2 filtered words in 20 seconds.

        **Arguments:**

        - `<count>` The amount of filtered words required to trigger a ban.
        - `<timeframe>` The period of time in which too many filtered words will trigger a ban.
        """
        if (count <= 0) != (timeframe <= 0):
            await ctx.send(
                (
                    "Count and timeframe either both need to be 0 "
                    "or both need to be greater than 0!"
                )
            )
            return
        elif count == 0 and timeframe == 0:
            async with self.filterconfig.guild(ctx.guild).all() as guild_data:
                guild_data["filterban_count"] = 0
                guild_data["filterban_time"] = 0
            await ctx.send(("Autoban disabled."))
        else:
            async with self.filterconfig.guild(ctx.guild).all() as guild_data:
                guild_data["filterban_count"] = count
                guild_data["filterban_time"] = timeframe
            await ctx.send(("Count and time have been set."))

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    async def addrole(
        self, ctx: commands.Context, rolename: discord.Role, *, user: discord.Member = None
    ):
        """
        Add a role to a user.

        Use double quotes if the role contains spaces.
        If user is left blank it defaults to the author of the command.
        """
        if user is None:
            user = ctx.author
        await self._addrole(ctx, user, rolename)

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    async def removerole(
        self, ctx: commands.Context, rolename: discord.Role, *, user: discord.Member = None
    ):
        """
        Remove a role from a user.

        Use double quotes if the role contains spaces.
        If user is left blank it defaults to the author of the command.
        """
        if user is None:
            user = ctx.author
        await self._removerole(ctx, user, rolename)

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    async def editrole(self, ctx: commands.Context):
        """Edit roles."""
        pass

    @editrole.command(name="colour", aliases=["color"])
    async def editrole_colour(
        self, ctx: commands.Context, role: discord.Role, value: discord.Colour
    ):
        """
        Edit a role's colour.

        Use double quotes if the role contains spaces.
        Colour must be in hexadecimal format.
        [Online colour picker](http://www.w3schools.com/colors/colors_picker.asp)

        Examples:
            `[p]editrole colour "The Transistor" #ff0000`
            `[p]editrole colour Test #ff9900`
        """
        author = ctx.author
        reason = "{}({}) changed the colour of role '{}'".format(author.name, author.id, role.name)

        if not self.pass_user_hierarchy_check(ctx, role):
            await ctx.send((ROLE_USER_HIERARCHY_ISSUE).format(role=role))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((ROLE_HIERARCHY_ISSUE).format(role=role))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send((NEED_MANAGE_ROLES))
            return
        try:
            await role.edit(reason=reason, color=value)
        except discord.Forbidden:
            await ctx.send((GENERIC_FORBIDDEN))
        else:
            log.info(reason)
            await ctx.send(("Done."))

    @editrole.command(name="name")
    async def edit_role_name(self, ctx: commands.Context, role: discord.Role, name: str):
        """
        Edit a role's name.

        Use double quotes if the role or the name contain spaces.

        Example:
            `[p]editrole name \"The Transistor\" Test`
        """
        author = ctx.message.author
        old_name = role.name
        reason = "{}({}) changed the name of role '{}' to '{}'".format(
            author.name, author.id, old_name, name
        )

        if not self.pass_user_hierarchy_check(ctx, role):
            await ctx.send((ROLE_USER_HIERARCHY_ISSUE).format(role=role))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((ROLE_HIERARCHY_ISSUE).format(role=role))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send((NEED_MANAGE_ROLES))
            return
        try:
            await role.edit(reason=reason, name=name)
        except discord.Forbidden:
            await ctx.send((GENERIC_FORBIDDEN))
        else:
            log.info(reason)
            await ctx.send(("Done."))

    async def _addrole(
        self, ctx: commands.Context, member: discord.Member, role: discord.Role, *, check_user=True
    ):
        if role in member.roles:
            await ctx.send(
                ("{member.display_name} already has the role {role.name}.").format(
                    role=role, member=member
                )
            )
            return
        if check_user and not self.pass_user_hierarchy_check(ctx, role):
            await ctx.send((USER_HIERARCHY_ISSUE_ADD).format(role=role, member=member))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((HIERARCHY_ISSUE_ADD).format(role=role, member=member))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send((NEED_MANAGE_ROLES))
            return
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            await ctx.send((GENERIC_FORBIDDEN))
        else:
            await ctx.send(
                ("I successfully added {role.name} to {member.display_name}").format(
                    role=role, member=member
                )
            )

    async def _removerole(
        self, ctx: commands.Context, member: discord.Member, role: discord.Role, *, check_user=True
    ):
        if role not in member.roles:
            await ctx.send(
                ("{member.display_name} does not have the role {role.name}.").format(
                    role=role, member=member
                )
            )
            return
        if check_user and not self.pass_user_hierarchy_check(ctx, role):
            await ctx.send((USER_HIERARCHY_ISSUE_REMOVE).format(role=role, member=member))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((HIERARCHY_ISSUE_REMOVE).format(role=role, member=member))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send((NEED_MANAGE_ROLES))
            return
        try:
            await member.remove_roles(role)
        except discord.Forbidden:
            await ctx.send((GENERIC_FORBIDDEN))
        else:
            await ctx.send(
                ("I successfully removed {role.name} from {member.display_name}").format(
                    role=role, member=member
                )
            )

    @commands.group()
    @commands.guild_only()
    @checks.guildowner_or_permissions(administrator=True)
    async def announceset(self, ctx):
        """Set a channel for bot update/maintenance announcements."""
        pass

    @announceset.command(name="channel")
    async def announceset_channel(self, ctx, *, channel: discord.TextChannel = None):
        """
        Change the channel where the bot will send announcements.

        If channel is left blank it defaults to the current channel.
        """
        if channel is None:
            channel = ctx.channel
        await self.adminconfig.guild(ctx.guild).announce_channel.set(channel.id)
        await ctx.send(
            ("The announcement channel has been set to {channel.mention}").format(channel=channel)
        )

    @announceset.command(name="clearchannel")
    async def announceset_clear_channel(self, ctx):
        """Unsets the channel for announcements."""
        await self.adminconfig.guild(ctx.guild).announce_channel.clear()
        await ctx.tick()

    @commands.group()
    @checks.admin_or_permissions(manage_roles=True)
    async def selfroleset(self, ctx: commands.Context):
        """Manage selfroles."""
        pass

    @selfroleset.command(name="add")
    async def selfroleset_add(self, ctx: commands.Context, *roles: discord.Role):
        """
        Add a role, or a selection of roles, to the list of available selfroles.

        NOTE: The role is case sensitive!
        """
        current_selfroles = await self.config.guild(ctx.guild).selfroles()
        for role in roles:
            if not self.pass_user_hierarchy_check(ctx, role):
                await ctx.send(
                    (
                        "I cannot let you add {role.name} as a selfrole because that role is"
                        " higher than or equal to your highest role in the Discord hierarchy."
                    ).format(role=role)
                )
                return
            if role.id not in current_selfroles:
                current_selfroles.append(role.id)
            else:
                await ctx.send(('The role "{role.name}" is already a selfrole.').format(role=role))
                return

        await self.config.guild(ctx.guild).selfroles.set(current_selfroles)
        if (count := len(roles)) > 1:
            message = ("Added {count} selfroles.").format(count=count)
        else:
            message = "Added 1 selfrole."

        await ctx.send(message)

    @selfroleset.command(name="remove")
    async def selfroleset_remove(self, ctx: commands.Context, *roles: SelfRole):
        """
        Remove a role, or a selection of roles, from the list of available selfroles.

        NOTE: The role is case sensitive!
        """
        current_selfroles = await self.config.guild(ctx.guild).selfroles()
        for role in roles:
            if not self.pass_user_hierarchy_check(ctx, role):
                await ctx.send(
                    (
                        "I cannot let you remove {role.name} from being a selfrole because that role is higher than or equal to your highest role in the Discord hierarchy."
                    ).format(role=role)
                )
                return
            current_selfroles.remove(role.id)

        await self.config.guild(ctx.guild).selfroles.set(current_selfroles)

        if (count := len(roles)) > 1:
            message = ("Removed {count} selfroles.").format(count=count)
        else:
            message = "Removed 1 selfrole."

        await ctx.send(message)

    @selfroleset.command(name="clear")
    async def selfroleset_clear(self, ctx: commands.Context):
        """Clear the list of available selfroles for this server."""
        current_selfroles = await self.config.guild(ctx.guild).selfroles()

        if not current_selfroles:
            return await ctx.send(("There are currently no selfroles."))

        await ctx.send(
            ("Are you sure you want to clear this server's selfrole list?") + " (yes/no)"
        )
        try:
            pred = MessagePredicate.yes_or_no(ctx, user=ctx.author)
            await ctx.bot.wait_for("message", check=pred, timeout=60)
        except asyncio.TimeoutError:
            await ctx.send(("You took too long to respond."))
            return
        if pred.result:
            for role in current_selfroles:
                role = ctx.guild.get_role(role)
                if role is None:
                    continue
                if not self.pass_user_hierarchy_check(ctx, role):
                    await ctx.send(
                        (
                            "I cannot clear the selfroles because the selfrole '{role.name}' is higher than or equal to your highest role in the Discord hierarchy."
                        ).format(role=role)
                    )
                    return
            await self.config.guild(ctx.guild).selfroles.clear()
            await ctx.send(("Selfrole list cleared."))
        else:
            await ctx.send(("No changes have been made."))

    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.group(name="reportset")
    async def reportset(self, ctx: commands.Context):
        """Manage Reports."""
        pass

    @reportset.command(name="output")
    async def reportset_output(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where reports will be sent."""
        await self.reportsconfig.guild(ctx.guild).output_channel.set(channel.id)
        await ctx.send(("The report channel has been set."))

    @reportset.command(name="toggle", aliases=["toggleactive"])
    async def reportset_toggle(self, ctx: commands.Context):
        """Enable or disable reporting for this server."""
        active = await self.reportsconfig.guild(ctx.guild).active()
        active = not active
        await self.reportsconfig.guild(ctx.guild).active.set(active)
        if active:
            await ctx.send(("Reporting is now enabled"))
        else:
            await ctx.send(("Reporting is now disabled."))

    @is_support_guild()
    @commands.command()
    async def setup(self, ctx):
        """Sends invite to support server."""
        if not ctx.guild.id == 128856147162562560:
            await ctx.send(
                "Join this server and run this command again in there.\n\nhttps://discord.gg/xxjdXmR"
            )
        else:
            for guild in self.bot.guilds:
                if ctx.author.id == guild.owner_id:
                    supportrole = ctx.guild.get_role(815025432507318304)
                    if supportrole in ctx.author.roles:
                        await ctx.author.remove_roles(supportrole)
                        return await ctx.send("Removed your support role.")
                    else:
                        await ctx.author.add_roles(supportrole)
                        return await ctx.send(
                            "You've now been given a support role with access to exclusive channels for information on the bot."
                        )
            return await ctx.send(
                (
                    f"You're not the owner of any servers that I'm in. The support role is only for server owners.\n"
                    f"Feel free to let me join one of your servers with `{ctx.clean_prefix}invite`"
                )
            )

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def leavers(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Sets a channel that logs when users leave.
        Leave blank to stop logging."""
        if channel:
            await self.leaverconfig.guild(ctx.guild).channel.set(channel.id)
            await ctx.maybe_send_embed("Will now log when people leave in " + channel.name)
        else:
            await self.leaverconfig.guild(ctx.guild).channel.set(None)
            await ctx.maybe_send_embed("Will no longer log people that leave")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild

        if await self.bot.cog_disabled_in_guild(self, guild):
            return

        channel = await self.leaverconfig.guild(guild).channel()

        if channel:
            channel = guild.get_channel(channel)
            out = "{} {}".format(member, member.nick if member.nick is not None else "")
            if await self.bot.embed_requested(channel, member):
                embed = discord.Embed(
                    description=out, color=(await self.bot.get_embed_color(channel))
                )
                embed.set_author(name="Member Left")
                embed.set_footer(f"ID: {member.id}")
                embed.timestamp = datetime.utcnow()
                await channel.send(embed=embed)
            else:
                await channel.send(f"{out} left the server.")

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def cleanupset(self, ctx: commands.Context):
        """Manage the settings for the cleanup command."""
        pass

    @commands.guild_only()
    @cleanupset.command(name="notify")
    async def cleanupset_notify(self, ctx: commands.Context):
        """Toggle clean up notification settings.

        When enabled, a message will be sent per cleanup, showing how many messages were deleted.
        This message will be deleted after 5 seconds.
        """
        toggle = await self.cleanupconfig.guild(ctx.guild).notify()
        if toggle:
            await self.cleanupconfig.guild(ctx.guild).notify.set(False)
            await ctx.send(("I will no longer notify of message deletions."))
        else:
            await self.cleanupconfig.guild(ctx.guild).notify.set(True)
            await ctx.send(("I will now notify of message deletions."))

    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    @commands.group()
    async def rrole(self, ctx: commands.Context):
        """Create or manage reaction roles."""

    @rrole.command(aliases=["new", "create"], usage="<type> <role> <emoji> <message> [channel]")
    async def make(
        self,
        ctx: commands.Context,
        rrtype: RRoleTypeConverter,
        role: discord.Role,
        emoji: TrueEmojiConverter,
        message: Union[discord.Message, int],
        channel: discord.TextChannel = None,
    ):
        """Set up a new reaction role.

        A channel is only required if the message is in another channel than where you're typing.

        The type can be one of:
        `Normal` - Gives role on react. Removes when reaction is removed.
        `Once` - Adds a role when first reacted to but can't be removed.
        `Remove` - Opposite to once by getting removed on first reaction and not granted again.
        `Toggle` - Will remove any other toggle roles a user has from the same message and only grant the current one.
        """

        if type(message) is not discord.Message:
            if not channel:
                return await ctx.send(
                    "You need to provide a channel if making reaction roles for messages outside of here."
                )
            message = await channel.fetch_message(message)
            if not message:
                return await ctx.send(
                    "Could not find that channel or message. Are you sure you typed in the right ID or mention?"
                )

        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.send((NEED_MANAGE_ROLES))

        if not ctx.guild.me.permissions_in(message.channel).add_reactions:
            return await ctx.send((NEED_ADD_REACTIONS))

        if not ctx.guild.me.permissions_in(message.channel).manage_messages:
            return await ctx.send((NEED_MANAGE_MESSAGES))

        if not self.pass_user_hierarchy_check(ctx, role):
            return await ctx.send(USER_HIERARCHY_ISSUE.format(role=role))

        if not self.pass_hierarchy_check(ctx, role):
            return await ctx.send(ROLE_HIERARCHY_ISSUE_ADD.format(role=role))

        emoji_id = self.emoji_id(emoji)
        async with self.rroleconfig.custom("RRole", ctx.guild.id, message.id).rroles() as r:
            if len(r) >= 20:
                return await ctx.send(
                    f"There is already 20 reaction roles assigned to that message. Discord limits messages to 20 reactions each. To replace one, use {inline(f'{ctx.clean_prefix}rrole remove')}"
                )
            try:
                old_rrole = r[emoji_id]
            except KeyError:
                old_rrole = None
            if old_rrole:
                old_role: discord.Role = ctx.guild.get_role(old_rrole["role_id"][0])
                if not old_role:
                    r.pop(emoji_id)
                else:
                    can_react = ctx.channel.permissions_for(ctx.me).add_reactions
                    msg: discord.Message = await ctx.send(
                        f"There is already a reaction role set up for {old_role}\nDo you want to override it?",
                        allowed_mentions=discord.AllowedMentions(users=False),
                    )

                    if can_react:
                        start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                        pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                        event = "reaction_add"
                    else:
                        pred = MessagePredicate.yes_or_no(ctx)
                        event = "message"
                    try:
                        await ctx.bot.wait_for(event, check=pred, timeout=30)
                    except asyncio.TimeoutError:
                        await msg.delete()
                        return await ctx.send("Cancelling reaction role creation.")
                    if pred.result:
                        await msg.delete()
                        r.pop(emoji_id)
                        await ctx.send(
                            f"Replacing the reaction role for {emoji} with {role} on <{message.jump_url}>.",
                            allowed_mentions=discord.AllowedMentions(users=False),
                        )
                    else:
                        await msg.delete()
                        return await ctx.send("Cancelling reaction role creation.")
            else:
                await ctx.send(
                    f"Adding reaction role for {emoji} with {role} on <{message.jump_url}>.",
                    allowed_mentions=discord.AllowedMentions(users=False),
                )

            r[emoji_id] = {"role_id": [role.id], "channel_id": message.channel.id, "type": rrtype}

        if str(emoji) in [str(emoji) for emoji in message.reactions]:
            await message.clear_reaction(emoji)
        await message.add_reaction(emoji)

        self._edit_cache(message.id)

    @rrole.command(aliases=["delete", "del"], usage="<message> [channel] [emoji]")
    async def remove(
        self,
        ctx: commands.Context,
        message: Union[discord.Message, int],
        channel: discord.TextChannel = None,
        emoji: TrueEmojiConverter = None,
    ):
        """Remove a reaction role.

        A channel is only required if the message is in another channel than where you're typing.

        If an emoji is provided only the specific emoji is removed.
        """
        if type(message) is not discord.Message:
            if not channel:
                return await ctx.send(
                    "You need to provide a channel if removing reaction roles for messages outside of here."
                )
            message = await channel.fetch_message(message)
            if not message:
                return await ctx.send(
                    "Could not find that channel or message. Are you sure you typed in the right ID or mention?"
                )

        if message.id not in self.rrolecache:
            return await ctx.send("There is no reaction roles set up on the provided message.")

        rroles = await self.rroleconfig.custom("RRole", ctx.guild.id, message.id).rroles.all()

        if emoji:
            emoji_id = self.emoji_id(emoji)
            if emoji_id not in rroles:
                return await ctx.send(
                    "That emoji isn't set up for reaction roles on the provided message."
                )

        can_react = ctx.channel.permissions_for(ctx.me).add_reactions

        if emoji:
            msg: discord.Message = await ctx.send(
                f"This will remove the reaction role with {emoji} from <{message.jump_url}>\nAre you sure?"
            )
        else:
            msg: discord.Message = await ctx.send(
                f"This will remove all reaction roles on <{message.jump_url}>\nAre you sure?"
            )

        if can_react:
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            event = "reaction_add"
        else:
            pred = MessagePredicate.yes_or_no(ctx)
            event = "message"
        try:
            await ctx.bot.wait_for(event, check=pred, timeout=30)
        except asyncio.TimeoutError:
            await msg.delete()
            return await ctx.send("Cancelling reaction role removal.")
        if pred.result:
            await msg.delete()
            if emoji:
                if message.channel.permissions_for(ctx.me).manage_messages:
                    try:
                        await message.clear_reaction(emoji)
                    except discord.HTTPException:
                        pass
                await self._remove_reaction_role(ctx.guild, message, [emoji_id])
                await ctx.send(f"Removed the {emoji} reaction role from {message.jump_url}")
            else:
                if message.channel.permissions_for(ctx.me).manage_messages:
                    try:
                        await message.clear_reactions()
                    except discord.HTTPException:
                        pass
                reactions = []
                for data in rroles:
                    reactions.append(data)
                await self._remove_reaction_role(ctx.guild, message, reactions)
                await ctx.send(f"Removed all reaction roles on {message.jump_url}")
        else:
            await msg.delete()
            return await ctx.send("Cancelling reaction role removal.")

    async def _update_cache(self):
        all_messages: dict = await self.rroleconfig.custom("RRole").all()
        self.rrolecache.update(
            int(msg_id)
            for data in all_messages.values()
            for msg_id, msg_data in data.items()
            if msg_data["rroles"]
        )

    def _edit_cache(self, message_id=None, remove=False):
        if remove:
            self.rrolecache.remove(message_id)
        else:
            self.rrolecache.add(message_id)

    async def _remove_reaction_role(
        self,
        guild: discord.Guild,
        message: Union[discord.Message, discord.Object],
        emoji_ids: List[str],
    ):
        async with self.rroleconfig.custom("RRole", guild.id, message.id).rroles() as r:
            for emoji_id in emoji_ids:
                r.pop(emoji_id)
            if not r:
                self._edit_cache(message.id, True)

    def emoji_id(self, emoji: Union[discord.Emoji, str]) -> str:
        return emoji if isinstance(emoji, str) else str(emoji.id)

    def _check_payload_to_cache(self, payload):
        return payload.message_id in self.rrolecache

    @commands.Cog.listener("on_raw_reaction_add")
    @commands.Cog.listener("on_raw_reaction_remove")
    async def on_raw_reaction_add_or_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return

        if not self._check_payload_to_cache(payload):
            return

        if await self.bot.cog_disabled_in_guild_raw(self.qualified_name, payload.guild_id):
            return

        guild: discord.Guild = self.bot.get_guild(payload.guild_id)
        if payload.event_type == "REACTION_ADD":
            member: discord.Member = payload.member
        else:
            member: discord.Member = guild.get_member(payload.user_id)

        if member is None or member.bot:
            return
        if not guild.me.guild_permissions.manage_roles:
            return

        reactions: dict = await self.rroleconfig.custom(
            "RRole", guild.id, payload.message_id
        ).rroles.all()
        emoji_id = (
            str(payload.emoji) if payload.emoji.is_unicode_emoji() else str(payload.emoji.id)
        )
        role_id = reactions[emoji_id]["role_id"][0]
        if not role_id:
            return
        role: discord.Role = guild.get_role(role_id)
        if not role:
            return await self._remove_reaction_role(
                guild, discord.Object(payload.message_id), [emoji_id]
            )
        if not guild.me.guild_permissions.manage_roles:
            return
        if not guild.me.top_role > role:
            return

        type = rroletype_solver(reactions[emoji_id]["type"])

        if payload.event_type == "REACTION_ADD":
            if type is RRoleType.NORMAL:
                if role not in member.roles:
                    await member.add_roles(role, reason="Reaction role")
            elif type is RRoleType.ONCE or type is RRoleType.REMOVE:
                try:
                    chn: discord.abc.GuildChannel = guild.get_channel(payload.channel_id)
                    msg: discord.Message = await chn.fetch_message(payload.message_id)
                    await msg.remove_reaction(payload.emoji, payload.member)
                except:
                    pass
                if type is RRoleType.ONCE:
                    if role not in member.roles:
                        await member.add_roles(role, reason="Reaction role")
                if type is RRoleType.REMOVE:
                    if role in member.roles:
                        await member.remove_roles(role, reason="Reaction role")
            elif type is RRoleType.TOGGLE:
                role_list = []
                chn = None
                msg = None
                try:
                    chn: discord.abc.GuildChannel = guild.get_channel(payload.channel_id)
                    msg: discord.Message = await chn.fetch_message(payload.message_id)
                except:
                    pass
                for e, r in reactions.items():
                    if rroletype_solver(r["type"]) is RRoleType.TOGGLE:
                        temprole: discord.Role = guild.get_role(r["role_id"][0])
                        if temprole and temprole is not role:
                            role_list.append(temprole)
                            if msg:
                                try:
                                    tempemoji: discord.Emoji = await guild.fetch_emoji(e)
                                    try:
                                        await msg.remove_reaction(tempemoji, payload.member)
                                    except:
                                        pass
                                except:
                                    try:
                                        await msg.remove_reaction(e, payload.member)
                                    except:
                                        pass
                for r in role_list:
                    try:
                        await member.remove_roles(r, reason="Reaction role")
                    except:
                        pass
                if role not in member.roles:
                    try:
                        await member.add_roles(role, reason="Reaction role")
                    except:
                        pass
        else:
            if type is RRoleType.NORMAL or type is RRoleType.TOGGLE:
                if role in member.roles:
                    await member.remove_roles(role, reason="Reaction role")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return

        if not self._check_payload_to_cache(payload):
            return

        if await self.bot.cog_disabled_in_guild_raw(self.qualified_name, payload.guild_id):
            return

        await self.config.custom("RRole", payload.guild_id, payload.message_id).clear()
        self._edit_cache(payload.message_id, True)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        if payload.guild_id is None:
            return

        if await self.bot.cog_disabled_in_guild_raw(self.qualified_name, payload.guild_id):
            return

        for message_id in payload.message_ids:
            if message_id in self.rrolecache:
                await self.rroleconfig.custom("RRole", payload.guild_id, message_id).clear()
                self._edit_cache(message_id, True)
