import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Union, cast

import discord
from redbot.core import checks, commands, modlog
from redbot.core.utils import bounded_gather
from redbot.core.utils.chat_formatting import (
    bold, humanize_list, humanize_timedelta, pagify
)
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.mod import get_audit_reason
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate

from .abc import MixinMeta
from .converters import MuteTime

MUTE_UNMUTE_ISSUES = {
    "already_muted": ("That user is already muted in this channel."),
    "already_unmuted": ("That user is not muted in this channel."),
    "hierarchy_problem": (
        "I cannot let you do that. You are not higher than the user in the role hierarchy."
    ),
    "assigned_role_hierarchy_problem": (
        "I cannot let you do that. You are not higher than the mute role in the role hierarchy."
    ),
    "is_admin": ("That user cannot be (un)muted, as they have the Administrator permission."),
    "permissions_issue_role": (
        "Failed to mute or unmute user. I need the Manage Roles "
        "permission and the user I'm muting must be "
        "lower than myself in the role hierarchy."
    ),
    "permissions_issue_channel": (
        "Failed to mute or unmute user. I need the Manage Permissions permission."
    ),
    "left_guild": ("The user has left the server while applying an overwrite."),
    "unknown_channel": ("The channel I tried to mute or unmute the user in isn't found."),
    "role_missing": ("The mute role no longer exists."),
    "voice_mute_permission": (
        "Because I don't have the Move Members permission, this will take into effect when the user rejoins."
    ),
}

log = logging.getLogger("red.angiedale.mod.mutes")


