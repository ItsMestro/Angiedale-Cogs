import discord
from redbot.core import commands
from typing import Union

from .helpers import get_command_for_dropping_points, get_command_for_exceeded_points


class Warnings:
    """Warn misbehaving users and take automated actions."""

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def warnset(self, ctx: commands.Context):
        """Manage warnings."""
        pass

    @warnset.command()
    @commands.guild_only()
    async def allowcustomreasons(self, ctx: commands.Context, allowed: bool):
        """Enable or disable custom reasons for a warning."""
        guild = ctx.guild
        await self.warnconfig.guild(guild).allow_custom_reasons.set(allowed)
        if allowed:
            await ctx.send(("Custom reasons have been enabled."))
        else:
            await ctx.send(("Custom reasons have been disabled."))

    @warnset.command()
    @commands.guild_only()
    async def sendtodm(self, ctx: commands.Context, true_or_false: bool):
        """Set whether warnings should be sent to users in DMs."""
        await self.warnconfig.guild(ctx.guild).toggle_dm.set(true_or_false)
        if true_or_false:
            await ctx.send(("I will now try to send warnings to users DMs."))
        else:
            await ctx.send(("Warnings will no longer be sent to users DMs."))

    @warnset.command()
    @commands.guild_only()
    async def includemoderator(self, ctx, true_or_false: bool):
        """Decide whether the name of the moderator warning a user should be included in the DM to that user."""
        await self.warnconfig.guild(ctx.guild).show_mod.set(true_or_false)
        if true_or_false:
            await ctx.send(
                (
                    "I will include the name of the moderator who issued the warning when sending a DM to a user."
                )
            )
        else:
            await ctx.send(
                (
                    "I will not include the name of the moderator who issued the warning when sending a DM to a user."
                )
            )

    @warnset.command()
    @commands.guild_only()
    async def warnchannel(
        self,
        ctx: commands.Context,
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel] = None,
    ):
        """Set the channel where warnings should be sent to.

        Leave empty to use the channel `[p]warn` command was called in.
        """
        guild = ctx.guild
        if channel:
            await self.warnconfig.guild(guild).warn_channel.set(channel.id)
            await ctx.send(
                ("The warn channel has been set to {channel}.").format(channel=channel.mention)
            )
        else:
            await self.warnconfig.guild(guild).warn_channel.set(channel)
            await ctx.send(("Warnings will now be sent in the channel command was used in."))

    @warnset.command()
    @commands.guild_only()
    async def usewarnchannel(self, ctx: commands.Context, true_or_false: bool):
        """
        Set if warnings should be sent to a channel set with `[p]warningset warnchannel`.
        """
        await self.warnconfig.guild(ctx.guild).toggle_channel.set(true_or_false)
        channel = self.bot.get_channel(await self.warnconfig.guild(ctx.guild).warn_channel())
        if true_or_false:
            if channel:
                await ctx.send(
                    ("Warnings will now be sent to {channel}.").format(channel=channel.mention)
                )
            else:
                await ctx.send(("Warnings will now be sent in the channel command was used in."))
        else:
            await ctx.send(("Toggle channel has been disabled."))

    @commands.group()
    @commands.guild_only()
    @commands.guildowner_or_permissions(administrator=True)
    async def warnaction(self, ctx: commands.Context):
        """Manage automated actions for Warnings.

        Actions are essentially command macros. Any command can be run
        when the action is initially triggered, and/or when the action
        is lifted.
        Actions must be given a name and a points threshold. When a
        user is warned enough so that their points go over this
        threshold, the action will be executed.
        """
        pass

    @warnaction.command(name="add")
    @commands.guild_only()
    async def action_add(self, ctx: commands.Context, name: str, points: int):
        """Create an automated action.

        Duplicate action names are not allowed.
        """
        guild = ctx.guild

        exceed_command = await get_command_for_exceeded_points(ctx)
        drop_command = await get_command_for_dropping_points(ctx)

        to_add = {
            "action_name": name,
            "points": points,
            "exceed_command": exceed_command,
            "drop_command": drop_command,
        }

        # Have all details for the action, now save the action
        guild_settings = self.warnconfig.guild(guild)
        async with guild_settings.actions() as registered_actions:
            for act in registered_actions:
                if act["action_name"] == to_add["action_name"]:
                    await ctx.send(("Duplicate action name found!"))
                    break
            else:
                registered_actions.append(to_add)
                # Sort in descending order by point count for ease in
                # finding the highest possible action to take
                registered_actions.sort(key=lambda a: a["points"], reverse=True)
                await ctx.send(("Action {name} has been added.").format(name=name))

    @warnaction.command(name="delete", aliases=["del", "remove"])
    @commands.guild_only()
    async def action_del(self, ctx: commands.Context, action_name: str):
        """Delete the action with the specified name."""
        guild = ctx.guild
        guild_settings = self.warnconfig.guild(guild)
        async with guild_settings.actions() as registered_actions:
            to_remove = None
            for act in registered_actions:
                if act["action_name"] == action_name:
                    to_remove = act
                    break
            if to_remove:
                registered_actions.remove(to_remove)
                await ctx.tick()
            else:
                await ctx.send(("No action named {name} exists!").format(name=action_name))

    @commands.group()
    @commands.guild_only()
    @commands.guildowner_or_permissions(administrator=True)
    async def warnreason(self, ctx: commands.Context):
        """Manage warning reasons.

        Reasons must be given a name, description and points value. The
        name of the reason must be given when a user is warned.
        """
        pass

    @warnreason.command(name="create", aliases=["add"])
    @commands.guild_only()
    async def reason_create(
        self, ctx: commands.Context, name: str, points: int, *, description: str
    ):
        """Create a warning reason."""
        guild = ctx.guild

        if name.lower() == "custom":
            await ctx.send(("*Custom* cannot be used as a reason name!"))
            return
        to_add = {"points": points, "description": description}
        completed = {name.lower(): to_add}

        guild_settings = self.warnconfig.guild(guild)

        async with guild_settings.reasons() as registered_reasons:
            registered_reasons.update(completed)

        await ctx.send(("The new reason has been registered."))

    @warnreason.command(name="delete", aliases=["remove", "del"])
    @commands.guild_only()
    async def reason_del(self, ctx: commands.Context, reason_name: str):
        """Delete a warning reason."""
        guild = ctx.guild
        guild_settings = self.warnconfig.guild(guild)
        async with guild_settings.reasons() as registered_reasons:
            if registered_reasons.pop(reason_name.lower(), None):
                await ctx.tick()
            else:
                await ctx.send(("That is not a registered reason name."))
