import re
import time
from math import ceil
from typing import Dict, List, Optional, Tuple, Union

import discord
from ossapi import Beatmap
from ossapi import Build as OsuBuild
from ossapi import GameMode
from ossapi import Mod as OsuMod
from ossapi import Rankings, RankingType
from ossapi.models import NewsPost, RankStatus, UpdateStream
from redbot.core import commands
from redbot.core.utils.chat_formatting import humanize_number
from redbot.core.utils.menus import menu

from .abc import MixinMeta
from .database import DatabaseLeaderboard
from .utilities import EMOJI, FAVICON, OsuUrls, del_message
from .utils.classes import CommandArgs, CommandParams, DoubleArgs, SingleArgs


class Embeds(MixinMeta):
    """Embed builders."""

    async def beatmap_embed(self, ctx: commands.Context, data: Beatmap) -> List[discord.Embed]:
        data_set = data.beatmapset()

        pretty_mode = self.prettify_mode(data.mode)

        if data.mode == GameMode.MANIA:
            max_combo = "{:.2%}".format(
                data.count_sliders / (data.count_sliders + data.count_circles)
            )
            max_combo_text = "LN Ratio"
            stats_one = f"Notes: `{humanize_number(data.count_circles)}` | Long Notes: `{humanize_number(data.count_sliders)}`"
            stats_two = f"OD: `{data.accuracy}` | HP: `{data.drain}`"
            version = re.sub(r"^\S*\s", "", data.version)
        else:
            max_combo = humanize_number(data.max_combo)
            max_combo_text = "Max Combo"
            stats_one = f"Circles: `{humanize_number(data.count_circles)}` | Sliders: `{humanize_number(data.count_sliders)}` | Spinners: `{humanize_number(data.count_spinners)}`"
            stats_two = (
                f"CS: `{data.cs}` | AR: `{data.ar}` | OD: `{data.accuracy}` | HP: `{data.drain}`"
            )
            version = data.version

        drain_time = time.gmtime(data.hit_length)
        if drain_time[3] > 0:
            drain_time = time.strftime("%-H:%M:%S", drain_time)
        else:
            drain_time = time.strftime("%-M:%S", drain_time)

        length = time.gmtime(data.total_length)
        if length[3] > 0:
            length = time.strftime("%-H:%M:%S", length)
        else:
            length = time.strftime("%-M:%S", length)

        download = f"[Link]({OsuUrls.BEATMAP_DOWNLOAD.value}{data_set.id})"
        if data_set.video:
            download += f" ([No Video]({OsuUrls.BEATMAP_DOWNLOAD.value}{data_set.id}n))"

        embed_list = []
        embed = discord.Embed(
            color=await self.bot.get_embed_color(ctx),
            title=f"{data_set.artist} - {data_set.title} [{version}]",
            url=data.url,
        )

        creator = await data_set.user()

        embed.set_author(
            name=f"Mapped by {data_set.creator} | osu!{pretty_mode.capitalize()}",
            url=f"{OsuUrls.USER.value}{data_set.user_id}",
            icon_url=creator.avatar.url,
        )

        embed.set_footer(text=f"Status: {data.status.name.capitalize()}")

        embed.set_image(url=data_set.covers.cover)

        embed.add_field(
            name="Stats",
            value=f"SR: `{data.difficulty_rating}★` | {stats_two}\n"
            f"{stats_one} | Total: `{data.count_circles + data.count_sliders + data.count_spinners}`",
            inline=False,
        )
        embed.add_field(name="Length / Drain", value=f"{length} / {drain_time}", inline=True)
        embed.add_field(name=EMOJI["BPM"], value=data.bpm, inline=True)
        embed.add_field(name=max_combo_text, value=max_combo, inline=True)
        embed.add_field(name="Playcount", value=humanize_number(data.playcount), inline=True)
        embed.add_field(
            name="Favorites", value=humanize_number(data_set.favourite_count), inline=True
        )
        embed.add_field(name="Download", value=download, inline=True)
        if not data_set.ratings is None and not sum(data_set.ratings) == 0:
            rating = 0
            p = 0
            s = 0
            star_emojis = ""

            for i in data_set.ratings:
                rating = rating + p * i
                p += 1
            final_rating = int(rating / sum(data_set.ratings))

            while s < final_rating:
                star_emojis = star_emojis + ":star:"
                s += 1
            embed.add_field(
                name="Rating",
                value=f"{star_emojis} {round(rating / sum(data_set.ratings), 1)} / 10",
                inline=False,
            )
        embed.add_field(
            name="Submitted",
            value=f"<t:{int(data_set.submitted_date.timestamp())}:R>",
            inline=True,
        )
        embed.add_field(
            name="Last Update", value=f"<t:{int(data.last_updated.timestamp())}:R>", inline=True
        )
        embed.add_field(
            name="Source", value=data_set.source if data_set.source != "" else "None", inline=True
        )
        if data_set.tags:
            embed.add_field(
                name="Tags", value=f'`{data_set.tags.replace(" ", "` `")}`', inline=False
            )

        if data.status.value == 1:
            status = "Ranked on"
            embed.timestamp = data_set.ranked_date
        elif data.status.value == 4:
            status = "Loved on"
            embed.timestamp = data_set.ranked_date
        elif data.status.value == -1:
            status = data.status.name.upper()
        else:
            status = data.status.name.capitalize()

        embed.set_footer(text=f"Status: {status}")

        embed_list.append(embed)

        return embed_list

    async def news_embed(self, ctx: commands.Context, data: List[NewsPost]) -> List[discord.Embed]:
        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        for i in range(len(data)):
            post_image = data[i].first_image
            if post_image.startswith("/"):
                post_image = f"{OsuUrls.MAIN.value}{post_image}"

            embed = base_embed.copy()
            embed.set_image(url=post_image)
            embed.set_author(name=data[i].author, icon_url=FAVICON)
            embed.url = f"{OsuUrls.NEWS.value}{data[i].slug}"
            embed.timestamp = data[i].published_at
            embed.title = data[i].title
            embed.description = data[i].preview
            embed.set_footer(text=f"Post {i + 1}/{len(data)}")

            embed_list.append(embed)

        return embed_list

    async def changelog_embed(
        self, ctx: commands.Context, data: List[OsuBuild], data_stream: UpdateStream
    ) -> List[discord.Embed]:
        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        activeusers = ""
        if not data_stream.name == "lazer" and not data_stream.name == "web":
            activeusers = f" ◈ Active users on branch: {humanize_number(data_stream.user_count)}"

        base_embed.set_author(
            name=f"Changelog | {data_stream.display_name}{activeusers}",
            icon_url=FAVICON,
        )

        page_num = 1
        for build in data:
            embed = base_embed.copy()

            embed.title = build.display_version

            categories: Dict[str, List[Dict[str, Union[bool, str]]]] = {}

            if build.changelog_entries is None:
                continue

            for entry in build.changelog_entries:
                pr_link = ""
                developer = ""

                if entry.github_pull_request_id:
                    pr_link = f' ([{entry.repository.replace("ppy/","")}#{entry.github_pull_request_id}]({entry.github_url}))'

                if entry.github_user.user_url:
                    developer = (
                        f" [{entry.github_user.display_name}]({entry.github_user.user_url})"
                    )
                elif entry.github_user.github_url:
                    developer = (
                        f" [{entry.github_user.display_name}]({entry.github_user.github_url})"
                    )

                if entry.category not in categories:
                    categories[entry.category] = []

                categories[entry.category].append(
                    {
                        "major": entry.major,
                        "full": f"{entry.title}{pr_link}{developer}",
                        "short": f"{entry.title}{developer}",
                        "mini": entry.title,
                    }
                )

            def entry_builder(
                embed: discord.Embed,
                entry: Dict[str, List[Dict[str, Union[bool, str]]]],
                index: int,
            ) -> discord.Embed:
                title_types = ["full", "short", "mini"]
                for _ in range(index):
                    title_types.pop(0)

                for category, items in entry.items():
                    entries = ""
                    for key in title_types:
                        entries = ""
                        for item in items:
                            major_string = "**" if item["major"] else ""
                            entries += f"{major_string}◈ {item[key]}{major_string}\n"
                        if len(entries) < 1024:
                            break
                        entries = (
                            f"◈ Too big for embed. {len(items)} changes to {category}. "
                            f"[Read on the site]({OsuUrls.CHANGELOG.value}{build.update_stream.name}/{build.version})"
                        )
                    embed.add_field(name=category, value=entries, inline=False)

                return embed

            def embed_content_counter(embed: discord.Embed) -> int:
                fields = [embed.title, embed.description, embed.footer.text, embed.author.name]

                fields.extend([field.name for field in embed.fields])
                fields.extend([field.value for field in embed.fields])

                full_string = ""
                for item in fields:
                    full_string += str(item) if str(item) != "Embed.Empty" else ""

                return len(full_string)

            embed = entry_builder(embed, categories, 0)
            count = embed_content_counter(embed)

            if count >= 6000:
                embed.clear_fields()

                embed = entry_builder(embed, categories, 1)
                count = embed_content_counter(embed)

                if count >= 6000:
                    embed.clear_fields()

                    embed = entry_builder(embed, categories, 2)
                    count = embed_content_counter(embed)

                    if count >= 6000:
                        embed.clear_fields()
                        embed.description = f"Too big to display in discord. [Read on the site]({OsuUrls.CHANGELOG.value}{build.update_stream.name}/{build.version})"

            embed.timestamp = build.created_at

            embed.set_footer(text=f"Page {page_num}/{len(data)}")

            embed_list.append(embed)
            page_num += 1

        return embed_list

    async def rankings_embed(
        self, ctx: commands.Context, data: Rankings, arguments: CommandArgs
    ) -> List[discord.Embed]:  # Country support
        pretty_mode = self.prettify_mode(arguments.mode)

        variant = ""
        if arguments.variant:
            variant = f"{arguments.variant} "

        pretty_type = arguments.type.value

        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        if arguments.country:
            pretty_type = data.ranking[0].user.country.name.capitalize()
            base_embed.set_thumbnail(url=f"{OsuUrls.FLAG.value}{arguments.country.upper()}.png")

        base_embed.set_author(
            name=f"{pretty_type.capitalize()} {variant}ranking | osu!{pretty_mode}",
            icon_url=FAVICON,
        )

        page_num = 1

        def shorten_number(number: int, decimals: int = 2):
            suffixes = ["", "K", "M", "B", "T", "Qa", "Qu", "S"]
            suffix_expressions = [1e0, 1e3, 1e6, 1e9, 1e12, 1e15, 1e18, 1e21]
            for i in range(len(suffix_expressions)):
                if number >= suffix_expressions[i] and number < suffix_expressions[i + 1]:
                    suffix = suffixes[i]
                    if suffix:
                        number_split = humanize_number(number).split(",", 1)
                        return (
                            number_split[0]
                            + "."
                            + str(number)[len(number_split[0]) :][:decimals]
                            + suffix
                        )
                    return str(number)

        while page_num <= len(data.ranking) / 10:
            i = (page_num - 1) * 10
            entries = []
            while i < (page_num * 10):
                if arguments.country:
                    entries.append(
                        f"**{i+1}.** | "
                        f"**{data.ranking[i].user.username}** ◈ "
                        f"{humanize_number(data.ranking[i].pp)}pp ◈ "
                        f"{round(data.ranking[i].hit_accuracy,2)}% ◈ "
                        f"{humanize_number(data.ranking[i].play_count)}"
                    )
                elif arguments.type == RankingType.SCORE:
                    entries.append(
                        f"**{i+1}.** | "
                        f":flag_{data.ranking[i].user.country_code.lower()}: "
                        f"**{data.ranking[i].user.username}** ◈ "
                        f"{humanize_number(data.ranking[i].ranked_score)} ◈ "
                        f"{round(data.ranking[i].hit_accuracy,2)}% ◈ "
                        f"{humanize_number(data.ranking[i].pp)}pp"
                    )
                elif arguments.type == RankingType.COUNTRY:
                    entries.append(
                        f"**{i+1}.** | "
                        f":flag_{data.ranking[i].code.lower()}: "
                        f"**{data.ranking[i].country.name}** ◈ "
                        f"{shorten_number(data.ranking[i].performance)}/{humanize_number(round(data.ranking[i].performance / data.ranking[i].active_users))} ◈ "
                        f"{shorten_number(data.ranking[i].ranked_score)}/{shorten_number(round(data.ranking[i].ranked_score / data.ranking[i].active_users))} ◈ "
                        f"{humanize_number(data.ranking[i].active_users)}"
                    )
                else:
                    entries.append(
                        f"**{i+1}.** | "
                        f":flag_{data.ranking[i].user.country_code.lower()}: "
                        f"**{data.ranking[i].user.username}** ◈ "
                        f"{humanize_number(data.ranking[i].pp)}pp ◈ "
                        f"{round(data.ranking[i].hit_accuracy,2)}% ◈ "
                        f"{humanize_number(data.ranking[i].play_count)}"
                    )
                i += 1

            embed = base_embed.copy()

            description = ""
            i = 0
            for entry in entries:
                if i == 0:
                    i += 1
                else:
                    description += "\n\n"
                description += entry

            embed.description = description

            if arguments.type == RankingType.SCORE:
                index_string = "Username ◈ Score ◈ Accuracy ◈ PP"
            elif arguments.type == RankingType.COUNTRY:
                index_string = "Country ◈ PP/Avg ◈ Score/Avg ◈ Active Users ◈ Play Count"
            else:
                index_string = "Username ◈ PP ◈ Accuracy ◈ Play Count"

            embed.set_footer(
                text=f"Page {page_num}/{int(len(data.ranking) / 10)} | {index_string}"
            )

            embed_list.append(embed)
            page_num += 1

        return embed_list

    async def leaderboard_embed(
        self,
        ctx: commands.Context,
        data: DatabaseLeaderboard,
        arguments: CommandParams,
        user_id: Optional[int],
    ) -> Tuple[Optional[List[discord.Embed]], int]:
        version = data.version
        if arguments.mode == GameMode.MANIA:
            version = re.sub(r"^\S*\s", "", data.version)

        pretty_mode = self.prettify_mode(arguments.mode)

        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        base_embed.set_author(
            name=f"Unranked leaderboard ◈ {data.artist} - {data.title} [{version}]",
            url=f"{OsuUrls.BEATMAP.value}{data.id}",
            icon_url=FAVICON,
        )

        score_strings = []
        index = 1
        page_start = 0
        if arguments.g:
            guild_users = []
            all_config = await self.osu_config.all_users()
            for user, user_data in all_config.items():
                if ctx.guild.get_member(user):
                    guild_users.append(user_data["user_id"])

        for score in data.leaderboard.values():
            if arguments.g:
                if not score.id in guild_users:  # type: ignore
                    continue

            if score.id == user_id and arguments.me:
                page_start = ceil(index / 5) - 1

            extra = ""
            if score.id == user_id:
                extra = "**"

            mods = ""
            if score.mods != OsuMod.NM:
                mods = f" +{score.mods.short_name()}"

            if arguments.mode == GameMode.MANIA:
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
                hits = "/".join(
                    [
                        f"{humanize_number(score.statistics.count_300)}",
                        f"{humanize_number(score.statistics.count_100)}",
                        f"{humanize_number(score.statistics.count_50)}",
                        f"{humanize_number(score.statistics.count_miss)}",
                    ]
                )

            score_strings.append(
                "\n".join(
                    [
                        f"**{index}.** "
                        f"{extra}{humanize_number(score.score)}{extra} ◈ "
                        f"{extra}{score.username}{extra} ◈ "
                        f":flag_{score.country_code.lower()}: ◈ "
                        f"<t:{int(score.created_at.timestamp())}:R>",
                        f'{"{:.2%}".format(score.accuracy)} ◈ '
                        f"{humanize_number(score.max_combo)}x ◈ "
                        f"{hits} ◈ "
                        f"{EMOJI[score.rank.value]}{mods}",
                    ]
                )
            )
            index += 1

        if (index - 1) == 0:
            return None, 0

        page_num = 1
        while page_num <= ceil(len(score_strings) / 5):
            embed = base_embed.copy()

            start_index = (page_num - 1) * 5
            end_index = start_index + 5

            embed.description = "\n\n".join(score_strings[start_index:end_index])

            embed.set_footer(
                text=(
                    f"Page {page_num}/{ceil(len(score_strings) / 5)} ◈ "
                    f"{index - 1} submitted score{'s' if index - 1 > 1 else ''} ◈ "
                    f"osu!{pretty_mode}"
                )
            )

            embed_list.append(embed)
            page_num += 1

        return embed_list, page_start


