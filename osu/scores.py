import asyncio
import re
from math import ceil
from typing import List, Optional, Union

import discord
from ossapi import Cursor, GameMode
from ossapi import Mod as OsuMod
from ossapi import RankingType
from ossapi import Score as OsuScore
from ossapi import ScoreType
from ossapi.models import Grade as OsuGrade
from redbot.core import commands
from redbot.core.utils.chat_formatting import humanize_number, inline
from redbot.core.utils.menus import menu

from .abc import MixinMeta
from .utilities import EMOJI, OsuUrls, del_message
from .utils.beatmapparser import DatabaseBeatmap
from .utils.classes import DoubleArgs, SingleArgs
from .utils.custommenu import check_controls, custom_menu


class Embeds(MixinMeta):
    """Embed builders."""

    async def score_embed(
        self, ctx: commands.Context, data: List[OsuScore], page: Optional[int] = None
    ) -> List[discord.Embed]:
        if page is not None:
            score = data[page]
        else:
            score = data[0]

        extra_data = await self.extra_beatmap_info(score.beatmap)

        pretty_mode = self.prettify_mode(score.mode)

        # If this is for recent. Only generate one embed. Else loop through all the data
        embed_list = []

        if page is not None:
            embed_list.append(
                await self.score_embed_builder(ctx, score, extra_data, data, pretty_mode, page)
            )
            return embed_list
        for score_index in range(len(data)):
            embed_list.append(
                await self.score_embed_builder(
                    ctx, data[score_index], extra_data, data, pretty_mode, score_index
                )
            )

        return embed_list

    async def score_embed_builder(
        self,
        ctx: commands.Context,
        score: OsuScore,
        extra_data: DatabaseBeatmap,
        data: List[OsuScore],
        pretty_mode: str,
        page: Optional[int] = None,
    ) -> discord.Embed:
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        if score.mode == GameMode.MANIA:
            comboratio = "Combo / Ratio"
            version = re.sub(r"^\S*\s", "", score.beatmap.version)
            try:
                ratio = round(score.statistics.count_geki / score.statistics.count_300, 2)
            except:
                ratio = "Perfect"
            combo = f"**{score.max_combo:,}x** / {ratio}"
            hits = "/".join(
                [
                    f"{humanize_number(score.statistics.count_geki)}",
                    f"{humanize_number(score.statistics.count_300)}",
                    f"{humanize_number(score.statistics.count_katu)}",
                    f"{humanize_number(score.statistics.count_100)}",
                    f"{humanize_number(score.statistics.count_50)}",
                    f"{humanize_number(score.statistics.count_miss)}",
                ]
            )
            stats = f"OD: `{score.beatmap.accuracy}` | HP: `{score.beatmap.drain}`"
        else:
            version = score.beatmap.version
            comboratio = "Combo"
            combo = f"**{score.max_combo:,}x**"
            hits = "/".join(
                [
                    f"{humanize_number(score.statistics.count_300)}",
                    f"{humanize_number(score.statistics.count_100)}",
                    f"{humanize_number(score.statistics.count_50)}",
                    f"{humanize_number(score.statistics.count_miss)}",
                ]
            )
            stats = (
                f"CS: `{score.beatmap.cs}` |"
                f"AR: `{score.beatmap.ar}` |"
                f"OD: `{score.beatmap.accuracy}` |"
                f"HP: `{score.beatmap.drain}`"
            )

        if score.rank == OsuGrade.F:
            if score.mode == GameMode.OSU:
                fail_point = (
                    score.statistics.count_300
                    + score.statistics.count_100
                    + score.statistics.count_50
                    + score.statistics.count_miss
                    - 1
                )
            elif score.mode == GameMode.CATCH:
                fail_point = (
                    score.statistics.count_300
                    - score.beatmap.count_sliders
                    + score.beatmap.count_circles
                    + score.statistics.count_katu
                    - 1
                )
            elif score.mode == GameMode.TAIKO:
                fail_point = (
                    score.statistics.count_geki
                    + score.statistics.count_300
                    + score.statistics.count_katu
                    + score.statistics.count_100
                    + score.statistics.count_50
                    + score.statistics.count_miss
                    - 1
                )
            elif score.mode == GameMode.MANIA:
                fail_point = (
                    score.statistics.count_geki
                    + score.statistics.count_300
                    + score.statistics.count_katu
                    + score.statistics.count_100
                    + score.statistics.count_50
                    + score.statistics.count_miss
                    - 1
                )

            try:
                map_start = extra_data.hitobjects[0].time
                map_end = extra_data.hitobjects[-1].time
                map_fail = extra_data.hitobjects[fail_point].time
                fail_string = "{:.2%}".format((map_fail - map_start) / (map_end - map_start))
            except KeyError:
                fail_string = ""

        mods = ""
        if score.mods != OsuMod.NM:
            mods = f" +{score.mods.short_name()}"

        try:
            performance = humanize_number(round(score.pp, 2))
        except TypeError:
            performance = 0

        download = f"[Link]({OsuUrls.BEATMAP_DOWNLOAD.value}{score.beatmapset.id})"
        if score.beatmapset.video:
            download += f" ([No Video]({OsuUrls.BEATMAP_DOWNLOAD.value}{score.beatmapset.id}n))"

        embed.set_author(
            name=f"{score.beatmapset.artist} - {score.beatmapset.title} [{version}] [{str(score.beatmap.difficulty_rating)}★]",
            url=score.beatmap.url,
            icon_url=score.user().avatar_url,
        )

        if score.rank == OsuGrade.F:
            if fail_string:
                embed.title = f"Failed at {fail_string}"
            else:
                embed.title = "Failed"
        else:
            embed.title = "Passed"

        embed.set_image(url=score.beatmapset.covers.cover)

        embed.add_field(name="Grade", value=f"{EMOJI[score.rank.value]}{mods}", inline=True)
        embed.add_field(name="Score", value=humanize_number(score.score), inline=True)
        embed.add_field(name="Accuracy", value="{:.2%}".format(score.accuracy), inline=True)
        embed.add_field(name="PP", value=f"**{performance}pp**", inline=True)
        embed.add_field(name=comboratio, value=combo, inline=True)
        embed.add_field(name="Hits", value=hits, inline=True)
        embed.add_field(
            name="Map Info",
            value="\n".join(
                [
                    f"Mapper: [{score.beatmapset.creator}]({OsuUrls.USER.value}{score.beatmapset.user_id}) |"
                    f' {EMOJI["BPM"]} `{score.beatmap.bpm}` |'
                    f" Objects: `{humanize_number(score.beatmap.count_circles + score.beatmap.count_sliders + score.beatmap.count_spinners)}` ",
                    f"Status: {inline(score.beatmapset.status.name.capitalize())} | {stats}",
                    f"Download: {download}",
                ]
            ),
            inline=False,
        )

        embed.set_footer(
            text=f"Play {page + 1}/{len(data)} | {score.user().username} | osu!{pretty_mode.capitalize()} | Played"
        )

        embed.timestamp = score.created_at

        return embed

    async def top_embed(
        self, ctx: commands.Context, data: List[OsuScore], sort_recent: bool, index: Optional[int]
    ) -> List[discord.Embed]:
        player = data[0].user()

        pretty_mode = self.prettify_mode(data[0].mode)

        recent_text = "Top"
        if sort_recent == True:
            for i in range(len(data)):
                data[i].index = i
            data = sorted(data, key=lambda item: item.created_at, reverse=True)
            recent_text = "Most recent top"

        author_text = "plays"
        if index:
            author_text = "#" + str(index)

        embed_list = []

        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        base_embed.set_author(
            name=f"{recent_text} {author_text} for {player.username} | osu!{pretty_mode.capitalize()}",
            url=f"{OsuUrls.USER.value}{player.id}",
            icon_url=f"{OsuUrls.FLAG.value}{player.country_code}.png",
        )

        base_embed.set_thumbnail(url=player.avatar_url)

        if index:
            score = data[index - 1]
            description = self.score_entry_builder(score, index)
            embed = base_embed.copy()
            embed.set_footer(
                text=f"Weighted pp | {round(score.weight.pp,1)}pp ({round(score.weight.percentage,1)}%)"
            )
            embed.description = description
            embed_list.append(embed)
        else:
            index = 0
            page_num = 1
            while page_num <= ceil(len(data) / 5):
                start_index = (page_num - 1) * 5
                end_index = (page_num - 1) * 5 + 5
                score_entries = []
                for score in data[start_index:end_index]:
                    score_entries.append(self.score_entry_builder(
                        score, score.index if sort_recent else index
                    ))
                    index += 1

                embed = base_embed.copy()

                embed.set_footer(text=f"Page {page_num}/{ceil(len(data) / 5)}")

                embed.description = "\n\n".join(score_entries)

                embed_list.append(embed)
                page_num += 1

        return embed_list

    async def top_compare_embed(
        self, ctx: commands.Context, author_data: List[OsuScore], compare_data: List[OsuScore]
    ) -> Optional[List[discord.Embed]]:
        for i in range(len(compare_data)):
            compare_data[i].index = i

        new_data = compare_data

        for author_score in author_data:
            for compare_score in compare_data:
                if compare_score.beatmap.id == author_score.beatmap.id:
                    new_data.remove(compare_score)
                    break
        compare_data = new_data

        if len(compare_data) < 1:
            return

        player = compare_data[0].user()

        pretty_mode = self.prettify_mode(compare_data[0].mode)

        embed_list = []

        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        base_embed.set_author(
            name=f"Comparing unique top plays for {player.username} | osu!{pretty_mode.capitalize()}",
            url=f"{OsuUrls.USER.value}{player.id}",
            icon_url=f"{OsuUrls.FLAG.value}{player.country_code}.png",
        )

        base_embed.set_thumbnail(url=player.avatar_url)

        page_num = 1
        while page_num <= ceil(len(compare_data) / 5):
            embed = base_embed.copy()

            start_index = (page_num - 1) * 5
            end_index = (page_num - 1) * 5 + 5
            score_entries = []
            for score in compare_data[start_index:end_index]:
                score_entries.append(self.score_entry_builder(score, score.index))

            embed.description = "\n\n".join(score_entries)

            embed.set_footer(
                text=f"Page {page_num}/{ceil(len(compare_data) / 5)} ◈ Found {len(compare_data)} unique plays not in top 100 for {author_data[0].user().username}"
            )

            embed_list.append(embed)
            page_num += 1

        return embed_list

    def score_entry_builder(self, score: OsuScore, index: int) -> str:
        mods = ""
        if score.mods != OsuMod.NM:
            mods = f" **+{score.mods.short_name()}**"

        if score.mode == GameMode.MANIA:
            version = re.sub(r"^\S*\s", "", score.beatmap.version)
            hits = "/".join(
                [
                    f"{humanize_number(score.statistics.count_geki)}",
                    f"{humanize_number(score.statistics.count_300)}",
                    f"{humanize_number(score.statistics.count_katu)}",
                    f"{humanize_number(score.statistics.count_100)}",
                    f"{humanize_number(score.statistics.count_50)}",
                    f"{humanize_number(score.statistics.count_miss)}",
                ]
            )
        else:
            version = score.beatmap.version
            hits = "/".join(
                [
                    f"{humanize_number(score.statistics.count_300)}",
                    f"{humanize_number(score.statistics.count_100)}",
                    f"{humanize_number(score.statistics.count_50)}",
                    f"{humanize_number(score.statistics.count_miss)}",
                ]
            )

        return "\n".join(
            [
                f"**{index + 1}.** **[{score.beatmapset.title} - [{version}]]({score.beatmap.url})**{mods} [{score.beatmap.difficulty_rating}★]",
                f"{EMOJI[score.rank.value]} **{humanize_number(round(score.pp,2))}pp** ◈{mods} ({'{:.2%}'.format(score.accuracy)}) ◈ {humanize_number(score.score)}",
                f"**{humanize_number(score.max_combo)}x** ◈ [{hits}] ◈ <t:{int(score.created_at.timestamp())}:R>",
            ]
        )


