import logging
import asyncio
from typing import Literal

import discord
from redbot.core import Config, checks, commands, modlog
from redbot.core.bot import Red
from redbot.core.utils import AsyncIter

from .converters import SelfRole
from .modlog import ModLog
from .modset import ModSettings
from .mutes import Mutes
from .warnings import Warnings

log = logging.getLogger("red.angiedale.admin")

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

    default_member_warnings = {"total_points": 0, "status": "", "warnings": {}}

    def __init__(self, bot: Red):
        self.bot = bot

        self.reportsconfig = Config.get_conf(self, identifier=1387000, force_registration=True, cog_name="Reports")

        self.modconfig = Config.get_conf(self, identifier=1387000, force_registration=True, cog_name="Mod")

        self.mutesconfig = Config.get_conf(self, identifier=1387000, force_registration=True, cog_name="Mutes")

        self.warnconfig = Config.get_conf(self, identifier=1387000, force_registration=True, cog_name="Warnings")
        self.warnconfig.register_guild(**self.default_guild_warnings)
        self.warnconfig.register_member(**self.default_member_warnings)
        self.registration_task = self.bot.loop.create_task(self.register_warningtype())

        self.adminconfig = Config.get_conf(self, identifier=1387000, force_registration=True, cog_name="OwnerAdmin")
        self.adminconfig.register_guild(
            announce_channel=None,  # Integer ID
            selfroles=[],  # List of integer ID's
        )

        self.filterconfig = Config.get_conf(self, identifier=1387000, force_registration=True, cog_name="Filter")
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
        """Base command to manage filter settings."""
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
        """Edit role settings."""
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
            await ctx.send((
                "I can not let you edit {role.name}"
                " because that role is higher than or equal to your highest role"
                " in the Discord hierarchy."
                ).format(role=role))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((
                "I can not edit {role.name}"
                " because that role is higher than my or equal to highest role"
                " in the Discord hierarchy."
                ).format(role=role))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send(("I need manage roles permission to do that."))
            return
        try:
            await role.edit(reason=reason, color=value)
        except discord.Forbidden:
            await ctx.send((
                "I attempted to do something that Discord denied me permissions for."
                " Your command failed to successfully complete."
                ))
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
            await ctx.send((
                "I can not let you edit {role.name}"
                " because that role is higher than or equal to your highest role"
                " in the Discord hierarchy."
                ).format(role=role))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((
                "I can not edit {role.name}"
                " because that role is higher than my or equal to highest role"
                " in the Discord hierarchy."
                ).format(role=role))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send(("I need manage roles permission to do that."))
            return
        try:
            await role.edit(reason=reason, name=name)
        except discord.Forbidden:
            await ctx.send((
                "I attempted to do something that Discord denied me permissions for."
                " Your command failed to successfully complete."
                ))
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
            await ctx.send((
                "I can not let you give {role.name} to {member.display_name}"
                " because that role is higher than or equal to your highest role"
                " in the Discord hierarchy."
                ).format(role=role, member=member))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((
                "I can not give {role.name} to {member.display_name}"
                " because that role is higher than or equal to my highest role"
                " in the Discord hierarchy."
                ).format(role=role, member=member))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send(("I need manage roles permission to do that."))
            return
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            await ctx.send((
                "I attempted to do something that Discord denied me permissions for."
                " Your command failed to successfully complete."))
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
            await ctx.send((
                "I can not let you remove {role.name} from {member.display_name}"
                " because that role is higher than or equal to your highest role"
                " in the Discord hierarchy."
                ).format(role=role, member=member))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((
                "I can not remove {role.name} from {member.display_name}"
                " because that role is higher than or equal to my highest role"
                " in the Discord hierarchy."
                ).format(role=role, member=member))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send(("I need manage roles permission to do that."))
            return
        try:
            await member.remove_roles(role)
        except discord.Forbidden:
            await ctx.send((
                "I attempted to do something that Discord denied me permissions for."
                " Your command failed to successfully complete."))
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
        """Change how announcements are sent in this guild."""
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
    async def selfroleset_add(self, ctx: commands.Context, *, role: discord.Role):
        """
        Add a role to the list of available selfroles.

        NOTE: The role is case sensitive!
        """
        if not self.pass_user_hierarchy_check(ctx, role):
            await ctx.send(
                (
                    "I cannot let you add {role.name} as a selfrole because that role is higher than or equal to your highest role in the Discord hierarchy."
                ).format(role=role)
            )
            return
        async with self.adminconfig.guild(ctx.guild).selfroles() as curr_selfroles:
            if role.id not in curr_selfroles:
                curr_selfroles.append(role.id)
                await ctx.send(("Added."))
                return

        await ctx.send(("That role is already a selfrole."))

    @selfroleset.command(name="remove")
    async def selfroleset_remove(self, ctx: commands.Context, *, role: SelfRole):
        """
        Remove a role from the list of available selfroles.

        NOTE: The role is case sensitive!
        """
        if not self.pass_user_hierarchy_check(ctx, role):
            await ctx.send(
                (
                    "I cannot let you remove {role.name} from being a selfrole because that role is higher than or equal to your highest role in the Discord hierarchy."
                ).format(role=role)
            )
            return
        async with self.adminconfig.guild(ctx.guild).selfroles() as curr_selfroles:
            curr_selfroles.remove(role.id)

        await ctx.send(("Removed."))

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
        """Enable or Disable reporting for this server."""
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
        """Sends invite to support server with information on setting up the bot."""
        if not ctx.guild.id == 128856147162562560:
            await ctx.send("Join this server and run this command again in there.\n\nhttps://discord.gg/xxjdXmR")
        else:
            for guild in self.bot.guilds:
                if ctx.author.id == guild.owner_id:
                    supportrole = ctx.guild.get_role(815025432507318304)
                    if supportrole in ctx.author.roles:
                        await ctx.author.remove_roles(supportrole)
                        return await ctx.send("Removed your support role.")
                    else:
                        await ctx.author.add_roles(supportrole)
                        return await ctx.send("You've now been given a support role with access to exclusive channels for information on the bot.")
            return await ctx.send((f"You're not the owner of any servers that I'm in. The support role is only for server owners.\n"
            f"Feel free to let me join one of your servers with `{ctx.clean_prefix}invite`"))