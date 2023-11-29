import re
from datetime import timedelta
from typing import List, Optional, Union

import discord
from ossapi import GameMode
from ossapi import Score as OsuScore
from ossapi import ScoreType
from ossapi import User as OsuUser
from ossapi import UserLookupKey
from redbot.core import commands
from redbot.core.utils.chat_formatting import box, humanize_number, humanize_timedelta
from redbot.core.utils.menus import menu

from .abc import MixinMeta
from .utilities import EMOJI, OsuUrls, del_message
from .utils.classes import DoubleArgs


class Embeds(MixinMeta):
    """Embed builders."""

    async def profile_embed(
        self, ctx: commands.Context, data: OsuUser, mode: GameMode = GameMode.OSU
    ):
        pretty_mode = self.prettify_mode(mode)

        global_rank = 0
        country_rank = 0
        if data.statistics.global_rank:
            global_rank = humanize_number(data.statistics.global_rank)
            country_rank = humanize_number(data.statistics.country_rank)

        if mode == GameMode.MANIA and data.statistics.variants is not None:
            pp_list: List[str] = []
            pp_list.append(f"{humanize_number(data.statistics.pp)}pp")
            if data.statistics.variants[0].pp:
                pp_list.append(f"{humanize_number(data.statistics.variants[0].pp)}pp | **4k**")
            if data.statistics.variants[1].pp:
                pp_list.append(f"{humanize_number(data.statistics.variants[1].pp)}pp | **7k**")

            pp_string = "\n".join(pp_list)

            ranking_list = []
            ranking_list.append(f"#{global_rank} ({data.country_code.upper()} #{country_rank})")
            if data.statistics.variants[0].global_rank:
                ranking_list.append(
                    f"#{humanize_number(data.statistics.variants[0].global_rank)} "
                    f"({data.country_code.upper()} "
                    f"#{humanize_number(data.statistics.variants[0].country_rank)}) "
                    f"| **4k**"
                )
            if data.statistics.variants[1].global_rank:
                ranking_list.append(
                    f"#{humanize_number(data.statistics.variants[1].global_rank)} "
                    f"({data.country_code.upper()} "
                    f"#{humanize_number(data.statistics.variants[1].country_rank)}) "
                    f"| **7k**"
                )

            ranking_string = "\n".join(ranking_list)
        else:
            pp_string = f"{humanize_number(data.statistics.pp)}pp"
            ranking_string = f"#{global_rank} ({data.country_code.upper()} #{country_rank})"

        if data.statistics.play_time:
            playtime = re.split(
                r",\s", humanize_timedelta(timedelta=timedelta(seconds=data.statistics.play_time))
            )
            try:
                playtime = f"{playtime[0]}, {playtime[1]}, {playtime[2]}"
            except IndexError:
                try:
                    playtime = f"{playtime[0]}, {playtime[1]}"
                except IndexError:
                    try:
                        playtime = f"{playtime[0]}"
                    except IndexError:
                        playtime = "0"
        else:
            playtime = "None"

        try:
            rank_history = list(map(int, data.rank_history.data))
            rank_history = box(
                "\n".join(
                    [
                        f" Delta |   Rank   | Date",
                        f"-----------------------",
                        f'   -   |{"{0:^10}".format(humanize_number(rank_history[0]))}| -90d',
                        f'{"{0:^7}".format(humanize_number(rank_history[0] - rank_history[14]))}|{"{0:^10}".format(humanize_number(rank_history[14]))}| -75d',
                        f'{"{0:^7}".format(humanize_number(rank_history[14] - rank_history[29]))}|{"{0:^10}".format(humanize_number(rank_history[29]))}| -60d',
                        f'{"{0:^7}".format(humanize_number(rank_history[29] - rank_history[44]))}|{"{0:^10}".format(humanize_number(rank_history[44]))}| -45d',
                        f'{"{0:^7}".format(humanize_number(rank_history[44] - rank_history[59]))}|{"{0:^10}".format(humanize_number(rank_history[59]))}| -30d',
                        f'{"{0:^7}".format(humanize_number(rank_history[59] - rank_history[74]))}|{"{0:^10}".format(humanize_number(rank_history[74]))}| -15d',
                        f'{"{0:^7}".format(humanize_number(rank_history[74] - rank_history[89]))}|{"{0:^10}".format(humanize_number(rank_history[89]))}|  Now',
                    ]
                )
            )
        except (TypeError, KeyError):
            rank_history = "This user doesn't have any rank history."

        embed_list: List[discord.Embed] = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        base_embed.set_author(
            name=f"{data.username} | osu!{pretty_mode.capitalize()}",
            url=f"{OsuUrls.USER.value}{data.id}",
            icon_url=f"{OsuUrls.FLAG.value}{data.country_code}.png",
        )

        base_embed.set_thumbnail(url=data.avatar_url)

        page = 1

        while page <= 2:
            embed = base_embed.copy()

            embed.clear_fields()

            embed.add_field(name="Ranking", value=ranking_string, inline=True)
            embed.add_field(name="Performance", value=pp_string, inline=True)
            embed.add_field(
                name="Accuracy", value=f"{round(data.statistics.hit_accuracy, 2)}%", inline=True
            )
            embed.add_field(
                name="Level",
                value=f"{data.statistics.level.current} ({data.statistics.level.progress}%)",
                inline=True,
            )
            embed.add_field(
                name="Max Combo", value=humanize_number(data.statistics.maximum_combo), inline=True
            )
            embed.add_field(
                name="Playcount", value=humanize_number(data.statistics.play_count), inline=True
            )
            embed.add_field(
                name="Grades",
                value=(
                    f'{EMOJI["XH"]} {data.statistics.grade_counts.ssh} '
                    f'{EMOJI["X"]} {data.statistics.grade_counts.ss} '
                    f'{EMOJI["SH"]} {data.statistics.grade_counts.sh} '
                    f'{EMOJI["S"]} {data.statistics.grade_counts.s} '
                    f'{EMOJI["A"]} {data.statistics.grade_counts.a}'
                ),
                inline=False,
            )

            if page >= 2:
                embed.add_field(
                    name="Ranked Score",
                    value=humanize_number(data.statistics.ranked_score),
                    inline=True,
                )
                embed.add_field(
                    name="#1 Scores", value=humanize_number(data.scores_first_count), inline=True
                )
                embed.add_field(name="Play Time", value=playtime, inline=True)
                embed.add_field(
                    name="Total Score",
                    value=humanize_number(data.statistics.total_score),
                    inline=True,
                )
                embed.add_field(
                    name="Replays Watched",
                    value=humanize_number(data.statistics.replays_watched_by_others),
                    inline=True,
                )
                embed.add_field(
                    name="Joined osu!",
                    value="\n".join(
                        [
                            f"<t:{int(data.join_date.timestamp())}:D> ◈",
                            f"<t:{int(data.join_date.timestamp())}:R> ◈",
                        ]
                    ),
                    inline=True,
                )
                embed.add_field(name="Rank Change", value=rank_history, inline=False)
                embed.add_field(
                    name="Total Hits",
                    value=humanize_number(data.statistics.total_hits),
                    inline=True,
                )
                embed.add_field(name="Medals", value=len(data.user_achievements), inline=True)
                embed.add_field(
                    name="Favorite Beatmaps",
                    value=humanize_number(data.favourite_beatmapset_count),
                    inline=True,
                )
                embed.add_field(
                    name="Followers", value=humanize_number(data.follower_count), inline=True
                )
                embed.add_field(
                    name="Mapping Followers",
                    value=humanize_number(data.mapping_follower_count),
                    inline=True,
                )
                embed.add_field(
                    name="Kudoso Total", value=humanize_number(data.kudosu.total), inline=True
                )
                embed.add_field(
                    name="Uploaded Beatmaps",
                    value=(
                        f"Ranked: **{data.ranked_and_approved_beatmapset_count}** "
                        f"◈ Loved: **{data.loved_beatmapset_count}** "
                        f"◈ Unranked: **{data.unranked_beatmapset_count}** "
                        f"◈ Graveyarded: **{data.graveyard_beatmapset_count}**"
                    ),
                    inline=False,
                )

            if data.is_online == True:
                embed.set_footer(text="Currently Online")
            elif not data.last_visit:
                embed.set_footer(text="Last Online | Unknown")
            else:
                embed.set_footer(text="Last Online")
                embed.timestamp = data.last_visit

            embed_list.append(embed)
            page += 1

        return embed_list

    async def pp_embed(self, ctx: commands.Context, data: List[OsuScore], pp_arg: float):
        player = data[0].user()
        _pp_list = []
        for score in data:
            _pp_list.append(score.pp)
        pp_average = sum(_pp_list) / len(_pp_list)
        pp_median = _pp_list[round((len(_pp_list) - 1) / 2)]

        pretty_mode = self.prettify_mode(data[0].mode)

        embed_list: List[discord.Embed] = []
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        if pp_arg != 0:
            count = 0
            for score in data:
                if score.pp is None:
                    continue
                if score.pp > pp_arg:
                    count += 1
            embed.title = f"You have {count} plays above {round(pp_arg, 2)}pp"

        embed.set_author(
            name=f"{player.username} | osu!{pretty_mode.capitalize()}",
            url=f"{OsuUrls.USER.value}{player.id}",
            icon_url=f"{OsuUrls.FLAG.value}{player.country_code}.png",
        )

        embed.set_thumbnail(url=player.avatar_url)

        embed.add_field(
            name="Highest pp Play", value=humanize_number(round(data[0].pp, 2)), inline=True
        )
        embed.add_field(
            name="Lowest pp Play", value=humanize_number(round(data[-1].pp, 2)), inline=True
        )
        embed.add_field(
            name="Average / Median",
            value=f"{humanize_number(round(pp_average,2))} / {humanize_number(round(pp_median,2))}",
            inline=True,
        )

        embed_list.append(embed)

        return embed_list


