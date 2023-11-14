import asyncio
import itertools
import logging
import random
import re
from abc import ABC
from datetime import datetime, timedelta
from typing import Dict, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import error, escape, humanize_timedelta, info, pagify
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate

from .converters import MULTI_RE, TIME_RE, PollOptions
from .polls import Poll
from .raffle import Raffle

log = logging.getLogger("red.angiedale.utility")

EMOJI_RE = re.compile(r"<a?:[a-zA-Z0-9\_]+:([0-9]+)>")


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass


class Utility(commands.Cog, Raffle, metaclass=CompositeMetaClass):
    """Utility commands."""

    raffle_guild_defaults = {
        "raffles": {},
        "raffles_history": [],
        "notification_role_id": None,
    }

    poll_guild_defaults = {
        "polls": {},
        "embed": True,
    }

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.polls: Dict[int, Dict[int, Poll]] = {}
        self.close_loop = True

        self.raffle_config = Config.get_conf(
            self, identifier=1387000, cog_name="UtilityRaffle", force_registration=True
        )
        self.poll_config = Config.get_conf(
            self, identifier=1387000, cog_name="UtilityReactPoll", force_registration=True
        )

        self.raffle_config.register_guild(**self.raffle_guild_defaults)
        self.poll_config.register_guild(**self.poll_guild_defaults)
        self.poll_config.register_global(polls=[])

        self._poll_loader_task = asyncio.create_task(self.load_polls())
        self.poll_task = asyncio.create_task(self.poll_closer())
        self._raffle_load_task = asyncio.create_task(self.load_raffles())

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    def cog_unload(self):
        self.close_loop = False
        if self._poll_loader_task:
            self._poll_loader_task.cancel()
        if self.poll_task:
            self.poll_task.cancel()
        if self._raffle_load_task:
            self._raffle_load_task.cancel()

    @commands.group(name="random", aliases=["rand", "rnd"])
    async def random(self, ctx: commands.Context):
        """Draw/Pick/Roll random things."""

    @random.command(name="pick", aliases=["choose", "random", "draw"])
    async def _pick(self, ctx: commands.Context, *items):
        """Chooses/picks a random item from N multiple items.

        To denote multiple-word items, you should use double quotes.

        Example:
        - `[p]random pick item1 "item 2" "a 3rd item"`
        """
        items = [escape(c, mass_mentions=True) for c in items]
        if len(items) < 1:
            await ctx.send(error("Not enough items to pick from."))
        else:
            await ctx.send(
                info("From {} items, I pick: {}".format(len(items), random.choice(items)))
            )

    @random.command(name="pickx", aliases=["choosex", "randomx", "drawx"])
    async def _pickx(self, ctx: commands.Context, x: int, *items):
        """From a set of N items, chooses/picks X items and display them.

        This is random choosing with replacement (Can be duplicate), and is the same as using the "pick" command multiple times.
        To denote multiple-word items, use double quotes.
        """
        items = [escape(c, mass_mentions=True) for c in items]
        if x < 1:
            await ctx.send(error("Must pick a positive number of items."))
        elif len(items) < 1:
            await ctx.send(error("Not enough items to pick from."))
        else:
            await ctx.send(
                info(
                    "From {} items, I pick: {}".format(
                        len(items), ", ".join(random.choices(items, k=x))
                    )
                )
            )

    @random.command(name="pickuniquex", aliases=["chooseuniquex", "randomuniquex", "drawuniquex"])
    async def _pickuniquex(self, ctx: commands.Context, x: int, *items):
        """From a set of N items, chooses/picks X items and display them.

        This is random drawing without replacement (No dupllicates).
        To denote multiple-word items, use double quotes."""
        items = [escape(c, mass_mentions=True) for c in items]
        if x < 1:
            await ctx.send(error("Must draw a positive number of items."))
        elif len(items) < 1 or len(items) < x:
            await ctx.send(error("Not enough items to draw from."))
        else:
            drawn = random.sample(range(len(items)), x)
            drawn = [items[i] for i in sorted(drawn)]
            await ctx.send(info("From {} items, I draw: {}".format(len(items), ", ".join(drawn))))

    @random.command(name="mix", aliases=["shuffle"])
    async def _mix(self, ctx: commands.Context, *items):
        """Shuffles/mixes a list of items.

        To denote multiple-word items, use double quotes.

        Example:
        - `[p]random mix item1 "item 2" "a 3rd item"`
        """
        items = [escape(c, mass_mentions=True) for c in items]
        if len(items) < 1:
            await ctx.send(error("Not enough items to shuffle."))
        else:
            await ctx.send(
                info(
                    "A randomized order of {} items: {}".format(
                        len(items), ", ".join(random.shuffle(items))
                    )
                )
            )

    @random.command(name="dice", aliases=["rolldice", "rolld", "roll"], usage=["[arguments]"])
    async def _dice(self, ctx: commands.Context, *bounds):
        """Rolls the specified single or multiple dice.

        Defaults to a single 6-sided die.

        **Arguments:**

        A single number `X`: Rolls one `X`-sided die (Example: `[p]random dice 17`).
        Two numbers `X` and `Y`: Rolls a die with a minimum `X` and maximum `Y` (Example: `[p]random dice 3 8`).
        The notation `NdX`: Rolls `N` dice with `X` sides (Example: `[p]random dice 3d20`).
        The `NdX` notation can be used multiple times with different dice in one command. If multiple dice are used, statistics will be shown.
        """
        bounds_string = " ".join(bounds).lower()
        if "d" in bounds_string:
            # Dice specifiers: Remove the spaces around "d" (so "1 d6" -> "1d6"
            while " d" in bounds_string or "d " in bounds_string:
                bounds = bounds_string.replace(" d", "d").replace("d ", "d").split(" ")
                bounds_string = " ".join(bounds)

        if len(bounds) == 0:
            # [p]random dice
            bounds = ["6"]
            # Fall through to "[p]random dice 6"

        if len(bounds) == 1:
            if bounds[0].isnumeric():
                # [p]random dice X
                # provided maximum roll is between 1 and X
                roll_max = int(bounds[0])
                await self._roll_dice(ctx, 1, roll_max)
                return

        if len(bounds) == 2:
            if bounds[0].isnumeric() and bounds[1].isnumeric():
                # [p]random dice X Y
                # provided minimum and maximum roll is between X and Y
                roll_min = int(bounds[0])
                roll_max = int(bounds[1])
                await self._roll_dice(ctx, roll_min, roll_max)
                return

        # Got here. Must have been non-numeric objects, possibly containing "d" dice specifiers?
        dice = []
        try:
            for spec in bounds:
                spec = spec.strip(",()")
                if not "d" in spec:
                    raise ValueError("Invalid input.")

                spspec = spec.split("d")
                if len(spspec) != 2:
                    raise ValueError("Invalid dice.")

                if len(spspec[0]) == 0:
                    roll_multiplier = 1
                elif spspec[0].isnumeric():
                    roll_multiplier = int(spspec[0])
                    if roll_multiplier < 1:
                        raise ValueError("Non-positive number of dice.")
                else:
                    raise ValueError("Non-numeric number of dice.")

                if spspec[1].isnumeric():
                    roll_max = int(spspec[1])
                    if roll_max < 1:
                        raise ValueError("Non-positive side count on dice.")
                    elif roll_max >= 10e100:
                        raise ValueError("Side count on dice too large.")
                else:
                    raise ValueError("Non-numeric side count on dice.")

                if len(dice) + roll_multiplier >= 1000:
                    dice = []
                    raise ValueError("Number of dice too large (over 999).")

                dice += itertools.repeat(roll_max, roll_multiplier)
        except ValueError as ex:
            await ctx.send(error(str(ex)))
            return

        if len(dice) == 0:
            await ctx.send(error("No collected dice to use."))
            return

        if len(dice) == 1:
            # One die
            await self._roll_dice(ctx, 1, dice[0])
            return

        dice_roll = [random.randint(1, X) for X in dice]

        dice_string = ""
        if len(dice) < 100:
            dice_string = "\r\nValues: {}".format(", ".join(["`{}`".format(x) for x in dice_roll]))

        await ctx.send(
            info(
                "Collected and rolled {die_count:,} dice!{values}\r\nTotal number of sides: {side_count:,}\r\n**Total value: {total_sum:,}  Average value: {total_avg:,.2f}**".format(
                    die_count=len(dice),
                    values=dice_string,
                    side_count=sum(dice),
                    total_sum=sum(dice_roll),
                    total_avg=sum(dice_roll) / len(dice),
                )
            )
        )

    async def _roll_dice(self, ctx: commands.Context, roll_min: int, roll_max: int) -> None:
        """Perform and print a single dice roll."""
        if roll_min >= 10e100:
            await ctx.send(error("Minimum value too large."))
            return
        if roll_max >= 10e100:
            await ctx.send(error("Maximum value too large."))
            return
        roll_sides = roll_max - roll_min + 1
        strange = "strange "
        a_an = "a"
        roll_range = ""
        if roll_min == 1:
            if roll_max in [4, 6, 8, 10, 12, 20]:
                strange = ""
                if roll_max == 8:
                    a_an = "an"
        else:
            roll_range = " ({:,} to {:,})".format(roll_min, roll_max)
        if roll_max < roll_min:
            await ctx.send(
                error("Between {} and {} is not a valid range.".format(roll_min, roll_max))
            )
        else:
            random_output = random.randint(roll_min, roll_max)
            await ctx.send(
                info(
                    "I roll {} {}{}-sided die{}, and it lands on: **{:,}**".format(
                        a_an, strange, roll_sides, roll_range, random_output
                    )
                )
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """
        Handle votes for polls
        """
        await self.bot.wait_until_red_ready()
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        if guild.id not in self.polls:
            # log.info(f"No polls in guild {payload.guild_id}")
            return
        if payload.message_id not in self.polls[guild.id]:
            # log.info(f"No polls in message {payload.message_id}")
            return
        poll = self.polls[guild.id][payload.message_id]
        await poll.add_vote(payload.user_id, str(payload.emoji))

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """
        Handle votes for polls
        """
        await self.bot.wait_until_red_ready()
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        if guild.id not in self.polls:
            # log.info(f"No polls in guild {payload.guild_id}")
            return
        if payload.message_id not in self.polls[guild.id]:
            # log.info(f"No polls in message {payload.message_id}")
            return
        poll = self.polls[guild.id][payload.message_id]
        await poll.remove_vote(payload.user_id, str(payload.emoji))

    async def poll_closer(self) -> None:
        await self.bot.wait_until_red_ready()
        while self.close_loop:
            # consider making < 60 second polls not use config + this task
            await asyncio.sleep(5)
            # log.debug("Checking for ended polls")
            now_time = datetime.utcnow()
            count = 0
            try:
                for g_id, polls in self.polls.items():
                    to_remove = []
                    for m_id, poll in polls.items():
                        if isinstance(poll.end_time, float):
                            poll.end_time = datetime.utcfromtimestamp(poll.end_time)
                        if isinstance(poll.end_time, int):
                            poll.end_time = datetime.utcfromtimestamp(poll.end_time)
                        if poll.end_time and poll.end_time <= now_time:
                            log.debug("ending poll")
                            try:
                                await poll.close_poll()
                            except Exception:
                                pass
                            # probs a better way to do this
                            to_remove.append(m_id)
                            # also need to delete from config
                            guild = discord.Object(id=g_id)
                            await self.delete_poll(guild, poll)
                        if count // 10:
                            count = 0
                            await self.store_poll(poll)
                        else:
                            count += 1
                    for m_id in to_remove:
                        del self.polls[g_id][m_id]
            except Exception as e:
                log.error("Error checking for ended polls", exc_info=e)

    async def delete_poll(self, guild: discord.Guild, poll: Poll) -> None:
        async with self.poll_config.guild(guild).polls() as polls:
            if str(poll.message_id) in polls:
                del polls[str(poll.message_id)]

    async def store_poll(self, poll: Poll) -> None:
        try:
            async with self.poll_config.guild(poll.guild).polls() as polls:
                polls[str(poll.message_id)] = poll.as_dict()
        except AttributeError:
            # The guild no longer exists or the channel was deleted.
            return

    async def load_polls(self) -> None:
        # unfortunately we have to deal with an issue where JSON
        # serialization fails if the config default list is used
        all_polls = await self.poll_config.all_guilds()

        for g_id, polls in all_polls.items():
            if g_id not in self.polls:
                self.polls[g_id] = {}
            for m_id, poll in polls["polls"].items():
                self.polls[g_id][int(m_id)] = Poll(self.bot, **poll)

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def pollset(self, ctx: commands.Context):
        """
        Settings for reaction polls
        """

    @pollset.command(name="embed", aliases=["embeds"])
    async def rpoll_set_embed(self, ctx: commands.Context):
        """
        Toggle embed usage for polls in this server
        """
        curr_setting = await self.poll_config.guild(ctx.guild).embed()
        await self.poll_config.guild(ctx.guild).embed.set(not curr_setting)
        if curr_setting:
            verb = "off"
        else:
            verb = "on"
        await ctx.send(f"Reaction poll embeds turned {verb}.")

    @commands.mod_or_permissions(manage_messages=True)
    @commands.group()
    @commands.guild_only()
    async def poll(self, ctx: commands.Context):
        """Commands for setting up reaction polls"""
        pass

    @poll.command(name="end", aliases=["close"])
    async def end_poll(self, ctx: commands.Context, poll_id: int):
        """
        Manually end a poll

        `<poll_id>` is the message ID for the poll.
        """
        if ctx.guild.id not in self.polls:
            return await ctx.send("There are no polls on this server.")
        if poll_id not in self.polls[ctx.guild.id]:
            return await ctx.send("That is not a valid poll message ID.")
        poll = self.polls[ctx.guild.id][poll_id]
        await poll.close_poll()
        await ctx.tick()

    async def handle_pagify(self, ctx: commands.Context, msg: str):
        for page in pagify(msg):
            await ctx.send(page)

    @poll.command(name="interactive")
    async def rpoll_interactive(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Interactive reaction poll creator

        Provide the channel to send the poll to. [botname] will ask
        you what the poll question will be and then ask you to provide
        options for the poll including emojis to be used.
        """
        if not channel.permissions_for(ctx.me).send_messages:
            return await ctx.send(
                f"I do not have permission to send messages in {channel.mention}"
            )
        poll_options = {
            "emojis": {},
            "options": [],
            "interactive": True,
            "author_id": ctx.author.id,
        }
        default_emojis = ReactionPredicate.NUMBER_EMOJIS + ReactionPredicate.ALPHABET_EMOJIS
        poll_options["channel_id"] = channel.id
        await ctx.send(
            "Enter the poll question. Entering `exit` at any time will end poll creation."
        )
        interactive = True
        count = 0
        while interactive:
            try:
                msg = await self.bot.wait_for(
                    "message", check=MessagePredicate.same_context(ctx), timeout=30
                )
            except asyncio.TimeoutError:
                await ctx.send("Poll creation ended due to timeout.")
                return
            if msg.content == "exit":
                interactive = False
                break
            if not msg.content:
                if msg.attachments:
                    await ctx.send("Polls cannot handle attachments. Try again.")
                continue
            if count > 20:
                await ctx.send("Maximum number of options provided.")
                interactive = False
                continue
            if count == 0:
                if not msg.content.endswith("?"):
                    await ctx.send("That doesn't look like a question, try again.")
                    continue
                else:
                    poll_options["question"] = msg.content
                    await ctx.send(
                        "Enter the options for the poll. Enter an emoji at the beginning of the message if you want to use custom emojis for the option counters."
                    )
                    count += 1
                    continue
            custom_emoji = EMOJI_RE.match(msg.content)
            time_match = TIME_RE.match(msg.content)
            multi_match = MULTI_RE.match(msg.content)
            if multi_match:
                poll_options["multiple_votes"] = True
                await ctx.send("Allowing multiple votes for this poll.")
                continue
            if time_match:
                time_data = {}
                for time in TIME_RE.finditer(msg.content):
                    for k, v in time.groupdict().items():
                        if v:
                            time_data[k] = int(v)
                poll_options["duration"] = timedelta(**time_data)
                await ctx.send(
                    f"Duration for the poll set to {humanize_timedelta(timedelta=poll_options['duration'])}"
                )
                continue
            if custom_emoji:
                if custom_emoji.group(0) in poll_options["emojis"]:
                    await ctx.send("That emoji option is already being used.")
                    continue
                try:
                    await msg.add_reaction(custom_emoji.group(0))
                    poll_options["emojis"][custom_emoji.group(0)] = msg.content.replace(
                        custom_emoji.group(0), ""
                    )
                    await ctx.send(
                        f"Option {custom_emoji.group(0)} set to {msg.content.replace(custom_emoji.group(0), '')}"
                    )
                    poll_options["options"].append(msg.content.replace(custom_emoji.group(0), ""))
                except Exception:
                    poll_options["emojis"][default_emojis[count]] = msg.content
                    poll_options["options"].append(msg.content)
                    await self.handle_pagify(
                        ctx, f"Option {default_emojis[count]} set to {msg.content}"
                    )
                count += 1
                continue
            else:
                try:
                    maybe_emoji = msg.content.split(" ")[0]
                    if maybe_emoji in poll_options["emojis"]:
                        await ctx.send("That emoji option is already being used.")
                        continue
                    await msg.add_reaction(maybe_emoji)
                    poll_options["emojis"][maybe_emoji] = " ".join(msg.content.split(" ")[1:])
                    poll_options["options"].append(" ".join(msg.content.split(" ")[1:]))
                    await self.handle_pagify(
                        ctx, f"Option {maybe_emoji} set to {' '.join(msg.content.split(' ')[1:])}"
                    )
                except Exception:
                    poll_options["emojis"][default_emojis[count]] = msg.content
                    poll_options["options"].append(msg.content)
                    await self.handle_pagify(
                        ctx, f"Option {default_emojis[count]} set to {msg.content}"
                    )
                count += 1
                continue
        if not poll_options["emojis"]:
            return await ctx.send("No poll created.")
        new_poll = Poll(self.bot, **poll_options)
        text, em = await new_poll.build_poll()
        if new_poll.embed:
            sample_msg = await ctx.send("Is this poll good?", embed=em)
        else:
            for page in pagify(f"Is this poll good?\n\n{text}"):
                sample_msg = await ctx.send(page)
        start_adding_reactions(sample_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = ReactionPredicate.yes_or_no(sample_msg, ctx.author)
        try:
            await ctx.bot.wait_for("reaction_add", check=pred)
        except asyncio.TimeoutError:
            await ctx.send("Not making poll.")
            return
        if pred.result:
            await new_poll.open_poll()
            if ctx.guild.id not in self.polls:
                self.polls[ctx.guild.id] = {}
            self.polls[ctx.guild.id][new_poll.message_id] = new_poll
            await self.store_poll(new_poll)
        else:
            await ctx.send("Not making poll.")

    @poll.command(name="new", aliases=["create"])
    async def rpoll_create(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
        *,
        poll_options: PollOptions,
    ):
        """
        Start a reaction poll

        `[channel]` is the optional channel you want to send the poll to. If no channel is provided
        it will default to the current channel.
        `<poll_options>` is a formatted string of poll options.
        The question is everything before the first occurance of `?`.
        The options are a list separated by `;`.
        The time the poll ends is a space separated list of units of time.
        if `multi-vote` is provided anywhere in the creation message the poll
        will allow users to vote on multiple choices.

        Example format (time argument is optional):
        `[p]rpoll new Is this a poll? Yes;No;Maybe; 2 hours 21 minutes 40 seconds multi-vote`
        """
        if not channel:
            send_channel = ctx.channel
        else:
            send_channel = channel
        if not send_channel.permissions_for(ctx.me).send_messages:
            return await ctx.send(
                f"I do not have permission to send messages in {send_channel.mention}"
            )
        poll_options["channel_id"] = send_channel.id
        # allow us to specify new channel for the poll

        guild = ctx.guild
        # log.info(poll_options)
        embed = (
            await self.poll_config.guild(guild).embed()
            and send_channel.permissions_for(ctx.me).embed_links
        )
        poll_options["embed"] = embed
        poll = Poll(self.bot, **poll_options)

        await poll.open_poll()
        if guild.id not in self.polls:
            self.polls[guild.id] = {}
        self.polls[guild.id][poll.message_id] = poll
        await self.store_poll(poll)
