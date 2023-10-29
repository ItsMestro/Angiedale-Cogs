import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Dict, List, Optional, Tuple, Union

import discord
from ossapi import GameMode
from ossapi import Mod as OsuMod
from ossapi import Score as OsuScore
from ossapi import User as OsuUser
from ossapi import UserLookupKey
from ossapi.models import Grade as OsuGrade
from redbot.core import commands
from redbot.core.utils.chat_formatting import (
    bold,
    box,
    humanize_number,
    humanize_timedelta,
    inline,
)
from redbot.core.utils.menus import menu, start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
from redbot.core.utils.views import ConfirmView

from .abc import MixinMeta
from .converters import BeatMode, BeatModeConverter, TimeConverter
from .utilities import EMOJI, OsuUrls, del_message
from .utils.classes import _GAMEMODES, Osubeat, OsubeatMap, OsubeatScore, ValueFound
from .utils.custommenu import chapter_menu

OSUBEAT_ALLOWED_MODS = [
    OsuMod("NM"),
    OsuMod("NF"),
    OsuMod("EZ"),
    OsuMod("HD"),
    OsuMod("HR"),
    OsuMod("DT"),
    OsuMod("HT"),
    OsuMod("NC"),
    OsuMod("FL"),
    OsuMod("SO"),
    OsuMod("FI"),
    OsuMod("MR"),
]
OSUBEAT_MODS_STANDARD = [OsuMod("NM"), OsuMod("HD"), OsuMod("HR"), OsuMod(["HD", "HR"])]
OSUBEAT_MODS_TAIKO = [OsuMod("NM"), OsuMod("HD"), OsuMod("HR"), OsuMod(["HD", "HR"])]
OSUBEAT_MODS_CATCH = [OsuMod("NM"), OsuMod("HD"), OsuMod("HR"), OsuMod(["HD", "HR"])]
OSUBEAT_MODS_MANIA = [
    OsuMod("NM"),
    OsuMod("HD"),
    OsuMod("FI"),
    OsuMod("FL"),
    OsuMod("MR"),
    OsuMod(["HD", "MR"]),
    OsuMod(["FI", "MR"]),
    OsuMod(["FL", "MR"]),
]