class Commands(Embeds):
    """Command logic."""

    async def profile_command(
        self,
        ctx: commands.Context,
        mode: GameMode,
        user: Optional[Union[discord.Member, str]] = None,
    ) -> None:
        """User profile."""
        user_id = await self.user_id_extractor(ctx, user)

        if not user_id:
            return

        data = await self.api.user(
            user_id,
            key=UserLookupKey.ID,
            mode=mode,
        )

        if data:
            embeds = await self.profile_embed(ctx, data, mode)
            return await menu(ctx, embeds, self.toggle_page(self.bot))

        if user:
            return await del_message(ctx, f"I can't seem to get {user}'s profile.")

        await del_message(ctx, "I can't seem to get your profile.")

    async def pp_command(self, ctx: commands.Context, mode: GameMode, user_or_args: tuple) -> None:
        """User pp stats."""
        arguments = await self.user_and_parameter_extractor(
            ctx, user_or_args, double_args=[DoubleArgs.PP]
        )

        if not arguments:
            return

        data = await self.api.user_scores(arguments.user_id, ScoreType.BEST, limit=100, mode=mode)

        if not data:
            return await del_message(
                ctx, f"There isn't enough plays by that user to use this command."
            )

        embeds = await self.pp_embed(ctx, data, arguments.pp)
        await menu(ctx, embeds)


