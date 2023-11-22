import asyncio
import itertools
import logging
import random
import re
from abc import ABC
from typing import List, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import bold, box, error, escape, info, inline

from .events import Events
from .polls import Polls
from .raffles import Raffles

log = logging.getLogger("red.angiedale.utility")


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass


class Utility(commands.Cog, Raffles, Polls, Events, metaclass=CompositeMetaClass):
    """Extra utility commands.

    __Main Features of Cog__
    - Run **polls** in servers.
    - Host **raffles/giveaways**.
    - Tools for randomly **picking/rolling items**.
    """

    raffle_guild_defaults = {
        "raffles": {},
        "raffles_history": [],
        "notification_role_id": None,
    }

    poll_guild_defaults = {
        "polls": {},
        "polls_history": [],
    }

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.active_poll_tasks: List[asyncio.Task] = []
        self.active_raffle_tasks: List[asyncio.Task] = []
        self.poll_cache_task: Optional[asyncio.Task] = None

        self.raffle_config = Config.get_conf(
            self, identifier=1387000, cog_name="UtilityRaffle", force_registration=True
        )
        self.poll_config = Config.get_conf(
            self, identifier=1387000, cog_name="UtilityPoll", force_registration=True
        )

        self.raffle_config.register_guild(**self.raffle_guild_defaults)
        self.poll_config.register_guild(**self.poll_guild_defaults)

        self._raffle_load_task = asyncio.create_task(self.load_raffles())
        self._poll_load_task = asyncio.create_task(self.load_polls())

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    def cog_unload(self):
        for task in self.active_poll_tasks:
            task.cancel()
        for task in self.active_raffle_tasks:
            task.cancel()
        if self._poll_load_task:
            self._poll_load_task.cancel()
        if self.poll_cache_task:
            self.poll_cache_task.cancel()
        if self._raffle_load_task:
            self._raffle_load_task.cancel()

    @commands.group(name="random", aliases=["rand", "rnd"])
    async def random(self, ctx: commands.Context):
        """Draw/pick/roll random things."""

    @random.command(name="pick", aliases=["choose", "random", "draw"])
    async def _pick(self, ctx: commands.Context, *items):
        """Chooses/picks a random item from a list of items.

        To denote multiple-word items, use double quotes.
        Alternatively you can send a text file with each item on a new line.

        Example:
        - `[p]random pick item1 "item 2" "a 3rd item"`
        """
        if len(ctx.message.attachments) > 0:
            attachement = ctx.message.attachments[0]
            file = await attachement.read()
            items = file.decode("utf-8").splitlines()

        items = [escape(item, mass_mentions=True) for item in items]
        if len(items) < 1:
            return await ctx.send_help()

        view = RerollView()
        message = await ctx.send(
            content=info(
                "From {} item{}, I pick: {}".format(
                    bold(str(len(items))),
                    "s" if len(items) > 1 else "",
                    inline(random.choice(items)),
                )
            ),
            view=view,
        )
        timed_out = False
        i = 1
        while not timed_out:
            timed_out = await view.wait()
            if timed_out:
                await message.edit(view=None)
                break

            view = RerollView()
            await message.edit(
                content=info(
                    "From {} item{} and after rerolling {} time{}, I pick: {}".format(
                        bold(str(len(items))),
                        "s" if len(items) > 1 else "",
                        bold(str(i)),
                        "s" if i > 1 else "",
                        inline(random.choice(items)),
                    )
                ),
                view=view,
            )
            i += 1

    @random.command(name="pickx", aliases=["choosex", "randomx", "drawx"])
    async def _pickx(self, ctx: commands.Context, x: int, *items):
        """From a list of items, chooses/picks X items and display them.

        This command can produce duplicates. Use `[p]pickuniquex` for a no duplicates version.

        To denote multiple-word items, use double quotes.
        Alternatively you can send a text file with each item on a new line.

        Example:
        - `[p]random pickx 2 item1 "item 2" "a 3rd item"`
        """
        if len(ctx.message.attachments) > 0:
            attachement = ctx.message.attachments[0]
            file = await attachement.read()
            items = file.decode("utf-8").splitlines()

        items = [escape(item, mass_mentions=True) for item in items]
        if x < 1:
            return await ctx.send(error("Must pick a positive number of items."))

        if len(items) < 1:
            return await ctx.send_help()

        view = RerollView()
        message = await ctx.send(
            info(
                "From {} item{}, I pick:\n{}".format(
                    bold(str(len(items))),
                    "s" if len(items) > 1 else "",
                    box("\n".join(random.choices(items, k=x))),
                )
            ),
            view=view,
        )
        timed_out = False
        i = 1
        while not timed_out:
            timed_out = await view.wait()
            if timed_out:
                await message.edit(view=None)
                break

            view = RerollView()
            await message.edit(
                content=info(
                    "From {} item{} and after rerolling {} time{}, I pick:\n{}".format(
                        bold(str(len(items))),
                        "s" if len(items) > 1 else "",
                        bold(str(i)),
                        "s" if i > 1 else "",
                        box("\n".join(random.choices(items, k=x))),
                    )
                ),
                view=view,
            )
            i += 1

    @random.command(
        name="pickuniquex",
        aliases=[
            "chooseuniquex",
            "randomuniquex",
            "drawuniquex",
            "pickux",
            "chooseux",
            "randomux",
            "drawux",
        ],
    )
    async def _pickuniquex(self, ctx: commands.Context, x: int, *items):
        """From a set of N items, chooses/picks X items and display them.

        This command avoids duplicates. Use `[p]pickx` for a version with duplicates allowed.

        To denote multiple-word items, use double quotes.
        Alternatively you can send a text file with each item on a new line.

        Example:
        - `[p]random pickuniquex 2 item1 "item 2" "a 3rd item"`
        """
        if len(ctx.message.attachments) > 0:
            attachement = ctx.message.attachments[0]
            file = await attachement.read()
            items = file.decode("utf-8").splitlines()

        items = [escape(item, mass_mentions=True) for item in items]
        if x < 1:
            return await ctx.send(error("Must pick a positive number of items."))

        if len(items) < 1:
            return await ctx.send_help()

        if len(items) < x:
            return await ctx.send(error("Not enough items to draw from."))

        drawn = random.sample(range(len(items)), x)
        drawn = [items[i] for i in sorted(drawn)]
        view = RerollView()
        message = await ctx.send(
            info(
                "From {} item{}, I pick:\n{}".format(
                    bold(str(len(items))), "s" if len(items) > 1 else "", box("\n".join(drawn))
                )
            ),
            view=view,
        )
        timed_out = False
        i = 1
        while not timed_out:
            timed_out = await view.wait()
            if timed_out:
                await message.edit(view=None)
                break

            drawn = random.sample(range(len(items)), x)
            drawn = [items[i] for i in sorted(drawn)]
            view = RerollView()
            await message.edit(
                content=info(
                    "From {} item{} and after rerolling {} time{}, I pick:\n{}".format(
                        bold(str(len(items))),
                        "s" if len(items) > 1 else "",
                        bold(str(i)),
                        "s" if i > 1 else "",
                        box("\n".join(drawn)),
                    )
                ),
                view=view,
            )
            i += 1

    @random.command(name="mix", aliases=["shuffle"])
    async def _mix(self, ctx: commands.Context, *items):
        """Shuffles/mixes a list of items.

        To denote multiple-word items, use double quotes.
        Alternatively you can send a text file with each item on a new line.

        Example:
        - `[p]random mix item1 "item 2" "a 3rd item"`
        """
        if len(ctx.message.attachments) > 0:
            attachement = ctx.message.attachments[0]
            file = await attachement.read()
            items = file.decode("utf-8").splitlines()

        items = [escape(item, mass_mentions=True) for item in items]
        if len(items) < 1:
            return await ctx.send_help()

        random.shuffle(items)
        view = RerollView()
        message = await ctx.send(
            info(
                "A randomized order of {} item{}:\n{}".format(
                    bold(str(len(items))),
                    "s" if len(items) > 1 else "",
                    box("\n".join(items)),
                )
            ),
            view=view,
        )
        timed_out = False
        i = 1
        while not timed_out:
            timed_out = await view.wait()
            if timed_out:
                await message.edit(view=None)
                break

            random.shuffle(items)
            view = RerollView()
            await message.edit(
                content=info(
                    "A randomized order of {} item{} after rerolling {} time{}:\n{}".format(
                        bold(str(len(items))),
                        "s" if len(items) > 1 else "",
                        bold(str(i)),
                        "s" if i > 1 else "",
                        box("\n".join(items)),
                    )
                ),
                view=view,
            )
            i += 1

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

        view = RerollView()
        message = await ctx.send(
            info(
                "Collected and rolled {die_count:,} dice!{values}\r\nTotal number of sides: {side_count:,}\r\n__Total value:__ **{total_sum:,}**  __Average value:__ **{total_avg:,.2f}**".format(
                    die_count=bold(str(len(dice))),
                    values=dice_string,
                    side_count=bold(str(sum(dice))),
                    total_sum=sum(dice_roll),
                    total_avg=sum(dice_roll) / len(dice),
                )
            ),
            view=view,
        )
        timed_out = False
        i = 1
        while not timed_out:
            timed_out = await view.wait()
            if timed_out:
                await message.edit(view=None)
                break

            dice_roll = [random.randint(1, X) for X in dice]

            dice_string = ""
            if len(dice) < 100:
                dice_string = "\r\nValues: {}".format(
                    ", ".join(["`{}`".format(x) for x in dice_roll])
                )

            view = RerollView()
            await message.edit(
                content=info(
                    "\n".join(
                        [
                            "After rerolling {} time{}.".format(
                                bold(str(i)),
                                "s" if i > 1 else "",
                            ),
                            "Collected and rolled {die_count:,} dice!{values}\r\nTotal number of sides: {side_count:,}\r\n**Total value: {total_sum:,}  Average value: {total_avg:,.2f}**".format(
                                die_count=len(dice),
                                values=dice_string,
                                side_count=sum(dice),
                                total_sum=sum(dice_roll),
                                total_avg=sum(dice_roll) / len(dice),
                            ),
                        ]
                    )
                ),
                view=view,
            )
            i += 1

    async def _roll_dice(self, ctx: commands.Context, roll_min: int, roll_max: int) -> None:
        """Perform and print a single dice roll."""
        if roll_min >= 10e100:
            await ctx.send(error("Minimum value too large."))
            return
        if roll_max >= 10e100:
            await ctx.send(error("Maximum value too large."))
            return
        roll_sides = bold(str(roll_max - roll_min + 1))
        strange = "strange "
        a_an = "a"
        roll_range = ""
        if roll_min == 1:
            if roll_max in [4, 6, 8, 10, 12, 20]:
                strange = ""
                if roll_max == 8:
                    a_an = "an"
        else:
            roll_range = " ({:,} to {:,})".format(bold(str(roll_min)), bold(str(roll_max)))
        if roll_max < roll_min:
            await ctx.send(
                error("Between {} and {} is not a valid range.".format(roll_min, roll_max))
            )
            return

        random_output = random.randint(roll_min, roll_max)
        view = RerollView()
        message = await ctx.send(
            info(
                "I roll {} {}{}-sided die{}, and it lands on: **{:,}**".format(
                    a_an, strange, roll_sides, roll_range, random_output
                )
            ),
            view=view,
        )
        timed_out = False
        i = 1
        while not timed_out:
            timed_out = await view.wait()
            if timed_out:
                await message.edit(view=None)
                break

            random_output = random.randint(roll_min, roll_max)
            view = RerollView()
            await message.edit(
                content=info(
                    "\n".join(
                        [
                            "After rerolling {} time{}.".format(
                                bold(str(i)),
                                "s" if i > 1 else "",
                            ),
                            "I roll {} {}{}-sided die{}, and it lands on: **{:,}**".format(
                                a_an, strange, roll_sides, roll_range, random_output
                            ),
                        ]
                    )
                ),
                view=view,
            )
            i += 1


class RerollView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await interaction.client.is_owner(interaction.user):
            return False
        return True

    @discord.ui.button(label="Re-Roll", style=discord.ButtonStyle.green, emoji="\N{GAME DIE}")
    async def reroll_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.defer()
        self.stop()
