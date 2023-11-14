import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.converter import TimedeltaConverter
from redbot.core.utils.chat_formatting import (
    bold,
    error,
    humanize_timedelta,
    inline,
    success,
    warning,
)

from .abc import MixinMeta
from .converters import RawMessageIds

log = logging.getLogger("red.angiedale.utility")

raffle_entry_emoji = "\N{ADMISSION TICKETS}\N{VARIATION SELECTOR-16}"

numbered_emojis = (
    "\N{DIGIT ONE}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{DIGIT TWO}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{DIGIT THREE}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{DIGIT FOUR}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{DIGIT FIVE}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{DIGIT SIX}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{DIGIT SEVEN}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{DIGIT EIGHT}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{DIGIT NINE}\N{COMBINING ENCLOSING KEYCAP}",
    "\N{KEYCAP TEN}",
)


class RaffleSettings:
    def __init__(self, **kwargs):
        self.title: str = kwargs.get("title", None)
        self.embed: discord.Embed = kwargs.get("embed", None)
        self.winners: int = kwargs.get("winners", None)
        self.dos: Optional[int] = kwargs.get("dos", 0)
        self.roles: List[discord.Role] = kwargs.get("roles", [])
        self.use_buttons: bool = kwargs.get("use_buttons", True)

        if self.embed is not None:
            self.title = self.embed.title
            self.url = self.embed.url
            self.winners = int(self.embed.fields[3].value)

            if len(self.embed.fields) > 4:
                self.dos = int(self.embed.fields[4].value)

    def role_ids(self) -> List[int]:
        return [role.id for role in self.roles]


