import datetime
import time
from enum import Enum
from random import randint, choice
from typing import Final, List, Literal

import urllib.parse
import aiohttp
import asyncio
import math
import pathlib

import io
import yaml
import discord

from collections import Counter
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from management.management import is_owner_if_bank_global
from redbot.core.data_manager import cog_data_path
from redbot.core.utils import AsyncIter
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS, start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
from redbot.core.utils.chat_formatting import (
    bold,
    escape,
    italics,
    humanize_number,
    humanize_timedelta,
    box,
    pagify,
)

from .checks import trivia_stop_check
from .converters import finite_float
from .log import LOG
from .session import TriviaSession

__all__ = ["Trivia", "UNIQUE_ID", "get_core_lists"]


class InvalidListError(Exception):
    """A Trivia list file is in invalid format."""

    pass

class RPS(Enum):
    rock = "\N{MOYAI}"
    paper = "\N{PAGE FACING UP}"
    scissors = "\N{BLACK SCISSORS}\N{VARIATION SELECTOR-16}"


class RPSParser:
    def __init__(self, argument):
        argument = argument.lower()
        if argument == "rock":
            self.choice = RPS.rock
        elif argument == "paper":
            self.choice = RPS.paper
        elif argument == "scissors":
            self.choice = RPS.scissors
        else:
            self.choice = None


MAX_ROLL: Final[int] = 2 ** 64 - 1


