import asyncio
import re
from enum import Enum
from types import MappingProxyType
from typing import List, Mapping, Optional, Tuple, Union

import discord
from ossapi import GameMode
from ossapi import Mod as OsuMod
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list, inline
from redbot.core.utils.menus import _ControlCallable, close_menu, next_page

from .abc import MixinMeta
from .utils.classes import (
    CommandArgs,
    CommandParams,
    ConflictingArgumentsError,
    DoubleArgs,
    InvalidCountryError,
    MissingValueError,
    OutOfRangeError,
    SingleArgs,
    TooManyArgumentsError,
)

EMOJI = {
    "XH": "<:SSH_Rank:794823890873483305>",
    "X": "<:SS_Rank:794823687807172608>",
    "SH": "<:SH_Rank:794823687311720450>",
    "S": "<:S_Rank:794823687492337714>",
    "A": "<:A_Rank:794823687470710815>",
    "B": "<:B_Rank:794823687446593557>",
    "C": "<:C_Rank:794823687488012308>",
    "D": "<:F_Rank:794823687781613609>",
    "F": "<:F_Rank:794823687781613609>",
    "BPM": "<:BPM:833130972668493824>",
}

FAVICON = "https://osu.ppy.sh/images/favicon/favicon-32x32.png"


class OsuUrls(Enum):
    """
    Commonly used url prefixes.

    Usage
    -----
    `USER`: `URL`+`User.id`

    `FLAG`: `URL`+`User.country_code`+`.png`

    `BEATMAP_DOWNLOAD`: `URL`+`Beatmapset.id` or `URL`+`Beatmapset.id`+`n` (For no video)

    `BEATMAP`: `URL`+`Beatmap.id`

    `NEWS`: `URL`+`NewsPost.slug`

    `CHANGELOG`: `URL`+`UpdateStream.name`+`Build.version`
    """

    MAIN = "https://osu.ppy.sh/"
    USER = "https://osu.ppy.sh/u/"
    FLAG = "https://osu.ppy.sh/images/flags/"
    BEATMAP_DOWNLOAD = "https://osu.ppy.sh/d/"
    BEATMAP = "https://osu.ppy.sh/beatmaps/"
    NEWS = "https://osu.ppy.sh/home/news/"
    CHANGELOG = "https://osu.ppy.sh/home/changelog/"