class Commands(Embeds):
    """Command logic."""

    async def beatmap_command(self, ctx: commands.Context, beatmap: str) -> None:
        map_id = self.beatmap_converter(beatmap)

        if map_id is None:
            return await del_message(ctx, f"That doesn't seem to be a valid map.")

        data = await self.api.beatmap(map_id)

        if not data:
            return await del_message(ctx, "I can't find the map specified.")

        embeds = await self.beatmap_embed(ctx, data)
        await menu(ctx, embeds)

    async def osu_news_command(self, ctx: commands.Context) -> None:
        data = await self.api.news_listing()

        if data:
            embeds = await self.news_embed(ctx, data.news_posts)
            await menu(ctx, embeds)

    async def osu_changelog_command(self, ctx: commands.Context, release_stream: str) -> None:
        release_stream = release_stream.lower()

        if release_stream == "stable":
            stream = "stable40"
        elif release_stream == "beta":
            stream = "beta40"
        elif (
            release_stream == "cuttingedge" or release_stream == "lazer" or release_stream == "web"
        ):
            stream = release_stream
        else:
            return await del_message(ctx, f"Please provide a valid release stream.")

        data = await self.api.changelog_listing(stream=stream)

        data_stream = data.streams[0]

        for s in data.streams:
            if s.name == stream:
                data_stream = s

        if data:
            embeds = await self.changelog_embed(ctx, data.builds, data_stream)
            await menu(ctx, embeds)

    async def osu_rankings_command(self, ctx: commands.Context, arg_input: tuple) -> None:
        arguments = await self.argument_extractor(ctx, arg_input)

        if not arguments:
            return

        data = await self.api.ranking(
            arguments.mode, arguments.type, country=arguments.country, variant=arguments.variant
        )

        embeds = await self.rankings_embed(ctx, data, arguments)
        await menu(ctx, embeds)

    async def osu_leaderboard_command(
        self, ctx: commands.Context, beatmap_or_args: Optional[tuple]
    ):
        if beatmap_or_args is None:
            return await ctx.send_help()

        arguments = await self.user_and_parameter_extractor(
            ctx,
            beatmap_or_args,
            single_args=[SingleArgs.GUILD, SingleArgs.ME],
            double_args=[DoubleArgs.MODE],
            skip_user=True,
        )

        if arguments.extra_param is None:
            return await ctx.send_help()

        map_id = self.beatmap_converter(arguments.extra_param)

        if map_id is None:
            return await del_message(ctx, "No valid beatmap was provided.")

        map_data = await self.api.beatmap(map_id)

        if map_data is None:
            return await del_message(ctx, "I can't find the given beatmap.")

        if (
            map_data.beatmapset().status == RankStatus.RANKED
            or map_data.beatmapset().status == RankStatus.LOVED
        ):
            return await del_message(
                ctx, "Leaderboards aren't available for ranked and loved maps."
            )

        if arguments.mode is None:
            arguments.mode = map_data.mode

        leaderboard_data = await self.get_unranked_leaderboard(map_id, arguments.mode)

        if leaderboard_data is None or len(leaderboard_data.leaderboard) == 0:
            return await del_message(
                ctx, "Nobody has set any plays on this map yet. Go ahead and be the first one!"
            )

        user_id = await self.osu_config.user(ctx.author).user_id()

        embeds, page_start = await self.leaderboard_embed(
            ctx, leaderboard_data, arguments, user_id
        )

        if embeds is None:
            return await del_message(
                "Nobody in this server with linked accounts have set scores on that map."
            )

        await menu(ctx, embeds, page=page_start)