class Embeds(MixinMeta):
    """Embed builders."""

    async def osubeat_announce_embed(
        self,
        ctx: commands.Context,
        data: Osubeat,
        beat_mode: BeatMode,
    ) -> discord.Embed:
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_author(
            name=f"osu!{self.prettify_mode(data.mode).capitalize()} Beat Competition!",
            icon_url=ctx.guild.icon.url
            if ctx.guild.icon is not None
            else ctx.bot.user.display_avatar.url,
        )

        embed.title = f"{data.beatmap.beatmapset.artist} - {data.beatmap.beatmapset.title} [{data.beatmap.version}]"
        embed.url = data.beatmap.url
        embed.set_image(url=data.beatmap.beatmapset.cover)

        embed.description = "\n".join(
            [
                f"{bold('New beat competition!')}",
                "Test your skill against other players in this server by playing the map linked above.",
            ]
        )

        if len(data.mods) > 0:
            mod_strings = []
            for mod_combo in data.mods:
                mod_strings.append(mod_combo.long_name().replace(" ", " + "))

            embed.add_field(
                name="Allowed Mods",
                value="\n".join(mod_strings),
                inline=False,
            )

        if beat_mode == BeatMode.NORMAL:
            mode_string = ""
        elif beat_mode == BeatMode.TUNNELVISION:
            mode_string = "\n".join(
                [
                    f"\n\nThis beat is using Tunnelvision!",
                    f"Scores will be hidden when running `{ctx.clean_prefix}osubeat standings` but placements are shown.\n\n",
                ]
            )
        elif beat_mode == BeatMode.SECRET:
            mode_string = "\n".join(
                [
                    f"\n\nThis beat is using Secret!",
                    f"You won't be able to see others scores while the competition is running.\n\n",
                ]
            )

        embed.add_field(
            name="How to participate",
            value="\n".join(
                [
                    f"◈ Sign up to this beat with `{ctx.clean_prefix}osubeat join`.",
                    f"◈ Set scores on the map linked above.",
                    f"◈ Use `{ctx.clean_prefix}recent<mode>` to submit your score. (Doesn't have to be in this server and even works in my DMs!)",
                    f"◈ Have the best score out of everyone in this server by the end of the competition."
                    f"{mode_string}",
                ]
            ),
            inline=False,
        )

        embed.add_field(
            name="Competition ends",
            value="\n\n".join(
                [
                    f"<t:{int(data.ends.timestamp())}:D> ◈ <t:{int(data.ends.timestamp())}:R>",
                    f"You can check the current standings with {inline(ctx.clean_prefix + 'osubeat standings')}",
                ]
            ),
            inline=False,
        )

        embed.set_footer(text=f"Beatmap competition hosted by {ctx.author.display_name}")

        embed.timestamp = datetime.now(timezone.utc).replace(second=0, microsecond=0)

        return embed

    async def osubeat_winner_embed(
        self,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        osubeat: Osubeat,
        scores: Dict[int, OsubeatScore],
    ) -> discord.Embed:
        embed = discord.Embed(color=await self.bot.get_embed_color(channel))

        embed.set_author(
            name=f"osu!{self.prettify_mode(GameMode(osubeat.mode)).capitalize()} Beat Competition has finished! Here's the results!",
            icon_url=channel.guild.icon.url
            if channel.guild.icon is not None
            else self.bot.user.display_avatar.url,
        )

        embed.title = f"{osubeat.beatmap.beatmapset.artist} - {osubeat.beatmap.beatmapset.title} [{osubeat.beatmap.version}]"
        embed.url = osubeat.beatmap.url
        embed.set_image(url=osubeat.beatmap.beatmapset.cover)

        if len(scores) == 0:
            embed.description = "Nobody competed during this beat and therefor no winner could be decided. Maybe next time."
            return embed

        emotes = [":first_place:", ":second_place:", ":third_place:"]

        index = 0
        for member_id, score in scores.items():
            member_mention_string = ""
            member = channel.guild.get_member(member_id)
            if member:
                member_mention_string = f"{member.mention} ◈ "

            if osubeat.mode == GameMode.MANIA:
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

            mods = ""
            if not score.mods.NM:
                mods = f" +{score.mods.short_name()}"

            embed.add_field(
                name=f"{emotes[index]} {humanize_number(score.score)}",
                value="\n".join(
                    [
                        f"{member_mention_string}"
                        f"[{score.user.username}]({OsuUrls.USER.value}{score.user.id}) ◈ "
                        f":flag_{score.user.country_code.lower()}: ◈ "
                        f"<t:{int(score.created_at.timestamp())}:R>",
                        f'{"{:.2%}".format(score.accuracy)} ◈ '
                        f"{humanize_number(score.max_combo)}x ◈ "
                        f"{hits} ◈ "
                        f"{EMOJI[score.rank.value]}{mods}",
                    ]
                ),
                inline=False,
            )

            index += 1

        embed.set_footer(text=f"Competitors: {index} ◈ Ended")

        embed.timestamp = datetime.now(timezone.utc).replace(second=0, microsecond=0)

        return embed

    async def osubeat_standings_embed(
        self,
        ctx: commands.Context,
        data: List[Dict[str, Union[discord.Guild, Osubeat, BeatMode, Dict[int, OsubeatScore]]]],
        page: int = 0,
        previous: bool = False,
    ) -> List[discord.Embed]:
        guild: discord.Guild = data[page]["guild"]
        osubeat: Osubeat = data[page]["beat_data"]
        scores: Dict[int, OsubeatScore] = data[page]["members"]
        beat_mode: BeatMode = data[page]["beat_mode"]

        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        # These two if it's a active beat
        beat_string = "Current beat standings"
        noplayerstring = "\n".join(
            [
                "Nobody has set any scores on this beats map. Be the first one!",
                f"Submit scores by using `{ctx.clean_prefix}recent<mode>` after signing up to the beat with `{ctx.clean_prefix}osubeat join`",
            ]
        )
        if previous:  # Otherwise this for old ones
            beat_string = "Previous beats results"
            noplayerstring = "Nobody set any scores last beat. Maybe next time."

        base_embed.set_author(
            name=f"{beat_string} ◈ {osubeat.beatmap.beatmapset.artist} - {osubeat.beatmap.beatmapset.title} [{osubeat.beatmap.version}]",
            url=osubeat.beatmap.url,
            icon_url=guild.icon.url if guild.icon is not None else ctx.bot.user.display_avatar.url,
        )

        if (
            not ctx.guild
        ):  # If message is from a DM. Try to get some kind of reference to the server/channel/message
            if osubeat.channel_id is not None:
                channel = guild.get_channel_or_thread(osubeat.channel_id)
                if channel is not None:
                    message: discord.Message = await channel.fetch_message(osubeat.message_id)
                    if message is not None:
                        base_embed.title = message.jump_url
                    else:
                        base_embed.title = channel.mention
                else:
                    base_embed.title = guild.name

        embed_list: List[discord.Embed] = []  # Output

        score_strings = []  # description items
        index = 0
        for member_id, score in scores.items():
            if score.score == 0:  # We reached end of leaderboard
                break

            if (
                beat_mode == BeatMode.SECRET
            ):  # Don't bother doing anything unless it's the author for SECRET
                if member_id != ctx.author.id:
                    index += 1
                    continue

            bold_highlight = ""
            if member_id == ctx.author.id:  # We bold highlight if it's the author author
                bold_highlight = "**"

            mods = ""
            if not score.mods.NM:  # Add mods if it wasn't played with NoMod
                mods = f" +{score.mods.short_name()}"

            if osubeat.mode == GameMode.MANIA:  # Our hit string that differs for mania
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

            index_string = f"**{index + 1}.** "

            if beat_mode == BeatMode.TUNNELVISION and member_id != ctx.author.id:
                score_strings.append(
                    f"{index_string}◈ "
                    f"{bold_highlight}{score.user.username}{bold_highlight} ◈ "
                    f":flag_{score.user.country_code.lower()}: ◈ "
                    f"<t:{int(score.created_at.timestamp())}:R>"
                )
            else:
                score_strings.append(
                    "\n".join(
                        [
                            f"{index_string if beat_mode != BeatMode.SECRET else ''}{bold_highlight}{humanize_number(score.score)}{bold_highlight} ◈ "
                            f"{bold_highlight}{score.user.username}{bold_highlight} ◈ "
                            f":flag_{score.user.country_code.lower()}: ◈ "
                            f"<t:{int(score.created_at.timestamp())}:R>",
                            f"{'{:.2%}'.format(score.accuracy)} ◈ "
                            f"{humanize_number(score.max_combo)}x ◈ "
                            f"{hits} ◈ "
                            f"{EMOJI[score.rank.value]}{mods}",
                        ]
                    )
                )

            index += 1

        if index == 0:
            embed = base_embed.copy()
            embed.description = noplayerstring
            embed.set_footer(text=f"Ended")
            embed.timestamp = osubeat.ends
            embed_list.append(embed)
            return embed_list

        if beat_mode == BeatMode.SECRET:
            embed = base_embed.copy()
            score_prefix = [
                f"This beat is using mode {inline(BeatMode.SECRET.name.capitalize())} "
                "so you're only able to see your own score."
            ]
            if len(score_strings) > 0:
                score_prefix.append(score_strings[0])
                embed.description = "\n\n".join(score_prefix)
            else:
                score_prefix.append(
                    f"You have yet to submit any scores to this beat. Do so with {inline(ctx.clean_prefix + 'recent<mode>')}."
                )
                embed.description = "\n\n".join(score_prefix)
            embed.set_footer(
                text=f'{index} submitted score{"s" if index > 1 else ""} ◈ '
                f'Ends{" in" if osubeat.ends < timedelta(days=1) else ""}'
            )
            embed.timestamp = osubeat.ends

            embed_list.append(embed)
            return embed_list

        page_num = 1
        while page_num <= ceil(len(score_strings) / 5):
            embed = base_embed.copy()

            start_index = (page_num - 1) * 5
            end_index = (page_num - 1) * 5 + 5

            embed.description = "\n\n".join(score_strings[start_index:end_index])

            if beat_mode == BeatMode.TUNNELVISION:
                embed.description = "\n\n".join(
                    [
                        f"This beat is using mode {inline(BeatMode.TUNNELVISION.name.capitalize())} "
                        "so you're only able to see the standings and your own score.",
                        embed.description,
                    ]
                )

            embed.set_footer(
                text=f"Page {page_num}/{ceil(len(score_strings) / 5)} ◈ "
                f'{index} submitted score{"s" if index > 1 else ""} ◈ '
                f'Ends{" in" if osubeat.ends < timedelta(days=1) else ""}'
            )

            embed.timestamp = osubeat.ends

            embed_list.append(embed)
            page_num += 1

        return embed_list

    async def osubeat_cancel_embed(self, ctx: commands.Context, osubeat: Osubeat) -> discord.Embed:
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name="Beat competition cancelled manually. No winners will be picked.",
            icon_url=ctx.guild.icon.url
            if ctx.guild.icon is not None
            else ctx.bot.user.display_avatar.url,
        )

        embed.title = f"{osubeat.beatmap.beatmapset.artist} - {osubeat.beatmap.beatmapset.title} [{osubeat.beatmap.version}]"
        embed.url = osubeat.beatmap.url
        embed.set_image(url=osubeat.beatmap.beatmapset.cover)

        embed.set_footer(text="Competition was cancelled")
        embed.timestamp = datetime.now(timezone.utc).replace(second=0, microsecond=0)

        return embed

    async def osubeat_signup_embed(self, ctx: commands.Context, data: OsuUser) -> discord.Embed:
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f"You'll be signing up as this user. Are you sure?",
            icon_url=f"{OsuUrls.FLAG.value}{data.country_code}.png",
        )
        embed.set_thumbnail(url=data.avatar_url)
        embed.title = data.username
        embed.url = f"{OsuUrls.USER.value}{data.id}"

        return embed