class User(Commands):
    """User related commands."""

    @commands.command(aliases=["osu", "std"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def standard(
        self, ctx: commands.Context, *, user: Optional[Union[discord.Member, str]] = None
    ) -> None:
        """Get a players osu! profile."""

        await self.profile_command(ctx, GameMode.OSU, user)

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def taiko(
        self, ctx: commands.Context, *, user: Optional[Union[discord.Member, str]] = None
    ) -> None:
        """Get a players osu! profile."""

        await self.profile_command(ctx, GameMode.TAIKO, user)

    @commands.command(aliases=["catch", "ctb"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def fruits(
        self, ctx: commands.Context, *, user: Optional[Union[discord.Member, str]] = None
    ) -> None:
        """Get a players osu! profile."""

        await self.profile_command(ctx, GameMode.CATCH, user)

    @commands.command(hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def mania(
        self, ctx: commands.Context, *, user: Optional[Union[discord.Member, str]] = None
    ) -> None:
        """Get a players osu! profile."""

        await self.profile_command(ctx, GameMode.MANIA, user)

    @commands.command(name="ppstandard", aliases=["ppstd", "pposu", "ppo"], usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pp_standard(self, ctx: commands.Context, *user_or_args):
        """Shows pp info for osu!.

        **Arguments:**

        - `-pp <number>` will display how many scores you have above `<number>`"""

        await self.pp_command(ctx, GameMode.OSU, user_or_args)

    @commands.command(name="pptaiko", aliases=["ppt"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pp_taiko(self, ctx: commands.Context, *user_or_args):
        """Shows pp info for osu!.

        **Arguments:**

        - `-pp <number>` will display how many scores you have above `<number>`"""

        await self.pp_command(ctx, GameMode.TAIKO, user_or_args)

    @commands.command(
        name="ppfruits", aliases=["ppf", "ppcatch", "ppctb"], hidden=True, usage="[user] [args]"
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pp_fruits(self, ctx: commands.Context, *user_or_args):
        """Shows pp info for osu!.

        **Arguments:**

        - `-pp <number>` will display how many scores you have above `<number>`"""

        await self.pp_command(ctx, GameMode.CATCH, user_or_args)

    @commands.command(name="ppmania", aliases=["ppm"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pp_mania(self, ctx: commands.Context, *user_or_args):
        """Shows pp info for osu!.

        **Arguments:**

        - `-pp <number>` will display how many scores you have above `<number>`"""

        await self.pp_command(ctx, GameMode.MANIA, user_or_args)