class Utilities(MixinMeta):
    """Various utilities for other functions."""

    def toggle_page(self, bot: Red) -> Mapping[str, _ControlCallable]:
        return MappingProxyType(
            {bot.get_emoji(755808377959088159): next_page, "\N{CROSS MARK}": close_menu}
        )

    @staticmethod
    async def profile_linking_onboarding(ctx: commands.Context) -> None:
        await ctx.maybe_send_embed(
            "\n\n".join(
                [
                    "\n".join(
                        [
                            "Looks like you haven't linked an account.",
                            f"You can do so using `{ctx.clean_prefix}osulink <username>`",
                        ]
                    ),
                    "\n".join(
                        [
                            "Alternatively you can use the command",
                            "with a username or id after it.",
                        ]
                    ),
                ]
            )
        )

    def beatmap_converter(self, search_string: Union[int, str]) -> Optional[int]:
        """Tries to get a beatmap id from a search string."""
        if "osu.ppy.sh/b/" in search_string or "osu.ppy.sh/beatmap" in search_string:
            if search_string.endswith("/"):
                search_string = search_string[-1:]
            return re.sub("[^0-9]", "", search_string.rsplit("/", 1)[-1])
        else:
            try:
                return int(search_string)
            except ValueError:
                pass
        return None

    async def user_id_extractor(
        self,
        ctx: commands.Context,
        user: Optional[Union[discord.Member, str]],
        check_leaderboard: bool = False,
    ) -> Union[Tuple[Optional[int], bool], Optional[int]]:
        """User ID extraction.

        A function that tries its best to find a user ID from a users config
        or by extracting it from the given string.
        """
        user_id = None
        if user is None:
            user_id: Optional[int] = await self.osu_config.user(ctx.author).user_id()
            if user_id is None:
                return await self.profile_linking_onboarding(ctx)
            elif check_leaderboard:
                return user_id, True
        else:
            if isinstance(user, discord.Member):
                user_id: Optional[int] = await self.osu_config.user(user).user_id()

            if user_id is None:
                if isinstance(user, discord.Member):
                    temp_user = user.name
                else:
                    temp_user = str(user)
                if (
                    user.startswith("https://osu.ppy.sh/users/")
                    or user.startswith("http://osu.ppy.sh/users/")
                    or user.startswith("https://osu.ppy.sh/u/")
                    or user.startswith("http://osu.ppy.sh/u/")
                ):
                    clean_user: str = (
                        user.replace("/osu", "")
                        .replace("/taiko", "")
                        .replace("/fruits", "")
                        .replace("/mania", "")
                    )
                    temp_user: str = clean_user.rsplit("/", 1)[-1]

                try:
                    data = await self.api.user(temp_user)
                except ValueError:
                    pass
                else:
                    if data is not None:
                        return data.id

            if user_id is None:
                try:
                    member = await commands.MemberConverter().convert(ctx, str(user))
                    user_id: Optional[int] = await self.osu_config.user(member).user_id()
                except:
                    await del_message(ctx, f"Could not find the user {user}.")

        return user_id

    async def user_and_parameter_extractor(
        self,
        ctx: commands.Context,
        params: Tuple[str],
        single_args: List[SingleArgs] = [],
        double_args: List[DoubleArgs] = [],
        skip_user: bool = False,
    ) -> Optional[CommandParams]:
        """The verbose part of the parameter search logic.

        Will look for the given arguments in our parameters
        and output any issues to the user.

        Will also try to find a functional user ID unless `skip_user` is `True`.
        """
        try:
            parameters = CommandParams(params, single_args, double_args)
        except TooManyArgumentsError:
            return await del_message(ctx, "You seem to have used too many arguments.")
        except MissingValueError as e:
            return await del_message(ctx, f"A proper number has to be provided for {inline(e)}.")
        except ConflictingArgumentsError as e:
            formatted_list = [inline(x) for x in e.parameters]
            arg_string = humanize_list(formatted_list)
            return await del_message(
                ctx,
                f"You can't use the arguments {arg_string} at the same time.",
            )
        except OutOfRangeError as e:
            return await del_message(
                ctx, f"Index for {inline(e.param)} have to be between 1-{e.value}"
            )

        if parameters.rank is not None and parameters.extra_param is not None:
            return await del_message(
                ctx,
                f"You can't use the {inline('-rank')} command and specify a user at the same time.",
            )

        if skip_user:
            return parameters

        parameters.user_id = await self.user_id_extractor(ctx, parameters.extra_param)

        if parameters.user_id:
            return parameters

    async def argument_extractor(
        self, ctx: commands.Context, args: Tuple[str]
    ) -> Optional[CommandArgs]:
        """The verbose part of the argument search logic.

        Will look for arguments that match criteria
        and output any issues to the user.
        """
        try:
            arguments = CommandArgs(args)
        except TooManyArgumentsError:
            return await del_message(ctx, "You seem to have used too many arguments.")
        except ConflictingArgumentsError as e:
            formatted_list = [inline(x) for x in e.parameters]
            arg_string = humanize_list(formatted_list)
            return await del_message(
                ctx,
                f"You can't use the arguments {arg_string} at the same time.",
            )
        except OutOfRangeError as e:
            return await del_message(
                ctx, f"You can't use the argument {inline(e.param)} with {inline(e.value)}"
            )
        except InvalidCountryError:
            return await del_message(ctx, "The 2 letter country code provided couldn't be found.")

        return arguments

    async def message_history_lookup(
        self, ctx: commands.Context
    ) -> Tuple[Optional[int], OsuMod, GameMode]:
        """Embed history searcher.

        Will look through old embeds from the bot
        and try to find a matching map id, mods and gamemode
        from the variou embeds formats the bot uses.
        """
        mods = OsuMod(0)
        embeds: List[discord.Embed] = []
        map_id: Optional[int] = None
        mode: Optional[GameMode]

        async for message in ctx.channel.history(limit=50):
            if message.author.id == self.bot.user.id and len(message.embeds) > 0:
                embeds.append(message.embeds[0])

        mode: Optional[GameMode] = None
        ugly_mode: Optional[str] = None

        if embeds:
            ugly_mode: Optional[str] = None
            for embed in embeds:
                description = None
                if embed.author.url:  # Author url
                    if "/beatmaps/" in embed.author.url:  # Beatmap
                        map_id = embed.author.url.rsplit("/", 1)[-1]
                        if " | osu!" in embed.footer.text:  # Mode
                            for s in embed.footer.text.split(" | "):
                                if "osu!" in s:
                                    ugly_mode = s[:-4].lower()
                        if "+" in embed.fields[0].value:  # Mods
                            mods = OsuMod(embed.fields[0].value.split("+")[1])
                        break
                if embed.url:  # Title url
                    if "/beatmaps/" in embed.url:  # Beatmap
                        map_id = embed.url.rsplit("/", 1)[-1]
                        if embed.author.name:  # Mode
                            if embed.author.name.startswith("osu!"):
                                ugly_mode = embed.author.name[:-4].split(" ", 1)[0].lower()
                        break
                if embed.description:  # Description
                    description = re.search(r"beatmaps/(.*?)\)", embed.description)
                if description:
                    map_id = description.group(1)  # Beatmap
                    if embed.author.name:  # Mode
                        if " | osu!" in embed.author.name:
                            ugly_mode = embed.author.name.rsplit(" | osu!", 1)[-1].lower()
                    firstrow = embed.description.split("\n")[0]
                    if "**+" in firstrow:  # Mods
                        mods = OsuMod(firstrow.split("**+")[1].split("** [")[0])
                    break

        if ugly_mode:  # Solution for me wanting catchy mode names
            if ugly_mode == "standard":
                mode = GameMode.OSU
            elif ugly_mode == "catch":
                mode = GameMode.CATCH
            else:
                mode = GameMode(ugly_mode)

        return map_id, mods, mode

    def prettify_mode(self, mode: GameMode) -> str:
        """Turns the api mode names into my own flavor of naming."""
        if mode == GameMode.OSU:
            pretty_mode = "standard"
        elif mode == GameMode.CATCH:
            pretty_mode = "catch"
        else:
            pretty_mode = mode.value

        return pretty_mode


async def del_message(ctx: commands.Context, message_text: str, timeout: int = 10) -> None:
    """Simple function to sends a small embed that auto-deletes."""

    message = await ctx.maybe_send_embed(message_text)
    await asyncio.sleep(timeout)
    try:
        await message.delete()
    except (discord.errors.NotFound, discord.errors.Forbidden):
        pass
