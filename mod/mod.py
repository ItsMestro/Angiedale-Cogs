import asyncio
import contextlib
import logging
from abc import ABC
from collections import defaultdict, namedtuple
from copy import copy
from datetime import datetime, timezone
from typing import Dict, Literal, Union, cast

import discord
from redbot.core import Config, checks, commands, modlog
from redbot.core.bot import Red
from redbot.core.commands import UserInputOptional
from redbot.core.utils import AsyncIter
from redbot.core.utils._internal_utils import send_to_owners_with_prefix_replaced
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.mod import get_audit_reason

from .cleanup import Cleanup
from .events import Events
from .filter import Filter
from .helpers import warning_points_add_check, warning_points_remove_check
from .info import Info
from .kickban import KickBanMixin
from .mutes import Mutes
from .slowmode import Slowmode
from .voicemutes import VoiceMutes

log = logging.getLogger("red.angiedale.mod")


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass


class Mod(
    Events,
    KickBanMixin,
    Slowmode,
    Filter,
    Cleanup,
    Info,
    Mutes,
    VoiceMutes,
    commands.Cog,
    metaclass=CompositeMetaClass,
):
    """Moderation tools."""

    default_global_settings = {
        "version": "",
        "track_all_names": True,
    }

    default_guild_settings = {
        "mention_spam": {"ban": None, "kick": None, "warn": None, "strict": False},
        "delete_repeats": -1,
        "ignored": False,
        "respect_hierarchy": True,
        "delete_delay": -1,
        "reinvite_on_unban": False,
        "current_tempbans": [],
        "dm_on_kickban": False,
        "default_days": 0,
        "default_tempban_duration": 60 * 60 * 24,
        "track_nicknames": True,
    }

    default_channel_settings = {
        "ignored": False,
    }

    default_member_settings = {
        "past_nicks": [],
        "perms_cache": {},
        "banned_until": False,
    }

    default_user_settings = {
        "past_names": [],
    }

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot

        self.filterconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Filter"
        )

        self.warnconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Warnings"
        )

        self.pattern_cache = {}

        self.config = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Mod"
        )
        self.config.register_global(**self.default_global_settings)
        self.config.register_guild(**self.default_guild_settings)
        self.config.register_channel(**self.default_channel_settings)
        self.config.register_member(**self.default_member_settings)
        self.config.register_user(**self.default_user_settings)
        self.cache: dict = {}
        self.tban_expiry_task = asyncio.create_task(self.tempban_expirations_task())
        self.last_case: dict = defaultdict(dict)

        self.mutesconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Mutes"
        )
        default_guild_mutes = {
            "sent_instructions": False,
            "mute_role": None,
            "notification_channel": None,
            "muted_users": {},
            "default_time": 0,
            "dm": False,
            "show_mod": False,
        }
        self.mutesconfig.register_global(force_role_mutes=True, schema_version=0)
        # Tbh I would rather force everyone to use role mutes.
        # I also honestly think everyone would agree they're the
        # way to go. If for whatever reason someone wants to
        # enable channel overwrite mutes for their bot they can.
        # Channel overwrite logic still needs to be in place
        # for channel mutes methods.
        self.mutesconfig.register_guild(**default_guild_mutes)
        self.mutesconfig.register_member(perms_cache={})
        self.mutesconfig.register_channel(muted_users={})
        self._server_mutes: Dict[int, Dict[int, dict]] = {}
        self._channel_mutes: Dict[int, Dict[int, dict]] = {}
        self._readymutes = asyncio.Event()
        self._unmute_tasks: Dict[str, asyncio.Task] = {}
        self._unmute_task = None
        self.mute_role_cache: Dict[int, int] = {}
        self._channel_mute_events: Dict[int, asyncio.Event] = {}
        # this is a dict of guild ID's and asyncio.Events
        # to wait for a guild to finish channel unmutes before
        # checking for manual overwrites

        self._ready = asyncio.Event()

        self._init_task = self.bot.loop.create_task(self._initialize())

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        """Mutes are considered somewhat critical
        Therefore the only data that we should delete
        is that which comes from discord requesting us to
        remove data about a user
        """
        if requester != "discord_deleted_user":
            return

        await self._readymutes.wait()
        all_members = await self.mutesconfig.all_members()
        for g_id, data in all_members.items():
            for m_id, mutes in data.items():
                if m_id == user_id:
                    await self.mutesconfig.member_from_ids(g_id, m_id).clear()

        all_members = await self.config.all_members()

        async for guild_id, guild_data in AsyncIter(all_members.items(), steps=100):
            if user_id in guild_data:
                await self.config.member_from_ids(guild_id, user_id).clear()

        await self.config.user_from_id(user_id).clear()

        guild_data = await self.config.all_guilds()

        async for guild_id, guild_data in AsyncIter(guild_data.items(), steps=100):
            if user_id in guild_data["current_tempbans"]:
                async with self.config.guild_from_id(guild_id).current_tempbans() as tbs:
                    try:
                        tbs.remove(user_id)
                    except ValueError:
                        pass
                    # possible with a context switch between here and getting all guilds

    async def _initialize(self):
        await self.bot.wait_until_red_ready()
        await self._maybe_update_config()

        await self.register_casetypes()

        guild_data = await self.mutesconfig.all_guilds()
        for g_id, mutes in guild_data.items():
            self._server_mutes[g_id] = {}
            if mutes["mute_role"]:
                self.mute_role_cache[g_id] = mutes["mute_role"]
            for user_id, mute in mutes["muted_users"].items():
                self._server_mutes[g_id][int(user_id)] = mute
        channel_data = await self.mutesconfig.all_channels()
        for c_id, mutes in channel_data.items():
            self._channel_mutes[c_id] = {}
            for user_id, mute in mutes["muted_users"].items():
                self._channel_mutes[c_id][int(user_id)] = mute
        self._unmute_task = asyncio.create_task(self._handle_automatic_unmute())
        self._readymutes.set()
        self._ready.set()

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        await self._ready.wait()
        await self._readymutes.wait()

    def cog_unload(self):
        self._init_task.cancel()
        self.tban_expiry_task.cancel()
        self._unmute_task.cancel()
        for task in self._unmute_tasks.values():
            task.cancel()

    async def _maybe_update_config(self):
        """Maybe update `delete_delay` value set by Config prior to Mod 1.0.0."""
        if not await self.config.version():
            guild_dict = await self.config.all_guilds()
            async for guild_id, info in AsyncIter(guild_dict.items(), steps=25):
                delete_repeats = info.get("delete_repeats", False)
                if delete_repeats:
                    val = 3
                else:
                    val = -1
                await self.config.guild_from_id(guild_id).delete_repeats.set(val)
            await self.config.version.set("1.0.0")  # set version of last update
        if await self.config.version() < "1.1.0":
            message_sent = False
            async for e in AsyncIter((await self.config.all_channels()).values(), steps=25):
                if e["ignored"] is not False:
                    msg = (
                        "Ignored guilds and channels have been moved. "
                        "Please use `[p]moveignoredchannels` to migrate the old settings."
                    )
                    self.bot.loop.create_task(send_to_owners_with_prefix_replaced(self.bot, msg))
                    message_sent = True
                    break
            if message_sent is False:
                async for e in AsyncIter((await self.config.all_guilds()).values(), steps=25):
                    if e["ignored"] is not False:
                        msg = (
                            "Ignored guilds and channels have been moved. "
                            "Please use `[p]moveignoredchannels` to migrate the old settings."
                        )
                        self.bot.loop.create_task(
                            send_to_owners_with_prefix_replaced(self.bot, msg)
                        )
                        break
            await self.config.version.set("1.1.0")
        if await self.config.version() < "1.2.0":
            async for e in AsyncIter((await self.config.all_guilds()).values(), steps=25):
                if e["delete_delay"] != -1:
                    msg = (
                        "Delete delay settings have been moved. "
                        "Please use `[p]movedeletedelay` to migrate the old settings."
                    )
                    self.bot.loop.create_task(send_to_owners_with_prefix_replaced(self.bot, msg))
                    break
            await self.config.version.set("1.2.0")
        if await self.config.version() < "1.3.0":
            guild_dict = await self.config.all_guilds()
            async for guild_id in AsyncIter(guild_dict.keys(), steps=25):
                async with self.config.guild_from_id(guild_id).all() as guild_data:
                    current_state = guild_data.pop("ban_mention_spam", False)
                    if current_state is not False:
                        if "mention_spam" not in guild_data:
                            guild_data["mention_spam"] = {}
                        guild_data["mention_spam"]["ban"] = current_state
            await self.config.version.set("1.3.0")

        schema_version = await self.config.schema_version()

        if schema_version == 0:
            await self._schema_0_to_1()
            schema_version += 1
            await self.config.schema_version.set(schema_version)

    async def _schema_0_to_1(self):
        """This contains conversion that adds guild ID to channel mutes data."""
        all_channels = await self.config.all_channels()
        if not all_channels:
            return

        start = datetime.now()
        log.info(
            "Config conversion to schema_version 1 started. This may take a while to proceed..."
        )
        async for channel_id in AsyncIter(all_channels.keys()):
            try:
                if (channel := self.bot.get_channel(channel_id)) is None:
                    channel = await self.bot.fetch_channel(channel_id)
                async with self.config.channel_from_id(channel_id).muted_users() as muted_users:
                    for mute_id, mute_data in muted_users.items():
                        mute_data["guild"] = channel.guild.id
            except (discord.NotFound, discord.Forbidden):
                await self.config.channel_from_id(channel_id).clear()

        log.info(
            "Config conversion to schema_version 1 done. It took %s to proceed.",
            datetime.now() - start,
        )

    async def mute_role_helper(self, ctx, role):
        """handle [p]muteset role"""

        if not role:
            await self.mutesconfig.guild(ctx.guild).mute_role.set(None)
            if ctx.guild.id in self.mute_role_cache:
                del self.mute_role_cache[ctx.guild.id]
            await self.mutesconfig.guild(ctx.guild).sent_instructions.set(False)
            # reset this to warn users next time they may have accidentally
            # removed the mute role
            await ctx.send(("Channel overwrites will be used for mutes instead."))
        else:
            if role >= ctx.author.top_role:
                await ctx.send(
                    ("You can't set this role as it is not lower than you in the role hierarchy.")
                )
                return
            await self.mutesconfig.guild(ctx.guild).mute_role.set(role.id)
            self.mute_role_cache[ctx.guild.id] = role.id
            await ctx.send(("Mute role set to {role}").format(role=role.name))
        if not await self.mutesconfig.guild(ctx.guild).notification_channel():
            command_1 = f"`{ctx.clean_prefix}muteset errornotification`"
            await ctx.send(
                (
                    "No notification channel has been setup, "
                    "use {command_1} to be updated when there's an issue in automatic unmutes."
                ).format(command_1=command_1)
            )

    # @commands.command()
    # @commands.is_owner()
    # async def moveignoredchannels(self, ctx: commands.Context) -> None:
    #     """Move ignored channels and servers to core"""
    #     all_guilds = await self.config.all_guilds()
    #     all_channels = await self.config.all_channels()
    #     for guild_id, settings in all_guilds.items():
    #         await self.bot._config.guild_from_id(guild_id).ignored.set(settings["ignored"])
    #         await self.config.guild_from_id(guild_id).ignored.clear()
    #     for channel_id, settings in all_channels.items():
    #         await self.bot._config.channel_from_id(channel_id).ignored.set(settings["ignored"])
    #         await self.config.channel_from_id(channel_id).clear()
    #     await ctx.send(("Ignored channels and guilds restored."))

    # @commands.command()
    # @commands.is_owner()
    # async def movedeletedelay(self, ctx: commands.Context) -> None:
    #     """
    #     Move deletedelay settings to core
    #     """
    #     all_guilds = await self.config.all_guilds()
    #     for guild_id, settings in all_guilds.items():
    #         await self.bot._config.guild_from_id(guild_id).delete_delay.set(
    #             settings["delete_delay"]
    #         )
    #         await self.config.guild_from_id(guild_id).delete_delay.clear()
    #     await ctx.send(("Delete delay settings restored."))

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(manage_nicknames=True)
    @checks.mod_or_permissions(manage_nicknames=True)
    async def rename(self, ctx: commands.Context, user: discord.Member, *, nickname: str = ""):
        """Change a user's nickname.

        Leaving the nickname empty will remove it.
        """
        nickname = nickname.strip()
        me = cast(discord.Member, ctx.me)
        if not nickname:
            nickname = None
        elif not 2 <= len(nickname) <= 32:
            await ctx.send(("Nicknames must be between 2 and 32 characters long."))
            return
        if not (
            (me.guild_permissions.manage_nicknames or me.guild_permissions.administrator)
            and me.top_role > user.top_role
            and user != ctx.guild.owner
        ):
            await ctx.send(
                (
                    "I do not have permission to rename that member. They may be higher than or "
                    "equal to me in the role hierarchy."
                )
            )
        else:
            try:
                await user.edit(reason=get_audit_reason(ctx.author, None), nick=nickname)
            except discord.Forbidden:
                # Just in case we missed something in the permissions check above
                await ctx.send(("I do not have permission to rename that member."))
            except discord.HTTPException as exc:
                if exc.status == 400:  # BAD REQUEST
                    await ctx.send(("That nickname is invalid."))
                else:
                    await ctx.send(("An unexpected error has occured."))
            else:
                await ctx.send(("Done."))

    @commands.group()
    @commands.guild_only()
    @checks.mod_or_permissions(ban_members=True)
    async def warnlist(self, ctx: commands.Context):
        """Manage settings for Warnings."""
        pass

    @warnlist.command()
    @commands.guild_only()
    async def reasons(self, ctx: commands.Context):
        """List all configured reasons for Warnings."""
        guild = ctx.guild
        guild_settings = self.warnconfig.guild(guild)
        msg_list = []
        async with guild_settings.reasons() as registered_reasons:
            for r, v in registered_reasons.items():
                if await ctx.embed_requested():
                    em = discord.Embed(
                        title=("Reason: {name}").format(name=r),
                        description=v["description"],
                        color=await ctx.embed_colour(),
                    )
                    em.add_field(name=("Points"), value=str(v["points"]))
                    msg_list.append(em)
                else:
                    msg_list.append(
                        (
                            "Name: {reason_name}\nPoints: {points}\nDescription: {description}"
                        ).format(reason_name=r, **v)
                    )
        if msg_list:
            await menu(ctx, msg_list, DEFAULT_CONTROLS)
        else:
            await ctx.send(("There are no reasons configured!"))

    @warnlist.command()
    @commands.guild_only()
    async def actions(self, ctx: commands.Context):
        """List all configured automated actions for Warnings."""
        guild = ctx.guild
        guild_settings = self.warnconfig.guild(guild)
        msg_list = []
        async with guild_settings.actions() as registered_actions:
            for r in registered_actions:
                if await ctx.embed_requested():
                    em = discord.Embed(
                        title=("Action: {name}").format(name=r["action_name"]),
                        color=await ctx.embed_colour(),
                    )
                    em.add_field(name=("Points"), value="{}".format(r["points"]), inline=False)
                    em.add_field(
                        name=("Exceed command"),
                        value=r["exceed_command"],
                        inline=False,
                    )
                    em.add_field(name=("Drop command"), value=r["drop_command"], inline=False)
                    msg_list.append(em)
                else:
                    msg_list.append(
                        (
                            "Name: {action_name}\nPoints: {points}\n"
                            "Exceed command: {exceed_command}\nDrop command: {drop_command}"
                        ).format(**r)
                    )
        if msg_list:
            await menu(ctx, msg_list, DEFAULT_CONTROLS)
        else:
            await ctx.send(("There are no actions configured!"))

    @commands.command()
    @commands.guild_only()
    @checks.mod_or_permissions(ban_members=True)
    async def warn(
        self,
        ctx: commands.Context,
        user: discord.Member,
        points: UserInputOptional[int] = 1,
        *,
        reason: str,
    ):
        """Warn the user for the specified reason.

        `<points>` number of points the warning should be for. If no number is supplied
        1 point will be given. Pre-set warnings disregard this.
        `<reason>` can be a registered reason if it exists or a custom one
        is created by default.
        """
        guild = ctx.guild
        if user == ctx.author:
            return await ctx.send(("You cannot warn yourself."))
        if user.bot:
            return await ctx.send(("You cannot warn other bots."))
        if user == ctx.guild.owner:
            return await ctx.send(("You cannot warn the server owner."))
        if user.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send(
                (
                    "The person you're trying to warn is equal or higher than you in the discord hierarchy, you cannot warn them."
                )
            )
        guild_settings = await self.warnconfig.guild(ctx.guild).all()
        custom_allowed = guild_settings["allow_custom_reasons"]

        reason_type = None
        async with self.warnconfig.guild(ctx.guild).reasons() as registered_reasons:
            if (reason_type := registered_reasons.get(reason.lower())) is None:
                msg = "That is not a registered reason!"
                if custom_allowed:
                    reason_type = {"description": reason, "points": points}
                else:
                    # logic taken from `[p]permissions canrun`
                    fake_message = copy(ctx.message)
                    fake_message.content = f"{ctx.prefix}warningset allowcustomreasons"
                    fake_context = await ctx.bot.get_context(fake_message)
                    com = ctx.bot.get_command("allowcustomreasons")
                    if com:
                        try:
                            can = await com.can_run(
                                fake_context, check_all_parents=True, change_permission_state=False
                            )
                        except commands.CommandError:
                            can = False
                    else:
                        can = False
                    if can:
                        msg += " " + (
                            "Do `{prefix}warningset allowcustomreasons true` to enable custom "
                            "reasons."
                        ).format(prefix=ctx.clean_prefix)
                    return await ctx.send(msg)
        if reason_type is None:
            return
        member_settings = self.warnconfig.member(user)
        current_point_count = await member_settings.total_points()
        warning_to_add = {
            str(ctx.message.id): {
                "points": reason_type["points"],
                "description": reason_type["description"],
                "mod": ctx.author.id,
            }
        }
        async with member_settings.warnings() as user_warnings:
            user_warnings.update(warning_to_add)
        current_point_count += reason_type["points"]
        await member_settings.total_points.set(current_point_count)

        await warning_points_add_check(self.warnconfig, ctx, user, current_point_count)
        dm = guild_settings["toggle_dm"]
        showmod = guild_settings["show_mod"]
        dm_failed = False
        if dm:
            if showmod:
                title = ("Warning from {user}").format(user=ctx.author)
            else:
                title = "Warning"
            em = discord.Embed(
                title=title, description=reason_type["description"], color=await ctx.embed_colour()
            )
            em.add_field(name=("Points"), value=str(reason_type["points"]))
            try:
                await user.send(
                    ("You have received a warning in {guild_name}.").format(
                        guild_name=ctx.guild.name
                    ),
                    embed=em,
                )
            except discord.HTTPException:
                dm_failed = True

        if dm_failed:
            await ctx.send(
                (
                    "A warning for {user} has been issued,"
                    " but I wasn't able to send them a warn message."
                ).format(user=user.mention)
            )

        toggle_channel = guild_settings["toggle_channel"]
        if toggle_channel:
            if showmod:
                title = ("Warning from {user}").format(user=ctx.author)
            else:
                title = "Warning"
            em = discord.Embed(
                title=title, description=reason_type["description"], color=await ctx.embed_colour()
            )
            em.add_field(name=("Points"), value=str(reason_type["points"]))
            warn_channel = self.bot.get_channel(guild_settings["warn_channel"])
            if warn_channel:
                if warn_channel.permissions_for(guild.me).send_messages:
                    with contextlib.suppress(discord.HTTPException):
                        await warn_channel.send(
                            ("{user} has been warned.").format(user=user.mention),
                            embed=em,
                        )

            if not dm_failed:
                if warn_channel:
                    await ctx.tick()
                else:
                    await ctx.send(("{user} has been warned.").format(user=user.mention), embed=em)
        else:
            if not dm_failed:
                await ctx.tick()
        reason_msg = (
            "{reason}\n\nUse `{prefix}unwarn {user} {message}` to remove this warning."
        ).format(
            reason=("{description}\nPoints: {points}").format(
                description=reason_type["description"], points=reason_type["points"]
            ),
            prefix=ctx.clean_prefix,
            user=user.id,
            message=ctx.message.id,
        )
        await modlog.create_case(
            self.bot,
            ctx.guild,
            ctx.message.created_at.replace(tzinfo=timezone.utc),
            "warning",
            user,
            ctx.message.author,
            reason_msg,
            until=None,
            channel=None,
        )

    @commands.command()
    @commands.guild_only()
    @checks.mod()
    async def warnings(self, ctx: commands.Context, user: Union[discord.Member, int]):
        """List the warnings for the specified user."""

        try:
            userid: int = user.id
        except AttributeError:
            userid: int = user
            user = ctx.guild.get_member(userid)
            user = user or namedtuple("Member", "id guild")(userid, ctx.guild)

        msg = ""
        member_settings = self.warnconfig.member(user)
        async with member_settings.warnings() as user_warnings:
            if not user_warnings.keys():  # no warnings for the user
                await ctx.send(("That user has no warnings!"))
            else:
                for key in user_warnings.keys():
                    mod_id = user_warnings[key]["mod"]
                    if mod_id == 0xDE1:
                        mod = "Deleted Moderator"
                    else:
                        bot = ctx.bot
                        mod = bot.get_user(mod_id) or ("Unknown Moderator ({})").format(mod_id)
                    msg += (
                        "{num_points} point warning {reason_name} issued by {user} for "
                        "{description}\n"
                    ).format(
                        num_points=user_warnings[key]["points"],
                        reason_name=key,
                        user=mod,
                        description=user_warnings[key]["description"],
                    )
                await ctx.send_interactive(
                    pagify(msg, shorten_by=58),
                    box_lang=("Warnings for {user}").format(
                        user=user if isinstance(user, discord.Member) else user.id
                    ),
                )

    @commands.command()
    @commands.guild_only()
    @checks.mod_or_permissions(ban_members=True)
    async def unwarn(
        self,
        ctx: commands.Context,
        user: Union[discord.Member, int],
        warn_id: str,
        *,
        reason: str = None,
    ):
        """Remove a warning from a user."""

        guild = ctx.guild

        try:
            user_id = user.id
            member = user
        except AttributeError:
            user_id = user
            member = guild.get_member(user_id)
            member = member or namedtuple("Member", "guild id")(guild, user_id)

        if user_id == ctx.author.id:
            return await ctx.send(("You cannot remove warnings from yourself."))

        member_settings = self.warnconfig.member(member)
        current_point_count = await member_settings.total_points()
        await warning_points_remove_check(self.warnconfig, ctx, member, current_point_count)
        async with member_settings.warnings() as user_warnings:
            if warn_id not in user_warnings.keys():
                return await ctx.send(("That warning doesn't exist!"))
            else:
                current_point_count -= user_warnings[warn_id]["points"]
                await member_settings.total_points.set(current_point_count)
                user_warnings.pop(warn_id)
        await modlog.create_case(
            self.bot,
            ctx.guild,
            ctx.message.created_at.replace(tzinfo=timezone.utc),
            "unwarned",
            member,
            ctx.message.author,
            reason,
            until=None,
            channel=None,
        )

        await ctx.tick()