class Games(commands.Cog):
    """Collection of games for you to play."""

    global _
    _ = lambda s: s
    ball = [
        ("As I see it, yes"),
        ("It is certain"),
        ("It is decidedly so"),
        ("Most likely"),
        ("Outlook good"),
        ("Signs point to yes"),
        ("Without a doubt"),
        ("Yes"),
        ("Yes – definitely"),
        ("You may rely on it"),
        ("Reply hazy, try again"),
        ("Ask again later"),
        ("Better not tell you now"),
        ("Cannot predict now"),
        ("Concentrate and ask again"),
        ("Don't count on it"),
        ("My reply is no"),
        ("My sources say no"),
        ("Outlook not so good"),
        ("Very doubtful"),
    ]

    def __init__(self, bot: Red):
        super().__init__()
        self.trivia_sessions = []
        self.config = Config.get_conf(self, identifier=1387004, cog_name="GamesTrivia", force_registration=True)

        self.config.register_guild(
            max_score=10,
            timeout=120.0,
            delay=15.0,
            bot_plays=False,
            reveal_answer=True,
            payout_multiplier=0.0,
            allow_override=True,
        )

        self.config.register_member(wins=0, games=0, total_score=0)

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        if requester != "discord_deleted_user":
            return

        all_members = await self.config.all_members()

        async for guild_id, guild_data in AsyncIter(all_members.items(), steps=100):
            if user_id in guild_data:
                await self.config.member_from_ids(guild_id, user_id).clear()

    @commands.command()
    async def roll(self, ctx, number: int = 100):
        """Roll a random number.

        The result will be between 1 and `<number>`.

        `<number>` defaults to 100.
        """
        author = ctx.author
        if 1 < number <= MAX_ROLL:
            n = randint(1, number)
            await ctx.send(
                "{author.mention} :game_die: {n} :game_die:".format(
                    author=author, n=humanize_number(n)
                )
            )
        elif number <= 1:
            await ctx.send(("{author.mention} Maybe higher than 1? ;P").format(author=author))
        else:
            await ctx.send(
                ("{author.mention} Max allowed number is {maxamount}.").format(
                    author=author, maxamount=humanize_number(MAX_ROLL)
                )
            )

    @commands.command()
    async def flip(self, ctx, user: discord.Member = None):
        """Flip a coin... or a user.

        Defaults to a coin.
        """
        if user is not None:
            msg = ""
            if user.id == ctx.bot.user.id:
                user = ctx.author
                msg = ("Nice try. You think this is funny?\n How about *this* instead:\n\n")
            char = "abcdefghijklmnopqrstuvwxyz"
            tran = "ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz"
            table = str.maketrans(char, tran)
            name = user.display_name.translate(table)
            char = char.upper()
            tran = "∀qƆpƎℲפHIſʞ˥WNOԀQᴚS┴∩ΛMX⅄Z"
            table = str.maketrans(char, tran)
            name = name.translate(table)
            await ctx.send(msg + "(╯°□°）╯︵ " + name[::-1])
        else:
            await ctx.send(("*flips a coin and... ") + choice([("HEADS!*"), ("TAILS!*")]))

    @commands.command()
    async def rps(self, ctx, your_choice: RPSParser):
        """Play Rock Paper Scissors."""
        author = ctx.author
        player_choice = your_choice.choice
        if not player_choice:
            return await ctx.send(
                ("This isn't a valid option. Try {r}, {p}, or {s}.").format(
                    r="rock", p="paper", s="scissors"
                )
            )
        red_choice = choice((RPS.rock, RPS.paper, RPS.scissors))
        cond = {
            (RPS.rock, RPS.paper): False,
            (RPS.rock, RPS.scissors): True,
            (RPS.paper, RPS.rock): True,
            (RPS.paper, RPS.scissors): False,
            (RPS.scissors, RPS.rock): False,
            (RPS.scissors, RPS.paper): True,
        }

        if red_choice == player_choice:
            outcome = None  # Tie
        else:
            outcome = cond[(player_choice, red_choice)]

        if outcome is True:
            await ctx.send(
                ("{choice} You win {author.mention}!").format(
                    choice=red_choice.value, author=author
                )
            )
        elif outcome is False:
            await ctx.send(
                ("{choice} You lose {author.mention}!").format(
                    choice=red_choice.value, author=author
                )
            )
        else:
            await ctx.send(
                ("{choice} We're square {author.mention}!").format(
                    choice=red_choice.value, author=author
                )
            )

    @commands.command(name="8", aliases=["8ball"])
    async def _8ball(self, ctx, *, question: str):
        """Ask 8 ball a question.

        Question must end with a question mark.
        """
        if question.endswith("?") and question != "?":
            await ctx.send("`" + T(choice(self.ball)) + "`")
        else:
            await ctx.send(("That doesn't look like a question."))

    @commands.group()
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def triviaset(self, ctx: commands.Context):
        """Manage Trivia settings."""

    @triviaset.command(name="showsettings")
    async def triviaset_showsettings(self, ctx: commands.Context):
        """Show the current trivia settings."""
        settings = self.config.guild(ctx.guild)
        settings_dict = await settings.all()
        msg = box(
            (
                "Current settings\n"
                "Bot gains points: {bot_plays}\n"
                "Answer time limit: {delay} seconds\n"
                "Lack of response timeout: {timeout} seconds\n"
                "Points to win: {max_score}\n"
                "Reveal answer on timeout: {reveal_answer}\n"
                "Payout multiplier: {payout_multiplier}\n"
                "Allow lists to override settings: {allow_override}"
            ).format(**settings_dict),
            lang="py",
        )
        await ctx.send(msg)

    @triviaset.command(name="maxscore")
    async def triviaset_max_score(self, ctx: commands.Context, score: int):
        """Set the total points required to win."""
        if score < 0:
            await ctx.send(("Score must be greater than 0."))
            return
        settings = self.config.guild(ctx.guild)
        await settings.max_score.set(score)
        await ctx.send(("Done. Points required to win set to {num}.").format(num=score))

    @triviaset.command(name="timelimit")
    async def triviaset_timelimit(self, ctx: commands.Context, seconds: finite_float):
        """Set the maximum seconds permitted to answer a question."""
        if seconds < 4.0:
            await ctx.send(("Must be at least 4 seconds."))
            return
        settings = self.config.guild(ctx.guild)
        await settings.delay.set(seconds)
        await ctx.send(("Done. Maximum seconds to answer set to {num}.").format(num=seconds))

    @triviaset.command(name="stopafter")
    async def triviaset_stopafter(self, ctx: commands.Context, seconds: finite_float):
        """Set how long until trivia stops due to no response."""
        settings = self.config.guild(ctx.guild)
        if seconds < await settings.delay():
            await ctx.send(("Must be larger than the answer time limit."))
            return
        await settings.timeout.set(seconds)
        await ctx.send(
            (
                "Done. Trivia sessions will now time out after {num} seconds of no responses."
            ).format(num=seconds)
        )

    @triviaset.command(name="override")
    async def triviaset_allowoverride(self, ctx: commands.Context, enabled: bool):
        """Allow/disallow trivia lists to override settings."""
        settings = self.config.guild(ctx.guild)
        await settings.allow_override.set(enabled)
        if enabled:
            await ctx.send(
                ("Done. Trivia lists can now override the trivia settings for this server.")
            )
        else:
            await ctx.send(
                (
                    "Done. Trivia lists can no longer override the trivia settings for this "
                    "server."
                )
            )

    @triviaset.command(name="botplays", usage="<true_or_false>")
    async def trivaset_bot_plays(self, ctx: commands.Context, enabled: bool):
        """Set whether or not the bot gains points.

        If enabled, the bot will gain a point if no one guesses correctly.
        """
        settings = self.config.guild(ctx.guild)
        await settings.bot_plays.set(enabled)
        if enabled:
            await ctx.send(("Done. I'll now gain a point if users don't answer in time."))
        else:
            await ctx.send(("Alright, I won't embarrass you at trivia anymore."))

    @triviaset.command(name="revealanswer", usage="<true_or_false>")
    async def trivaset_reveal_answer(self, ctx: commands.Context, enabled: bool):
        """Set whether or not the answer is revealed.

        If enabled, the bot will reveal the answer if no one guesses correctly
        in time.
        """
        settings = self.config.guild(ctx.guild)
        await settings.reveal_answer.set(enabled)
        if enabled:
            await ctx.send(("Done. I'll reveal the answer if no one knows it."))
        else:
            await ctx.send(("Alright, I won't reveal the answer to the questions anymore."))

    @is_owner_if_bank_global()
    @checks.admin_or_permissions(manage_guild=True)
    @triviaset.command(name="payout")
    async def triviaset_payout_multiplier(self, ctx: commands.Context, multiplier: finite_float):
        """Set the payout multiplier.

        This can be any positive decimal number. If a user wins trivia when at
        least 3 members are playing, they will receive credits. Set to 0 to
        disable.

        The number of credits is determined by multiplying their total score by
        this multiplier.
        """
        settings = self.config.guild(ctx.guild)
        if multiplier < 0:
            await ctx.send(("Multiplier must be at least 0."))
            return
        await settings.payout_multiplier.set(multiplier)
        if multiplier:
            await ctx.send(("Done. Payout multiplier set to {num}.").format(num=multiplier))
        else:
            await ctx.send(("Done. I will no longer reward the winner with a payout."))

    @triviaset.group(name="custom")
    @commands.is_owner()
    async def triviaset_custom(self, ctx: commands.Context):
        """Manage Custom Trivia lists."""
        pass

    @triviaset_custom.command(name="list")
    async def custom_trivia_list(self, ctx: commands.Context):
        """List uploaded custom trivia."""
        personal_lists = sorted([p.resolve().stem for p in cog_data_path(self).glob("*.yaml")])
        no_lists_uploaded = ("No custom Trivia lists uploaded.")

        if not personal_lists:
            if await ctx.embed_requested():
                await ctx.send(
                    embed=discord.Embed(
                        colour=await ctx.embed_colour(), description=no_lists_uploaded
                    )
                )
            else:
                await ctx.send(no_lists_uploaded)
            return

        if await ctx.embed_requested():
            await ctx.send(
                embed=discord.Embed(
                    title=("Uploaded trivia lists"),
                    colour=await ctx.embed_colour(),
                    description=", ".join(sorted(personal_lists)),
                )
            )
        else:
            msg = box(
                bold(("Uploaded trivia lists")) + "\n\n" + ", ".join(sorted(personal_lists))
            )
            if len(msg) > 1000:
                await ctx.author.send(msg)
            else:
                await ctx.send(msg)

    @commands.is_owner()
    @triviaset_custom.command(name="upload", aliases=["add"])
    async def trivia_upload(self, ctx: commands.Context):
        """Upload a trivia file."""
        if not ctx.message.attachments:
            await ctx.send(("Supply a file with next message or type anything to cancel."))
            try:
                message = await ctx.bot.wait_for(
                    "message", check=MessagePredicate.same_context(ctx), timeout=30
                )
            except asyncio.TimeoutError:
                await ctx.send(("You took too long to upload a list."))
                return
            if not message.attachments:
                await ctx.send(("You have cancelled the upload process."))
                return
            parsedfile = message.attachments[0]
        else:
            parsedfile = ctx.message.attachments[0]
        try:
            await self._save_trivia_list(ctx=ctx, attachment=parsedfile)
        except yaml.error.MarkedYAMLError as exc:
            await ctx.send(("Invalid syntax: ") + str(exc))
        except yaml.error.YAMLError:
            await ctx.send(
                ("There was an error parsing the trivia list. See logs for more info.")
            )
            LOG.exception("Custom Trivia file %s failed to upload", parsedfile.filename)

    @commands.is_owner()
    @triviaset_custom.command(name="delete", aliases=["remove"])
    async def trivia_delete(self, ctx: commands.Context, name: str):
        """Delete a trivia file."""
        filepath = cog_data_path(self) / f"{name}.yaml"
        if filepath.exists():
            filepath.unlink()
            await ctx.send(("Trivia {filename} was deleted.").format(filename=filepath.stem))
        else:
            await ctx.send(("Trivia file was not found."))

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    async def trivia(self, ctx: commands.Context, *categories: str):
        """Start trivia session on the specified category.

        You may list multiple categories, in which case the trivia will involve
        questions from all of them.
        """
        if not categories:
            await ctx.send_help()
            return
        categories = [c.lower() for c in categories]
        session = self._get_trivia_session(ctx.channel)
        if session is not None:
            await ctx.send(("There is already an ongoing trivia session in this channel."))
            return
        trivia_dict = {}
        authors = []
        for category in reversed(categories):
            # We reverse the categories so that the first list's config takes
            # priority over the others.
            try:
                dict_ = self.get_trivia_list(category)
            except FileNotFoundError:
                await ctx.send(
                    (
                        "Invalid category `{name}`. See `{prefix}trivia list` for a list of "
                        "trivia categories."
                    ).format(name=category, prefix=ctx.clean_prefix)
                )
            except InvalidListError:
                await ctx.send(
                    (
                        "There was an error parsing the trivia list for the `{name}` category. It "
                        "may be formatted incorrectly."
                    ).format(name=category)
                )
            else:
                trivia_dict.update(dict_)
                authors.append(trivia_dict.pop("AUTHOR", None))
                continue
            return
        if not trivia_dict:
            await ctx.send(
                ("The trivia list was parsed successfully, however it appears to be empty!")
            )
            return
        settings = await self.config.guild(ctx.guild).all()
        config = trivia_dict.pop("CONFIG", None)
        if config and settings["allow_override"]:
            settings.update(config)
        settings["lists"] = dict(zip(categories, reversed(authors)))
        session = TriviaSession.start(ctx, trivia_dict, settings)
        self.trivia_sessions.append(session)
        LOG.debug("New trivia session; #%s in %d", ctx.channel, ctx.guild.id)

    @trivia_stop_check()
    @trivia.command(name="stop")
    async def trivia_stop(self, ctx: commands.Context):
        """Stop an ongoing trivia session."""
        session = self._get_trivia_session(ctx.channel)
        if session is None:
            await ctx.send(("There is no ongoing trivia session in this channel."))
            return
        await session.end_game()
        session.force_stop()
        await ctx.send(("Trivia stopped."))

    @trivia.command(name="list")
    async def trivia_list(self, ctx: commands.Context):
        """List available trivia categories."""
        lists = set(p.stem for p in self._all_lists())
        if await ctx.embed_requested():
            await ctx.send(
                embed=discord.Embed(
                    title=("Available trivia lists"),
                    colour=await ctx.embed_colour(),
                    description=", ".join(sorted(lists)),
                )
            )
        else:
            msg = box(bold(("Available trivia lists")) + "\n\n" + ", ".join(sorted(lists)))
            if len(msg) > 1000:
                await ctx.author.send(msg)
            else:
                await ctx.send(msg)

    @trivia.group(
        name="leaderboard", aliases=["lboard"], autohelp=False, invoke_without_command=True
    )
    async def trivia_leaderboard(self, ctx: commands.Context):
        """Leaderboard for trivia.

        Defaults to the top 10 of this server, sorted by total wins. Use
        subcommands for a more customised leaderboard.
        """
        cmd = self.trivia_leaderboard_server
        if isinstance(ctx.channel, discord.abc.PrivateChannel):
            cmd = self.trivia_leaderboard_global
        await ctx.invoke(cmd, "wins", 10)

    @trivia_leaderboard.command(name="server")
    @commands.guild_only()
    async def trivia_leaderboard_server(
        self, ctx: commands.Context, sort_by: str = "wins", top: int = 10
    ):
        """Leaderboard for this server.

        `<sort_by>` can be any of the following fields:
         - `wins`  : total wins
         - `avg`   : average score
         - `total` : total correct answers
         - `games` : total games played

        `<top>` is the number of ranks to show on the leaderboard.
        """
        key = self._get_sort_key(sort_by)
        if key is None:
            await ctx.send(
                (
                    "Unknown field `{field_name}`, see `{prefix}help trivia leaderboard server` "
                    "for valid fields to sort by."
                ).format(field_name=sort_by, prefix=ctx.clean_prefix)
            )
            return
        guild = ctx.guild
        data = await self.config.all_members(guild)
        data = {guild.get_member(u): d for u, d in data.items()}
        data.pop(None, None)  # remove any members which aren't in the guild
        await self.send_leaderboard(ctx, data, key, top)

    @trivia_leaderboard.command(name="global")
    async def trivia_leaderboard_global(
        self, ctx: commands.Context, sort_by: str = "wins", top: int = 10
    ):
        """Global trivia leaderboard.

        `<sort_by>` can be any of the following fields:
         - `wins`  : total wins
         - `avg`   : average score
         - `total` : total correct answers from all sessions
         - `games` : total games played

        `<top>` is the number of ranks to show on the leaderboard.
        """
        key = self._get_sort_key(sort_by)
        if key is None:
            await ctx.send(
                (
                    "Unknown field `{field_name}`, see `{prefix}help trivia leaderboard server` "
                    "for valid fields to sort by."
                ).format(field_name=sort_by, prefix=ctx.clean_prefix)
            )
            return
        data = await self.config.all_members()
        collated_data = {}
        for guild_id, guild_data in data.items():
            guild = ctx.bot.get_guild(guild_id)
            if guild is None:
                continue
            for member_id, member_data in guild_data.items():
                member = guild.get_member(member_id)
                if member is None:
                    continue
                collated_member_data = collated_data.get(member, Counter())
                for v_key, value in member_data.items():
                    collated_member_data[v_key] += value
                collated_data[member] = collated_member_data
        await self.send_leaderboard(ctx, collated_data, key, top)

    @staticmethod
    def _get_sort_key(key: str):
        key = key.lower()
        if key in ("wins", "average_score", "total_score", "games"):
            return key
        elif key in ("avg", "average"):
            return "average_score"
        elif key in ("total", "score", "answers", "correct"):
            return "total_score"

    async def send_leaderboard(self, ctx: commands.Context, data: dict, key: str, top: int):
        """Send the leaderboard from the given data.

        Parameters
        ----------
        ctx : commands.Context
            The context to send the leaderboard to.
        data : dict
            The data for the leaderboard. This must map `discord.Member` ->
            `dict`.
        key : str
            The field to sort the data by. Can be ``wins``, ``total_score``,
            ``games`` or ``average_score``.
        top : int
            The number of members to display on the leaderboard.

        Returns
        -------
        `list` of `discord.Message`
            The sent leaderboard messages.

        """
        if not data:
            await ctx.send(("There are no scores on record!"))
            return
        leaderboard = self._get_leaderboard(data, key, top)
        ret = []
        for page in pagify(leaderboard, shorten_by=10):
            ret.append(await ctx.send(box(page, lang="py")))
        return ret

    @staticmethod
    def _get_leaderboard(data: dict, key: str, top: int):
        # Mix in average score
        for member, stats in data.items():
            if stats["games"] != 0:
                stats["average_score"] = stats["total_score"] / stats["games"]
            else:
                stats["average_score"] = 0.0
        # Sort by reverse order of priority
        priority = ["average_score", "total_score", "wins", "games"]
        try:
            priority.remove(key)
        except ValueError:
            raise ValueError(f"{key} is not a valid key.")
        # Put key last in reverse priority
        priority.append(key)
        items = data.items()
        for key in priority:
            items = sorted(items, key=lambda t: t[1][key], reverse=True)
        max_name_len = max(map(lambda m: len(str(m)), data.keys()))
        # Headers
        headers = (
            ("Rank"),
            ("Member") + " " * (max_name_len - 6),
            ("Wins"),
            ("Games Played"),
            ("Total Score"),
            ("Average Score"),
        )
        lines = [" | ".join(headers), " | ".join(("-" * len(h) for h in headers))]
        # Header underlines
        for rank, tup in enumerate(items, 1):
            member, m_data = tup
            # Align fields to header width
            fields = tuple(
                map(
                    str,
                    (
                        rank,
                        member,
                        m_data["wins"],
                        m_data["games"],
                        m_data["total_score"],
                        round(m_data["average_score"], 2),
                    ),
                )
            )
            padding = [" " * (len(h) - len(f)) for h, f in zip(headers, fields)]
            fields = tuple(f + padding[i] for i, f in enumerate(fields))
            lines.append(" | ".join(fields))
            if rank == top:
                break
        return "\n".join(lines)

    @commands.Cog.listener()
    async def on_trivia_end(self, session: TriviaSession):
        """Event for a trivia session ending.

        This method removes the session from this cog's sessions, and
        cancels any tasks which it was running.

        Parameters
        ----------
        session : TriviaSession
            The session which has just ended.

        """
        channel = session.ctx.channel
        LOG.debug("Ending trivia session; #%s in %s", channel, channel.guild.id)
        if session in self.trivia_sessions:
            self.trivia_sessions.remove(session)
        if session.scores:
            await self.update_leaderboard(session)

    async def update_leaderboard(self, session):
        """Update the leaderboard with the given scores.

        Parameters
        ----------
        session : TriviaSession
            The trivia session to update scores from.

        """
        max_score = session.settings["max_score"]
        for member, score in session.scores.items():
            if member.id == session.ctx.bot.user.id:
                continue
            stats = await self.config.member(member).all()
            if score == max_score:
                stats["wins"] += 1
            stats["total_score"] += score
            stats["games"] += 1
            await self.config.member(member).set(stats)

    def get_trivia_list(self, category: str) -> dict:
        """Get the trivia list corresponding to the given category.

        Parameters
        ----------
        category : str
            The desired category. Case sensitive.

        Returns
        -------
        `dict`
            A dict mapping questions (`str`) to answers (`list` of `str`).

        """
        try:
            path = next(p for p in self._all_lists() if p.stem == category)
        except StopIteration:
            raise FileNotFoundError("Could not find the `{}` category.".format(category))

        with path.open(encoding="utf-8") as file:
            try:
                dict_ = yaml.safe_load(file)
            except yaml.error.YAMLError as exc:
                raise InvalidListError("YAML parsing failed.") from exc
            else:
                return dict_

    async def _save_trivia_list(
        self, ctx: commands.Context, attachment: discord.Attachment
    ) -> None:
        """Checks and saves a trivia list to data folder.

        Parameters
        ----------
        file : discord.Attachment
            A discord message attachment.

        Returns
        -------
        None
        """
        filename = attachment.filename.rsplit(".", 1)[0]

        # Check if trivia filename exists in core files or if it is a command
        if filename in self.trivia.all_commands or any(
            filename == item.stem for item in get_core_lists()
        ):
            await ctx.send(
                (
                    "{filename} is a reserved trivia name and cannot be replaced.\n"
                    "Choose another name."
                ).format(filename=filename)
            )
            return

        file = cog_data_path(self) / f"{filename}.yaml"
        if file.exists():
            overwrite_message = ("{filename} already exists. Do you wish to overwrite?").format(
                filename=filename
            )

            can_react = ctx.channel.permissions_for(ctx.me).add_reactions
            if not can_react:
                overwrite_message += " (y/n)"

            overwrite_message_object: discord.Message = await ctx.send(overwrite_message)
            if can_react:
                # noinspection PyAsyncCall
                start_adding_reactions(
                    overwrite_message_object, ReactionPredicate.YES_OR_NO_EMOJIS
                )
                pred = ReactionPredicate.yes_or_no(overwrite_message_object, ctx.author)
                event = "reaction_add"
            else:
                pred = MessagePredicate.yes_or_no(ctx=ctx)
                event = "message"
            try:
                await ctx.bot.wait_for(event, check=pred, timeout=30)
            except asyncio.TimeoutError:
                await ctx.send(("You took too long answering."))
                return

            if pred.result is False:
                await ctx.send(("I am not replacing the existing file."))
                return

        buffer = io.BytesIO(await attachment.read())
        yaml.safe_load(buffer)
        buffer.seek(0)

        with file.open("wb") as fp:
            fp.write(buffer.read())
        await ctx.send(("Saved Trivia list as {filename}.").format(filename=filename))

    def _get_trivia_session(self, channel: discord.TextChannel) -> TriviaSession:
        return next(
            (session for session in self.trivia_sessions if session.ctx.channel == channel), None
        )

    def _all_lists(self) -> List[pathlib.Path]:
        personal_lists = [p.resolve() for p in cog_data_path(self).glob("*.yaml")]

        return personal_lists + get_core_lists()

    def cog_unload(self):
        for session in self.trivia_sessions:
            session.force_stop()


def get_core_lists() -> List[pathlib.Path]:
    """Return a list of paths for all trivia lists packaged with the bot."""
    core_lists_path = pathlib.Path(__file__).parent.resolve() / "data/lists"
    return list(core_lists_path.glob("*.yaml"))