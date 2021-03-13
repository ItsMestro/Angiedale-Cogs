import logging
from datetime import timedelta
from typing import Optional

import discord
from redbot.core import checks, commands
from redbot.core.utils import bounded_gather
from redbot.core.utils.chat_formatting import (
    humanize_list, humanize_timedelta, pagify
)

from .converters import MuteTime

log = logging.getLogger("red.angiedale.mod.mutes")


class Mutes():
    """
    Mute users temporarily or indefinitely.
    """

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_channels=True)
    async def muteset(self, ctx: commands.Context):
        """Mute settings."""
        pass

    @muteset.command()
    @commands.guild_only()
    async def senddm(self, ctx: commands.Context, true_or_false: bool):
        """Set whether mute notifications should be sent to users in DMs."""
        await self.mutesconfig.guild(ctx.guild).dm.set(true_or_false)
        if true_or_false:
            await ctx.send(("I will now try to send mute notifications to users DMs."))
        else:
            await ctx.send(("Mute notifications will no longer be sent to users DMs."))

    @muteset.command()
    @commands.guild_only()
    async def showmoderator(self, ctx, true_or_false: bool):
        """Decide whether the name of the moderator muting a user should be included in the DM to that user."""
        await self.mutesconfig.guild(ctx.guild).show_mod.set(true_or_false)
        if true_or_false:
            await ctx.send(
                (
                    "I will include the name of the moderator who issued the mute when sending a DM to a user."
                )
            )
        else:
            await ctx.send(
                (
                    "I will not include the name of the moderator who issued the mute when sending a DM to a user."
                )
            )

    @muteset.command(name="role")
    @checks.admin_or_permissions(manage_channels=True, manage_roles=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    async def mute_role(self, ctx: commands.Context, *, role: discord.Role = None):
        """Sets the role to be applied when muting a user.

        If no role is setup the bot will attempt to mute a user by setting
        channel overwrites in all channels to prevent the user from sending messages.

        Note: If no role is setup a user may be able to leave the server
        and rejoin no longer being muted.
        """
        modcog = self.bot.get_cog("Mod")
        if modcog:
            await modcog.mute_role_helper(ctx, role)
        else:
            await ctx.send("Something went wrong. Please contact the bot owner.")

    @muteset.command(name="settings", aliases=["showsettings"])
    async def show_mutes_settings(self, ctx: commands.Context):
        """
        Shows the current mute settings for this guild.
        """
        data = await self.mutesconfig.guild(ctx.guild).all()

        mute_role = ctx.guild.get_role(data["mute_role"])
        notification_channel = ctx.guild.get_channel(data["notification_channel"])
        default_time = timedelta(seconds=data["default_time"])
        msg = (
            "Mute Role: {role}\n"
            "Notification Channel: {channel}\n"
            "Default Time: {time}\n"
            "Send DM: {dm}\n"
            "Show moderator: {show_mod}"
        ).format(
            role=mute_role.mention if mute_role else ("None"),
            channel=notification_channel.mention if notification_channel else ("None"),
            time=humanize_timedelta(timedelta=default_time) if default_time else ("None"),
            dm=data["dm"],
            show_mod=data["show_mod"],
        )
        await ctx.maybe_send_embed(msg)

    @muteset.command(name="errornotification")
    async def notification_channel_set(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """
        Set the notification channel for automatic unmute issues.

        If no channel is provided this will be cleared and notifications
        about issues when unmuting users will not be sent anywhere.
        """
        if channel is None:
            await self.mutesconfig.guild(ctx.guild).notification_channel.clear()
            await ctx.send(("Notification channel for unmute issues has been cleard."))
        else:
            await self.mutesconfig.guild(ctx.guild).notification_channel.set(channel.id)
            await ctx.send(
                ("I will post unmute issues in {channel}.").format(channel=channel.mention)
            )

    @muteset.command(name="makerole")
    @checks.has_permissions(manage_roles=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def make_mute_role(self, ctx: commands.Context, *, name: str):
        """Create a Muted role.

        This will create a role and apply overwrites to all available channels
        to more easily setup muting a user.

        If you already have a muted role created on the server use
        `[p]mutesetrole ROLE_NAME_HERE`
        """
        if await self.mutesconfig.guild(ctx.guild).mute_role():
            command = f"`{ctx.clean_prefix}mutesetrole`"
            return await ctx.send(
                (
                    "There is already a mute role setup in this server. "
                    "Please remove it with {command} before trying to "
                    "create a new one."
                ).format(command=command)
            )
        async with ctx.typing():
            perms = discord.Permissions()
            perms.update(send_messages=False, speak=False, add_reactions=False)
            try:
                role = await ctx.guild.create_role(
                    name=name, permissions=perms, reason=("Mute role setup")
                )
                await self.mutesconfig.guild(ctx.guild).mute_role.set(role.id)
                # save the role early incase of issue later
            except discord.errors.Forbidden:
                return await ctx.send(("I could not create a muted role in this server."))
            errors = []
            tasks = []
            for channel in ctx.guild.channels:
                tasks.append(self._set_mute_role_overwrites(role, channel))
            errors = await bounded_gather(*tasks)
            if any(errors):
                msg = (
                    "I could not set overwrites for the following channels: {channels}"
                ).format(channels=humanize_list([i for i in errors if i]))
                for page in pagify(msg, delims=[" "]):
                    await ctx.send(page)

            await ctx.send(("Mute role set to {role}").format(role=role.name))
        if not await self.mutesconfig.guild(ctx.guild).notification_channel():
            command_1 = f"`{ctx.clean_prefix}muteset notification`"
            await ctx.send(
                (
                    "No notification channel has been setup, "
                    "use {command_1} to be updated when there's an issue in automatic unmutes."
                ).format(command_1=command_1)
            )

    async def _set_mute_role_overwrites(
        self, role: discord.Role, channel: discord.abc.GuildChannel
    ) -> Optional[str]:
        """
        This sets the supplied role and channel overwrites to what we want
        by default for a mute role
        """
        if not channel.permissions_for(channel.guild.me).manage_permissions:
            return channel.mention
        overs = discord.PermissionOverwrite()
        overs.send_messages = False
        overs.add_reactions = False
        overs.speak = False
        try:
            await channel.set_permissions(role, overwrite=overs, reason=("Mute role setup"))
            return None
        except discord.errors.Forbidden:
            return channel.mention

    @muteset.command(name="defaulttime", aliases=["time"])
    async def default_mute_time(self, ctx: commands.Context, *, time: Optional[MuteTime] = None):
        """
        Set the default mute time for the mute command.

        If no time interval is provided this will be cleared.
        """

        if not time:
            await self.mutesconfig.guild(ctx.guild).default_time.clear()
            await ctx.send(("Default mute time removed."))
        else:
            data = time.get("duration", {})
            if not data:
                return await ctx.send(("Please provide a valid time format."))
            await self.mutesconfig.guild(ctx.guild).default_time.set(data.total_seconds())
            await ctx.send(
                ("Default mute time set to {time}.").format(
                    time=humanize_timedelta(timedelta=data)
                )
            )