class Commands(Embeds):
    """Command logic."""

    async def recent_command(
        self, ctx: commands.Context, user: Optional[str], mode: GameMode
    ) -> None:
        """Recent scores."""
        user_id = await self.user_id_extractor(ctx, user, check_leaderboard=True)

        if not user_id:
            return

        use_leaderboard = False
        if isinstance(user_id, tuple):
            use_leaderboard = True
            user_id = user_id[0]

        data = await self.api.user_scores(
            user_id, ScoreType.RECENT, include_fails=True, limit=5, mode=mode
        )

        if data:
            if self.osubeat_maps:
                await self.queue_osubeat_check(ctx, data)
            if use_leaderboard:
                self.queue_leaderboard(data, mode)

            return await custom_menu(
                ctx,
                await self.score_embed(ctx, data, 0),
                check_controls(data),
                data=data,
                funct=self.score_embed,
            )

        if user:
            return await del_message(
                ctx, f"Looks like {user} don't have any recent plays in that mode."
            )

        await del_message(ctx, f"Looks like you don't have any recent plays in that mode.")

    async def top_command(
        self, ctx: commands.Context, user_or_args: tuple, mode: GameMode
    ) -> None:
        """Top scores."""
        arguments = await self.user_and_parameter_extractor(
            ctx, user_or_args, single_args=[SingleArgs.RECENT], double_args=[DoubleArgs.INDEX]
        )

        if not arguments:
            return

        data = await self.api.user_scores(arguments.user_id, ScoreType.BEST, limit=100, mode=mode)

        if not data:
            return await del_message(
                ctx, f"I can't find any top plays for that user in this mode."
            )

        embeds = await self.top_embed(ctx, data, arguments.r, arguments.p)
        await menu(ctx, embeds)

    async def unique_top_command(self, ctx: commands.Context, user_or_args: tuple, mode: GameMode):
        """Top score comparison."""
        if not user_or_args:
            return await ctx.send_help()

        author_id: Optional[int] = await self.osu_config.user(ctx.author).user_id()

        if author_id is None:
            return await del_message(
                ctx,
                "\n".join(
                    [
                        "You need to have your account linked before using this command.",
                        f"You can do so using `{ctx.clean_prefix}{self.osu_link.name} <username>`",
                    ]
                ),
            )

        arguments = await self.user_and_parameter_extractor(
            ctx, user_or_args, double_args=[DoubleArgs.RANK], skip_user=True
        )

        if arguments is None:
            return

        if arguments.rank is not None:
            data = await self.api.ranking(
                type=RankingType.PERFORMANCE,
                cursor=Cursor(page=ceil(arguments.rank / 50)),
                mode=mode,
            )
            user_id = data.ranking[(arguments.rank % 50) - 1].user.id
        else:
            user_id = arguments.user_id

        if not user_id:
            return

        compare_top_data = await self.api.user_scores(
            user_id, ScoreType.BEST, limit=100, mode=mode
        )

        if not compare_top_data:
            return await del_message(
                ctx, "That user doesn't seem to have any top plays in this mode."
            )

        author_top_data = await self.api.user_scores(
            author_id, ScoreType.BEST, limit=100, mode=mode
        )

        if not author_top_data:
            return await del_message(ctx, "You don't seem to have any top plays in this mode.")

        embeds = await self.top_compare_embed(ctx, author_top_data, compare_top_data)

        if embeds:
            return await menu(ctx, embeds)

        await del_message(
            ctx, "Your top plays are surprisingly identical. (None of them are unique)"
        )

    async def osu_compare_command(self, ctx: commands.Context, user: Optional[str]) -> None:
        """Score comparison."""
        user_id = await self.user_id_extractor(ctx, user)

        if user_id is None:
            return

        map_id, mods, mode = await self.message_history_lookup(ctx)

        if not map_id:
            return await del_message(
                ctx, "Could not find any recently displayed maps in this channel."
            )

        data = await self.api.beatmap_user_scores(map_id, user_id, mode)

        if not data:
            if user:
                return await del_message(ctx, f"I cant find a play from that user on this map")
            else:
                return await del_message(ctx, f"Looks like you don't have a score on that map.")

        await asyncio.sleep(0.2)
        beatmap_data = await self.api.beatmap(map_id)
        beatmapset_data = beatmap_data.beatmapset()

        i = 0
        match = False
        for score in data:
            score.beatmap = beatmap_data
            score.beatmapset = beatmapset_data
            if score.mods == mods:
                match = True

            if not match:
                i += 1

        if match:
            data.insert(0, data.pop(i))

        await menu(ctx, await self.score_embed(ctx, data))

    async def osu_score_command(
        self, ctx: commands.Context, beatmap: Union[int, str], user: Optional[str]
    ) -> None:
        user_id = await self.user_id_extractor(ctx, user)

        if user_id is None:
            return

        map_id = self.beatmap_converter(beatmap)

        if not map_id:
            return await del_message(ctx, f"That doesn't seem to be a valid map.")

        beatmap_data = await self.api.beatmap(map_id)

        if not beatmap_data:
            return await del_message(ctx, "I can't find the map specified.")

        await asyncio.sleep(0.2)
        data = await self.api.beatmap_user_scores(map_id, user_id)

        if not data:
            if user:
                return await del_message(ctx, f"I cant find a play from that user on this map")
            else:
                return await del_message(ctx, f"Looks like you don't have a score on that map.")

        beatmapset_data = beatmap_data.beatmapset()

        for score in data:
            score.beatmap = beatmap_data
            score.beatmapset = beatmapset_data

        await menu(ctx, await self.score_embed(ctx, data))