class Raffle(MixinMeta):
    """Raffle/Giveaways."""

    @commands.group()
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def raffle(self, ctx: commands.Context):
        """Raffles/Giveaways."""

    @raffle.command(name="clearguild", alises=["resetguild"], hidden=True)
    @commands.is_owner()
    async def _clear_guild(self, ctx: commands.Context):
        await self.raffle_config.guild(ctx.guild).raffles.clear()
        await self.raffle_config.guild(ctx.guild).raffles_history.clear()
        await ctx.send("Raffle data cleared out.")

    @commands.max_concurrency(1, commands.BucketType.guild)
    @raffle.command(name="start", aliases=["create", "make", "new"])
    async def _start(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        *,
        time: Optional[TimedeltaConverter] = None,
    ):
        """Start a raffle/giveaway.

        Time should be a string that doesn't exceed 8 weeks.

        Examples:
        - `[p]raffle start #giveaways 2w` (Channel Mention)
        - `[p]raffle start channel-name 1week12days` (Channel Name)
        - `[p]raffle start 672816387625975808 20 hours 30 min` (Channel ID)

        Up to four raffles can be active per server.
        """
        if (
            not ctx.channel.permissions_for(ctx.guild.me).embed_links
            or not channel.permissions_for(ctx.guild.me).embed_links
        ):
            return await ctx.send("I need the Embed Links permission to be able to start raffles.")
        if (
            not ctx.channel.permissions_for(ctx.guild.me).add_reactions
            or not channel.permissions_for(ctx.guild.me).add_reactions
        ):
            return await ctx.send(
                "I need the Add Reactions permission to be able to start raffles."
            )

        if time is None:
            return await ctx.send("You need to provide a valid time for the raffle to last.")

        guild_settings: Dict[
            str, Union[Optional[int], List[dict]]
        ] = await self.raffle_config.guild(ctx.guild).all()

        if len(guild_settings["raffles"]) >= 4:
            return await ctx.send(
                "You already have 4 raffles running in the server."
                "Wait for one of them to finish first before starting another one."
            )

        if time > timedelta(weeks=8):
            return await ctx.send(f"The time can't be longer than {inline('8 weeks')}.")
        if time < timedelta(minutes=5):
            return await ctx.send(f"The raffle can't be shorter than {inline('5 minutes')}.")

        time_now = datetime.now(timezone.utc)

        raffle: Optional[RaffleSettings] = await self.raffle_setup(
            ctx, int((time_now + time).timestamp()), channel
        )

        if raffle is None:
            return

        notification_role = None
        if guild_settings["notification_role_id"]:
            notification_role = ctx.guild.get_role(guild_settings["notification_role_id"])

            if notification_role is None:
                await ctx.send(
                    "\n".join(
                        [
                            "I was unable to get the notification role that's set.",
                            "The raffle will still be sent but you should set a new notification role with "
                            f"{inline(f'{ctx.clean_prefix}{self.raffle.name} {self._raffle_set.name} {self._mention.name} <role>')}",
                        ]
                    )
                )

        raffle_message = await channel.send(
            content=notification_role.mention if notification_role else None,
            embed=raffle.embed,
        )

        if raffle.use_buttons:
            view = RaffleView(
                config=self.raffle_config,
                roles=raffle.roles,
                dos=raffle.dos,
            )

            await raffle_message.edit(
                view=view,
            )
        else:
            await raffle_message.add_reaction(raffle_entry_emoji)

        await ctx.send(success(f"Raffle sent! {raffle_message.jump_url}"))

        timestamp = int((time_now + time).timestamp())

        async with self.raffle_config.guild(ctx.guild).raffles() as raffles:
            raffles[raffle_message.id] = {
                "timestamp": timestamp,
                "title": raffle.title,
                "channel_id": channel.id,
                "winners": raffle.winners,
                "dos": raffle.dos,
                "roles": raffle.role_ids(),
                "entries": [],
                "use_buttons": raffle.use_buttons,
            }

        _ = asyncio.create_task(self.raffle_timer(ctx.guild, raffle_message.id, timestamp))

    @commands.max_concurrency(1, commands.BucketType.guild)
    @raffle.command(name="end", aliases=["stop"])
    async def _end(self, ctx: commands.Context, message_id: Optional[RawMessageIds] = None):
        """Ends a raffle early and picks winner(s).

        If `message_id` is left empty you're given a menu
        where you can pick which to one to end.
        """
        raffles: dict = await self.raffle_config.guild(ctx.guild).raffles()
        if len(raffles) == 0:
            return await ctx.send("There are no active raffles running in the server.")

        message = await self.raffle_selection(ctx, raffles, message_id)
        if message is None:
            return

        await self.end_raffle(ctx.guild, message, raffles.get(str(message.id)))

    @commands.max_concurrency(1, commands.BucketType.guild)
    @raffle.command(name="cancel")
    async def _cancel(self, ctx: commands.Context, message_id: Optional[RawMessageIds] = None):
        """Cancels an on-going raffle without picking a winner.

        If `message_id` is left empty you're given a menu
        where you can pick which to one to cancel.
        """
        raffles: dict = await self.raffle_config.guild(ctx.guild).raffles()
        if len(raffles) == 0:
            return await ctx.send("There are no active raffles running in the server.")

        message = await self.raffle_selection(ctx, raffles, message_id)
        if message is None:
            return

        finished_embed = await self.get_finished_raffle_embed(ctx.guild, message.embeds[0])
        await message.edit(embed=finished_embed, view=None)

        await self.clear_raffle_entry(ctx.guild, message.id)
        await message.channel.send(
            f"The {bold(message.embeds[0].title)} raffle was cancelled. No winners will be pulled!",
            reference=message,
        )

    @commands.max_concurrency(1, commands.BucketType.guild)
    @raffle.command(name="reroll", aliases=["redraw"])
    async def _reroll(self, ctx: commands.Context, message_id: Optional[RawMessageIds] = None):
        """Reroll the winner for a raffle.

        The last 5 raffles ran can be rerolled.
        It's possible for the same user(s) to be drawn.

        If `message_id` is left empty you're given a menu
        where you can pick which to one to reroll.
        """
        raffles: list = await self.raffle_config.guild(ctx.guild).raffles_history()
        if len(raffles) == 0:
            return await ctx.send("You haven't ran any raffles yet!")

        if message_id is None:
            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
            embed.set_author(name="List of past raffles.", icon_url=ctx.me.display_avatar.url)
            embed.title = "Select which raffle you'd like to reroll"
            embed.set_thumbnail(url=ctx.guild.icon.url)

            output: List[str] = []
            index = 0
            valid_raffles: List[Dict[str, Union[int, str]]] = []
            messages: Dict[int, discord.Message] = {}
            channels: Dict[int, Union[discord.abc.GuildChannel, discord.Thread]] = {}
            for raffle in raffles:
                extension = ""
                channel = ctx.guild.get_channel_or_thread(raffle["channel_id"])
                if channel is None:
                    continue
                channels[raffle["message_id"]] = channel

                message = await channel.fetch_message(raffle["message_id"])
                if message is not None:
                    extension = f" ◈ {message.jump_url}"
                    messages[message.id] = message

                output.append(f"{numbered_emojis[index]} {bold(raffle['title'])}{extension}")
                valid_raffles.append({"title": raffle["title"], "id": raffle["message_id"]})
                index += 1

            if len(channels) == 0:
                return await ctx.send(
                    warning("All the channels your previous raffles have ran in are unavaliable!")
                )

            embed.description = "\n".join(output)

            view = ItemSelectView(raffles=valid_raffles)
            select_message = await ctx.send(embed=embed, view=view)
            timed_out = await view.wait()
            if timed_out:
                await select_message.edit(content="Selection timed out!", embed=None, view=None)
                return

            if not view.result:
                await select_message.edit(content="Selection cancelled!", embed=None, view=None)
                return

            await select_message.delete()

            raffle: dict = None
            for raf in raffles:
                if raf["message_id"] == int(view.value):
                    raffle = raf
            reference_message = messages.get(raffle["message_id"], None)
            channel = channels.get(raffle["message_id"])
        else:
            raffle: dict = None
            for raf in raffles:
                if raf["message_id"] == str(message_id):
                    raffle = raf

            if raffle is None:
                return await ctx.send(
                    "I couldn't find any raffle by that message ID in my history!"
                )

            channel = ctx.guild.get_channel_or_thread(raffle["channel_id"])
            if channel is None:
                return await ctx.send("I couldn't find the channel the raffle was ran in!")

            reference_message: Optional[discord.Message] = None
            message = await channel.fetch_message(raffle["message_id"])
            if message is not None:
                reference_message = message

        if not channel.permissions_for(ctx.me).send_messages:
            return await ctx.send(
                warning("I'm not allowed to send messages in that raffles channel anymore.")
            )

        winners = await self.pick_winner(
            ctx.guild, channel, reference_message, raffle, reroll=True
        )
        if winners is None:
            return

        async with self.raffle_config.guild(ctx.guild).raffles_history() as raffles_history:
            for i, raf in enumerate(raffles_history):
                if raf["message_id"] == raffle["message_id"]:
                    raf["winner_list"] = [winner.id for winner in winners]
                    raffles_history[i] = raf
                    break

    @commands.cooldown(1, 30, commands.BucketType.member)
    @raffle.command(name="list")
    async def _list(self, ctx: commands.Context):
        """List current and past raffles."""
        guild_data: dict = await self.raffle_config.guild(ctx.guild).all()
        if len(guild_data["raffles"]) == 0 and len(guild_data["raffles_history"]) == 0:
            return await ctx.send("There are no current or past raffles in this server.")

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f"List of raffles in {ctx.guild.name}", icon_url=ctx.guild.me.display_avatar.url
        )
        embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.set_footer(text="Name ◈ Message Link ◈ Ends/Ended ◈ Entries")

        view: Optional[ItemSelectView] = None
        if len(guild_data["raffles"]) > 0:
            output = []
            for message_id, raffle in guild_data["raffles"].items():
                jump_url = ""
                channel = ctx.guild.get_channel_or_thread(raffle["channel_id"])
                if channel is not None:
                    message = await channel.fetch_message(message_id)
                    if message is not None:
                        jump_url = f" ◈ {message.jump_url}"
                output.append(
                    "\n".join(
                        [
                            f"{raffle['title']}" f"{jump_url}",
                            f"<t:{raffle['timestamp']}:D> <t:{raffle['timestamp']}:R>",
                        ]
                    )
                )

            embed.add_field(name="Active Raffles", value="\n\n".join(output), inline=False)

        if len(guild_data["raffles_history"]) > 0:
            output = []
            raffles: List[Dict[str, Union[int, str]]] = []
            for raffle in guild_data["raffles_history"]:
                jump_url = ""
                channel = ctx.guild.get_channel_or_thread(raffle["channel_id"])
                if channel is not None:
                    message = await channel.fetch_message(raffle["message_id"])
                    if message is not None:
                        jump_url = f" ◈ {message.jump_url}"
                output.append(
                    "\n".join(
                        [
                            f"{raffle['title']}" f"{jump_url}",
                            f"<t:{raffle['timestamp']}:D> <t:{raffle['timestamp']}:R>"
                            f" ◈ Entries: {len(raffle['entries'])}",
                        ]
                    )
                )
                raffles.append({"title": raffle["title"], "id": raffle["message_id"]})

            embed.add_field(name="Past Raffles", value="\n\n".join(output), inline=False)

            view = ItemSelectView(
                raffles,
                use_cancel=False,
                selection_label="Select a past raffle to show it's results",
            )

        original_response = await ctx.send(embed=embed, view=view)
        if view is None:
            return

        timed_out = False
        response_message: Optional[discord.Message] = None
        while not timed_out:
            timed_out = await view.wait()
            if timed_out:
                await original_response.edit(view=None)
                if response_message is not None:
                    await response_message.delete()
                break

            raffle: Optional[Dict[str, Union[int, str]]] = None
            for raf in guild_data["raffles_history"]:
                if str(raf["message_id"]) == view.value:
                    raffle: Dict[str, Union[int, str]] = raf

            if raffle is None:
                return await ctx.send("An unknown error occured.")

            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

            embed.title = raffle["title"]
            embed.set_author(name=f"Raffle Results!", icon_url=ctx.guild.me.display_avatar.url)

            winners: List[Union[discord.Member, int]] = []
            for winner in raffle["winner_list"]:
                member = ctx.guild.get_member(winner)
                winners.append(member if member is not None else winner)

            if len(winners) == 1:
                output = (
                    winners[0].mention if isinstance(winners[0], discord.Member) else winners[0]
                )
            else:
                mentions = []
                for i, member in enumerate(winners):
                    mentions.append(
                        f"{bold(f'{i+1}.')} {member.mention if isinstance(member, discord.Member) else member}"
                    )

                output = "\n".join(mentions)

            embed.add_field(name="Winners", value=output, inline=False)

            allowed_roles = "@everyone"
            if len(raffle["roles"]) > 0:
                allowed_roles = []
                for role_id in raffle["roles"]:
                    role = ctx.guild.get_role(role_id)
                    allowed_roles.append((role.id if role is not None else role_id))
                allowed_roles = "\n".join([])

            embed.add_field(name="Allowed Roles", value=allowed_roles, inline=True)
            embed.add_field(name="Winners Pulled", value=raffle["winners"], inline=True)
            if raffle["dos"] > 0:
                embed.add_field(name="Days on Server to Enter", value=raffle["dos"], inline=True)

            if response_message is None:
                response_message = await ctx.send(embed=embed)
            else:
                await response_message.edit(embed=embed)

            view = ItemSelectView(
                raffles,  # type: ignore
                use_cancel=False,
                selection_label="Select a past raffle to show it's results",
            )
            await original_response.edit(view=view)

    # @commands.max_concurrency(1, commands.BucketType.guild)
    # @raffle.command(name="removemissing", hidden=True)
    # async def _remove(self, ctx: commands.Context, message_id: Optional[RawMessageIds] = None):
    #     """Remove a raffle that the bot is still tracking but can't see.

    #     Useful in case the raffle message was manually deleted
    #     but somehow is still tracked by the bot.

    #     If `message_id` is left empty you're given a menu
    #     where you can pick which to one to end.
    #     """

    #     raffles: dict = await self.raffle_config.guild(ctx.guild).raffles()
    #     if len(raffles) == 0:
    #         return await ctx.send("There are no active raffles running in the server.")

    #     message = await self.raffle_selection(ctx, raffles, message_id, forced=True)
    #     if message is None:
    #         return

    #     await self.clear_raffle_entry(ctx.guild, message.id)
    #     await ctx.send("Raffle cleared!")

    @raffle.group(name="set")
    @commands.guildowner()
    async def _raffle_set(self, ctx: commands.Context):
        """Change raffle/giveaway settings."""

    @_raffle_set.command(name="mention", aliases=["role", "notification"])
    async def _mention(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        """Set a role I should ping for raffles."""
        if role:
            await self.raffle_config.guild(ctx.guild).notification_role_id.set(role.id)
            return await ctx.send(
                f"I will now mention {bold(role if role.is_default() else role.name)} for new raffles."
            )

        await self.raffle_config.guild(ctx.guild).Mention.clear()
        await ctx.send("I will no longer mention any role for new raffles.")

    async def load_raffles(self) -> None:
        await self.bot.wait_until_red_ready()
        try:
            coroutines = []
            guilds = await self.raffle_config.all_guilds()
            timestamp_now = int(datetime.now(timezone.utc).timestamp())
            for g_id, g_data in guilds.items():
                if len(g_data["raffles"]) > 0:
                    guild = self.bot.get_guild(g_id)
                    if guild is None:
                        continue

                    for m_id, raffle in g_data["raffles"].items():
                        if timestamp_now > raffle["timestamp"]:
                            await self.end_raffle(guild, int(m_id), raffle)
                            continue

                        channel = guild.get_channel_or_thread(raffle["channel_id"])

                        if channel is None:
                            continue

                        message = await channel.fetch_message(int(m_id))

                        if message is None:
                            continue

                        if raffle["use_buttons"]:
                            roles: List[discord.Role] = []
                            for role_id in raffle["roles"]:
                                role = guild.get_role(role_id)
                                if role is not None:
                                    roles.append(role)

                            view = RaffleView(
                                config=self.raffle_config,
                                roles=roles,
                                dos=raffle["dos"],
                            )
                            self.bot.add_view(
                                view,
                                message_id=int(m_id),
                            )
                            await message.edit(view=view)

                        coroutines.append(self.raffle_timer(guild, int(m_id), raffle["timestamp"]))

            await asyncio.gather(*coroutines)
        except Exception:
            log.error("Error during raffle initialization", exc_info=True)

    async def raffle_selection(
        self, ctx: commands.Context, raffles: dict, message_id: Optional[int] = None
    ) -> Optional[discord.Message]:
        if message_id is None:
            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
            embed.set_author(
                name="List of currently active raffles.", icon_url=ctx.me.display_avatar.url
            )
            embed.title = "Select which raffle you'd like to end"
            embed.set_thumbnail(url=ctx.guild.icon.url)

            output: List[str] = []
            index = 0
            messages: List[discord.Message] = []
            for m_id, raffle in raffles.items():
                channel = ctx.guild.get_channel_or_thread(raffle["channel_id"])
                if channel is None:
                    continue
                message = await channel.fetch_message(m_id)
                if message is None:
                    continue

                output.append(
                    f"{numbered_emojis[index]} {bold(message.embeds[0].title)} ◈ {message.jump_url}"
                )
                messages.append(message)
                index += 1

            if len(messages) == 0:
                await ctx.send("There are no active raffles running in the server.")
                return

            embed.description = "\n".join(output)

            valid_raffles: List[Dict[str, Union[int, str]]] = []
            for message in messages:
                valid_raffles.append({"title": message.embeds[0].title, "id": message.id})
            view = ItemSelectView(raffles=valid_raffles)
            select_message = await ctx.send(embed=embed, view=view)
            timed_out = await view.wait()
            if timed_out:
                await select_message.edit(content="Selection timed out!", embed=None, view=None)
                return

            if not view.result:
                await select_message.edit(content="Selection cancelled!", embed=None, view=None)
                return

            await select_message.delete()

            result = None
            for message in messages:
                if message.id == int(view.value):
                    result = message
                    break
        else:
            raffle = raffles.get(message_id, None)

            if raffle is None:
                await ctx.send("I couldn't find a raffle with that message ID.")
                return

            channel = ctx.guild.get_channel_or_thread(raffle["channel_id"])
            if raffle is None:
                await ctx.send("I couldn't find the channel that the raffle is supposed to be in.")
                return

            message: discord.Message = await channel.fetch_message(message_id)
            if message is None:
                await ctx.send("I couldn't find the raffle message.")
                return

            result = message

        return result

    async def raffle_timer(self, guild: discord.Guild, message_id: int, timestamp: int) -> None:
        time = (
            datetime.fromtimestamp(timestamp, timezone.utc) - datetime.now(timezone.utc)
        ).seconds
        await asyncio.sleep(time)
        async with self.raffle_config.guild(guild).raffles() as raffle:
            data: Dict[str, Union[List[int], int]] = raffle.get(str(message_id))
        if data:
            await self.end_raffle(guild, message_id, data)

    async def end_raffle(
        self,
        guild: discord.Guild,
        message_or_id: Union[discord.Message, int],
        data: Dict[str, Union[List[int], int]],
        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        ] = None,
    ) -> None:
        if channel is None:
            channel = guild.get_channel_or_thread(data["channel_id"])

        if (
            channel is None
            or not channel.permissions_for(guild.me).read_messages
            or not channel.permissions_for(guild.me).send_messages
        ):
            return await self.clear_raffle_entry(
                guild, message_or_id if isinstance(message_or_id, int) else message_or_id.id
            )

        message = message_or_id

        if isinstance(message_or_id, int):
            try:
                message: Union[
                    discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
                ] = await channel.fetch_message(message_or_id)
            except discord.NotFound:
                return await self.clear_raffle_entry(guild, message_or_id)

        winners = await self.pick_winner(guild, channel, message, data)
        await self.move_raffle_to_history(
            guild, message_or_id if isinstance(message_or_id, int) else message_or_id.id, winners
        )

    async def clear_raffle_entry(self, guild: discord.Guild, message_id: int) -> None:
        async with self.raffle_config.guild(guild).raffles() as raffles:
            try:
                del raffles[str(message_id)]
            except KeyError:
                pass

    async def move_raffle_to_history(
        self, guild: discord.Guild, message_id: int, winners: Optional[List[discord.Member]]
    ) -> None:
        if winners is None:
            winners = []
        async with self.raffle_config.guild(guild).all() as guild_data:
            raffle = guild_data["raffles"].pop(str(message_id), None)
            if raffle is not None:
                raffle["winner_list"] = []
                if len(winners) > 0:
                    raffle["winner_list"] = [winner.id for winner in winners]
                raffle["message_id"] = message_id
                guild_data["raffles_history"].insert(0, raffle)
                if len(guild_data["raffles_history"]) > 5:
                    guild_data["raffles_history"].pop()

    async def pick_winner(
        self,
        guild: discord.Guild,
        channel: Union[
            discord.TextChannel,
            discord.CategoryChannel,
            discord.VoiceChannel,
            discord.StageChannel,
            discord.ForumChannel,
            discord.Thread,
        ],
        message: Optional[discord.Message],
        data: Dict[str, Union[List[int], int, bool]],
        reroll=False,
    ) -> Optional[List[discord.Member]]:
        raffle_embed = None
        if message is not None:
            raffle_embed = await self.get_finished_raffle_embed(guild, message.embeds[0])

        if len(data["entries"]) == 0 and not data["use_buttons"]:
            reaction = None
            if message is not None:
                reaction = next(
                    filter(lambda x: x.emoji == raffle_entry_emoji, message.reactions), None
                )

            if reaction is None:
                await channel.send(
                    "Was unable to find/read the reaction emoji for the "
                    f"{bold(data['title'])} raffle so no winner could be picked."
                )
                return

            data["entries"] = [
                member.id
                for member in [user async for user in reaction.users()]
                if isinstance(member, discord.Member) and not member == guild.me
            ]

        if len(data["entries"]) == 0:
            if raffle_embed is not None:
                await message.edit(embed=raffle_embed, view=None)
            await channel.send(
                "Seems like nobody entered the raffle for "
                f"{bold(data['title'])} so no winner could be picked.",
                reference=message,
            )
            return

        valid_entries = await self.get_valid_winners(guild, data)

        if len(valid_entries) == 0:
            if raffle_embed is not None:
                await message.edit(embed=raffle_embed, view=None)
            await channel.send(
                "Couldn't find any valid entries for the "
                f"{bold(data['title'])} raffle so no winner could be picked.",
                reference=message,
            )
            return

        if reroll:
            output = "Raffle has been rerolled!"
            output = "\n\n".join(
                [
                    output,
                    f"The winner{'s' if len(valid_entries) > 1 else ''} for the {bold(data['title'])} raffle is:",
                ]
            )
        else:
            output = f"The winner{'s' if len(valid_entries) > 1 else ''} for the {bold(data['title'])} raffle is:"

        if len(valid_entries) > data["winners"]:
            while True:
                winners = random.sample(valid_entries, data["winners"])

                if not reroll:
                    break
                if sorted([m.id for m in winners]) != data["winner_list"]:
                    break
        else:
            winners = valid_entries

        if len(winners) == 1:
            winners_string = winners[0].mention
        else:
            mentions = []
            for i, member in enumerate(winners):
                mentions.append(f"{bold(f'{i+1}.')} {member.mention}")

            winners_string = "\n".join(mentions)

        output = "\n\n".join([output, winners_string])

        if len(valid_entries) < data["winners"]:
            output = "\n\n".join(
                [
                    output,
                    f"There was only {bold(str(len(valid_entries)))} valid entries "
                    f"out of the {bold(str(data['winners']))} maximum allowed. So everyone is a winner!",
                ]
            )

        output = "\n\n".join([output, ":tada::tada: Congratulations! :tada::tada:"])

        raffle_embed.add_field(name="Winners", value=winners_string, inline=False)

        await message.edit(embed=raffle_embed, view=None)
        await channel.send(output, reference=message)

        return winners

    async def get_finished_raffle_embed(
        self, guild: discord.Guild, base_embed: discord.Embed
    ) -> discord.Embed:
        raffle_embed = base_embed

        while len(raffle_embed.fields) > 2:
            raffle_embed.remove_field(len(raffle_embed.fields) - 1)

        end_timestamp = int(datetime.now(timezone.utc).timestamp())

        raffle_embed.set_field_at(
            index=0,
            name="Ended on",
            value=f"<t:{end_timestamp}:D> ◈ <t:{end_timestamp}:R>",
            inline=False,
        )
        raffle_embed.set_footer(text=f"Guild raffles brought to you by {guild.me.display_name}!")

        return raffle_embed

    async def get_valid_winners(self, guild: discord.Guild, data: dict) -> List[discord.Member]:
        members: List[discord.Member] = []
        for user_id in data["entries"]:
            member = guild.get_member(user_id)
            if member is not None:
                members.append(member)

        if len(members) == 0:
            return []

        if data["dos"] > 0:
            members = [
                member
                for member in members
                if data["dos"] < (member.joined_at.now(timezone.utc) - member.joined_at).days
            ]

        if len(data["roles"]) > 0:
            roles: List[discord.Role] = []
            for role_id in data["roles"]:
                role = guild.get_role(role_id)
                if role is not None:
                    roles.append(role)

            members = [
                member
                for member in members
                if any(
                    role in [member_role.name for member_role in member.roles] for role in roles
                )
            ]

        return members

    async def raffle_setup(
        self,
        ctx: commands.Context,
        end_timestamp: int,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
    ) -> Optional[RaffleSettings]:
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.set_author(
            name=f"{ctx.guild.name} Raffle!", icon_url=self.bot.user.display_avatar.url
        )

        embed.add_field(
            name="Ends", value=f"<t:{end_timestamp}:D> ◈ <t:{end_timestamp}:R>", inline=False
        )

        embed.add_field(name="Hosted By", value=ctx.author.mention, inline=False)

        embed.add_field(name="Allowed Roles", value="@everyone", inline=True)

        embed.add_field(name="Winners Pulled", value=1)

        embed.set_footer(
            text="Click the button to enter the raffle. If interaction fails, try again later. Bot might be down."
        )

        embed_message = await ctx.send(embed=embed, view=RaffleView(preview=True))

        view = RaffleSetupView(self.bot, ctx, embed, embed_message, end_timestamp)
        message = await ctx.send(
            "\n\n".join(
                [
                    f"This is a preview of how the embed will look when sent in {channel.mention}",
                    f"Use the buttons below to customize it and click {inline('Finished')} when done. "
                    "Keep in mind that you can search in the list of roles.",
                ]
            ),
            view=view,
        )
        timed_out = await view.wait()
        if timed_out:
            await message.edit(content="Raffle creation timed out!", view=None)
            return

        if not view.result:
            return

        await message.delete()
        return RaffleSettings(embed=view.embed, roles=view.roles, use_buttons=view.use_buttons)