class Mutes(MixinMeta):
    """
    Mute users temporarily or indefinitely.
    """

    async def is_allowed_by_hierarchy(
        self, guild: discord.Guild, mod: discord.Member, user: discord.Member
    ):
        is_special = mod == guild.owner or await self.bot.is_owner(mod)
        return mod.top_role > user.top_role or is_special

    async def _handle_automatic_unmute(self):
        """This is the core task creator and loop
        for automatic unmutes

        A resolution of 30 seconds seems appropriate
        to allow for decent resolution on low timed
        unmutes and without being too busy on our event loop
        """
        await self.bot.wait_until_red_ready()
        await self._readymutes.wait()
        while True:
            await self._clean_tasks()
            try:
                await self._handle_server_unmutes()
            except Exception:
                log.error("error checking server unmutes", exc_info=True)
            await asyncio.sleep(0.1)
            try:
                await self._handle_channel_unmutes()
            except Exception:
                log.error("error checking channel unmutes", exc_info=True)
            await asyncio.sleep(30)

    async def _clean_tasks(self):
        """This is here to cleanup our tasks
        and log when we have something going wrong
        inside our tasks.
        """
        log.debug("Cleaning unmute tasks")
        is_debug = log.getEffectiveLevel() <= logging.DEBUG
        for task_id in list(self._unmute_tasks.keys()):
            task = self._unmute_tasks[task_id]

            if task.cancelled():
                self._unmute_tasks.pop(task_id, None)
                continue

            if task.done():
                try:
                    r = task.result()
                except Exception:
                    if is_debug:
                        log.exception("Dead task when trying to unmute")
                self._unmute_tasks.pop(task_id, None)

    async def _handle_server_unmutes(self):
        """This is where the logic for role unmutes is taken care of"""
        log.debug("Checking server unmutes")
        for g_id in self._server_mutes:
            guild = self.bot.get_guild(g_id)
            if guild is None or await self.bot.cog_disabled_in_guild(self, guild):
                continue
            for u_id in self._server_mutes[guild.id]:
                if self._server_mutes[guild.id][u_id]["until"] is None:
                    continue
                time_to_unmute = (
                    self._server_mutes[guild.id][u_id]["until"]
                    - datetime.now(timezone.utc).timestamp()
                )
                if time_to_unmute < 60.0:
                    task_name = f"server-unmute-{g_id}-{u_id}"
                    if task_name in self._unmute_tasks:
                        continue
                    log.debug(f"Creating task: {task_name}")
                    self._unmute_tasks[task_name] = asyncio.create_task(
                        self._auto_unmute_user(guild, self._server_mutes[guild.id][u_id])
                    )

    async def _auto_unmute_user(self, guild: discord.Guild, data: dict):
        """
        This handles role unmutes automatically

        Since channel overwrite mutes are handled under the separate
        _auto_channel_unmute_user methods here we don't
        need to worry about the dict response for message
        since only role based mutes get added here
        """
        delay = data["until"] - datetime.now(timezone.utc).timestamp()
        if delay < 1:
            delay = 0
        await asyncio.sleep(delay)

        member = guild.get_member(data["member"])
        author = guild.get_member(data["author"])
        if not member:
            async with self.mutesconfig.guild(guild).muted_users() as muted_users:
                if str(data["member"]) in muted_users:
                    del muted_users[str(data["member"])]
            del self._server_mutes[guild.id][data["member"]]
            return
        success = await self.unmute_user(guild, author, member, ("Automatic unmute"))
        async with self.mutesconfig.guild(guild).muted_users() as muted_users:
            if str(member.id) in muted_users:
                del muted_users[str(member.id)]
        if success["success"]:
            await modlog.create_case(
                self.bot,
                guild,
                datetime.now(timezone.utc),
                "sunmute",
                member,
                author,
                ("Automatic unmute"),
                until=None,
            )
            await self._send_dm_notification(
                member, author, guild, ("Server unmute"), ("Automatic unmute")
            )
        else:
            chan_id = await self.mutesconfig.guild(guild).notification_channel()
            notification_channel = guild.get_channel(chan_id)
            if not notification_channel:
                return
            if not notification_channel.permissions_for(guild.me).send_messages:
                return
            error_msg = (
                "I am unable to unmute {user} for the following reason:\n{reason}"
            ).format(user=member, reason=success["reason"])
            try:
                await notification_channel.send(error_msg)
            except discord.errors.Forbidden:
                log.info(error_msg)
                return

    async def _handle_channel_unmutes(self):
        """This is where the logic for handling channel unmutes is taken care of"""
        log.debug("Checking channel unmutes")
        multiple_mutes = {}
        for c_id, c_data in self._channel_mutes.items():
            for u_id in self._channel_mutes[c_id]:
                if (
                    not self._channel_mutes[c_id][u_id]
                    or not self._channel_mutes[c_id][u_id]["until"]
                ):
                    continue
                guild = self.bot.get_guild(self._channel_mutes[c_id][u_id]["guild"])
                if guild is None or await self.bot.cog_disabled_in_guild(self, guild):
                    continue
                time_to_unmute = (
                    self._channel_mutes[c_id][u_id]["until"]
                    - datetime.now(timezone.utc).timestamp()
                )
                if time_to_unmute < 60.0:
                    if guild not in multiple_mutes:
                        multiple_mutes[guild] = {}
                    if u_id not in multiple_mutes[guild]:
                        multiple_mutes[guild][u_id] = {c_id: self._channel_mutes[c_id][u_id]}
                    else:
                        multiple_mutes[guild][u_id][c_id] = self._channel_mutes[c_id][u_id]

        for guild, users in multiple_mutes.items():
            for user, channels in users.items():
                if len(channels) > 1:
                    task_name = f"server-unmute-channels-{guild.id}-{user}"
                    if task_name in self._unmute_tasks:
                        continue
                    log.debug(f"Creating task: {task_name}")
                    member = guild.get_member(user)
                    self._unmute_tasks[task_name] = asyncio.create_task(
                        self._auto_channel_unmute_user_multi(member, guild, channels)
                    )

                else:
                    for channel, mute_data in channels.items():
                        task_name = f"channel-unmute-{channel}-{user}"
                        log.debug(f"Creating task: {task_name}")
                        if task_name in self._unmute_tasks:
                            continue
                        self._unmute_tasks[task_name] = asyncio.create_task(
                            self._auto_channel_unmute_user(guild.get_channel(channel), mute_data)
                        )

        del multiple_mutes

    async def _auto_channel_unmute_user_multi(
        self, member: discord.Member, guild: discord.Guild, channels: Dict[int, dict]
    ):
        """This is meant to handle multiple channels all being unmuted at once"""
        if guild.id in self._channel_mute_events:
            self._channel_mute_events[guild.id].clear()
        else:
            self._channel_mute_events[guild.id] = asyncio.Event()
        tasks = []
        for channel, mute_data in channels.items():
            author = guild.get_member(mute_data["author"])
            tasks.append(
                self._auto_channel_unmute_user(guild.get_channel(channel), mute_data, False)
            )
        results = await bounded_gather(*tasks)
        unmuted_channels = [guild.get_channel(c) for c in channels.keys()]
        for result in results:
            if not result:
                continue
            _mmeber, channel, reason = result
            unmuted_channels.remove(channel)
        modlog_reason = ("Automatic unmute")

        channel_list = humanize_list([c.mention for c in unmuted_channels if c is not None])
        if channel_list:
            modlog_reason += ("\nUnmuted in channels: ") + channel_list

        await modlog.create_case(
            self.bot,
            guild,
            datetime.now(timezone.utc),
            "sunmute",
            member,
            author,
            modlog_reason,
            until=None,
        )
        await self._send_dm_notification(
            member, author, guild, ("Server unmute"), ("Automatic unmute")
        )
        self._channel_mute_events[guild.id].set()
        if any(results):
            reasons = {}
            for result in results:
                if not result:
                    continue
                _member, channel, reason = result
                if reason not in reasons:
                    reasons[reason] = [channel]
                else:
                    reasons[reason].append(channel)
            error_msg = ("{member} could not be unmuted for the following reasons:\n").format(
                member=member
            )
            for reason, channel_list in reasons.items():
                error_msg += ("{reason} In the following channels: {channels}\n").format(
                    reason=reason,
                    channels=humanize_list([c.mention for c in channel_list]),
                )
            chan_id = await self.mutesconfig.guild(guild).notification_channel()
            notification_channel = guild.get_channel(chan_id)
            if notification_channel is None:
                return None
            if not notification_channel.permissions_for(guild.me).send_messages:
                return None
            try:
                await notification_channel.send(error_msg)
            except discord.errors.Forbidden:
                log.info(error_msg)
                return None

    async def _auto_channel_unmute_user(
        self, channel: discord.abc.GuildChannel, data: dict, create_case: bool = True
    ) -> Optional[Tuple[discord.Member, discord.abc.GuildChannel, str]]:
        """This is meant to unmute a user in individual channels"""
        delay = data["until"] - datetime.now(timezone.utc).timestamp()
        if delay < 1:
            delay = 0
        await asyncio.sleep(delay)
        member = channel.guild.get_member(data["member"])
        author = channel.guild.get_member(data["author"])
        if not member:
            async with self.mutesconfig.channel(channel).muted_users() as muted_users:
                if str(data["member"]) in muted_users:
                    del muted_users[str(data["member"])]
            if (
                channel.id in self._channel_mutes
                and data["member"] in self._channel_mutes[channel.id]
            ):
                del self._channel_mutes[channel.id][data["member"]]
            return None
        success = await self.channel_unmute_user(
            channel.guild, channel, author, member, ("Automatic unmute")
        )
        async with self.mutesconfig.channel(channel).muted_users() as muted_users:
            if str(member.id) in muted_users:
                del muted_users[str(member.id)]
        if success["success"]:
            if create_case:
                if isinstance(channel, discord.VoiceChannel):
                    unmute_type = "vunmute"
                    notification_title = ("Voice unmute")
                else:
                    unmute_type = "cunmute"
                    notification_title = ("Channel unmute")
                await modlog.create_case(
                    self.bot,
                    channel.guild,
                    datetime.now(timezone.utc),
                    unmute_type,
                    member,
                    channel.guild.me,
                    ("Automatic unmute"),
                    until=None,
                    channel=channel,
                )
                await self._send_dm_notification(
                    member, author, channel.guild, notification_title, ("Automatic unmute")
                )
            return None
        else:
            error_msg = (
                "I am unable to unmute {user} in {channel} for the following reason:\n{reason}"
            ).format(user=member, channel=channel.mention, reason=success["reason"])
            if create_case:
                chan_id = await self.mutesconfig.guild(channel.guild).notification_channel()
                notification_channel = channel.guild.get_channel(chan_id)
                if not notification_channel:
                    return None
                if not notification_channel.permissions_for(channel.guild.me).send_messages:
                    return None
                try:
                    await notification_channel.send(error_msg)
                except discord.errors.Forbidden:
                    log.info(error_msg)
                    return None
            else:
                return (member, channel, success["reason"])

    async def _send_dm_notification(
        self,
        user: Union[discord.User, discord.Member],
        moderator: Optional[Union[discord.User, discord.Member]],
        guild: discord.Guild,
        mute_type: str,
        reason: Optional[str],
        duration=None,
    ):
        if not await self.mutesconfig.guild(guild).dm():
            return

        show_mod = await self.mutesconfig.guild(guild).show_mod()
        title = bold(mute_type)
        if duration:
            duration_str = humanize_timedelta(timedelta=duration)
            until = datetime.now(timezone.utc) + duration
            until_str = until.strftime("%Y-%m-%d %H:%M:%S UTC")

        if moderator is None:
            moderator_str = ("Unknown")
        else:
            moderator_str = str(moderator)

        if not reason:
            reason = ("No reason provided.")

        # okay, this is some poor API to require PrivateChannel here...
        if await self.bot.embed_requested(await user.create_dm(), user):
            em = discord.Embed(
                title=title,
                description=reason,
                color=await self.bot.get_embed_color(user),
            )
            em.timestamp = datetime.utcnow()
            if duration:
                em.add_field(name=("Until"), value=until_str)
                em.add_field(name=("Duration"), value=duration_str)
            em.add_field(name=("Guild"), value=guild.name, inline=False)
            if show_mod:
                em.add_field(name=("Moderator"), value=moderator_str)
            try:
                await user.send(embed=em)
            except discord.Forbidden:
                pass
        else:
            message = f"{title}\n>>> "
            message += reason
            message += (
                ("\n**Moderator**: {moderator}").format(moderator=moderator_str)
                if show_mod
                else ""
            )
            message += (
                ("\n**Until**: {until}\n**Duration**: {duration}").format(
                    until=until_str, duration=duration_str
                )
                if duration
                else ""
            )
            message += ("\n**Guild**: {guild_name}").format(guild_name=guild.name)
            try:
                await user.send(message)
            except discord.Forbidden:
                pass

    @commands.Cog.listener("on_member_update")
    async def mutes_on_member_update(self, before: discord.Member, after: discord.Member):
        """
        Used to handle the cache if a member manually has the muted role removed
        """
        guild = before.guild
        if await self.bot.cog_disabled_in_guild(self, guild):
            return
        if guild.id not in self.mute_role_cache:
            return
        should_save = False
        mute_role_id = self.mute_role_cache[guild.id]
        mute_role = guild.get_role(mute_role_id)
        if not mute_role:
            return
        b = set(before.roles)
        a = set(after.roles)
        roles_removed = list(b - a)
        roles_added = list(a - b)
        if mute_role in roles_removed:
            # send modlog case for unmute and remove from cache
            if guild.id not in self._server_mutes:
                # they weren't a tracked mute so we can return early
                return
            if after.id in self._server_mutes[guild.id]:
                await modlog.create_case(
                    self.bot,
                    guild,
                    datetime.now(timezone.utc),
                    "sunmute",
                    after,
                    None,
                    ("Manually removed mute role"),
                )
                del self._server_mutes[guild.id][after.id]
                should_save = True
                await self._send_dm_notification(
                    after, None, guild, ("Server unmute"), ("Manually removed mute role")
                )
        elif mute_role in roles_added:
            # send modlog case for mute and add to cache
            if guild.id not in self._server_mutes:
                # initialize the guild in the cache to prevent keyerrors
                self._server_mutes[guild.id] = {}
            if after.id not in self._server_mutes[guild.id]:
                await modlog.create_case(
                    self.bot,
                    guild,
                    datetime.now(timezone.utc),
                    "smute",
                    after,
                    None,
                    ("Manually applied mute role"),
                )
                self._server_mutes[guild.id][after.id] = {
                    "author": None,
                    "member": after.id,
                    "until": None,
                }
                should_save = True
                await self._send_dm_notification(
                    after, None, guild, ("Server mute"), ("Manually applied mute role")
                )
        if should_save:
            await self.mutesconfig.guild(guild).muted_users.set(self._server_mutes[guild.id])

    @commands.Cog.listener("on_guild_channel_update")
    async def mutes_on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ):
        """
        This handles manually removing overwrites for a user that has been muted
        """
        if await self.bot.cog_disabled_in_guild(self, after.guild):
            return
        if after.guild.id in self._channel_mute_events:
            await self._channel_mute_events[after.guild.id].wait()
        if after.id in self._channel_mutes:
            before_perms: Dict[int, Dict[str, Optional[bool]]] = {
                o.id: {name: attr for name, attr in p} for o, p in before.overwrites.items()
            }
            after_perms: Dict[int, Dict[str, Optional[bool]]] = {
                o.id: {name: attr for name, attr in p} for o, p in after.overwrites.items()
            }
            to_del: List[int] = []
            for user_id in self._channel_mutes[after.id].keys():
                send_messages = False
                speak = False
                if user_id in after_perms:
                    send_messages = (
                        after_perms[user_id]["send_messages"] is None
                        or after_perms[user_id]["send_messages"] is True
                    )
                    speak = (
                        after_perms[user_id]["speak"] is None
                        or after_perms[user_id]["speak"] is True
                    )
                # explicit is better than implicit :thinkies:
                if user_id in before_perms and (
                    user_id not in after_perms or any((send_messages, speak))
                ):
                    user = after.guild.get_member(user_id)
                    send_dm_notification = True
                    if not user:
                        send_dm_notification = False
                        user = discord.Object(id=user_id)
                    log.debug(f"{user} - {type(user)}")
                    to_del.append(user_id)
                    log.debug("creating case")
                    if isinstance(after, discord.VoiceChannel):
                        unmute_type = "vunmute"
                        notification_title = ("Voice unmute")
                    else:
                        unmute_type = "cunmute"
                        notification_title = ("Channel unmute")
                    if send_dm_notification:
                        await self._send_dm_notification(
                            user,
                            None,
                            after.guild,
                            notification_title,
                            ("Manually removed channel overwrites"),
                        )
                    await modlog.create_case(
                        self.bot,
                        after.guild,
                        datetime.now(timezone.utc),
                        unmute_type,
                        user,
                        None,
                        ("Manually removed channel overwrites"),
                        until=None,
                        channel=after,
                    )
                    log.debug("created case")
            if to_del:
                for u_id in to_del:
                    del self._channel_mutes[after.id][u_id]
                await self.mutesconfig.channel(after).muted_users.set(self._channel_mutes[after.id])

    @commands.Cog.listener("on_member_join")
    async def mutes_on_member_join(self, member: discord.Member):
        guild = member.guild
        if await self.bot.cog_disabled_in_guild(self, guild):
            return
        mute_role = await self.mutesconfig.guild(guild).mute_role()
        if not mute_role:
            # channel overwrite mutes would quickly allow a malicious
            # user to globally rate limit the bot therefore we are not
            # going to support re-muting users via channel overwrites
            return
        if guild.id in self._server_mutes:
            if member.id in self._server_mutes[guild.id]:
                role = guild.get_role(mute_role)
                if not role:
                    return
                if self._server_mutes[guild.id][member.id]["until"]:
                    until = datetime.fromtimestamp(
                        self._server_mutes[guild.id][member.id]["until"]
                    )
                else:
                    until = None
                await self.mute_user(
                    guild, guild.me, member, until, ("Previously muted in this server.")
                )

    async def _check_for_mute_role(self, ctx: commands.Context) -> bool:
        """
        This explains to the user whether or not mutes are setup correctly for
        automatic unmutes.
        """
        command_1 = f"{ctx.clean_prefix}mutesetrole"
        command_2 = f"{ctx.clean_prefix}muteset makerole"
        msg = (
            "This server does not have a mute role setup. "
            " You can setup a mute role with `{command_1}` or"
            "`{command_2}` if you just want a basic role created setup.\n\n"
        ).format(command_1=command_1, command_2=command_2)
        mute_role_id = await self.mutesconfig.guild(ctx.guild).mute_role()
        mute_role = ctx.guild.get_role(mute_role_id)
        sent_instructions = await self.mutesconfig.guild(ctx.guild).sent_instructions()
        force_role_mutes = await self.mutesconfig.force_role_mutes()
        if force_role_mutes and not mute_role:
            await ctx.send(msg)
            return False

        if mute_role or sent_instructions:
            return True
        else:
            msg += (
                "Channel overwrites for muting users can get expensive on Discord's API "
                "as such we recommend that you have an admin setup a mute role instead. "
                "Channel overwrites will also not re-apply on guild join, so a user "
                "who has been muted may leave and re-join and no longer be muted. "
                "Role mutes do not have this issue.\n\n"
                "Are you sure you want to continue with channel overwrites? "
            )
            can_react = ctx.channel.permissions_for(ctx.me).add_reactions
            if can_react:
                msg += (
                    "Reacting with \N{WHITE HEAVY CHECK MARK} will continue "
                    "the mute with overwrites and stop this message from appearing again, "
                    "Reacting with \N{NEGATIVE SQUARED CROSS MARK} will end the mute attempt."
                )
            else:
                msg += (
                    "Saying `yes` will continue "
                    "the mute with overwrites and stop this message from appearing again, "
                    "saying `no` will end the mute attempt."
                )
            query: discord.Message = await ctx.send(msg)
            if can_react:
                # noinspection PyAsyncCall
                start_adding_reactions(query, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(query, ctx.author)
                event = "reaction_add"
            else:
                pred = MessagePredicate.yes_or_no(ctx)
                event = "message"
            try:
                await ctx.bot.wait_for(event, check=pred, timeout=30)
            except asyncio.TimeoutError:
                await query.delete()
                return False

            if not pred.result:
                if can_react:
                    await query.delete()
                else:
                    await ctx.send(("OK then."))

                return False
            else:
                if can_react:
                    with contextlib.suppress(discord.Forbidden):
                        await query.clear_reactions()
                await self.mutesconfig.guild(ctx.guild).sent_instructions.set(True)
                return True

    @commands.command()
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def activemutes(self, ctx: commands.Context):
        """
        Displays active mutes on this server.
        """
        msg = ""
        if ctx.guild.id in self._server_mutes:
            mutes_data = self._server_mutes[ctx.guild.id]
            if mutes_data:

                msg += ("__Server Mutes__\n")
                for user_id, mutes in mutes_data.items():
                    if not mutes:
                        continue
                    user = ctx.guild.get_member(user_id)
                    if not user:
                        user_str = f"<@!{user_id}>"
                    else:
                        user_str = user.mention
                    if mutes["until"]:
                        time_left = timedelta(
                            seconds=mutes["until"] - datetime.now(timezone.utc).timestamp()
                        )
                        time_str = humanize_timedelta(timedelta=time_left)
                    else:
                        time_str = ""
                    msg += f"{user_str} "
                    if time_str:
                        msg += ("__Remaining__: {time_left}\n").format(time_left=time_str)
                    else:
                        msg += "\n"
        for channel_id, mutes_data in self._channel_mutes.items():
            if not mutes_data:
                continue
            if ctx.guild.get_channel(channel_id):
                msg += ("__<#{channel_id}> Mutes__\n").format(channel_id=channel_id)
                for user_id, mutes in mutes_data.items():
                    if not mutes:
                        continue
                    user = ctx.guild.get_member(user_id)
                    if not user:
                        user_str = f"<@!{user_id}>"
                    else:
                        user_str = user.mention
                    if mutes["until"]:
                        time_left = timedelta(
                            seconds=mutes["until"] - datetime.now(timezone.utc).timestamp()
                        )
                        time_str = humanize_timedelta(timedelta=time_left)
                    else:
                        time_str = ""
                    msg += f"{user_str} "
                    if time_str:
                        msg += ("__Remaining__: {time_left}\n").format(time_left=time_str)
                    else:
                        msg += "\n"
        if msg:
            for page in pagify(msg):
                await ctx.maybe_send_embed(page)
            return
        await ctx.maybe_send_embed(("There are no mutes on this server right now."))

    @commands.command(usage="<users...> [time_and_reason]")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def mute(
        self,
        ctx: commands.Context,
        users: commands.Greedy[discord.Member],
        *,
        time_and_reason: MuteTime = {},
    ):
        """Mute users.

        `<users...>` is a space separated list of usernames, ID's, or mentions.
        `[time_and_reason]` is the time to mute for and reason. Time is
        any valid time length such as `30 minutes` or `2 days`. If nothing
        is provided the mute will use the set default time or indefinite if not set.

        Examples:
        `[p]mute @member1 @member2 spam 5 hours`
        `[p]mute @member1 3 days`

        """
        if not users:
            return await ctx.send_help()
        if ctx.me in users:
            return await ctx.send(("You cannot mute me."))
        if ctx.author in users:
            return await ctx.send(("You cannot mute yourself."))

        if not await self._check_for_mute_role(ctx):
            return
        async with ctx.typing():
            duration = time_and_reason.get("duration", None)
            reason = time_and_reason.get("reason", None)
            time = ""
            until = None
            if duration:
                until = datetime.now(timezone.utc) + duration
                time = (" for {duration}").format(duration=humanize_timedelta(timedelta=duration))
            else:
                default_duration = await self.mutesconfig.guild(ctx.guild).default_time()
                if default_duration:
                    until = datetime.now(timezone.utc) + timedelta(seconds=default_duration)
                    time = (" for {duration}").format(
                        duration=humanize_timedelta(timedelta=timedelta(seconds=default_duration))
                    )
            author = ctx.message.author
            guild = ctx.guild
            audit_reason = get_audit_reason(author, reason, shorten=True)
            success_list = []
            issue_list = []
            for user in users:
                success = await self.mute_user(guild, author, user, until, audit_reason)
                if success["success"]:
                    success_list.append(user)
                    if success["channels"]:
                        # incase we only muted a user in 1 channel not all
                        issue_list.append(success)
                    await modlog.create_case(
                        self.bot,
                        guild,
                        ctx.message.created_at.replace(tzinfo=timezone.utc),
                        "smute",
                        user,
                        author,
                        reason,
                        until=until,
                        channel=None,
                    )
                    await self._send_dm_notification(
                        user, author, guild, ("Server mute"), reason, duration
                    )
                else:
                    issue_list.append(success)
        if success_list:
            if ctx.guild.id not in self._server_mutes:
                self._server_mutes[ctx.guild.id] = {}
            msg = ("{users} has been muted in this server{time}.")
            if len(success_list) > 1:
                msg = ("{users} have been muted in this server{time}.")
            await ctx.send(
                msg.format(users=humanize_list([f"{u}" for u in success_list]), time=time)
            )
        if issue_list:
            await self.handle_issues(ctx, issue_list)

    def parse_issues(self, issue_list: dict) -> str:
        reasons = {}
        reason_msg = issue_list["reason"] + "\n" if issue_list["reason"] else None
        channel_msg = ""
        error_msg = ("{member} could not be (un)muted for the following reasons:\n").format(
            member=issue_list["user"]
        )
        if issue_list["channels"]:
            for channel, reason in issue_list["channels"]:
                if reason not in reasons:
                    reasons[reason] = [channel]
                else:
                    reasons[reason].append(channel)

            for reason, channel_list in reasons.items():
                channel_msg += ("- {reason} In the following channels: {channels}\n").format(
                    reason=reason,
                    channels=humanize_list([c.mention for c in channel_list]),
                )
        error_msg += reason_msg or channel_msg
        return error_msg

    async def handle_issues(self, ctx: commands.Context, issue_list: List[dict]) -> None:
        """
        This is to handle the various issues that can return for each user/channel
        """
        message = (
            "Some users could not be properly muted. Would you like to see who, where, and why?"
        )

        can_react = ctx.channel.permissions_for(ctx.me).add_reactions
        if not can_react:
            message += " (y/n)"
        query: discord.Message = await ctx.send(message)
        if can_react:
            # noinspection PyAsyncCall
            start_adding_reactions(query, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(query, ctx.author)
            event = "reaction_add"
        else:
            pred = MessagePredicate.yes_or_no(ctx)
            event = "message"
        try:
            await ctx.bot.wait_for(event, check=pred, timeout=30)
        except asyncio.TimeoutError:
            await query.delete()
            return

        if not pred.result:
            if can_react:
                await query.delete()
            else:
                await ctx.send(("OK then."))
            return
        else:
            if can_react:
                with contextlib.suppress(discord.Forbidden):
                    await query.clear_reactions()
            issue = "\n".join(self.parse_issues(issue) for issue in issue_list)
            resp = pagify(issue)
            await ctx.send_interactive(resp)

    @commands.command(
        name="mutechannel", aliases=["channelmute"], usage="<users...> [time_and_reason]"
    )
    @checks.mod_or_permissions(manage_roles=True)
    @commands.bot_has_guild_permissions(manage_permissions=True)
    async def channel_mute(
        self,
        ctx: commands.Context,
        users: commands.Greedy[discord.Member],
        *,
        time_and_reason: MuteTime = {},
    ):
        """Mute a user in the current text channel.

        `<users...>` is a space separated list of usernames, ID's, or mentions.
        `[time_and_reason]` is the time to mute for and reason. Time is
        any valid time length such as `30 minutes` or `2 days`. If nothing
        is provided the mute will use the set default time or indefinite if not set.

        Examples:
        `[p]mutechannel @member1 @member2 spam 5 hours`
        `[p]mutechannel @member1 3 days`
        """
        if not users:
            return await ctx.send_help()
        if ctx.me in users:
            return await ctx.send(("You cannot mute me."))
        if ctx.author in users:
            return await ctx.send(("You cannot mute yourself."))
        async with ctx.typing():
            duration = time_and_reason.get("duration", None)
            reason = time_and_reason.get("reason", None)
            time = ""
            until = None
            if duration:
                until = datetime.now(timezone.utc) + duration
                time = (" for {duration}").format(duration=humanize_timedelta(timedelta=duration))
            else:
                default_duration = await self.mutesconfig.guild(ctx.guild).default_time()
                if default_duration:
                    until = datetime.now(timezone.utc) + timedelta(seconds=default_duration)
                    time = (" for {duration}").format(
                        duration=humanize_timedelta(timedelta=timedelta(seconds=default_duration))
                    )
            author = ctx.message.author
            channel = ctx.message.channel
            guild = ctx.guild
            audit_reason = get_audit_reason(author, reason, shorten=True)
            issue_list = []
            success_list = []
            for user in users:
                success = await self.channel_mute_user(
                    guild, channel, author, user, until, audit_reason
                )
                if success["success"]:
                    success_list.append(user)

                    await modlog.create_case(
                        self.bot,
                        guild,
                        ctx.message.created_at.replace(tzinfo=timezone.utc),
                        "cmute",
                        user,
                        author,
                        reason,
                        until=until,
                        channel=channel,
                    )
                    await self._send_dm_notification(
                        user, author, guild, ("Channel mute"), reason, duration
                    )
                    async with self.mutesconfig.member(user).perms_cache() as cache:
                        cache[channel.id] = success["old_overs"]
                else:
                    issue_list.append((user, success["reason"]))

        if success_list:
            msg = ("{users} has been muted in this channel{time}.")
            if len(success_list) > 1:
                msg = ("{users} have been muted in this channel{time}.")
            await channel.send(
                msg.format(users=humanize_list([f"{u}" for u in success_list]), time=time)
            )
        if issue_list:
            msg = ("The following users could not be muted\n")
            for user, issue in issue_list:
                msg += f"{user}: {issue}\n"
            await ctx.send_interactive(pagify(msg))

    @commands.command(usage="<users...> [reason]")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def unmute(
        self,
        ctx: commands.Context,
        users: commands.Greedy[discord.Member],
        *,
        reason: Optional[str] = None,
    ):
        """Unmute users.

        `<users...>` is a space separated list of usernames, ID's, or mentions.
        `[reason]` is the reason for the unmute.
        """
        if not users:
            return await ctx.send_help()
        if ctx.me in users:
            return await ctx.send(("You cannot unmute me."))
        if ctx.author in users:
            return await ctx.send(("You cannot unmute yourself."))
        if not await self._check_for_mute_role(ctx):
            return
        async with ctx.typing():
            guild = ctx.guild
            author = ctx.author
            audit_reason = get_audit_reason(author, reason, shorten=True)
            issue_list = []
            success_list = []
            if guild.id in self._channel_mute_events:
                self._channel_mute_events[guild.id].clear()
            else:
                self._channel_mute_events[guild.id] = asyncio.Event()
            for user in users:
                success = await self.unmute_user(guild, author, user, audit_reason)

                if success["success"]:
                    success_list.append(user)
                    await modlog.create_case(
                        self.bot,
                        guild,
                        ctx.message.created_at.replace(tzinfo=timezone.utc),
                        "sunmute",
                        user,
                        author,
                        reason,
                        until=None,
                    )
                    await self._send_dm_notification(
                        user, author, guild, ("Server unmute"), reason
                    )
                else:
                    issue_list.append(success)
        self._channel_mute_events[guild.id].set()
        if success_list:
            if ctx.guild.id in self._server_mutes and self._server_mutes[ctx.guild.id]:
                await self.mutesconfig.guild(ctx.guild).muted_users.set(
                    self._server_mutes[ctx.guild.id]
                )
            else:
                await self.mutesconfig.guild(ctx.guild).muted_users.clear()
            await ctx.send(
                ("{users} unmuted in this server.").format(
                    users=humanize_list([f"{u}" for u in success_list])
                )
            )
        if issue_list:
            await self.handle_issues(ctx, issue_list)

    @checks.mod_or_permissions(manage_roles=True)
    @commands.command(name="unmutechannel", aliases=["channelunmute"], usage="<users...> [reason]")
    @commands.bot_has_guild_permissions(manage_permissions=True)
    async def unmute_channel(
        self,
        ctx: commands.Context,
        users: commands.Greedy[discord.Member],
        *,
        reason: Optional[str] = None,
    ):
        """Unmute a user in this channel.

        `<users...>` is a space separated list of usernames, ID's, or mentions.
        `[reason]` is the reason for the unmute.
        """
        if not users:
            return await ctx.send_help()
        if ctx.me in users:
            return await ctx.send(("You cannot unmute me."))
        if ctx.author in users:
            return await ctx.send(("You cannot unmute yourself."))
        async with ctx.typing():
            channel = ctx.channel
            author = ctx.author
            guild = ctx.guild
            audit_reason = get_audit_reason(author, reason, shorten=True)
            success_list = []
            issue_list = []
            for user in users:
                success = await self.channel_unmute_user(
                    guild, channel, author, user, audit_reason
                )

                if success["success"]:
                    success_list.append(user)
                    await modlog.create_case(
                        self.bot,
                        guild,
                        ctx.message.created_at.replace(tzinfo=timezone.utc),
                        "cunmute",
                        user,
                        author,
                        reason,
                        until=None,
                        channel=channel,
                    )
                    await self._send_dm_notification(
                        user, author, guild, ("Channel unmute"), reason
                    )
                else:
                    issue_list.append((user, success["reason"]))
        if success_list:
            if channel.id in self._channel_mutes and self._channel_mutes[channel.id]:
                await self.mutesconfig.channel(channel).muted_users.set(self._channel_mutes[channel.id])
            else:
                await self.mutesconfig.channel(channel).muted_users.clear()
            await ctx.send(
                ("{users} unmuted in this channel.").format(
                    users=humanize_list([f"{u}" for u in success_list])
                )
            )
        if issue_list:
            msg = ("The following users could not be unmuted\n")
            for user, issue in issue_list:
                msg += f"{user}: {issue}\n"
            await ctx.send_interactive(pagify(msg))

    async def mute_user(
        self,
        guild: discord.Guild,
        author: discord.Member,
        user: discord.Member,
        until: Optional[datetime] = None,
        reason: Optional[str] = None,
    ) -> Dict[
        str, Optional[Union[List[Tuple[discord.abc.GuildChannel, str]], discord.Member, bool, str]]
    ]:
        """
        Handles muting users
        """
        permissions = user.guild_permissions
        ret: Dict[
            str,
            Union[bool, Optional[str], List[Tuple[discord.abc.GuildChannel, str]], discord.Member],
        ] = {
            "success": False,
            "reason": None,
            "channels": [],
            "user": user,
        }
        # TODO: This typing is ugly and should probably be an object on its own
        # along with this entire method and some othe refactorization
        # v1.0.0 is meant to look ugly right :')
        if permissions.administrator:
            ret["reason"] = (MUTE_UNMUTE_ISSUES["is_admin"])
            return ret
        if not await self.is_allowed_by_hierarchy(guild, author, user):
            ret["reason"] = (MUTE_UNMUTE_ISSUES["hierarchy_problem"])
            return ret
        mute_role = await self.mutesconfig.guild(guild).mute_role()

        if mute_role:

            role = guild.get_role(mute_role)
            if not role:
                ret["reason"] = (MUTE_UNMUTE_ISSUES["role_missing"])
                return ret
            if author != guild.owner and role >= author.top_role:
                ret["reason"] = (MUTE_UNMUTE_ISSUES["assigned_role_hierarchy_problem"])
                return ret
            if not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role:
                ret["reason"] = (MUTE_UNMUTE_ISSUES["permissions_issue_role"])
                return ret
            # This is here to prevent the modlog case from happening on role updates
            # we need to update the cache early so it's there before we receive the member_update event
            if guild.id not in self._server_mutes:
                self._server_mutes[guild.id] = {}

            self._server_mutes[guild.id][user.id] = {
                "author": author.id,
                "member": user.id,
                "until": until.timestamp() if until else None,
            }
            try:
                await user.add_roles(role, reason=reason)
                await self.mutesconfig.guild(guild).muted_users.set(self._server_mutes[guild.id])
            except discord.errors.Forbidden:
                if guild.id in self._server_mutes and user.id in self._server_mutes[guild.id]:
                    del self._server_mutes[guild.id][user.id]
                ret["reason"] = (MUTE_UNMUTE_ISSUES["permissions_issue_role"])
                return ret
            ret["success"] = True
            return ret
        else:
            perms_cache = {}
            tasks = []
            for channel in guild.channels:
                tasks.append(self.channel_mute_user(guild, channel, author, user, until, reason))
            task_result = await bounded_gather(*tasks)
            for task in task_result:
                if not task["success"]:
                    ret["channels"].append((task["channel"], task["reason"]))
                else:
                    chan_id = task["channel"].id
                    perms_cache[str(chan_id)] = task.get("old_overs")
                    ret["success"] = True
            await self.mutesconfig.member(user).perms_cache.set(perms_cache)
            return ret

    async def unmute_user(
        self,
        guild: discord.Guild,
        author: discord.Member,
        user: discord.Member,
        reason: Optional[str] = None,
    ) -> Dict[
        str,
        Union[bool, Optional[str], List[Tuple[discord.abc.GuildChannel, str]], discord.Member],
    ]:
        """
        Handles unmuting users
        """
        ret: Dict[
            str,
            Union[bool, Optional[str], List[Tuple[discord.abc.GuildChannel, str]], discord.Member],
        ] = {
            "success": False,
            "reason": None,
            "channels": [],
            "user": user,
        }
        mute_role = await self.mutesconfig.guild(guild).mute_role()
        if not await self.is_allowed_by_hierarchy(guild, author, user):
            ret["reason"] = (MUTE_UNMUTE_ISSUES["hierarchy_problem"])
            return ret
        if mute_role:

            role = guild.get_role(mute_role)
            if not role:
                ret["reason"] = (MUTE_UNMUTE_ISSUES["role_missing"])
                return ret

            if guild.id in self._server_mutes:
                if user.id in self._server_mutes[guild.id]:
                    del self._server_mutes[guild.id][user.id]
            if not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role:
                ret["reason"] = (MUTE_UNMUTE_ISSUES["permissions_issue_role"])
                return ret
            try:
                await user.remove_roles(role, reason=reason)
            except discord.errors.Forbidden:
                ret["reason"] = (MUTE_UNMUTE_ISSUES["permissions_issue_role"])
                return ret
            ret["success"] = True
            return ret
        else:
            tasks = []
            for channel in guild.channels:
                tasks.append(self.channel_unmute_user(guild, channel, author, user, reason))
            results = await bounded_gather(*tasks)
            for task in results:
                if not task["success"]:
                    ret["channels"].append((task["channel"], task["reason"]))
                else:
                    ret["success"] = True
            await self.mutesconfig.member(user).clear()
            return ret

    async def channel_mute_user(
        self,
        guild: discord.Guild,
        channel: discord.abc.GuildChannel,
        author: discord.Member,
        user: discord.Member,
        until: Optional[datetime] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Optional[Union[discord.abc.GuildChannel, str, bool]]]:
        """Mutes the specified user in the specified channel"""
        overwrites = channel.overwrites_for(user)
        permissions = channel.permissions_for(user)

        if permissions.administrator:
            return {
                "success": False,
                "channel": channel,
                "reason": (MUTE_UNMUTE_ISSUES["is_admin"]),
            }

        new_overs: dict = {}
        move_channel = False
        new_overs.update(send_messages=False, add_reactions=False, speak=False)
        send_reason = None
        if user.voice and user.voice.channel:
            if channel.permissions_for(guild.me).move_members:
                move_channel = True
            else:
                send_reason = (MUTE_UNMUTE_ISSUES["voice_mute_permission"])

        if not await self.is_allowed_by_hierarchy(guild, author, user):
            return {
                "success": False,
                "channel": channel,
                "reason": (MUTE_UNMUTE_ISSUES["hierarchy_problem"]),
            }

        old_overs = {k: getattr(overwrites, k) for k in new_overs}
        overwrites.update(**new_overs)
        if channel.id not in self._channel_mutes:
            self._channel_mutes[channel.id] = {}
        if user.id in self._channel_mutes[channel.id]:
            return {
                "success": False,
                "channel": channel,
                "reason": (MUTE_UNMUTE_ISSUES["already_muted"]),
            }
        if not channel.permissions_for(guild.me).manage_permissions:
            return {
                "success": False,
                "channel": channel,
                "reason": (MUTE_UNMUTE_ISSUES["permissions_issue_channel"]),
            }
        self._channel_mutes[channel.id][user.id] = {
            "author": author.id,
            "member": user.id,
            "until": until.timestamp() if until else None,
        }
        try:
            await channel.set_permissions(user, overwrite=overwrites, reason=reason)
            async with self.mutesconfig.channel(channel).muted_users() as muted_users:
                muted_users[str(user.id)] = self._channel_mutes[channel.id][user.id]
        except discord.NotFound as e:
            if channel.id in self._channel_mutes and user.id in self._channel_mutes[channel.id]:
                del self._channel_mutes[channel.id][user.id]
            if e.code == 10003:
                if (
                    channel.id in self._channel_mutes
                    and user.id in self._channel_mutes[channel.id]
                ):
                    del self._channel_mutes[channel.id][user.id]
                return {
                    "success": False,
                    "channel": channel,
                    "reason": (MUTE_UNMUTE_ISSUES["unknown_channel"]),
                }
            elif e.code == 10009:
                if (
                    channel.id in self._channel_mutes
                    and user.id in self._channel_mutes[channel.id]
                ):
                    del self._channel_mutes[channel.id][user.id]
                return {
                    "success": False,
                    "channel": channel,
                    "reason": (MUTE_UNMUTE_ISSUES["left_guild"]),
                }
        if move_channel:
            try:
                await user.move_to(channel)
            except discord.HTTPException:
                # catch all discord errors because the result will be the same
                # we successfully muted by this point but can't move the user
                return {
                    "success": True,
                    "channel": channel,
                    "reason": (MUTE_UNMUTE_ISSUES["voice_mute_permission"]),
                    "old_overs": old_overs,
                }
        return {"success": True, "channel": channel, "old_overs": old_overs, "reason": send_reason}

    async def channel_unmute_user(
        self,
        guild: discord.Guild,
        channel: discord.abc.GuildChannel,
        author: discord.Member,
        user: discord.Member,
        reason: Optional[str] = None,
    ) -> Dict[str, Optional[Union[discord.abc.GuildChannel, str, bool]]]:
        """Unmutes the specified user in a specified channel"""
        overwrites = channel.overwrites_for(user)
        perms_cache = await self.mutesconfig.member(user).perms_cache()

        move_channel = False
        if channel.id in perms_cache:
            old_values = perms_cache[channel.id]
        else:
            old_values = {"send_messages": None, "add_reactions": None, "speak": None}

        if user.voice and user.voice.channel:
            if channel.permissions_for(guild.me).move_members:
                move_channel = True

        if not await self.is_allowed_by_hierarchy(guild, author, user):
            return {
                "success": False,
                "channel": channel,
                "reason": (MUTE_UNMUTE_ISSUES["hierarchy_problem"]),
            }

        overwrites.update(**old_values)
        if channel.id in self._channel_mutes and user.id in self._channel_mutes[channel.id]:
            del self._channel_mutes[channel.id][user.id]
        else:
            return {
                "success": False,
                "channel": channel,
                "reason": (MUTE_UNMUTE_ISSUES["already_unmuted"]),
            }
        if not channel.permissions_for(guild.me).manage_permissions:
            return {
                "success": False,
                "channel": channel,
                "reason": (MUTE_UNMUTE_ISSUES["permissions_issue_channel"]),
            }
        try:
            if overwrites.is_empty():
                await channel.set_permissions(
                    user, overwrite=cast(discord.PermissionOverwrite, None), reason=reason
                )
            else:
                await channel.set_permissions(user, overwrite=overwrites, reason=reason)
            async with self.mutesconfig.channel(channel).muted_users() as muted_users:
                if str(user.id) in muted_users:
                    del muted_users[str(user.id)]
        except discord.NotFound as e:
            if e.code == 10003:
                return {
                    "success": False,
                    "channel": channel,
                    "reason": (MUTE_UNMUTE_ISSUES["unknown_channel"]),
                }
            elif e.code == 10009:
                return {
                    "success": False,
                    "channel": channel,
                    "reason": (MUTE_UNMUTE_ISSUES["left_guild"]),
                }
        if move_channel:
            try:
                await user.move_to(channel)
            except discord.HTTPException:
                # catch all discord errors because the result will be the same
                # we successfully muted by this point but can't move the user
                return {
                    "success": True,
                    "channel": channel,
                    "reason": (MUTE_UNMUTE_ISSUES["voice_mute_permission"]),
                }
        return {"success": True, "channel": channel, "reason": None}