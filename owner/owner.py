import asyncio
import json
import logging
import os
import re
import shutil
from abc import ABC
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import choice
from typing import Dict, List, Optional, Union
from zipfile import ZipFile

import discord
from dateutil.easter import easter
from github import Auth, Github
from redbot.core import Config, commands, data_manager
from redbot.core.bot import Red
from redbot.core.commands.converter import TimedeltaConverter
from redbot.core.data_manager import bundled_data_path, cog_data_path
from redbot.core.utils import AsyncIter
from redbot.core.utils.angiedale import ANGIEDALE_VERSION
from redbot.core.utils.chat_formatting import (
    bold,
    humanize_list,
    humanize_number,
    humanize_timedelta,
    inline,
    pagify,
)
from redbot.core.utils.menus import menu
from redbot.core.utils.tunnel import Tunnel

from .events import Events

log = logging.getLogger("red.angiedale.owner")


RUNNING_ANNOUNCEMENT = (
    "I am already announcing something. If you would like to make a"
    " different announcement please use `{prefix}announce cancel`"
    " first."
)


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass


class Owner(commands.Cog, Events, metaclass=CompositeMetaClass):
    """Bot set-up commands."""

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.interaction: List[Union[discord.Member, discord.User]] = []
        self.statuses: Dict[str, List[str]] = {}
        self.new_traceback_alert_settings: bool = True
        self.traceback_last_date: Optional[datetime] = None
        self.current_announcer: Optional[Announcer] = None

        self.admin_config = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="OwnerAdmin"
        )
        self.mutes_config = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Mutes"
        )
        self.stats_config = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Stats"
        )
        self.owner_config = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Owner"
        )

        self.admin_config.register_global(serverlocked=False, schema_version=1)
        self.stats_config.register_global(channel_id=None, message_id=None, bonk=0)
        self.owner_config.register_global(
            changelog={
                "channel_id": None,
                "last_version": "0.0.0",
                "github_pat": None,
                "repo": None,
                "role_id": None,
            },
            traceback={"channel_id": None, "interval": 216000},
        )

        self.presence_task = asyncio.create_task(self.maybe_update_presence())
        self.traceback_alert_task = asyncio.create_task(self.traceback_alert())
        self.stats_task = asyncio.create_task(self.update_stats())

        self._changelog_task = asyncio.create_task(self.check_changelog())

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    def cog_unload(self) -> None:
        if self.traceback_alert_task:
            self.traceback_alert_task.cancel()
        if self.presence_task:
            self.presence_task.cancel()
        if self.stats_task:
            self.stats_task.cancel()
        if self._changelog_task:
            self._changelog_task.cancel()
        try:
            self.current_announcer.cancel()
        except AttributeError:
            pass
        for user in self.interaction:
            asyncio.create_task(self.stop_interaction(user))

    async def traceback_alert(self) -> None:
        await self.bot.wait_until_red_ready()

        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        ] = None
        channel_id: Optional[int] = None
        interval: int = 216000

        try:
            while True:
                if self.new_traceback_alert_settings:
                    async with self.owner_config.traceback() as traceback:
                        channel_id = traceback["channel_id"]
                        new_interval = traceback["interval"]
                        channel = None

                    if new_interval is not None:
                        interval = new_interval

                    self.new_traceback_alert_settings = False

                if channel_id is None:
                    self.traceback_last_date = None
                    break

                if channel is None:
                    channel = self.bot.get_channel(channel_id)

                if channel is None:
                    self.traceback_last_date = None
                    break

                if self.traceback_last_date is None:
                    self.traceback_last_date = datetime.now()

                await asyncio.sleep(interval)

                location = data_manager.core_data_path() / "logs"
                if not location.exists():
                    break

                latest_logs: List[Path] = []

                for path in location.iterdir():
                    match = re.match(r"latest(?:-part\d+)?\.log", path.name)
                    if match:
                        latest_logs.append(path)

                latest_logs = sorted(latest_logs, key=lambda log: log.name, reverse=True)

                count = {"ERROR": 0, "WARNING": 0, "INFO": 0}

                break_loop = False
                for log_path in latest_logs:
                    with open(log_path) as error_log:
                        lines = error_log.readlines()

                    for line in reversed(lines):
                        date_match = re.match(
                            r"\[(?P<date>(\d|-|\s|:)+)\] \[(?P<type>INFO|WARNING|ERROR)\]", line
                        )
                        if date_match is None:
                            continue

                        date = datetime.strptime(date_match.group("date"), "%Y-%m-%d %H:%M:%S")

                        if date < self.traceback_last_date:
                            break_loop = True
                            break

                        count[date_match.group("type")] += 1

                    if break_loop:
                        break

                if count["ERROR"] > 0 or count["WARNING"] > 0:
                    output_list: List[str] = []
                    if count["ERROR"] > 0:
                        output_list.append(
                            f'{count["ERROR"]} error{"s" if count["ERROR"] > 1 else ""}'
                        )
                    if count["WARNING"] > 0:
                        output_list.append(
                            f'{count["WARNING"]} warning{"s" if count["WARNING"] > 1 else ""}'
                        )
                    if count["INFO"] > 0:
                        output_list.append(
                            f'{count["INFO"]} info message{"s" if count["INFO"] > 1 else ""}'
                        )

                    output = humanize_list(output_list)
                    try:
                        timestamp = int(self.traceback_last_date.timestamp())
                        owners = ""
                        if self.bot.owner_ids is not None:
                            for user_id in self.bot.owner_ids:
                                if (member := channel.guild.get_member(user_id)) is not None:
                                    owners += member.mention + " "

                        await channel.send(
                            f"{owners}"
                            f"{inline(self.bot.user.display_name)} has experienced a total of "
                            f"{bold(output)} since <t:{timestamp}:f> <t:{timestamp}:R>"
                        )
                    except:
                        log.exception("Traceback alerts failed to send message")
                        self.traceback_last_date = None
                        break

                self.traceback_last_date = datetime.now()
        except asyncio.CancelledError:
            return

    async def update_stats(self) -> None:
        await self.bot.wait_until_red_ready()

        async with self.stats_config.all() as data:
            channel_id = data["channel_id"]
            message_id = data["message_id"]

        if channel_id is None:
            return

        try:
            while True:
                await self.update_statsembed(channel_id, message_id)

                await asyncio.sleep(60 * 10)
        except asyncio.CancelledError:
            pass

    async def update_statsembed(self, channel_id: int, message_id: int) -> None:
        latencies = self.bot.latencies

        latency_message = ""
        for shard, ping_time in latencies:
            latency_message += "Shard **{}/{}**: `{}ms`\n".format(
                shard + 1, len(latencies), round(ping_time * 1000)
            )

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        message = await channel.fetch_message(message_id)
        if message is None:
            return

        embed = message.embeds[0]

        time_now = datetime.now(timezone.utc)

        embed.set_field_at(0, name="Serving Users", value=len(self.bot.users))
        embed.set_field_at(1, name="In Servers", value=len(self.bot.guilds))
        embed.set_field_at(2, name="Commands", value=len(self.bot.commands))
        embed.set_field_at(3, name="Emojis", value=len(self.bot.emojis))
        embed.set_field_at(4, name="Bonked Users", value=await self.stats_config.bonk())
        embed.set_field_at(
            5,
            name="Uptime",
            value=humanize_timedelta(timedelta=time_now - self.bot.uptime),
            inline=False,
        )
        embed.set_field_at(6, name="Latency", value=latency_message, inline=False)

        embed.timestamp = time_now

        await message.edit(embed=embed)

    @commands.command(name="setstatschannel")
    @commands.guild_only()
    @commands.is_owner()
    async def set_stats_channel(
        self,
        ctx: commands.Context,
        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        ] = None,
    ):
        """Set a channel for displaying bot stats."""
        if not self.stats_message_id and not channel:
            return await ctx.send("Please provide a channel to start displaying bot stats in.")

        response: List[str] = []
        if self.stats_task is None:
            return

        if not self.stats_task.done():
            self.stats_task.cancel()

            data = await self.stats_config.all()

            deletion_response = (
                "removed your old stats channel from config but was unable to delete its message"
            )

            if data["channel_id"] is not None:
                old_channel = self.bot.get_channel(data["channel_id"])

                if old_channel is not None:
                    try:
                        old_message = await old_channel.fetch_message(data["message_id"])
                    except:
                        pass
                    else:
                        try:
                            await old_message.delete()
                            deletion_response = "deleted the previous stats message"
                        except:
                            pass
            await self.stats_config.clear_all()
            response.append(deletion_response)

        if channel is not None:
            if (
                isinstance(channel, discord.Thread)
                and not channel.permissions_for(ctx.guild.me).send_messages_in_threads
            ):
                response.append(
                    f"tried to start displaying stats in {channel.mention} but I don't have "
                    f"the permission to send messages in that thread."
                )
            elif not channel.permissions_for(ctx.guild.me).send_messages:
                response.append(
                    f"tried to start displaying stats in {channel.mention} but I don't have "
                    f"the permission to send messages there."
                )
            else:
                embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
                embed.title = f"Statistics for {self.bot.user.name}"
                embed.set_thumbnail(url=self.bot.user.avatar.url)
                embed.add_field(name="Serving Users", value=0)
                embed.add_field(name="In Servers", value=0)
                embed.add_field(name="Commands", value=0)
                embed.add_field(name="Emojis", value=0)
                embed.add_field(name="Bonked Users", value=0)
                embed.add_field(name="Uptime", value=0, inline=False)
                embed.add_field(name="Latency", value=0, inline=False)
                embed.timestamp = datetime.now(timezone.utc)
                embed.set_footer(text="Updated")

                message = await channel.send(embed=embed)

                await self.stats_config.channel_id.set(channel.id)
                await self.stats_config.message_id.set(message.id)

                self.stats_task = asyncio.create_task(self.update_stats())

                response.append(
                    f"started displaying stats for {self.bot.user.name} in {channel.mention}"
                )

        await ctx.send(" and ".join(response).capitalize() + ".")

    async def stop_interaction(self, user: Union[discord.Member, discord.User]) -> None:
        self.interaction.remove(user)
        await user.send(("Session closed"))

    def is_announcing(self) -> bool:
        """
        Is the bot currently announcing something?
        """
        if self.current_announcer is None:
            return False

        return self.current_announcer.active or False

    @commands.group(invoke_without_command=True)
    @commands.is_owner()
    async def announce(self, ctx: commands.Context, *, message: str):
        """Announce a message to all servers the bot is in."""
        if not self.is_announcing():
            announcer = Announcer(ctx, message, config=self.admin_config)
            announcer.start()

            self.current_announcer = announcer

            await ctx.send(("The announcement has begun."))
        else:
            prefix = ctx.clean_prefix
            await ctx.send((RUNNING_ANNOUNCEMENT).format(prefix=prefix))

    @announce.command(name="cancel")
    async def _announce_cancel(self, ctx: commands.Context):
        """Cancel a running announce."""
        if not self.is_announcing():
            await ctx.send(("There is no currently running announcement."))
            return
        self.current_announcer.cancel()
        await ctx.send(("The current announcement has been cancelled."))

    @commands.command()
    @commands.is_owner()
    async def serverlock(self, ctx: commands.Context):
        """Lock a bot to its current servers only."""
        serverlocked = await self.admin_config.serverlocked()
        await self.admin_config.serverlocked.set(not serverlocked)

        if serverlocked:
            await ctx.send(("The bot is no longer serverlocked."))
        else:
            await ctx.send(("The bot is now serverlocked."))

    @commands.command(name="settracebackalerts")
    @commands.is_owner()
    async def set_traceback_alerts(
        self,
        ctx: commands.Context,
        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        ] = None,
        *,
        interval: Optional[TimedeltaConverter] = None,
    ):
        """Set a channel to recieve traceback alerts.

        **Arguments:**
        - `<channel>` - Discord channel to send alerts to.
        - `[interval]` - The time interval between alerts.
        """
        if interval is not None and interval < timedelta(minutes=5):
            return await ctx.send("interval can't be shorter than 5 minutes.")

        async with self.owner_config.traceback() as traceback:
            if channel is None:
                traceback["channel_id"] = None
                output = ["Removed the alerts channel."]
            else:
                traceback["channel_id"] = channel.id
                output = [f"Set the alerts channel to {channel.mention}."]

            if interval is not None:
                traceback["interval"] = int(interval.total_seconds())
                output.append(
                    f"Updated the time interval between alerts to {humanize_timedelta(timedelta=interval)}."
                )

        await ctx.send("\n\n".join(output))

        self.new_traceback_alert_settings = True

        if not self.traceback_alert_task.done():
            self.traceback_alert_task.cancel()

        self.traceback_alert_task = asyncio.create_task(self.traceback_alert())

    async def say_message(
        self,
        ctx: commands.Context,
        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        ],
        text: str,
        files: list,
        mentions: discord.AllowedMentions = None,
        delete_after: int = None,
    ):
        if channel is None:
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
            await channel.send(
                text, files=files, allowed_mentions=mentions, delete_after=delete_after
            )
        except discord.errors.HTTPException as e:
            if (
                isinstance(channel, discord.Thread)
                and not channel.permissions_for(ctx.me).send_messages_in_threads
            ):
                try:
                    await ctx.send(
                        ("I am not allowed to send messages in ") + channel.mention,
                        delete_after=2,
                    )
                except discord.errors.Forbidden:
                    await ctx.author.send(
                        ("I am not allowed to send messages in ") + channel.mention,
                        delete_after=15,
                    )
                    # If this fails then fuck the command author
            elif not channel.permissions_for(ctx.me).send_messages:
                try:
                    await ctx.send(
                        ("I am not allowed to send messages in ") + channel.mention,
                        delete_after=2,
                    )
                except discord.errors.Forbidden:
                    await ctx.author.send(
                        ("I am not allowed to send messages in ") + channel.mention,
                        delete_after=15,
                    )
                    # If this fails then fuck the command author
            elif not channel.permissions_for(ctx.me).attach_files:
                try:
                    await ctx.send(
                        ("I am not allowed to upload files in ") + channel.mention, delete_after=2
                    )
                except discord.errors.Forbidden:
                    await ctx.author.send(
                        ("I am not allowed to upload files in ") + channel.mention,
                        delete_after=15,
                    )
            else:
                log.error(
                    f"Unknown permissions error when sending a message.\n{error_message}",
                    exc_info=e,
                )

    @commands.command(name="say")
    @commands.is_owner()
    async def say(
        self,
        ctx: commands.Context,
        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        ],
        *,
        text: str = "",
    ):
        """
        Make the bot say what you want in the desired channel.

        If no channel is specified, the message will be send in the current channel.
        You can attach some files to upload them to Discord.

        Example usage :
        - `!say #general hello there`
        - `!say owo I have a file` (a file is attached to the command message)
        """

        files = await Tunnel.files_from_attatch(ctx.message)
        await self.say_message(ctx, channel, text, files)

    @commands.command(name="saydelete", aliases=["sayd", "sd"])
    @commands.is_owner()
    async def say_delete(
        self,
        ctx: commands.Context,
        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        ],
        *,
        text: str = "",
    ):
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

        await self.say_message(ctx, channel, text, files)

    @commands.command(name="interact")
    @commands.is_owner()
    async def interact(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ] = None,
    ):
        """Start receiving and sending messages as the bot through DM"""

        if channel is None:
            if isinstance(ctx.channel, discord.DMChannel):
                await ctx.send(
                    (
                        "You need to give a channel to enable this in DM. You can "
                        "give the channel ID too."
                    )
                )
                return
            else:
                channel = ctx.channel

        if ctx.author in self.interaction:
            await ctx.send(("A session is already running."))
            return

        message = await ctx.author.send(
            (
                "I will start sending you messages from {0}.\n"
                "Just send me any message and I will send it in that channel.\n"
                "React with ❌ on this message to end the session.\n"
                "If no message was send or received in the last 5 minutes, "
                "the request will time out and stop."
            ).format(channel.mention)
        )
        await message.add_reaction("❌")
        self.interaction.append(ctx.author)

        while True:
            if ctx.author not in self.interaction:
                return

            try:
                message: discord.Message = await self.bot.wait_for("message", timeout=300)
            except asyncio.TimeoutError:
                await ctx.author.send(("Request timed out. Session closed"))
                self.interaction.remove(ctx.author)
                return

            if message.author == ctx.author and isinstance(message.channel, discord.DMChannel):
                files = await Tunnel.files_from_attatch(message)
                if message.content.startswith(tuple(await self.bot.get_valid_prefixes())):
                    return
                await channel.send(message.content, files=files)
            elif (
                message.channel != channel
                or message.author == channel.guild.me
                or message.author == ctx.author
            ):
                pass

            else:
                embed = discord.Embed(color=message.author.color)
                embed.set_author(
                    name="{} | {}".format(str(message.author), message.author.id),
                    icon_url=message.author.avatar.url,
                )
                embed.set_footer(text=message.created_at.strftime("%d %b %Y %H:%M"))
                embed.description = message.content

                if message.attachments != []:
                    embed.set_image(url=message.attachments[0].url)

                await ctx.author.send(embed=embed)

    @commands.command(name="listguilds", aliases=["listservers", "guildlist", "serverlist"])
    @commands.is_owner()
    async def list_guilds(self, ctx: commands.Context):
        """List the servers the bot is in."""
        guilds: List[discord.Guild] = sorted(self.bot.guilds, key=lambda g: g.member_count)

        base_embed = discord.Embed(color=await ctx.embed_colour())

        base_embed.set_author(
            name=f"{self.bot.user.name} is in {len(guilds)} servers",
            icon_url=self.bot.user.avatar.url,
        )

        guild_list = []
        for guild in guilds:
            entry = f"**{guild.name}** ◈ {humanize_number(guild.member_count)} Users ◈ {guild.id}"
            guild_list.append(entry)

        embeds = []
        pages = list(pagify("\n".join(guild_list), delims=["\n"], page_length=1000))

        i = 1
        for page in pages:
            embed = base_embed.copy()
            embed.description = page
            embed.set_footer(text=f"Page {i}/{len(pages)}")

            embeds.append(embed)
            i += 1

        await menu(ctx, embeds)

    async def maybe_update_presence(self) -> None:
        await self.bot.wait_until_red_ready()

        with Path(bundled_data_path(self) / "statuses.json").open("r") as file:
            self.statuses = json.load(file)

        delay = 90
        while True:
            try:
                await self.presence_updater()
            except Exception as e:
                log.exception("Something went wrong in maybe_update_presence task:", exc_info=e)

            await asyncio.sleep(int(delay))

    async def presence_updater(self) -> None:
        try:
            guild = next(g for g in self.bot.guilds if not g.unavailable)
        except StopIteration:
            return
        try:
            current_status = str(guild.me.activity.name)
            current_status = current_status.split(" | ")[0]
        except AttributeError:
            current_status = None

        prefix = (await self.bot.get_valid_prefixes())[0]

        statuses = self.statuses["default"]

        statuses.append(f"with {len(self.bot.users)} users")
        statuses.append(f"in {len(self.bot.guilds)} servers")

        date_today = datetime.now(timezone(timedelta(hours=14))).date()
        easter_at = easter(date_today.year)

        # Easter + 7 days
        if date_today >= easter_at and date_today < easter_at + timedelta(days=7):
            statuses += self.statuses["easter"]
        # Valentines day + 7 days
        elif date_today.month == 2 and date_today.day >= 14 and date_today.day < 21:
            statuses += self.statuses["valentine"]
        # Christmas until the 30th
        elif date_today.month == 12 and date_today.day >= 24 and date_today.day < 31:
            statuses += self.statuses["christmas"]
        # 31st of previous year or the first 6 days of the new year
        elif (
            date_today.month == 12
            and date_today.day == 31
            or date_today.month == 1
            and date_today.day < 7
        ):
            statuses += self.statuses["new_year"]
        # Day of halloween or the following 6 days
        elif (
            date_today.month == 10
            and date_today.day == 31
            or date_today.month == 11
            and date_today.day < 7
        ):
            statuses += self.statuses["halloween"]
        else:
            statuses += self.statuses["extra"]

        new_status = self.random_status(current_status, statuses)

        if (current_status != new_status) or (current_status is None):
            new_status = " | ".join((new_status, f"{prefix}help"))

            await self.bot.change_presence(
                activity=discord.Activity(
                    name=new_status,
                    type=discord.ActivityType.playing,
                    state=f"Invite this bot to your own server with {prefix}invite",
                ),
                status=discord.Status.online,
            )

    def random_status(self, current_status: str, statuses: List[str]) -> str:
        new_statuses = [status for status in statuses if status != current_status]

        if len(new_statuses) > 1:
            return choice(new_statuses)
        elif len(new_statuses) == 1:
            return new_statuses[0]
        return current_status

    @commands.command(name="forcerolemutes")
    @commands.is_owner()
    async def force_role_mutes(self, ctx: commands.Context, true_or_false: bool):
        """
        Whether or not to force role only mutes on the bot
        """
        await self.mutes_config.force_role_mutes.set(true_or_false)
        if true_or_false:
            await ctx.send(("Okay I will enforce role mutes before muting users."))
        else:
            await ctx.send(("Okay I will allow channel overwrites for muting users."))

    @commands.command(name="dumpemotes", aliases=["dumpemojis", "dumpe"])
    @commands.guild_only()
    @commands.is_owner()
    async def dump_emotes(self, ctx: commands.Context, guild_id: int = None):
        """Dumps emotes from a server."""
        if guild_id is None:
            guild = ctx.guild
        else:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return await ctx.send("Couldn't find a guild with that ID.")

        path = Path(f"{cog_data_path(self)}/{guild.id}")

        message = await ctx.send(
            "\n".join(
                [
                    f"Found {inline(str(len(guild.emojis)))} emojis.",
                    "Give me a moment to download them...",
                ]
            )
        )

        path.mkdir(parents=True, exist_ok=True)

        async with ctx.typing():
            for emoji in guild.emojis:
                if emoji.animated:
                    await emoji.save(f"{path}/{emoji.name}.gif")
                else:
                    await emoji.save(f"{path}/{emoji.name}.png")
                await asyncio.sleep(0.2)
            try:
                await message.delete()
            except:
                pass

        with ZipFile(f"{path}.zip", "w") as zip:
            for file in os.listdir(path):
                zip.write(f"{path}/{file}", file)

        with open(f"{path}.zip", "rb") as fp:
            await ctx.send(
                content="Here's your emotes!", file=discord.File(fp, f"{guild.name} Emotes.zip")
            )

        os.remove(f"{path}.zip")
        shutil.rmtree(path)

    @commands.group(name="setchangelog")
    @commands.is_owner()
    async def set_changelog(self, ctx: commands.Context):
        """Set values for the changelog feature."""

    @set_changelog.command(name="channel")
    @commands.guild_only()
    async def _set_changelog_channel(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ] = None,
    ):
        """Set the channel where changelogs will be sent."""
        if channel is None:
            await self.owner_config.changelog.channel_id.clear()
            return await ctx.maybe_send_embed("Cleared the changelog channel")

        async with self.owner_config.changelog() as config:
            config["channel_id"] = channel.id

        await ctx.maybe_send_embed(f"Now set the changelog channel to {channel.mention}")

    @set_changelog.command(name="pat")
    @commands.dm_only()
    async def _set_changelog_pat(self, ctx: commands.Context, clear: str = None):
        """Set the PAT token used to access github api."""
        if clear is not None:
            async with self.owner_config.changelog() as data:
                data["github_pat"] = None
            await ctx.send("PAT key cleared.")
        else:
            view = PATView(self.owner_config)
            message = await ctx.send("Click the button below to set your PAT key.", view=view)
            timed_out = await view.wait()
            if timed_out:
                await message.edit(content="The key submission timed out.", view=None)

    @set_changelog.command(name="repo")
    async def _set_changelog_repo(self, ctx: commands.Context, repo: str = None):
        """Set the repo to be checked for new updates."""
        if repo is None:
            await ctx.maybe_send_embed("Cleared the repo.")
        else:
            if repo.endswith("/"):
                repo = repo[:-1]
            repo_split = repo.split("/")
            repo = f"{repo_split[-2]}/{repo_split[-1]}"
            await ctx.maybe_send_embed(f"Set the repo used to {repo}")

        async with self.owner_config.changelog() as data:
            data["repo"] = repo

    @set_changelog.command(name="role")
    async def _set_changelog_role(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set the repo to be checked for new updates."""
        if role is None:
            async with self.owner_config.changelog() as data:
                data["role_id"] = None
            await ctx.maybe_send_embed("Cleared the role.")
        else:
            async with self.owner_config.changelog() as data:
                data["role_id"] = role.id
            await ctx.maybe_send_embed(f"Set the role used to {role.name}")

    async def check_changelog(self) -> None:
        await self.bot.wait_until_red_ready()

        data = await self.owner_config.changelog()

        if data["channel_id"] is None:
            return

        if data["github_pat"] is None:
            return

        if data["repo"] is None:
            return

        github = Github(auth=Auth.Token(data["github_pat"]))

        repo = github.get_repo(data["repo"])
        release = repo.get_latest_release()

        new_version = release.title

        if "v" in new_version:
            new_version = new_version.replace("v", "")

        if new_version == data["last_version"]:
            return log.info(f"{self.bot.user.name} is running the most recent version.")

        if ANGIEDALE_VERSION != new_version:
            return log.info(
                f"{self.bot.user.name} isn't running the same version as is available on github."
            )

        channel = self.bot.get_channel(data["channel_id"])
        if channel is None:
            return log.warning(
                f"Couldn't find the channel with id {data['channel_id']} to send the changelog embed to."
            )

        embed = discord.Embed(color=await self.bot.get_embed_color(channel))

        embed.set_author(
            name=f"{release.author.name} ◈ {release.target_commitish[:7]}",
            url=release.author.html_url,
            icon_url=release.author.avatar_url,
        )

        embed.set_thumbnail(url=self.bot.user.avatar.url)

        embed.title = data["repo"]
        embed.url = f"https://github.com/{data['repo']}"

        preface = [f"# {release.title} - [Release Link]({release.html_url})"]

        embed.timestamp = release.created_at

        lines = release.body.split("\r\n")

        current_header = None
        headers: Dict[str, List[str]] = {}
        for line in lines:
            if not line:
                continue

            if line.startswith("---"):
                continue

            if line.startswith("## "):
                current_header = line
                headers[line] = []
                continue

            if line.startswith("### "):
                line = f"- {bold(line[4:])}"

            if len(headers) == 0:
                preface.append(line)
                continue

            headers[current_header].append(line)

            embed.description = "\n\n".join(preface)

        for header, body in headers.items():
            embed.add_field(name=header[3:], value="\n".join(body), inline=False)

        role = channel.guild.get_role(data["role_id"])
        try:
            if role is not None:
                await channel.send(content=role.mention, embed=embed)
            else:
                await channel.send(embed=embed)
            async with self.owner_config.changelog() as config:
                config["last_version"] = new_version
        except Exception as e:
            log.exception("Error trying to send changelog embed", exc_info=e)


class PATModal(discord.ui.Modal, title="PAT Key"):
    def __init__(self, config: Config):
        self.config = config
        super().__init__(title=self.title)

        self.key_input = discord.ui.TextInput(
            label="Key", style=discord.TextStyle.long, required=True
        )

        self.add_item(self.key_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await interaction.client.is_owner(interaction.user):
            return

        async with self.config.changelog() as data:
            data["github_pat"] = self.key_input.value

        await interaction.response.send_message(f"PAT key saved", ephemeral=True)


class PATView(discord.ui.View):
    def __init__(self, config: Config):
        self.config = config
        super().__init__()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await interaction.client.is_owner(interaction.user):
            return False
        return True

    @discord.ui.button(label=("Set PAT"), style=discord.ButtonStyle.grey)
    async def button(self, interaction: discord.Interaction, button: discord.Button):
        return await interaction.response.send_modal(PATModal(self.config))


class Announcer:
    def __init__(self, ctx: commands.Context, message: str, config: Optional[Config] = None):
        """
        :param ctx:
        :param message:
        :param config: Used to determine channel overrides
        """
        self.ctx = ctx
        self.message = message
        self.config = config

        self.active: Optional[bool] = None

    def start(self) -> None:
        """
        Starts an announcement.
        :return:
        """
        if self.active is None:
            self.active = True
            asyncio.create_task(self.announcer())

    def cancel(self) -> None:
        """
        Cancels a running announcement.
        :return:
        """
        self.active = False

    async def _get_announce_channel(
        self, guild: discord.Guild
    ) -> Optional[
        Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
    ]:
        if await self.ctx.bot.cog_disabled_in_guild_raw("Admin", guild.id):
            return
        channel_id: int = await self.config.guild(guild).announce_channel()
        return guild.get_channel_or_thread(channel_id)

    async def announcer(self):
        guild_list = self.ctx.bot.guilds
        failed: List[str] = []
        count = 0
        async for g in AsyncIter(guild_list, delay=0.5):
            if not self.active:
                return

            channel = await self._get_announce_channel(g)

            if channel is not None:
                if (
                    isinstance(channel, discord.Thread)
                    and channel.permissions_for(g.me).send_messages_in_threads
                ):
                    try:
                        await channel.send(self.message)
                        count += 1
                    except discord.Forbidden:
                        failed.append(str(g.id))
                elif channel.permissions_for(g.me).send_messages:
                    try:
                        await channel.send(self.message)
                        count += 1
                    except discord.Forbidden:
                        failed.append(str(g.id))
                else:
                    failed.append(str(g.id))

        if failed:
            msg = f"Finished announcing to {count} server{'s' if count > 1 else ''}.\n\n"
            msg += (
                ("I could not announce to the following server: ")
                if len(failed) == 1
                else ("I could not announce to the following servers: ")
            )
            msg += humanize_list(tuple(map(inline, failed)))
            await self.ctx.bot.send_to_owners(msg)
        else:
            await self.ctx.bot.send_to_owners(
                f"Finished announcing to {count} server{'s' if count > 1 else ''}."
            )
        self.active = False
