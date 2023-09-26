import asyncio
from datetime import datetime, timezone
from typing import Optional, Union

import discord
from redbot.core import checks, commands, modlog
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.predicates import MessagePredicate


class ModLog:
    """Manage log channels for moderation actions."""

    @commands.group()
    @checks.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def modlog(self, ctx: commands.Context):
        """Manage modlogs."""
        pass

    @checks.is_owner()
    @modlog.command(hidden=True, name="fixcasetypes")
    async def reapply_audittype_migration(self, ctx: commands.Context):
        """Command to fix misbehaving casetypes."""
        await modlog.handle_auditype_key()
        await ctx.tick()

    @modlog.command(aliases=["channel"])
    async def logchannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set a channel as the modlog.

        Omit `[channel]` to disable the modlog.
        """
        guild = ctx.guild
        if channel:
            if channel.permissions_for(guild.me).send_messages:
                await modlog.set_modlog_channel(guild, channel)
                await ctx.send(
                    ("Mod events will be sent to {channel}.").format(channel=channel.mention)
                )
            else:
                await ctx.send(
                    ("I do not have permissions to send messages in {channel}!").format(
                        channel=channel.mention
                    )
                )
        else:
            try:
                await modlog.get_modlog_channel(guild)
            except RuntimeError:
                await ctx.send(("Mod log is already disabled."))
            else:
                await modlog.set_modlog_channel(guild, None)
                await ctx.send(("Mod log deactivated."))

    @modlog.command(name="cases")
    async def set_cases(self, ctx: commands.Context, action: str = None):
        """
        Enable or disable case creation for a mod action.
        An action can be enabling or disabling specific cases. (Ban, kick, mute, etc.)
        Example: `[p]modlogset cases kick enabled`
        """
        guild = ctx.guild

        if action is None:  # No args given
            casetypes = await modlog.get_all_casetypes(guild)
            await ctx.send_help()
            lines = []
            for ct in casetypes:
                enabled = ("enabled") if await ct.is_enabled() else ("disabled")
                lines.append(f"{ct.name} : {enabled}")

            await ctx.send(("Current settings:\n") + box("\n".join(lines)))
            return

        casetype = await modlog.get_casetype(action, guild)
        if not casetype:
            await ctx.send(("That action is not registered."))
        else:
            enabled = await casetype.is_enabled()
            await casetype.set_enabled(not enabled)
            await ctx.send(
                ("Case creation for {action_name} actions is now {enabled}.").format(
                    action_name=action, enabled=("enabled") if not enabled else ("disabled")
                )
            )

    @modlog.command()
    async def resetcases(self, ctx: commands.Context):
        """Reset all modlog cases in this server."""
        guild = ctx.guild
        await ctx.send(
            ("Are you sure you would like to reset all modlog cases in this server?") + " (yes/no)"
        )
        try:
            pred = MessagePredicate.yes_or_no(ctx, user=ctx.author)
            msg = await ctx.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            await ctx.send(("You took too long to respond."))
            return
        if pred.result:
            await modlog.reset_cases(guild)
            await ctx.send(("Cases have been reset."))
        else:
            await ctx.send(("No changes have been made."))

    @modlog.command()
    async def case(self, ctx: commands.Context, number: int):
        """Show the specified case."""
        try:
            case = await modlog.get_case(number, ctx.guild, self.bot)
        except RuntimeError:
            await ctx.send(("That case does not exist for that server."))
            return
        else:
            if await ctx.embed_requested():
                await ctx.send(embed=await case.message_content(embed=True))
            else:
                message = ("{case}\n**Timestamp:** {timestamp}").format(
                    case=await case.message_content(embed=False),
                    timestamp=f"<t:{int(case.created_at)}>",
                )
                await ctx.send(message)

    @modlog.command()
    async def casesfor(self, ctx: commands.Context, *, member: Union[discord.Member, int]):
        """Display cases for the specified member."""
        async with ctx.typing():
            try:
                if isinstance(member, int):
                    cases = await modlog.get_cases_for_member(
                        bot=ctx.bot, guild=ctx.guild, member_id=member
                    )
                else:
                    cases = await modlog.get_cases_for_member(
                        bot=ctx.bot, guild=ctx.guild, member=member
                    )
            except discord.NotFound:
                return await ctx.send(("That user does not exist."))
            except discord.HTTPException:
                return await ctx.send(
                    ("Something unexpected went wrong while fetching that user by ID.")
                )

            if not cases:
                return await ctx.send(("That user does not have any cases."))

            embed_requested = await ctx.embed_requested()
            if embed_requested:
                rendered_cases = [await case.message_content(embed=True) for case in cases]
            else:
                rendered_cases = []
                for case in cases:
                    message = ("{case}\n**Timestamp:** {timestamp}").format(
                        case=await case.message_content(embed=False),
                        timestamp=f"<t:{int(case.created_at)}>",
                    )
                    rendered_cases.append(message)

        await menu(ctx, rendered_cases, DEFAULT_CONTROLS)

    @modlog.command()
    async def listcases(self, ctx: commands.Context, *, member: Union[discord.Member, int]):
        """List cases for the specified member."""
        async with ctx.typing():
            try:
                if isinstance(member, int):
                    cases = await modlog.get_cases_for_member(
                        bot=ctx.bot, guild=ctx.guild, member_id=member
                    )
                else:
                    cases = await modlog.get_cases_for_member(
                        bot=ctx.bot, guild=ctx.guild, member=member
                    )
            except discord.NotFound:
                return await ctx.send(("That user does not exist."))
            except discord.HTTPException:
                return await ctx.send(
                    ("Something unexpected went wrong while fetching that user by ID.")
                )
            if not cases:
                return await ctx.send(("That user does not have any cases."))

            rendered_cases = []
            message = ""
            for case in cases:
                message += ("{case}\n**Timestamp:** {timestamp}\n\n").format(
                    case=await case.message_content(embed=False),
                    timestamp=f"<t:{int(case.created_at)}>",
                )
            for page in pagify(message, ["\n\n", "\n"], priority=True):
                rendered_cases.append(page)
        await menu(ctx, rendered_cases, DEFAULT_CONTROLS)

    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def reason(self, ctx: commands.Context, case: Optional[int], *, reason: str):
        """Specify a reason for a modlog case.

        Please note that you can only edit cases you are
        the owner of unless you are a mod, admin or server owner.

        If no case number is specified, the latest case will be used.
        """
        author = ctx.author
        guild = ctx.guild
        if case is None:
            # get the latest case
            case_obj = await modlog.get_latest_case(guild, self.bot)
            if case_obj is None:
                await ctx.send(("There are no modlog cases in this server."))
                return
        else:
            try:
                case_obj = await modlog.get_case(case, guild, self.bot)
            except RuntimeError:
                await ctx.send(("That case does not exist!"))
                return

        is_guild_owner = author == guild.owner
        is_case_author = author == case_obj.moderator
        author_is_mod = await ctx.bot.is_mod(author)
        if not (is_guild_owner or is_case_author or author_is_mod):
            await ctx.send(("You are not authorized to modify that case!"))
            return
        to_modify = {"reason": reason}
        if case_obj.moderator != author:
            to_modify["amended_by"] = author
        to_modify["modified_at"] = ctx.message.created_at.replace(tzinfo=timezone.utc).timestamp()
        await case_obj.edit(to_modify)
        await ctx.send(
            ("Reason for case #{num} has been updated.").format(num=case_obj.case_number)
        )