class Misc(Commands):
    "Miscellaneous osu! commands."

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def beatmap(self, ctx: commands.Context, beatmap: str):
        """Get info about a osu! map."""

        await self.beatmap_command(ctx, beatmap)

    @commands.command(name="osunews")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu_news(self, ctx: commands.Context):
        """Shows the news from the osu! front page."""

        await self.osu_news_command(ctx)

    @commands.command(name="osuchangelog", aliases=["osucl"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu_changelog(self, ctx: commands.Context, release_stream: str = "stable"):
        """Gets the changelog for different parts of osu!.

        Supported Release Streams:
        `stable`
        `beta`
        `cuttingedge`
        `lazer`
        `web`
        """

        await self.osu_changelog_command(ctx, release_stream)

    @commands.command(name="osurankings", aliases=["osur"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu_rankings(self, ctx: commands.Context, *arguments):
        """Show the top players or countries from each leaderboard.

        Examples:
            - `[p]osurankings catch SE`
            - `[p]osur mania 4k`

        **Arguments:**

        - `<mode>` one of the 4 gamemodes. Defaults to standard.
        - `<type>` One of `pp`, `score` or `country`. Defaults to pp.
        - `<country>` A 2 character ISO country code to get that countries leaderboard. Does not work with `<type>`.
        - `<variant>` Either 4k or 7k when `<mode>` is mania. Leave blank for global. Does not work with `<type>`.
        """

        await self.osu_rankings_command(ctx, arguments)

    @commands.command(name="osuleaderboard", aliases=["osl", "osul"], usage="[beatmap] [args]")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def osu_leaderboard(self, ctx: commands.Context, *beatmap_or_args):
        """Unranked leaderboards for osu! maps.

        To submit scores, use the recent commands after having set scores on a map.
        You need to have your account linked for it to be submitted. Link yours with `[p]osulink`.
        Only works for maps that are not ranked or loved.

        **Arguments:**
        `-mode <mode>` Choose the mode to display. Only needed for converts.
        `-me` Starts the embed at the page your score is if your account is linked.
        `-g` Show only scores by users in this guild that have linked accounts.
        """

        await self.osu_leaderboard_command(ctx, beatmap_or_args)
