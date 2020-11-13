import discord
from pylint import epylint as lint
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path


class Utility(commands.Cog):
    """Utility commands"""

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1387007, cog_name="UtilityPyLint", force_registration=True)
        default_global = {"lint": True}
        default_guild = {}

        self.path = str(cog_data_path(self)).replace("\\", "/")

        self.do_lint = None
        self.counter = 0

        # self.answer_path = self.path + "/tmpfile.py"

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    @commands.command()
    async def autopylint(self, ctx: commands.Context):
        """Toggles automatically linting code"""
        curr = await self.config.lint()

        self.do_lint = not curr
        await self.config.lint.set(not curr)
        await ctx.maybe_send_embed("Autolinting is now set to {}".format(not curr))

    @commands.command()
    async def pylint(self, ctx: commands.Context, *, code):
        """Lint python code

        Toggle autolinting with `[p]autopylint`
        """
        await self.lint_message(ctx.message)

    async def lint_code(self, code):
        self.counter += 1
        path = self.path + "/{}.py".format(self.counter)
        with open(path, "w") as codefile:
            codefile.write(code)

        future = await self.bot.loop.run_in_executor(None, lint.py_run, path, "return_std=True")

        if future:
            (pylint_stdout, pylint_stderr) = future
        else:
            (pylint_stdout, pylint_stderr) = None, None

        # print(pylint_stderr)
        # print(pylint_stdout)

        return pylint_stdout, pylint_stderr

    async def lint_message(self, message):
        if self.do_lint is None:
            self.do_lint = await self.config.lint()
        if not self.do_lint:
            return
        code_blocks = message.content.split("```")[1::2]

        for c in code_blocks:
            is_python, code = c.split(None, 1)
            is_python = is_python.lower() in ["python", "py"]
            if is_python:  # Then we're in business
                linted, errors = await self.lint_code(code)
                linted = linted.getvalue()
                errors = errors.getvalue()
                await message.channel.send(linted)
                # await message.channel.send(errors)

    async def on_message(self, message: discord.Message):
        await self.lint_message(message)

    @commands.command()
    async def pick(self, ctx, *items):
        """Chooses/picks a random item from N multiple items.

        To denote multiple-word items, you should use double quotes."""
        items = [escape(c, mass_mentions=True) for c in items]
        if len(items) < 1:
            await ctx.send(error("Not enough items to pick from."))
        else:
            await ctx.send(info("From {} items, I pick: {}".format(len(items), choice(items))))

    @commands.command()
    async def pickx(self, ctx, x : int, *items):
        """From a set of N items, chooses/picks X items and display them.
        
        This is random choosing with replacement, and is the same as using the "pick" command multiple times.
        To denote multiple-word items, use double quotes."""
        items = [escape(c, mass_mentions=True) for c in items]
        if x < 1:
            await ctx.send(error("Must pick a positive number of items."))
        elif len(items) < 1:
            await ctx.send(error("Not enough items to pick from."))
        else:
            await ctx.send(info("From {} items, I pick: {}".format(len(items), ", ".join(choices(items, k=x)))))

    @commands.command()
    async def drawx(self, ctx, x : int, *items):
        """From a set of N items, draw X items and display them.
        
        This is random drawing without replacement.
        To denote multiple-word items, use double quotes."""
        items = [escape(c, mass_mentions=True) for c in items]
        if x < 1:
            await ctx.send(error("Must draw a positive number of items."))
        elif len(items) < 1 or len(items) < x:
            await ctx.send(error("Not enough items to draw from."))
        else:
            drawn = sample(range(len(items)), x)
            drawn = [items[i] for i in sorted(drawn)]
            await ctx.send(info("From {} items, I draw: {}".format(len(items), ", ".join(drawn))))

    @commands.command()
    async def mix(self, ctx, *items):
        """Shuffles/mixes a list of items.

        To denote multiple-word items, use double quotes."""
        items = [escape(c, mass_mentions=True) for c in items]
        if len(items) < 1:
            await ctx.send(error("Not enough items to shuffle."))
        else:
            await ctx.send(info("A randomized order of {} items: {}".format(len(items), ", ".join(shuffle(items)))))


    @commands.command(aliases=["rolld"])
    async def rolldice(self, ctx, *bounds):
        """Rolls the specified single or multiple dice.
        
        Possible arguments:
        NONE rolls a 6-sided die.
        A single number X: rolls an X-sided die (example: ".roll 17").
        Two numbers X and Y: rolls a strange die with a minimum X and maximum Y (example: ".roll 3 8").
        The text NdX: rolls N dice with X sides (example: ".roll 3d20".
        The NdX "dice specification" can be repeated to roll a variety of dice at once. If multiple dice are used, statistics will be shown."""
        sbounds = " ".join(bounds).lower()
        if "d" in sbounds:
            # dice specifiers: remove the spaces around "d" (so "1 d6" -> "1d6"
            while " d" in sbounds or "d " in sbounds:
                bounds = sbounds.replace(" d", "d").replace("d ", "d").split(" ")
                sbounds = " ".join(bounds)

        if len(bounds) == 0:
            # .roll
            bounds = ["6"]
            # fall through to ".roll 6"
        
        if len(bounds) == 1:
            if bounds[0].isnumeric():
                # .roll X
                # provided maximum, roll is between 1 and X
                r_max = int(bounds[0])
                await self._roll1(ctx, 1, r_max)
                return

        if len(bounds) == 2:
            if bounds[0].isnumeric() and bounds[1].isnumeric():
                # .roll X Y
                # provided minimum and maximum, roll is between X and Y
                r_min = int(bounds[0])
                r_max = int(bounds[1])
                await self._roll1(ctx, r_min, r_max)
                return

        # got here, must have been non-numeric objects, possibly containing "d" dice specifiers?
        dice = []
        valid = True
        try:
            for spec in bounds:
                spec = spec.strip(",()")
                if not "d" in spec:
                    raise ValueError("Invalid input.")

                spspec = spec.split("d")
                if len(spspec) != 2:
                    raise ValueError("Invalid dice.")

                if len(spspec[0]) == 0:
                    r_mul = 1
                elif spspec[0].isnumeric():
                    r_mul = int(spspec[0])
                    if r_mul < 1:
                        raise ValueError("Non-positive number of dice.")
                else:
                    raise ValueError("Non-numeric number of dice.")

                if spspec[1].isnumeric():
                    r_max = int(spspec[1])
                    if r_max < 1:
                        raise ValueError("Non-positive side count on dice.")
                    elif r_max >= 10e100:
                        raise ValueError("Side count on dice too large.")
                else:
                    raise ValueError("Non-numeric side count on dice.")

                if len(dice) + r_mul >= 1000:
                    dice = []
                    raise ValueError("Number of dice too large (over 999).")

                dice += itertools.repeat(r_max, r_mul)
        except ValueError as ex:
            await ctx.send(error(str(ex)))
            return
        
        if len(dice) == 0:
            await ctx.send(error("No collected dice to use."))
            return

        if len(dice) == 1:
            # one die
            await self._roll1(ctx, 1, dice[0])
            return

        d_rol = [randint(1, X) for X in dice]

        d_ind = ""
        if len(dice) < 100:
            d_ind = "\r\nValues: {}".format(", ".join(["`{}`".format(x) for x in d_rol]))

        await ctx.send(info("Collected and rolled {die_count:,} dice!{values}\r\nTotal number of sides: {side_count:,}\r\n**Total value: {total_sum:,}  Average value: {total_avg:,.2f}**".format( \
                die_count=len(dice),
                values=d_ind,
                side_count=sum(dice),
                total_sum=sum(d_rol),
                total_avg=sum(d_rol)/len(dice))))


    async def _roll1(self, ctx, r_min, r_max):
        """Perform and print a single dice roll."""
        if r_min >= 10e100:
            await ctx.send(error("Minimum value too large."))
            return
        if r_max >= 10e100:
            await ctx.send(error("Maximum value too large."))
            return
        r_cnt = r_max - r_min + 1
        strange = "strange "
        a_an = "a"
        r_rng = ""
        if r_min == 1:
            if r_max in [4, 6, 8, 10, 12, 20]:
                strange = ""
                if r_max == 8:
                    a_an = "an"
        else:
            r_rng = " ({:,} to {:,})".format(r_min, r_max)
        if r_max < r_min:
            await ctx.send(error("Between {} and {} is not a valid range.".format(r_min, r_max)))
        else:
            r = randint(r_min, r_max)
            await ctx.send(info("I roll {} {}{}-sided die{}, and it lands on: **{:,}**".format(a_an, strange, r_cnt, r_rng, r)))