class Functions(Embeds):
    """Utility functions."""

    async def check_osu_beat(self):
        """Checks if any beat competitions should end."""

        await self.bot.wait_until_red_ready()

        while True:
            if not self.osubeat_maps:
                break

            osubeat_list = deepcopy(self.osubeat_maps)

            for map_id, g_ids in osubeat_list.items():
                for g_id, data in g_ids.items():
                    if data["ends"] <= datetime.now(timezone.utc):
                        await self.end_osubeat(g_id, map_id)
                        await asyncio.sleep(1)
                    await asyncio.sleep(1)

            await asyncio.sleep(50)

    def add_guild_to_osubeat(
        self,
        guild: discord.Guild,
        osubeat: Osubeat,
    ) -> None:
        try:
            self.osubeat_maps[osubeat.beatmap.id]
        except KeyError:
            self.osubeat_maps[osubeat.beatmap.id] = {}

        self.osubeat_maps[osubeat.beatmap.id][guild.id] = {
            "ends": osubeat.ends,
            "created_at": osubeat.created_at,
            "mods": osubeat.mods,
            "mode": osubeat.mode,
        }
        if self.osubeat_task.done():
            self.osubeat_task: asyncio.Task = asyncio.create_task(self.check_osu_beat())

    async def end_osubeat(self, guild_id: int, map_id: int) -> None:
        """Handles ending beat competitions and sends results."""

        if not map_id in self.osubeat_maps:
            return

        if not guild_id in self.osubeat_maps[map_id]:
            return

        del self.osubeat_maps[map_id][guild_id]

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await self.osu_config.guild_from_id(guild_id).clear()

        await self.osu_config.guild(guild).running_beat.set(False)
        beat_current = Osubeat(await self.osu_config.guild(guild).beat_current())

        channel = guild.get_channel_or_thread(beat_current.channel_id)
        if channel is None:  # Missing channel so silently end
            beat_current.channel_id = None
            beat_current.message_id = None
            beat_current.pinned = False
            await self.osu_config.guild(guild).beat_current.clear()
            return await self.osu_config.guild(guild).beat_last.set(beat_current.to_dict())

        participants = await self.osu_config.all_members(guild)

        leaderboard = dict(
            sorted(
                participants.items(),
                key=lambda item: item[1]["beat_score"]["score"],
                reverse=True,
            )
        )

        scores: Dict[int, OsubeatScore] = {}
        for member_id, data in leaderboard.items():
            if data["beat_score"]["score"] == 0:
                break
            scores[member_id] = OsubeatScore(data["beat_score"])

        embed = await self.osubeat_winner_embed(channel, beat_current, scores)

        if beat_current.pinned:
            try:
                msg = channel.get_partial_message(beat_current.message_id)
                if msg:
                    await msg.unpin(reason="Cleanup after osubeat ended")
            except (discord.Forbidden, discord.NotFound):
                pass
            beat_current.pinned = False

        try:
            msg = await channel.send(embed=embed)
            beat_current.message_id = msg.id
        except (discord.Forbidden, discord.NotFound):
            beat_current.channel_id = None
            beat_current.message_id = None

        beat_current.ends = datetime.now(timezone.utc).replace(second=0)
        await self.osu_config.guild(guild).beat_current.clear()
        await self.osu_config.guild(guild).beat_last.set(beat_current.to_dict())

        # Remove map from dict if this was last guild entry
        if len(self.osubeat_maps[map_id]) == 0:
            del self.osubeat_maps[map_id]

    async def cancel_osubeat(self, ctx: commands.Context, guild_id: int, map_id: int) -> None:
        """End a beat and announce it's cancellation."""

        if not map_id in self.osubeat_maps:
            return

        if not guild_id in self.osubeat_maps[map_id]:
            return

        del self.osubeat_maps[map_id][guild_id]

        await self.osu_config.guild(ctx.guild).running_beat.set(False)
        beat_current = Osubeat(await self.osu_config.guild(ctx.guild).beat_current())

        channel = ctx.guild.get_channel_or_thread(beat_current.channel_id)
        if channel is None:  # Missing channel so silently end
            beat_current.channel_id = None
            beat_current.message_id = None
            beat_current.pinned = False
            return await self.osu_config.guild(ctx.guild).beat_current.clear()

        embed = await self.osubeat_cancel_embed(ctx, beat_current)

        if beat_current.pinned:
            try:
                msg = channel.get_partial_message(beat_current.message_id)
                if msg:
                    await msg.unpin(reason="Cleanup after osubeat ended")
            except (discord.Forbidden, discord.NotFound):
                pass
            beat_current.pinned = False

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.NotFound):
            pass

        await self.osu_config.guild(ctx.guild).beat_current.clear()

        # Remove map from dict if this was last guild entry
        if len(self.osubeat_maps[map_id]) == 0:
            del self.osubeat_maps[map_id]

    async def queue_osubeat_check(self, ctx: commands.Context, data: List[OsuScore]) -> None:
        """Adds a list of scores to be checked against osubeat, to the queue."""

        task = asyncio.create_task(self.check_score_for_osubeat(ctx, data))
        self.osubeat_check_tasks.add(task)
        task.add_done_callback(self.osubeat_check_tasks.discard)

    async def check_score_for_osubeat(self, ctx: commands.Context, data: List[OsuScore]) -> None:
        """Finds plays that fit beat criteria and adds to leaderboard."""

        for score in data:
            # No beat for the scores map
            if not score.beatmap.id in self.osubeat_maps.keys():
                continue

            # Don't count fails.
            if score.rank == OsuGrade.F:
                continue

            for guild_id, beat_data in self.osubeat_maps[score.beatmap.id].items():
                if score.mode != beat_data["mode"]:
                    continue

                if len(beat_data["mods"]) > 0:  # len() == 0 is "FM". Not "NM"
                    if not score.mods in beat_data["mods"]:
                        continue
                else:  # This is a solution to handle beats that run with "FM"
                    split_mods = score.mods.decompose()
                    try:
                        for mod in split_mods:
                            if not OsuMod(mod) in OSUBEAT_ALLOWED_MODS:
                                raise ValueFound
                    except ValueFound:
                        continue

                beat_score = OsubeatScore(
                    await self.osu_config.member_from_ids(guild_id, ctx.author.id).beat_score()
                )

                if beat_score.score is None:  # User hasn't signed up for this beat
                    continue

                if score.user().id != beat_score.user.id:
                    continue

                if beat_score.score >= score.score:
                    continue

                if beat_data["created_at"] > score.created_at:
                    continue

                await self.osu_config.member_from_ids(guild_id, ctx.author.id).beat_score.set(
                    OsubeatScore(score).to_dict()
                )