class Scores(Commands):
    """Score related commands."""

    @commands.command(
        name="recentstandard",
        aliases=["rsstd", "recentosu", "rsosu", "rsstandard", "recentstd", "rso", "recento"],
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recent_standard(self, ctx: commands.Context, *, user: str = None):
        """Get a players recent osu! plays.

        Includes failed plays.
        """

        await self.recent_command(ctx, user, GameMode.OSU)

    @commands.command(name="recenttaiko", aliases=["rst", "rstaiko", "recentt"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recent_taiko(self, ctx: commands.Context, *, user: str = None):
        """Get a players recent osu! plays.

        Includes failed plays.
        """

        await self.recent_command(ctx, user, GameMode.TAIKO)

    @commands.command(
        name="recentfruits",
        aliases=["rsctb", "recentcatch", "recentctb", "rscatch", "rsfruits", "recentf", "rsf"],
        hidden=True,
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recent_fruits(self, ctx: commands.Context, *, user: str = None):
        """Get a players recent osu! plays.

        Includes failed plays.
        """

        await self.recent_command(ctx, user, GameMode.CATCH)

    @commands.command(name="recentmania", aliases=["rsm", "recentm", "rsmania"], hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def recent_mania(self, ctx: commands.Context, *, user: str = None):
        """Get a players recent osu! plays.

        Includes failed plays.
        """

        await self.recent_command(ctx, user, GameMode.MANIA)

    @commands.command(
        name="topstandard", aliases=["topstd", "toposu", "topo"], usage="[user] [args]"
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def top_standard(self, ctx: commands.Context, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        await self.top_command(ctx, user_or_args, GameMode.OSU)

    @commands.command(name="toptaiko", aliases=["topt"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def top_taiko(self, ctx: commands.Context, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        await self.top_command(ctx, user_or_args, GameMode.TAIKO)

    @commands.command(
        name="topfruits",
        aliases=["topcatch", "topctb", "topf"],
        hidden=True,
        usage="[user] [args]",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def top_fruits(self, ctx: commands.Context, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        await self.top_command(ctx, user_or_args, GameMode.CATCH)

    @commands.command(name="topmania", aliases=["topm"], hidden=True, usage="[user] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def top_mania(self, ctx: commands.Context, *user_or_args):
        """Get a players osu! top plays.

        **Arguments:**
        `-r` Sorts your bests by date acquired.
        `-p <index>` Gets a score by specific index.
        """

        await self.top_command(ctx, user_or_args, GameMode.MANIA)

    @commands.command(
        name="uniquetopstandard",
        aliases=[
            "tco",
            "tcstd",
            "tcosu",
            "topcompareosu",
            "topcomparestd",
            "topcompareo",
            "topcomparestandard",
            "uniquetoposu",
        ],
        usage="[user] [args]",
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def unique_top_standard(self, ctx: commands.Context, *user_or_args):
        """Get a list of top plays from another user that are not on your top.

        Requires to have your account linked with the bot.

        **Arguments:**

        - `-rank <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        await self.unique_top_command(ctx, user_or_args, GameMode.OSU)

    @commands.command(
        name="uniquetoptaiko",
        aliases=["tct", "tctaiko", "topcomparet", "topcomparetaiko"],
        hidden=True,
        usage="[user] [args]",
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def unique_top_taiko(self, ctx: commands.Context, *user_or_args):
        """Get a list of top plays from another user that are not on your top.

        Requires to have your account linked with the bot.

        **Arguments:**

        - `-rank <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        await self.unique_top_command(ctx, user_or_args, GameMode.TAIKO)

    @commands.command(
        name="uniquetopfruits",
        aliases=[
            "tcf",
            "tcctb",
            "topcomparecatch",
            "topcomparectb",
            "tcfruits",
            "tccatch",
            "topcomparef",
            "topcomparefruits",
            "uniquetopcatch",
        ],
        hidden=True,
        usage="[user] [args]",
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def unique_top_fruits(self, ctx: commands.Context, *user_or_args):
        """Get a list of top plays from another user that are not on your top.

        Requires to have your account linked with the bot.

        **Arguments:**

        - `-rank <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        await self.unique_top_command(ctx, user_or_args, GameMode.CATCH)

    @commands.command(
        name="uniquetopmania",
        aliases=["tcm", "tcmania", "topcomparem", "topcomparemania"],
        hidden=True,
        usage="[user] [args]",
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def unique_top_mania(self, ctx: commands.Context, *user_or_args):
        """Get a list of top plays from another user that are not on your top.

        Requires to have your account linked with the bot.

        **Arguments:**

        - `-rank <rank>` will compare you with the person at `<rank>` rank. Can not be higher than 10,000.
        - `<user>` compares you with the specific user.
        """

        await self.unique_top_command(ctx, user_or_args, GameMode.MANIA)

    @commands.command(name="osucompare", aliases=["osuc", "oc"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu_compare(self, ctx: commands.Context, *, user: str = None):
        """Compare your or someone elses score with the last one sent in the channel."""

        await self.osu_compare_command(ctx, user)

    @commands.command(name="osuscore", aliases=["osus", "os"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu_score(
        self, ctx: commands.Context, beatmap: Union[int, str], *, user: Optional[str] = None
    ):
        """Get your or another users scores for a specified map."""

        await self.osu_score_command(ctx, beatmap, user)
