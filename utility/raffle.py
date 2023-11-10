import asyncio
import calendar
import logging
import random
from datetime import datetime
from typing import List, Union

import discord
from redbot.core import commands
from redbot.core.utils.predicates import MessagePredicate

from .abc import MixinMeta

log = logging.getLogger("red.angiedale.utility")


class Raffle(MixinMeta):
    """Raffle/Giveaways."""

    @commands.group()
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def raffle(self, ctx: commands.Context):
        """Raffles/Giveaways."""

    @raffle.command(hidden=True)
    @commands.is_owner()
    async def clear(self, ctx: commands.Context):
        await self.raffle_config.guild(ctx.guild).Raffles.clear()
        await ctx.send("Raffle data cleared out.")

    @raffle.command()
    async def start(self, ctx: commands.Context, time: str, *, title: str):
        """Start a raffle/giveaway.

        Time accepts a integer input that represents seconds or it will
        take the format of HH:MM:SS.

        Examples:
        - `[p]raffle start 80`: 1 minute and 20 seconds (80 seconds)
        - `[p]raffle start 30:10`: 30 minutes and 10 seconds
        - `[p]raffle start 24:00:00`: 1 day (24 hours)

        Only one raffle can be active per server.
        """
        if not ctx.channel.permissions_for(ctx.guild.me).embed_links:
            await ctx.send("I need the Embed Links permission to be able to start raffles.")
            return
        if not ctx.channel.permissions_for(ctx.guild.me).add_reactions:
            await ctx.send("I need the Add Reactions permission to be able to start raffles.")
            return
        time = await self.start_checks(ctx, time)
        if time is None:
            return

        try:
            description, url, winners, dos, roles = await self.raffle_setup(ctx)
        except asyncio.TimeoutError:
            return await ctx.send("Response timed out. A raffle failed to start.")
        str_roles = [r[0] for r in roles]
        description = f"{description}\n\nReact to this message with <:KannaPog:755808378210746400> to enter.\n\n"

        channel = await self._get_channel(ctx)
        mention = await self.raffle_config.guild(ctx.guild).Mention()
        fmt_end = calendar.timegm(ctx.message.created_at.utctimetuple()) + time

        if mention:
            mention = ctx.guild.get_role(mention)

        if not mention.is_default():
            mention = mention.mention

        color = await self.bot.get_embed_color(ctx)
        embed = discord.Embed(description=description, title=title, color=color)
        embed.add_field(name="Days on Server", value=f"{dos}")
        role_info = f'{", ".join(str_roles) if roles else "@everyone"}'
        embed.add_field(name="Allowed Roles", value=role_info)
        embed.add_field(name="Hosted by", value=ctx.author.mention)
        if mention:
            msg = await channel.send(
                content=mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
            )
        else:
            msg = await channel.send(embed=embed)
        embed.set_footer(
            text=(
                f"Started by: {ctx.author.name} | Winners: {winners} | Ends at {fmt_end} UTC | Raffle ID: {msg.id}"
            )
        )
        await msg.edit(embed=embed)
        await msg.add_reaction("<:KannaPog:755808378210746400>")

        async with self.raffle_config.guild(ctx.guild).Raffles() as r:
            new_raffle = {
                "Channel": channel.id,
                "Timestamp": fmt_end,
                "DOS": dos,
                "Roles": roles,
                "ID": msg.id,
                "Title": title,
            }
            r[msg.id] = new_raffle

        await self.raffle_timer(ctx.guild, new_raffle, time)

    @raffle.command()
    async def end(self, ctx: commands.Context, message_id: int = None):
        """Ends a raffle early. A winner will still be chosen."""
        if message_id is None:
            try:
                message_id = await self._menu(ctx)
            except ValueError:
                return await ctx.send("There are no active raffles to end.")
            except asyncio.TimeoutError:
                return await ctx.send("Response timed out.")

        try:
            await self.raffle_teardown(ctx.guild, message_id)
        except discord.NotFound:
            await ctx.send("The message id provided could not be found.")
        else:
            await ctx.send("The raffle has been ended.")

    @raffle.command()
    async def cancel(self, ctx: commands.Context, message_id: int = None):
        """Cancels an on-going raffle. No winner is chosen."""
        if message_id is None:
            try:
                message_id = await self._menu(ctx, end="cancel")
            except ValueError:
                return await ctx.send("There are no active raffles to cancel.")
            except asyncio.TimeoutError:
                return await ctx.send("Response timed out.")

        try:
            await self.raffle_removal(ctx, message_id)
        except discord.NotFound:
            await ctx.send("The message id provided could not be found.")
        else:
            await ctx.send("The raffle has been canceled.")
        finally:
            # Attempt to cleanup if a message was deleted and it's still stored in config.
            async with self.raffle_config.guild(ctx.guild).Raffles() as r:
                try:
                    del r[str(message_id)]
                except KeyError:
                    pass

    async def _menu(self, ctx: commands.Context, end="end") -> int:
        title = f"Which of the following **Active** Raffles would you like to {end}?"
        async with self.raffle_config.guild(ctx.guild).Raffles() as r:
            if not r:
                raise ValueError
            raffles = list(r.items())
        color = await self.bot.get_embed_color(ctx)
        embed = self.embed_builder(raffles, color, title)
        msg = await ctx.send(embed=embed)

        def predicate(m):
            if m.channel == ctx.channel and m.author == ctx.author:
                return int(m.content) in range(1, 11)

        resp = await ctx.bot.wait_for("message", timeout=60, check=predicate)
        message_id = raffles[int(resp.content) - 1][0]
        await resp.delete()
        await msg.delete()
        return message_id

    def embed_builder(self, raffles, color, title) -> discord.Embed:
        embeds = []
        # FIXME Come back and make this more dynamic
        truncate = raffles[:10]
        emojis = (
            ":one:",
            ":two:",
            ":three:",
            ":four:",
            ":five:",
            ":six:",
            ":seven:",
            ":eight:",
            ":nine:",
            ":ten:",
        )
        e = discord.Embed(colour=color, title=title)
        description = ""
        for raffle, number_emoji in zip(truncate, emojis):
            description += f"{number_emoji} - {raffle[1]['Title']}\n"
            e.description = description
            e.set_footer(text="Type the number of the raffle you wish to end.")
            embeds.append(e)
        return e

    @raffle.command()
    async def reroll(self, ctx: commands.Context, channel: discord.TextChannel, messageid: int):
        """Reroll the winner for a raffle. Requires the channel and message id."""
        if not channel.permissions_for(channel.guild.me).read_messages:
            return await ctx.send("I can't read messages in that channel.")
        if not channel.permissions_for(channel.guild.me).send_messages:
            return await ctx.send("I can't send messages in that channel.")
        try:
            msg = await channel.fetch_message(messageid)
        except discord.Forbidden:
            return await ctx.send("Invalid message id or I can't view that channel or message.")
        except discord.HTTPException:
            return await ctx.send("Invalid message id or the message doesn't exist.")
        try:
            await self.pick_winner(ctx.guild, channel, msg)
        except AttributeError:
            return await ctx.send("This is not a raffle message.")
        except IndexError:
            return await ctx.send(
                "Nice try slim. You can't add a reaction to a random msg "
                "and think that I am stupid enough to say you won something."
            )

    @raffle.group(name="set")
    @commands.guildowner()
    async def _raffle_set(self, ctx: commands.Context):
        """Change raffle/giveaway settings."""

    @_raffle_set.command(name="channel")
    async def _raffle_set_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the output channel for raffles."""
        if channel:
            await self.raffle_config.guild(ctx.guild).Channel.set(channel.id)
            return await ctx.send(f"Raffle output channel set to {channel.mention}.")
        await self.raffle_config.guild(ctx.guild).Channel.clear()
        await ctx.send("Raffles will now be started where they were created.")

    @_raffle_set.command(name="mention")
    async def _raffle_set_mention(self, ctx: commands.Context, role: discord.Role = None):
        """Set a role I should ping for raffles."""
        if role:
            if role.is_default():
                await self.raffle_config.guild(ctx.guild).Mention.set(role.id)
                return await ctx.send(f"I will now mention {role} for new raffles.")
            else:
                await self.raffle_config.guild(ctx.guild).Mention.set(role.id)
                return await ctx.send(
                    f"I will now mention {role.mention} for new raffles.",
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
        await self.raffle_config.guild(ctx.guild).Mention.clear()
        await ctx.send("I will no longer mention any role for new raffles.")

    async def start_checks(self, ctx: commands.Context, timer: str):
        timer = self.time_converter(timer)
        if timer is None:
            await ctx.send(
                "Incorrect time format. Please use help on this command for more information."
            )
            return None
        else:
            return timer

    async def _get_response(self, ctx: commands.Context, question: str, predicate) -> str:
        question = await ctx.send(question)
        resp = await ctx.bot.wait_for(
            "message",
            timeout=60,
            check=lambda m: (m.author == ctx.author and m.channel == ctx.channel and predicate(m)),
        )
        if ctx.channel.permissions_for(ctx.me).manage_messages:
            await resp.delete()
        await question.delete()
        return resp.content

    async def _get_roles(self, ctx: commands.Context) -> List[tuple]:
        q = await ctx.send(
            "What role or roles are allowed to enter? Use commas to separate "
            "multiple entries. For example: `Admin, Patrons, super mod, helper`"
        )

        def predicate(m):
            if m.author == ctx.author and m.channel == ctx.channel:
                given = set(m.content.split(", "))
                guild_roles = {r.name for r in ctx.guild.roles}
                return guild_roles.issuperset(given)
            else:
                return False

        resp = await ctx.bot.wait_for("message", timeout=60, check=predicate)
        roles = []
        for name in resp.content.split(", "):
            for role in ctx.guild.roles:
                if name == role.name:
                    roles.append((name, role.id))
        await q.delete()
        if ctx.channel.permissions_for(ctx.me).manage_messages:
            await resp.delete()
        return roles

    async def _get_channel(
        self, ctx: commands.Context
    ) -> Union[
        discord.TextChannel,
        discord.CategoryChannel,
        discord.VoiceChannel,
        discord.StageChannel,
        discord.ForumChannel,
        discord.Thread,
    ]:
        channel_id = await self.raffle_config.guild(ctx.guild).Channel()
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = ctx.channel
        return channel

    async def raffle_setup(self, ctx: commands.Context):
        predicate1 = lambda m: len(m.content) <= 1000

        def predicate2(m):
            try:
                if int(m.content) >= 1:
                    return True
                return False
            except ValueError:
                return False

        predicate3 = MessagePredicate.yes_or_no(ctx, ctx.channel, ctx.author)

        def predicate4(m):
            try:
                if int(m.content) > 9:
                    return False
                if int(m.content) >= 0:
                    return True
                return False
            except ValueError:
                return False

        predicate5 = lambda m: m.content.startswith("http")

        q1 = "Please set a brief description (1000 chars max)"
        q2 = "Would you like to link this raffle somewhere?"
        q3 = (
            "Please set how many winners are pulled, __*Maximum of up to and including 9*__.\n**Note**: If there are "
            "more winners than entries, I will make everyone a winner."
        )
        q4 = "Would you like to set a 'days on server' requirement?"
        q5 = "Do you want to limit this raffle to specific roles?"

        description = await self._get_response(ctx, q1, predicate1)
        url = ""

        if await self._get_response(ctx, q2, predicate3) == "yes":
            url = await self._get_response(ctx, "What's the link?", predicate5)

        winners = await self._get_response(ctx, q3, predicate2)
        dos = 0
        roles = []

        resp = await self._get_response(ctx, q3, predicate3)
        if resp.lower() == "yes":
            dos = await self._get_response(
                ctx, "How many days on the server are required?", predicate4
            )

        resp = await self._get_response(ctx, q4, predicate3)
        if resp.lower() == "yes":
            roles = await self._get_roles(ctx)

        return description, url, int(winners), int(dos), roles

    async def raffle_worker(self) -> None:
        """Restarts raffle timers
        This worker will attempt to restart raffle timers incase of a cog reload or
        if the bot has been restart or shutdown. The task is only created when the cog
        is loaded, and is destroyed when it has finished.
        """
        try:
            await self.bot.wait_until_red_ready()
            guilds: List[discord.Guild] = []
            guilds_in_config = await self.raffle_config.all_guilds()
            for guild in guilds_in_config:
                guild_obj = self.bot.get_guild(guild)
                if guild_obj is not None:
                    guilds.append(guild_obj)
                else:
                    continue
            coros = []
            for guild in guilds:
                raffles = await self.raffle_config.guild(guild).Raffles.all()
                if raffles:
                    now = calendar.timegm(datetime.utcnow().utctimetuple())
                    for key, value in raffles.items():
                        remaining = raffles[key]["Timestamp"] - now
                        if remaining <= 0:
                            await self.raffle_teardown(guild, raffles[key]["ID"])
                        else:
                            coros.append(self.raffle_timer(guild, raffles[key], remaining))
            await asyncio.gather(*coros)
        except Exception:
            log.error("Error in raffle_worker task.", exc_info=True)

    async def raffle_timer(self, guild: discord.Guild, raffle: dict, remaining: int) -> None:
        """Helper function for starting the raffle countdown.

        This function will silently pass when the unique raffle id is not found or
        if a raffle is empty. It will call `raffle_teardown` if the ID is still
        current when the sleep call has completed.

        Parameters
        ----------
        guild : Guild
            The guild object
        raffle : dict
            All of the raffle information gained from the config to include:
            ID, channel, message, timestamp, and entries.
        remaining : int
            Number of seconds remaining until the raffle should end
        """
        await asyncio.sleep(remaining)
        async with self.raffle_config.guild(guild).Raffles() as r:
            data = r.get(str(raffle["ID"]))
        if data:
            await self.raffle_teardown(guild, raffle["ID"])

    async def raffle_teardown(self, guild: discord.Guild, message_id: int) -> None:
        errored = False
        raffles = await self.raffle_config.guild(guild).Raffles.all()
        channel = self.bot.get_channel(raffles[str(message_id)]["Channel"])
        if not channel:
            errored = True
        else:
            if (
                not channel.permissions_for(guild.me).read_messages
                or not channel.permissions_for(guild.me).send_messages
            ):
                errored = True
            if not errored:
                try:
                    msg = await channel.fetch_message(raffles[str(message_id)]["ID"])
                except discord.NotFound:
                    # they deleted the raffle message
                    errored = True

        if not errored:
            await self.pick_winner(guild, channel, msg)

        async with self.raffle_config.guild(guild).Raffles() as r:
            try:
                del r[str(message_id)]
            except KeyError:
                pass

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
        msg: discord.Message,
    ) -> None:
        reaction = next(
            filter(lambda x: x.emoji == self.bot.get_emoji(755808378210746400), msg.reactions),
            None,
        )
        if reaction is None:
            return await channel.send(
                "It appears there were no valid entries, so a winner for the raffle could not be picked."
            )
        users = [user async for user in reaction.users() if guild.get_member(user.id)]
        users.remove(self.bot.user)
        try:
            amt = int(msg.embeds[0].footer.text.split("Winners: ")[1][0])
        except AttributeError:  # the footer was not set in time
            return await channel.send(
                "An error occurred, so a winner for the raffle could not be picked."
            )
        valid_entries = await self.validate_entries(users, msg)
        winners = random.sample(valid_entries, min(len(valid_entries), amt))
        if not winners:
            await channel.send(
                "It appears there were no valid entries, so a winner for the raffle could not be picked."
            )
        else:
            display = ", ".join(winner.mention for winner in winners)
            await channel.send(
                f"Congratulations {display}! You have won the {msg.embeds[0].title} raffle!"
            )

    async def validate_entries(self, users, msg) -> list:
        try:
            dos, roles, timestamp = msg.embeds[0].fields
        except ValueError:
            dos, roles = msg.embeds[0].fieldss
        dos = int(dos.value)
        roles = roles.value.split(", ")

        try:
            if dos:
                users = [
                    user for user in users if dos < (user.joined_at.now() - user.joined_at).days
                ]

            if roles:
                users = [
                    user
                    for user in users
                    if any(role in [r.name for r in user.roles] for role in roles)
                ]
        except AttributeError:
            return None
        return users

    async def raffle_removal(self, ctx, message_id) -> None:
        async with self.raffle_config.guild(ctx.guild).Raffles() as r:
            try:
                del r[str(message_id)]
            except KeyError:
                pass

    @staticmethod
    def time_converter(units: str):
        try:
            return sum(int(x) * 60**i for i, x in enumerate(reversed(units.split(":"))))
        except ValueError:
            return None
