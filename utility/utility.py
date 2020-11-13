import discord
import asyncio
import calendar
import logging
import random
import re
from typing import Dict, Optional
from datetime import datetime, timedelta
from pylint import epylint as lint
from redbot.core import Config, commands, checks
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import humanize_timedelta, pagify
from redbot.core.utils.menus import start_adding_reactions
from .polls import Poll
from .converters import PollOptions, TIME_RE, MULTI_RE

log = logging.getLogger("red.raffle")
log = logging.getLogger("red.flapjackcogs.reactpoll")

EMOJI_RE = re.compile(r"<a?:[a-zA-Z0-9\_]+:([0-9]+)>")


class Utility(commands.Cog):
    """Utility commands"""

    raffle_defaults = {"Channel": None, "Raffles": {}}

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1387007, cog_name="UtilityPyLint", force_registration=True)
        default_global = {"lint": True}
        default_guild = {}
        self.raffleconfig = Config.get_conf(self, 1387009, cog_name="UtilityRaffle", force_registration=True)
        self.raffleconfig.register_guild(**self.raffle_defaults)
        self.load_check = self.bot.loop.create_task(self.raffle_worker())
        self.conf = Config.get_conf(self, identifier=1387011, cog_name="UtilityReactPoll" force_registration=True)
        default_guild_settings = {"polls": {}, "embed": True}
        self.conf.register_guild(**default_guild_settings)
        self.conf.register_global(polls=[])
        self.polls: Dict[int, Dict[int, Poll]] = {}
        self.migrate = self.bot.loop.create_task(self.migrate_old_polls())
        self.loop = self.bot.loop.create_task(self.load_polls())
        self.poll_task = self.bot.loop.create_task(self.poll_closer())
        self.close_loop = True

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

    @commands.group(autohelp=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def raffle(self, ctx):
        """Raffle group command"""
        pass

    @raffle.command()
    async def version(self, ctx):
        """Displays the currently installed version of raffle."""
        await ctx.send(f"You are running raffle version {__version__}")

    @raffle.command(hidden=True)
    @commands.is_owner()
    async def clear(self, ctx):
        await self.raffleconfig.guild(ctx.guild).Raffles.clear()
        await ctx.send("Raffle data cleared out.")

    @raffle.command()
    async def start(self, ctx, timer, *, title: str):
        """Starts a raffle.

        Timer accepts a integer input that represents seconds or it will
        take the format of HH:MM:SS. For example:

        80       - 1 minute and 20 seconds or 80 seconds
        30:10    - 30 minutes and 10 seconds
        24:00:00 - 1 day or 24 hours

        Title should not be longer than 35 characters.
        Only one raffle can be active per server.
        """
        timer = await self.start_checks(ctx, timer, title)
        if timer is None:
            return

        try:
            description, winners, dos, roles = await self.raffle_setup(ctx)
        except asyncio.TimeoutError:
            return await ctx.send("Response timed out. A raffle failed to start.")
        str_roles = [r[0] for r in roles]
        description = f"{description}\n\nReact to this message with \U0001F39F to enter.\n\n"

        channel = await self._get_channel(ctx)
        end = calendar.timegm(ctx.message.created_at.utctimetuple()) + timer
        fmt_end = time.strftime("%a %d %b %Y %H:%M:%S", time.gmtime(end))

        try:
            embed = discord.Embed(
                description=description, title=title, color=self.bot.color
            )  ### old compat, i think ?
        except:
            color = await self.bot.get_embed_color(ctx)
            embed = discord.Embed(description=description, title=title, color=color)  ### new code
        embed.add_field(name="Days on Server", value=f"{dos}")
        role_info = f'{", ".join(str_roles) if roles else "@everyone"}'
        embed.add_field(name="Allowed Roles", value=role_info)
        msg = await channel.send(embed=embed)
        embed.set_footer(
            text=(
                f"Started by: {ctx.author.name} | Winners: {winners} | Ends at {fmt_end} UTC | Raffle ID: {msg.id}"
            )
        )
        await msg.edit(embed=embed)
        await msg.add_reaction("\U0001F39F")

        async with self.raffleconfig.guild(ctx.guild).Raffles() as r:
            new_raffle = {
                "Channel": channel.id,
                "Timestamp": end,
                "DOS": dos,
                "Roles": roles,
                "ID": msg.id,
                "Title": title,
            }
            r[msg.id] = new_raffle

        await self.raffle_timer(ctx.guild, new_raffle, timer)

    @raffle.command()
    async def end(self, ctx, message_id: int = None):
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
    async def cancel(self, ctx, message_id: int = None):
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
            async with self.raffleconfig.guild(ctx.guild).Raffles() as r:
                try:
                    del r[str(message_id)]
                except KeyError:
                    pass

    async def _menu(self, ctx, end="end"):
        title = f"Which of the following **Active** Raffles would you like to {end}?"
        async with self.raffleconfig.guild(ctx.guild).Raffles() as r:
            if not r:
                raise ValueError
            raffles = list(r.items())
        try:
            # pre-3.2 compatibility layer
            embed = self.embed_builder(raffles, ctx.bot.color, title)
        except AttributeError:
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

    def embed_builder(self, raffles, color, title):
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
            embeds.append(e)
        return e

    @raffle.command()
    async def reroll(self, ctx, channel: discord.TextChannel, messageid: int):
        """Reroll the winner for a raffle. Requires the channel and message id."""
        try:
            msg = await channel.get_message(messageid)
        except AttributeError:
            try:
                msg = await channel.fetch_message(messageid)
            except discord.HTTPException:
                return await ctx.send("Invalid message id.")
        except discord.HTTPException:
            return await ctx.send("Invalid message id.")
        try:
            await self.pick_winner(ctx.guild, channel, msg)
        except AttributeError:
            return await ctx.send("This is not a raffle message.")
        except IndexError:
            return await ctx.send(
                "Nice try slim. You can't add a reaction to a random msg "
                "and think that I am stupid enough to say you won something."
            )

    @commands.group(autohelp=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setraffle(self, ctx):
        """Set Raffle group command"""
        pass

    @setraffle.command()
    async def channel(self, ctx, channel: discord.TextChannel = None):
        """Set the output channel for raffles."""
        if channel:
            await self.raffleconfig.guild(ctx.guild).Channel.set(channel.id)
            return await ctx.send(f"Raffle output channel set to {channel.mention}.")
        await self.raffleconfig.guild(ctx.guild).Channel.clear()
        await ctx.send("Raffles will now be started where they were created.")

    def cog_unload(self):
        self.__unload()
        self.close_loop = False
        self.poll_task.cancel()
        pass

    def __unload(self):
        self.load_check.cancel()

    async def start_checks(self, ctx, timer, title):
        timer = self.time_converter(timer)
        if len(title) > 35:
            await ctx.send("Title is too long. Must be 35 characters or less.")
            return None
        elif timer is None:
            await ctx.send("Incorrect time format. Please use help on this command for more information.")
            return None
        else:
            return timer

    async def _get_response(self, ctx, question, predicate):
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

    async def _get_roles(self, ctx):
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

    async def _get_channel(self, ctx):
        channel_id = await self.raffleconfig.guild(ctx.guild).Channel()
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = ctx.channel
        return channel

    async def raffle_setup(self, ctx):
        predicate1 = lambda m: len(m.content) <= 200

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
                if int(m.content) >= 0:
                    return True
                return False
            except ValueError:
                return False

        q1 = "Please set a brief description (200 chars max)"
        q2 = (
            "Please set how many winners are pulled.\n**Note**: If there are "
            "more winners than entries, I will make everyone a winner."
        )
        q3 = "Would you like to set a 'days on server' requirement?"
        q4 = "Do you want to limit this raffle to specific roles?"

        description = await self._get_response(ctx, q1, predicate1)
        winners = await self._get_response(ctx, q2, predicate2)
        dos = 0
        roles = []

        if await self._get_response(ctx, q3, predicate3) == "yes":
            dos = await self._get_response(ctx, "How many days on the server are required?", predicate4)

        if await self._get_response(ctx, q4, predicate3) == "yes":
            roles = await self._get_roles(ctx)

        return description, int(winners), int(dos), roles

    async def raffle_worker(self):
        """Restarts raffle timers
        This worker will attempt to restart raffle timers incase of a cog reload or
        if the bot has been restart or shutdown. The task is only created when the cog
        is loaded, and is destroyed when it has finished.
        """
        try:
            await self.bot.wait_until_ready()
            guilds = [self.bot.get_guild(guild) for guild in await self.raffleconfig.all_guilds()]
            coros = []
            for guild in guilds:
                raffles = await self.raffleconfig.guild(guild).Raffles.all()
                if raffles:
                    now = calendar.timegm(datetime.utcnow().utctimetuple())
                    for key, value in raffles.items():
                        remaining = raffles[key]["Timestamp"] - now
                        if remaining <= 0:
                            await self.raffle_teardown(guild, raffles[key]["ID"])
                        else:
                            coros.append(self.raffle_timer(guild, raffles[key], remaining))
            await asyncio.gather(*coros)
        except Exception as e:
            print(e)

    async def raffle_timer(self, guild, raffle: dict, remaining: int):
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
        async with self.raffleconfig.guild(guild).Raffles() as r:
            data = r.get(str(raffle["ID"]))
        if data:
            await self.raffle_teardown(guild, raffle["ID"])

    async def raffle_teardown(self, guild, message_id):
        raffles = await self.raffleconfig.guild(guild).Raffles.all()
        channel = self.bot.get_channel(raffles[str(message_id)]["Channel"])

        errored = False
        try:
            msg = await channel.get_message(raffles[str(message_id)]["ID"])
        except AttributeError:
            try:
                msg = await channel.fetch_message(raffles[str(message_id)]["ID"])
            except discord.NotFound:
                errored = True
        except discord.errors.NotFound:
            errored = True
        if not errored:
            await self.pick_winner(guild, channel, msg)

        async with self.raffleconfig.guild(guild).Raffles() as r:
            try:
                del r[str(message_id)]
            except KeyError:
                pass

    async def pick_winner(self, guild, channel, msg):
        reaction = next(filter(lambda x: x.emoji == "\U0001F39F", msg.reactions), None)
        if reaction is None:
            return await channel.send(
                "It appears there were no valid entries, so a winner for the raffle could not be picked."
            )
        users = [user for user in await reaction.users().flatten() if guild.get_member(user.id)]
        users.remove(self.bot.user)
        try:
            amt = int(msg.embeds[0].footer.text.split("Winners: ")[1][0])
        except AttributeError:  # the footer was not set in time
            return await channel.send("An error occurred, so a winner for the raffle could not be picked.")
        valid_entries = await self.validate_entries(users, msg)
        winners = random.sample(valid_entries, min(len(valid_entries), amt))
        if not winners:
            await channel.send(
                "It appears there were no valid entries, so a winner for the raffle could not be picked."
            )
        else:
            display = ", ".join(winner.mention for winner in winners)
            await channel.send(f"Congratulations {display}! You have won the {msg.embeds[0].title} raffle!")

    async def validate_entries(self, users, msg):
        dos, roles = msg.embeds[0].fields
        dos = int(dos.value)
        roles = roles.value.split(", ")

        try:
            if dos:
                users = [user for user in users if dos < (user.joined_at.now() - user.joined_at).days]

            if roles:
                users = [user for user in users if any(role in [r.name for r in user.roles] for role in roles)]
        except AttributeError:
            return None
        return users

    async def raffle_removal(self, ctx, message_id):
        async with self.raffleconfig.guild(ctx.guild).Raffles() as r:
            try:
                del r[str(message_id)]
            except KeyError:
                pass

    @staticmethod
    def time_converter(units):
        try:
            return sum(int(x) * 60 ** i for i, x in enumerate(reversed(units.split(":"))))
        except ValueError:
            return None

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

    async def poll_closer(self):
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

    async def delete_poll(self, guild: discord.Guild, poll: Poll):
        async with self.conf.guild(guild).polls() as polls:
            if str(poll.message_id) in polls:
                del polls[str(poll.message_id)]

    async def store_poll(self, poll: Poll):
        try:
            async with self.conf.guild(poll.guild).polls() as polls:
                polls[str(poll.message_id)] = poll.as_dict()
        except AttributeError:
            # The guild no longer exists or the channel was deleted.
            return

    async def load_polls(self):
        # unfortunately we have to deal with an issue where JSON
        # serialization fails if the config default list is used
        all_polls = await self.conf.all_guilds()

        for g_id, polls in all_polls.items():
            if g_id not in self.polls:
                self.polls[g_id] = {}
            for m_id, poll in polls["polls"].items():
                self.polls[g_id][int(m_id)] = Poll(self.bot, **poll)

    async def migrate_old_polls(self):
        try:
            polls = await self.conf.polls()
        except AttributeError:
            log.error("Error migrating old poll")
            return
        for poll in polls:
            # log.info(poll)
            poll["author_id"] = poll["author"]
            poll["message_id"] = poll["message"]
            poll["channel_id"] = poll["channel"]
            new_poll = Poll(self.bot, **poll)
            if not new_poll.channel:
                continue
            old_poll_msg = await new_poll.get_message()
            move_msg = (
                "Hello, due to a upgrade in the reaction poll cog "
                "one of your polls is no longer compatible and cannot "
                "be automatically tallied. If you wish to continue the poll, "
                "it is recommended to create a new one or manually tally the results. "
                f"The poll can be found at {old_poll_msg.jump_url}"
            )
            if new_poll.author:
                try:
                    await new_poll.author.send(move_msg)
                except discord.errors.Forbidden:
                    pass

        await self.conf.polls.clear()

    @commands.group()
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    async def pollset(self, ctx: commands.Context):
        """
            Settings for reaction polls
        """

    @rpollset.command(name="embed", aliases=["embeds"])
    async def rpoll_set_embed(self, ctx: commands.Context):
        """
            Toggle embed usage for polls in this server
        """
        curr_setting = await self.conf.guild(ctx.guild).embed()
        await self.conf.guild(ctx.guild).embed.set(not curr_setting)
        if curr_setting:
            verb = "off"
        else:
            verb = "on"
        await ctx.send(f"Reaction poll embeds turned {verb}.")

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
            return await ctx.send(f"I do not have permission to send messages in {channel.mention}")
        poll_options = {"emojis": {}, "options": [], "interactive": True, "author_id": ctx.author.id}
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
                    await self.handle_pagify(ctx, f"Option {default_emojis[count]} set to {msg.content}")
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
                    await self.handle_pagify(ctx, f"Option {maybe_emoji} set to {' '.join(msg.content.split(' ')[1:])}")
                except Exception:
                    poll_options["emojis"][default_emojis[count]] = msg.content
                    poll_options["options"].append(msg.content)
                    await self.handle_pagify(ctx, f"Option {default_emojis[count]} set to {msg.content}")
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
            return await ctx.send(f"I do not have permission to send messages in {send_channel.mention}")
        poll_options["channel_id"] = send_channel.id
        # allow us to specify new channel for the poll

        guild = ctx.guild
        # log.info(poll_options)
        embed = (
            await self.conf.guild(guild).embed()
            and send_channel.permissions_for(ctx.me).embed_links
        )
        poll_options["embed"] = embed
        poll = Poll(self.bot, **poll_options)

        await poll.open_poll()
        if guild.id not in self.polls:
            self.polls[guild.id] = {}
        self.polls[guild.id][poll.message_id] = poll
        await self.store_poll(poll)