class Commands(Functions):
    """Command logic."""

    async def set_beat_time_command(
        self, ctx: commands.Context, time: Optional[timedelta]
    ) -> None:
        if time:
            if time > timedelta(weeks=4):
                return await del_message(ctx, "You can't set a time longer than 4 weeks.")
            await self.osu_config.guild(ctx.guild).default_beat_time.set(time.total_seconds())
            return await ctx.send(f"Beats will now last for {humanize_timedelta(timedelta=time)}.")

        await self.osu_config.guild(ctx.guild).default_beat_time.clear()
        await ctx.send("Default time for beats reset to 1 day.")

    async def new_beat_command(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        beatmap: str,
        mode: str,
        mods: Tuple[str],
    ):
        beat_data: dict = await self.osu_config.guild(ctx.guild).all()

        if beat_data["running_beat"]:
            return await del_message(
                ctx,
                "\n\n".join(
                    [
                        f"There is already a beat competition running in this server.",
                        f"Either wait for it to end first or end it manually with `{ctx.clean_prefix}osubeat end`.",
                        f"If you don't want a winner picked you can cancel it with `{ctx.clean_prefix}osubeat cancel`.",
                    ]
                ),
                timeout=20,
            )

        if (
            isinstance(channel, discord.Thread)
            and not channel.permissions_for(ctx.guild.me).send_messages_in_threads
        ):
            return await ctx.send(f"I'm not allowed to send messages in {channel.mention}")
        elif not channel.permissions_for(ctx.guild.me).send_messages:
            return await ctx.send(f"I'm not allowed to send messages in {channel.mention}")

        osubeat = Osubeat()

        for key, mode_values in _GAMEMODES.items():
            if mode in mode_values:
                osubeat.mode = GameMode(key)

        if osubeat.mode is None:
            return await del_message(ctx, f"{mode} is not a valid gamemode.")

        if not mods:
            return await del_message(
                ctx,
                "\n\n".join(
                    [
                        f"Please specify what mods should be allowed.",
                        f"To allow all mods use `FM`",
                        f"Valid mods can be found with `{ctx.clean_prefix}osubeat mods`",
                    ]
                ),
                timeout=20,
            )

        free_mod = False
        for mod in mods:
            if mod.upper() == "FM":
                free_mod = True
            elif mod.upper() == "TOURNAMENT" or mod.upper() == "TOURNEY":
                if osubeat.mode == GameMode.OSU:
                    osubeat.mods = OSUBEAT_MODS_STANDARD
                elif osubeat.mode == GameMode.TAIKO:
                    osubeat.mods = OSUBEAT_MODS_TAIKO
                elif osubeat.mode == GameMode.CATCH:
                    osubeat.mods = OSUBEAT_MODS_CATCH
                elif osubeat.mode == GameMode.MANIA:
                    osubeat.mods = OSUBEAT_MODS_MANIA
                break
            else:
                try:
                    new_mod = OsuMod(mod)
                    osubeat.mods.append(new_mod)
                except ValueError:
                    return await del_message(
                        ctx,
                        (
                            f"Your list of mods is invalid. Make sure to only use "
                            f"mods/combinations from the ones listed with `{ctx.clean_prefix}osubeat mods"
                        ),
                    )

        if free_mod and len(osubeat.mods) > 0:
            return await del_message(ctx, "Nice try but FM can't be combined with other mods.")

        map_id = self.beatmap_converter(beatmap)

        if map_id is None:
            return await del_message(ctx, f"{beatmap} isn't a valid map url/id.")

        map_data = await self.api.beatmap(map_id)

        if not map_data:
            return await del_message(ctx, "I can't find the map specified.")

        if osubeat.mode != map_data.mode and osubeat.mode != GameMode.OSU:
            return await del_message(
                ctx,
                f"{self.prettify_mode(osubeat.mode).capitalize()} can't be used with {self.prettify_mode(map_data.mode).capitalize()} maps.",
            )

        osubeat.beatmap = OsubeatMap(map_data)

        osubeat.created_at = datetime.now(timezone.utc)
        osubeat.ends = (
            osubeat.created_at + timedelta(seconds=beat_data["default_beat_time"])
        ).replace(second=0, microsecond=0)

        embed = await self.osubeat_announce_embed(
            ctx,
            osubeat,
            BeatMode(beat_data["beat_mode"]),
        )

        async def predicate(
            text: str,
        ) -> Tuple[bool, Optional[Union[ConfirmView, discord.Message]]]:
            if await self.bot.use_buttons():
                view = ConfirmView(ctx.author, timeout=30)
                view.message = await ctx.send(text, view=view)
                await view.wait()
                if not view.result:
                    return False, view
                await view.message.delete()
            else:
                can_react = ctx.channel.permissions_for(ctx.me).add_reactions
                msg = await ctx.send(text)
                if can_react:
                    start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                    pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                    event = "reaction_add"
                else:
                    pred = MessagePredicate.yes_or_no(ctx)
                    event = "message"
                try:
                    await ctx.bot.wait_for(event, check=pred, timeout=30)
                except asyncio.TimeoutError:
                    pass
                if not pred.result:
                    await msg.clear_reactions()
                    return False, msg
                await msg.delete()
            return True, None

        embed_msg = await ctx.send(embed=embed)
        msg_text = "\n\n".join(
            [
                f"This is a preview of how the embed will look when sent in {channel.mention}",
                "Are you sure you wish to start this beat?",
            ]
        )

        result, view_or_msg = await predicate(msg_text)
        await embed_msg.delete()
        if not result:
            if view_or_msg is not None:
                text = "Cancelled beat competition creation."
                if isinstance(view_or_msg, ConfirmView):
                    await view_or_msg.message.edit(content=text)
                else:
                    await view_or_msg.edit(content=text)
                return

        if channel.permissions_for(ctx.me).manage_messages:
            msg_text = "\n".join(
                [
                    "Would you like for me to pin the message in that channel as well?",
                    f"I'll automatically un-pin it again when the beat ends as long "
                    "as I have {inline('manage_messages')} permission.",
                ]
            )
            result, view_or_msg = await predicate(msg_text)

            if result:
                osubeat.pinned = True

        osubeat.channel_id = channel.id

        osubeat_message = await channel.send(embed=embed)
        if osubeat.pinned:
            await osubeat_message.pin(reason="Osubeat announcement pinning")

        osubeat.message_id = osubeat_message.id

        await self.osu_config.guild(ctx.guild).beat_current.set(osubeat.to_dict())

        await self.osu_config.clear_all_members(ctx.guild)

        await self.osu_config.guild(ctx.guild).running_beat.set(True)

        self.add_guild_to_osubeat(ctx.guild, osubeat)

    async def join_beat_command(self, ctx: commands.Context, user: str) -> None:
        if not await self.osu_config.guild(ctx.guild).running_beat():
            return await del_message(
                ctx,
                "There's currently no running beat competition in the server. Maybe encourage the server owner to start one!",
            )

        if await self.osu_config.member(ctx.author).beat_score():
            return await del_message(ctx, "You're already signed up for this beat. Go play!")

        user_id = await self.user_id_extractor(ctx, user)

        if user_id is None:
            return

        data = await self.api.user(user_id, key=UserLookupKey.ID)

        if data is None:
            return await del_message(ctx, f"I can't seem to find {user}'s profile.")

        signups = await self.osu_config.all_members(ctx.guild)
        for user_data in signups.values():
            if user_data["beat_score"]["user"]["id"] == data.id:
                return await del_message(
                    ctx, f'{data["username"]} is already signed up for this beat.'
                )

        embed = await self.osubeat_signup_embed(ctx, data)

        if await self.bot.use_buttons():
            view = ConfirmView(ctx.author, timeout=30)
            view.message = await ctx.send(embed=embed, view=view)
            await view.wait()
            if not view.result:
                return await view.message.edit("Cancelled beat signup.", embed=None)
            await view.message.delete()
        else:
            can_react = ctx.channel.permissions_for(ctx.me).add_reactions
            embed_msg = await ctx.send(embed=embed)
            if can_react:
                start_adding_reactions(embed_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(embed_msg, ctx.author)
                event = "reaction_add"
            else:
                pred = MessagePredicate.yes_or_no(ctx)
                event = "message"
            try:
                await ctx.bot.wait_for(event, check=pred, timeout=30)
            except asyncio.TimeoutError:
                pass
            if not pred.result:
                return await embed_msg.edit("Cancelled beat signup.", embed=None)
            await embed_msg.delete()

        async with self.osu_config.member(ctx.author).beat_score() as osubeat:
            osubeat["score"] = 0
            osubeat["user"] = {}
            osubeat["user"]["id"] = data.id

        return await ctx.send(
            f"Now signed up as {data.username}. Start playing the map and submit your scores with `{ctx.clean_prefix}recent<mode>`."
        )

    async def standings_beat_command(self, ctx: commands.Context) -> None:
        if not ctx.guild:
            all_beat_data = await self.osu_config.all_guilds()

            filtered_beats: List[
                Dict[str, Union[discord.Guild, Osubeat, Dict[int, OsubeatScore], BeatMode]]
            ] = []
            for g_id, data in all_beat_data.items():
                if not data["running_beat"]:  # Guild isn't running a beat
                    continue

                guild = self.bot.get_guild(g_id)
                if guild is None:  # Guild is missing
                    continue

                if not guild.get_member(ctx.author.id):  # User not in guild
                    continue

                member_data = await self.osu_config.all_members(guild)

                leaderboard = dict(
                    sorted(
                        member_data.items(),
                        key=lambda item: item[1]["beat_score"]["score"],
                        reverse=True,
                    )
                )

                scores: Dict[int, OsubeatScore] = {}
                for member_id, score_data in leaderboard.items():
                    if score_data["beat_score"]["score"] == 0:
                        break
                    scores[member_id] = OsubeatScore(score_data["beat_score"])

                filtered_beats.append(
                    {
                        "guild": guild,
                        "beat_data": Osubeat(data["beat_current"]),
                        "members": scores,
                        "beat_mode": BeatMode(data["beat_mode"]),
                    }
                )

            if len(filtered_beats) == 0:
                return await ctx.send("There's no active beats in any of the servers you're in.")
            elif len(filtered_beats) == 1:
                return await menu(ctx, await self.osubeat_standings_embed(ctx, filtered_beats))
            else:
                return await chapter_menu(
                    ctx,
                    data=filtered_beats,
                    funct=self.osubeat_standings_embed,
                )

        beat_data = await self.osu_config.guild(ctx.guild).all()
        member_data = await self.osu_config.all_members(ctx.guild)

        leaderboard = dict(
            sorted(
                member_data.items(),
                key=lambda item: item[1]["beat_score"]["score"],
                reverse=True,
            )
        )

        scores: Dict[int, OsubeatScore] = {}
        for member_id, data in leaderboard.items():
            if data["beat_score"]["score"] == 0:
                break
            scores[member_id] = OsubeatScore(data["beat_score"])

        payload: Dict[str, Union[discord.Guild, Osubeat, BeatMode, Dict[int, OsubeatScore]]] = {
            "guild": ctx.guild,
            "members": scores,
            "beat_mode": BeatMode(beat_data["beat_mode"]),
        }
        if beat_data["running_beat"]:
            payload["beat_data"] = Osubeat(beat_data["beat_current"])
            embeds = await self.osubeat_standings_embed(
                ctx,
                [payload],
            )
            if payload["beat_mode"] != BeatMode.NORMAL:
                return await ctx.send(
                    f"The beat in this guild is running with the mode {inline(payload['beat_mode'].name.capitalize())} "
                    "so use the command in DMs with me to get the leaderboard!"
                )
        elif beat_data["beat_last"]["beatmap"]:
            payload["beat_data"] = Osubeat(beat_data["beat_last"])
            embeds = await self.osubeat_standings_embed(ctx, [payload], previous=True)
        else:
            return await del_message(
                ctx,
                "There hasn't been any beats ran in this server yet. Maybe ask the server owner to host one?",
            )

        await menu(ctx, embeds)

    async def mode_beat_command(self, ctx: commands.Context, beatmode: BeatModeConverter) -> None:
        if await self.osu_config.guild(ctx.guild).running_beat():
            return await del_message(
                ctx, "The beat mode can't be changed while a beat is running."
            )

        await self.osu_config.guild(ctx.guild).beat_mode.set(beatmode.value)

        if beatmode == BeatMode.NORMAL:
            return await ctx.send("Set the mode for future osubeats to `Normal`")
        if beatmode == BeatMode.TUNNELVISION:
            return await ctx.send("Set the mode for future osubeats to `Tunnelvision`")
        if beatmode == BeatMode.SECRET:
            return await ctx.send("Set the mode for future osubeats to `Secret`")

    async def mods_beat_command(self, ctx: commands.Context) -> None:
        section_one = []
        section_one.append("Acronym: Name")
        section_one.append("FM: FreeMod")
        longest = 0
        for mod in OSUBEAT_ALLOWED_MODS:
            text = f"{mod.short_name()}: {mod.long_name()}"
            if len(text) > longest:
                longest = len(text)
            section_one.append(text)

        lines = ""
        for i in range(longest):
            lines += "-"
        section_one.insert(1, lines)

        section_two = []
        section_two.append("Tournament: Mod Combinations")
        longest = 0

        def make_string(strings: List[str], mods: List[OsuMod]) -> str:
            nonlocal longest
            for mod in mods:
                strings.append(mod.short_name())

            text = " ".join(strings)
            if len(text) > longest:
                longest = len(text)

            return text

        section_two.append(make_string(["STANDARD:"], OSUBEAT_MODS_STANDARD))
        section_two.append(make_string(["TAIKO:"], OSUBEAT_MODS_TAIKO))
        section_two.append(make_string(["CATCH:"], OSUBEAT_MODS_CATCH))
        section_two.append(make_string(["MANIA:"], OSUBEAT_MODS_MANIA))

        lines = ""
        for i in range(longest):
            lines += "-"
        section_two.insert(1, lines)

        section_one = "\n".join(section_one)
        section_two = "\n".join(section_two)

        output = "\n\n".join([section_one, section_two])

        await ctx.send(box(output, "apache"))

    async def end_beat_command(self, ctx: commands.Context) -> None:
        if not await self.osu_config.guild(ctx.guild).running_beat():
            return await del_message(
                ctx,
                f"There is currently no beat running in this server. Start one with `{ctx.clean_prefix}osubeat new`.",
            )

        beat_current = await self.osu_config.guild(ctx.guild).beat_current()

        msg_text = "\n\n".join(
            [
                "This will immediately end the currently running beat in "
                f'{inline(beat_current["mode"])} on '
                f'{inline(beat_current["beatmap"]["beatmapset"]["title"])}',
                "Are you sure?",
            ]
        )

        if await self.bot.use_buttons():
            view = ConfirmView(ctx.author, timeout=30)
            view.message = await ctx.send(msg_text, view=view)
            await view.wait()
            if not view.result:
                return await view.message.edit("Cancelled beat competition creation.")
            await view.message.delete()
        else:
            can_react = ctx.channel.permissions_for(ctx.me).add_reactions
            msg = await ctx.send(msg_text)
            if can_react:
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                event = "reaction_add"
            else:
                pred = MessagePredicate.yes_or_no(ctx)
                event = "message"
            try:
                await ctx.bot.wait_for(event, check=pred, timeout=30)
            except asyncio.TimeoutError:
                pass
            if not pred.result:
                await msg.clear_reactions()
                return await msg.edit("Cancelled beat competition creation.")
            await msg.delete()

        await self.end_osubeat(ctx.guild.id, beat_current["beatmap"]["id"])

    async def cancel_beat_command(self, ctx: commands.Context) -> None:
        if not await self.osu_config.guild(ctx.guild).running_beat():
            return await del_message(
                ctx,
                f"There is currently no beat running in this server. Start one with `{ctx.clean_prefix}osubeat new`.",
            )

        beat_current = await self.osu_config.guild(ctx.guild).beat_current()

        msg_text = "\n\n".join(
            [
                "This will cancel the currently running beat in "
                f'{inline(beat_current["mode"])} on '
                f'{inline(beat_current["beatmap"]["beatmapset"]["title"])} '
                "without picking a winner.",
                "Are you sure?",
            ]
        )

        if await self.bot.use_buttons():
            view = ConfirmView(ctx.author, timeout=30)
            view.message = await ctx.send(msg_text, view=view)
            await view.wait()
            if not view.result:
                return await view.message.edit("Cancelled beat competition creation.")
            await view.message.delete()
        else:
            can_react = ctx.channel.permissions_for(ctx.me).add_reactions
            msg = await ctx.send(msg_text)
            if can_react:
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                event = "reaction_add"
            else:
                pred = MessagePredicate.yes_or_no(ctx)
                event = "message"
            try:
                await ctx.bot.wait_for(event, check=pred, timeout=30)
            except asyncio.TimeoutError:
                pass
            if not pred.result:
                await msg.clear_reactions()
                return await msg.edit("Cancelled beat competition creation.")
            await msg.delete()

        await self.cancel_osubeat(ctx, ctx.guild.id, beat_current["beatmap"]["id"])


class OsuBeat(Commands):
    "osu! competitions for guilds."

    @commands.group(name="osubeat", aliases=["osub", "osb"])
    async def osubeat(self, ctx: commands.Context):
        """osu! competitions run per server."""

    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    @osubeat.command(name="settime")
    async def _set_beat_time(self, ctx: commands.Context, *, time: TimeConverter = None):
        """Set the time that all future beats last.

        Can be a maximum of 4 weeks.

        Examples:
            - `[p]osubeat settime 1 week 2 days`
            - `[p]osubeat settime 3d20hr`
        """

        await self.set_beat_time_command(ctx, time)

    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    @osubeat.command(name="new")
    async def _new_beat(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        beatmap: str,
        mode: str,
        *mods,
    ):
        """Run a new beat competition in this server.

        Users will sign up through the bot and submit scores with `[p]recent<mode>`
        The maps can be unranked and you're able to limit what mods are allowed

        The beat will last for 1 day by default and can be changed with `[p]osubeat settime`
        There's 3 different beat modes the beat can be ran in. Find out more and pick which one with `[p]osubeat mode`

        Examples:
            - `[p]osubeat new #osu 2929654 mania FM` - Run a mania beat on 2929654 with any mod allowed that is announced in #osu.
            - `[p]osubeat new #osu 378131 osu DTHD DT` - Run a standard beat on 378131 where DTHD or DT is allowed and is announced in #osu.
            - `[p]osubeat new #osubeat 2807630 mania tournament` - Run a mania beat on 2807630 using the typical set of mods allowed in tournaments (FreeMod category) for that mode.
        """

        await self.new_beat_command(ctx, channel, beatmap, mode, mods)

    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    @osubeat.command(name="mode")
    async def _mode_beat(self, ctx: commands.Context, beatmode: BeatModeConverter):
        """Set the beat mode to be used for competitions.

        The mode can be one of:
        `Normal` - Standings are fully shown like normal.
        `Tunnelvision` - Other players scores will be hidden but standings will still show.
        `Secret` - Standings won't be revealed until the end of the beat.
        """

        await self.mode_beat_command(ctx, beatmode)

    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    @osubeat.command(name="mods", hidden=True)
    async def _mods_beat(self, ctx: commands.Context):
        """Displays what mods can be used for beat competitions."""

        await self.mods_beat_command(ctx)

    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    @osubeat.command(name="end")
    async def _end_beat(self, ctx: commands.Context):
        """Manually end a beat early."""

        await self.end_beat_command(ctx)

    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    @osubeat.command(name="cancel")
    async def _cancel_beat(self, ctx: commands.Context):
        """Cancel a running beat without selecting winners."""

        await self.cancel_beat_command(ctx)

    @commands.guild_only()
    @osubeat.command(name="join")
    async def _join_beat(self, ctx: commands.Context, user: str):
        """Join a beat competition."""

        await self.join_beat_command(ctx, user)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @osubeat.command(name="standings", aliases=["leaderboard", "results"])
    async def _standings_beat(self, ctx: commands.Context):
        """Check the current standings in the beat competition."""

        await self.standings_beat_command(ctx)