class ItemSelectView(discord.ui.View):
    def __init__(
        self,
        raffles: List[Dict[str, Union[int, str]]],
        use_cancel=True,
        selection_label: str = None,
    ):
        super().__init__(timeout=30)

        self.raffles = raffles
        self.result: bool = False
        self.value: Optional[int] = None

        self.add_item(ItemSelect(self.raffles, selection_label))

        if use_cancel:
            self.add_item(CancelButton(row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await interaction.client.is_owner(interaction.user):
            return False
        return True


class ItemSelect(discord.ui.Select):
    def __init__(self, raffles: List[Dict[str, Union[int, str]]], placeholder: Optional[str]):
        options: List[discord.SelectOption] = []
        for i, raffle in enumerate(raffles):
            options.append(
                discord.SelectOption(
                    label=raffle["title"],
                    value=raffle["id"],
                    emoji=numbered_emojis[i],
                )
            )

        super().__init__(options=options, row=0, placeholder=placeholder)

    async def callback(self, interaction: discord.Interaction) -> Any:
        self.view.result = True
        self.view.value = self.values[0]
        await interaction.response.defer()
        self.view.stop()


class CancelButton(discord.ui.Button):
    def __init__(self, row: int = 0):
        super().__init__(style=discord.ButtonStyle.red, label="Cancel", row=max(0, min(row, 4)))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.defer()
        self.view.stop()


class RaffleButtonType(Enum):
    title = 0
    description = 1
    url = 2
    roles = 3
    winners = 4
    dos = 5


class RaffleView(discord.ui.View):
    def __init__(self, **kwargs):
        super().__init__(timeout=None)
        self.config: Config = kwargs.get("config")
        self.roles: List[discord.Role] = kwargs.get("roles", [])
        self.dos: int = kwargs.get("dos", None)
        self.preview: bool = kwargs.get("preview", False)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.preview:
            await interaction.response.defer()
            return False

        if self.dos > 0:
            if (
                self.dos
                > (interaction.user.joined_at.now(timezone.utc) - interaction.user.joined_at).days
            ):
                await interaction.response.send_message(
                    "\n\n".join(
                        [
                            f"You don't meet the {inline('Days on Server')} requirement of {bold(str(self.dos))} days.",
                            "You'd have to continue being a member for "
                            f"{bold(humanize_timedelta(timedelta=(datetime.now(timezone.utc) + timedelta(days=self.dos)) - interaction.user.joined_at))} to enter!",
                        ]
                    ),
                    ephemeral=True,
                    delete_after=20,
                )
                return False

        if len(self.roles) > 0:
            if not any(role in interaction.user.roles for role in self.roles):
                await interaction.response.send_message(
                    "You don't have any of the required roles to interact with this raffle!",
                    ephemeral=True,
                    delete_after=20,
                )
                return False

        return True

    @discord.ui.button(
        label="Enter Raffle!",
        style=discord.ButtonStyle.green,
        emoji=raffle_entry_emoji,
        custom_id="raffle_entry",
    )
    async def entry_button(self, interaction: discord.Interaction, button: discord.Button):
        async with self.config.guild(interaction.guild).raffles() as raffles:
            entries = raffles[str(interaction.message.id)]["entries"]
            if interaction.user.id in entries:
                raffles[str(interaction.message.id)]["entries"].remove(interaction.user.id)
                entered = False
            else:
                raffles[str(interaction.message.id)]["entries"].append(interaction.user.id)
                entered = True

            entrants = raffles[str(interaction.message.id)]["entries"]

        if len(entrants) == 0:
            button.label = "Enter Raffle!"
        else:
            button.label = f"Enter Raffle! ◈ {len(entrants)}"

        await interaction.message.edit(view=self)

        if entered:
            await interaction.response.send_message(
                "Successfully entered the raffle!", ephemeral=True, delete_after=30
            )
        else:
            await interaction.response.send_message(
                "Removed your entry from the raffle!", ephemeral=True, delete_after=30
            )


class RaffleSetupView(discord.ui.View):
    def __init__(
        self,
        bot: Red,
        ctx: commands.Context,
        embed: discord.Embed,
        embed_message: discord.Message,
        timestamp: int,
    ):
        super().__init__(timeout=60 * 5)
        self.bot = bot
        self.ctx = ctx
        self.embed = embed
        self.embed_message = embed_message
        self.end_time = datetime.fromtimestamp(timestamp, timezone.utc)
        self.result: bool = False
        self.roles: List[discord.Role] = []
        self.use_buttons: bool = True

        self.winners_select = WinnerSelect(view=self, row=3)

        self.add_item(self.winners_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await interaction.client.is_owner(interaction.user):
            return False
        return True

    async def on_timeout(self) -> None:
        await self.embed_message.delete()

    @discord.ui.button(label="Finished", style=discord.ButtonStyle.green, row=0, disabled=True)
    async def finished_button(self, interaction: discord.Interaction, button: discord.Button):
        await self.embed_message.delete()
        if datetime.now(timezone.utc) > self.end_time:
            await interaction.message.edit(
                content="The time this raffle was supposed to end has already passed. Try again!",
                view=None,
            )
        else:
            self.result = True

        self.stop()

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.red,
        row=0,
    )
    async def cancel_button(self, interaction: discord.Interaction, button: discord.Button):
        await self.embed_message.delete()
        await interaction.message.edit(content="Cancelled raffle creation!", view=None)
        self.stop()

    @discord.ui.button(
        label="Switch to reaction based entry",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def switch_type_button(self, interaction: discord.Interaction, button: discord.Button):
        self.use_buttons = not self.use_buttons
        if self.use_buttons:
            self.switch_type_button.label = "Switch to reaction based entry"

            self.embed.set_footer(
                text="Click the button to enter the raffle. If interaction fails, try again later. Bot might be down."
            )
            await self.embed_message.clear_reactions()
            await self.embed_message.edit(embed=self.embed, view=RaffleView(preview=True))
        else:
            self.switch_type_button.label = "Switch to button based entry"

            self.embed.set_footer(text="React with the ticket emoji below to enter the raffle.")
            await self.embed_message.edit(embed=self.embed, view=None)
            await self.embed_message.add_reaction(raffle_entry_emoji)

        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            success("Successfully swapped the method of entry for the raffle!"),
            ephemeral=True,
            delete_after=10,
        )

    @discord.ui.button(label="Add Title", style=discord.ButtonStyle.primary, row=1)
    async def title_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.send_modal(
            RaffleModal(
                "Title",
                discord.ui.TextInput(
                    label="Raffle Title",
                    style=discord.TextStyle.short,
                    required=True,
                    default=self.embed.title,
                    max_length=256,
                ),
                self,
                RaffleButtonType.title,
            )
        )

    @discord.ui.button(label="Add Description", style=discord.ButtonStyle.grey, row=1)
    async def description_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.send_modal(
            RaffleModal(
                "Description",
                discord.ui.TextInput(
                    label="Raffle Description",
                    style=discord.TextStyle.paragraph,
                    required=False,
                    default=self.embed.description,
                    max_length=4000,
                ),
                self,
                RaffleButtonType.description,
            )
        )

    @discord.ui.button(label="Add Link", style=discord.ButtonStyle.grey, row=1)
    async def link_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.send_modal(
            RaffleModal(
                "Link",
                discord.ui.TextInput(
                    label="Raffle Website Link",
                    style=discord.TextStyle.short,
                    required=False,
                    default=self.embed.url,
                    min_length=10,
                ),
                self,
                RaffleButtonType.url,
            )
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Set Allowed Roles (Leave empty for @everyone)",
        min_values=0,
        max_values=5,
        row=2,
    )
    async def roles_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if select.values == self.roles:
            return

        if len(select.values) == 0:
            self.roles = []
        else:
            self.roles = select.values

        roles_string = (
            " ".join([role.mention for role in self.roles]) if len(self.roles) > 0 else "@everyone"
        )
        self.embed.set_field_at(index=2, name="Allowed Roles", value=roles_string, inline=True)
        await self.embed_message.edit(embed=self.embed)

        await interaction.message.edit(view=self)
        return await interaction.response.send_message(
            content=success("Successfully changed the allowed roles for the raffle!"),
            ephemeral=True,
            delete_after=10,
        )

    @discord.ui.button(
        label="Add Days on Server requirement", style=discord.ButtonStyle.grey, row=4
    )
    async def dos_button(self, interaction: discord.Interaction, button: discord.Button):
        default_value = 0
        if len(self.embed.fields) > 4:
            default_value = self.embed.fields[4].value

        await interaction.response.send_modal(
            RaffleModal(
                "Days on Server",
                discord.ui.TextInput(
                    label="Days on Server requirement",
                    style=discord.TextStyle.short,
                    required=True,
                    default=default_value,
                ),
                self,
                RaffleButtonType.dos,
            )
        )


class RaffleModal(discord.ui.Modal):
    def __init__(
        self,
        title: str,
        discord_ui: discord.ui.TextInput,
        view: RaffleSetupView,
        buttontype: RaffleButtonType,
    ):
        super().__init__(title=title)

        self.answer = discord_ui
        self.embed_message = view.embed_message
        self.embed = view.embed
        self.view = view

        self.buttontype = buttontype

        self.output = ""

        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await interaction.client.is_owner(interaction.user):
            return

        if self.buttontype is RaffleButtonType.title:
            self.output = "Successfully changed the raffle title."
            self.view.title_button.label = "Change Title"
            self.view.title_button.style = discord.ButtonStyle.grey
            self.view.finished_button.disabled = False

            self.embed.title = self.answer.value
        elif self.buttontype is RaffleButtonType.description:
            if self.answer.value == "":
                self.output = "Cleared the raffle description."
                self.view.description_button.label = "Add Description"
            else:
                self.output = "Successfully changed the raffle description."
                self.view.description_button.label = "Change Description"

            self.embed.description = self.answer.value
        elif self.buttontype is RaffleButtonType.url:
            if self.answer.value == "":
                self.output = "Cleared the raffle url."
                self.view.link_button.label = "Add Link"
            else:
                self.output = "Successfully changed the raffle website url."
                self.view.link_button.label = "Change Link"

            self.embed.url = self.answer.value
        elif self.buttontype is RaffleButtonType.dos:
            try:
                int(self.answer.value)
            except ValueError:
                return await interaction.response.send_message(
                    warning(f"The value for {inline('Days on Server')} has to be a number."),
                    ephemeral=True,
                    delete_after=10,
                )

            if int(self.answer.value) <= 0:
                if len(self.embed.fields > 4):
                    return

                self.output = f"Removed the {inline('Days on Server')} requirement."
                self.embed.remove_field(4)

                self.view.dos_button.label = "Add Days on Server requirement"
            else:
                self.output = f"Set the {inline('Days on Server')} requirement to {bold(str(self.answer.value))}."

                if len(self.embed.fields) > 4:
                    self.embed.set_field_at(
                        index=4,
                        name="Days on Server to Enter",
                        value=int(self.answer.value),
                        inline=True,
                    )
                else:
                    self.embed.add_field(
                        name="Days On Server To Enter", value=int(self.answer.value), inline=True
                    )

                    self.view.dos_button.label = "Change Days on Server requirement"

        self.view.embed = self.embed

        await self.update_embed()

        await interaction.message.edit(view=self.view)

        return await interaction.response.send_message(
            success(self.output), ephemeral=True, delete_after=10
        )

    async def update_embed(self):
        await self.embed_message.edit(embed=self.embed)

    async def on_error(self, interaction: discord.Interaction, exception: Exception) -> None:
        if type(exception) is discord.HTTPException:
            self.embed.url = ""
            self.view.link_button.label = "Add Link"
            return await interaction.response.send_message(
                error("Failed to set the url. Are you sure it's a proper link?"),
                ephemeral=True,
                delete_after=10,
            )

        if type(exception) is discord.NotFound:
            return await interaction.response.send_message(
                error("Failed to submit response. Message has likely timed out."),
                ephemeral=True,
                delete_after=10,
            )

        await interaction.response.send_message(
            error(
                "A unknown error has occured and has been logged. "
                "If you'd like to help out resolving it. Post a bug report in the support server "
                f"which you can join with {inline('-support')}"
            ),
            ephemeral=True,
            delete_after=20,
        )
        log.exception("Unhandled exception in raffle setup modal.", exc_info=exception)


class WinnerSelect(discord.ui.Select):
    def __init__(self, view: RaffleSetupView, row: Optional[int] = None):
        self.embed = view.embed
        self.embed_message = view.embed_message
        self.parent_view = view
        options: List[discord.SelectOption] = []
        for i in range(1, 11):
            options.append(
                discord.SelectOption(
                    label=str(i),
                    value=str(i),
                    emoji=numbered_emojis[i - 1],
                )
            )

        super().__init__(
            placeholder="Change how many winners to pull",
            min_values=0,
            max_values=1,
            options=options,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        if len(self.values) == 0 or self.values[0] == self.embed.fields[3].value:
            return await interaction.response.defer()

        self.embed.set_field_at(index=3, name="Winners Pulled", value=self.values[0], inline=True)
        self.parent_view.embed = self.embed

        await self.embed_message.edit(embed=self.embed)

        await interaction.message.edit(view=self.parent_view)

        return await interaction.response.send_message(
            success("Successfully changed the amount of winners that get picked for the raffle!"),
            ephemeral=True,
            delete_after=10,
        )
