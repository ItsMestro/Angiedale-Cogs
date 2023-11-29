import logging
import re
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from math import ceil
from typing import Dict, List, Optional, Union

import discord
from ossapi import Beatmap, GameMode
from ossapi import Mod as OsuMod
from ossapi import Score as OsuScore
from ossapi import ScoreType, Statistics
from ossapi.models import Grade as OsuGrade
from redbot.core import commands
from redbot.core.utils.angiedale import inkopolis, inkopolis_invite
from redbot.core.utils.chat_formatting import bold, box, humanize_number, inline
from redbot.core.utils.menus import menu

from .abc import MixinMeta
from .utilities import EMOJI, OsuUrls, del_message

log = logging.getLogger("red.angiedale.osu")

FUWWY_ALLOWED_MODS = [
    OsuMod("NM"),
    OsuMod("NF"),
    OsuMod("HD"),
    OsuMod("DT"),
    OsuMod("NC"),
    OsuMod("FL"),
    OsuMod("FI"),
    OsuMod("MR"),
]


class FuwwyBeatmapIDs(Enum):
    JACKS = 2847726
    STREAMS = 2847727
    LN = 2847728
    TECH = 4363239
    FULL = 2741027


FUWWY_BEATMAP_STAGES = {
    FuwwyBeatmapIDs.FULL: {"name": "Nuzzles", "stage": "True Furry"},
    FuwwyBeatmapIDs.JACKS: {"name": "Fluff", "stage": "Stage 1"},
    FuwwyBeatmapIDs.STREAMS: {"name": "Maws", "stage": "Stage 2"},
    FuwwyBeatmapIDs.LN: {"name": "Paws", "stage": "Stage 3"},
    FuwwyBeatmapIDs.TECH: {"name": "Beans", "stage": "Stage 4"},
}

FUWWY_ACC_THRESHOLD = 0.98
FUWWY_GRADE_THRESHOLD = OsuGrade.A
FUWWY_GRADES = [OsuGrade.A, OsuGrade.S, OsuGrade.SH, OsuGrade.SS, OsuGrade.SSH]


def inkopolis_server_check():
    async def pred(ctx: commands.Context):
        if not ctx.guild:
            return False
        if ctx.guild.id == inkopolis:
            return True
        else:
            return False

    return commands.check(pred)


class FuwwyScore:
    """A simplified `ossapi.Score` for storing in `Config`."""

    def __init__(self, data: Union[OsuScore, dict]):
        self.score: int = None
        self.accuracy: float = None
        self.max_combo: int = None
        self.rank: OsuGrade = None
        self.created_at: datetime = None
        self.mods: OsuMod = None
        self.map_version: datetime = None
        self.statistics: Statistics = None

        if isinstance(data, OsuScore):
            self.score = data.score
            self.accuracy = data.accuracy
            self.max_combo = data.max_combo
            self.rank = data.rank
            self.created_at = data.created_at
            self.mods = data.mods
            self.map_version = data.beatmap.last_updated
            self.statistics = data.statistics
        elif data:
            self.score = data["score"]
            self.accuracy = data["accuracy"]
            self.max_combo = data["max_combo"]
            self.rank = OsuGrade(data["rank"])
            self.created_at = datetime.strptime(data["created_at"], "%Y-%m-%dT%H:%M:%S%z")
            self.mods = OsuMod(data["mods"])
            self.map_version = datetime.strptime(data["map_version"], "%Y-%m-%dT%H:%M:%S%z")

            self.statistics = Statistics()
            self.statistics.count_geki = data["statistics"]["count_geki"]
            self.statistics.count_katu = data["statistics"]["count_katu"]
            self.statistics.count_300 = data["statistics"]["count_300"]
            self.statistics.count_100 = data["statistics"]["count_100"]
            self.statistics.count_50 = data["statistics"]["count_50"]
            self.statistics.count_miss = data["statistics"]["count_miss"]

    def to_dict(self):
        output = {}

        output["score"] = self.score
        output["accuracy"] = self.accuracy
        output["max_combo"] = self.max_combo
        output["rank"] = self.rank.value
        output["created_at"] = self.created_at.strftime("%Y-%m-%dT%H:%M:%S%z")
        output["mods"] = self.mods.short_name()
        output["map_version"] = self.map_version.strftime("%Y-%m-%dT%H:%M:%S%z")

        output["statistics"] = {}
        output["statistics"]["count_geki"] = self.statistics.count_geki
        output["statistics"]["count_katu"] = self.statistics.count_katu
        output["statistics"]["count_300"] = self.statistics.count_300
        output["statistics"]["count_100"] = self.statistics.count_100
        output["statistics"]["count_50"] = self.statistics.count_50
        output["statistics"]["count_miss"] = self.statistics.count_miss

        return output


class FuwwyUser:
    def __init__(self, data: Dict[Union[str, FuwwyBeatmapIDs], Union[bool, str, dict]]):
        self.member: bool = data["member"]
        self.join_date: Optional[datetime] = None
        if data["join_date"] is not None:
            self.join_date: datetime = datetime.strptime(data["join_date"], "%Y-%m-%dT%H:%M:%S%z")

        self.FULL = None
        self.JACKS = None
        self.STREAMS = None
        self.LN = None
        self.TECH = None

        if data[FuwwyBeatmapIDs.FULL.name]:
            self.FULL = FuwwyScore(data[FuwwyBeatmapIDs.FULL.name])
        if data[FuwwyBeatmapIDs.JACKS.name]:
            self.JACKS = FuwwyScore(data[FuwwyBeatmapIDs.JACKS.name])
        if data[FuwwyBeatmapIDs.STREAMS.name]:
            self.STREAMS = FuwwyScore(data[FuwwyBeatmapIDs.STREAMS.name])
        if data[FuwwyBeatmapIDs.LN.name]:
            self.LN = FuwwyScore(data[FuwwyBeatmapIDs.LN.name])
        if data[FuwwyBeatmapIDs.TECH.name]:
            self.TECH = FuwwyScore(data[FuwwyBeatmapIDs.TECH.name])

    def get_stage(self, stage: FuwwyBeatmapIDs) -> Optional[FuwwyScore]:
        return getattr(self, stage.name)

    def get_stages(self, stages: List[FuwwyBeatmapIDs]) -> Dict[FuwwyBeatmapIDs, FuwwyScore]:
        output: Dict[FuwwyBeatmapIDs, Optional[FuwwyScore]] = {}

        for stage in stages:
            data = self.get_stage(stage)
            if data is not None:
                output[stage] = data

        return output


def fuwwy_to_stage_string(fuwwy_type: FuwwyBeatmapIDs) -> str:
    """Just creates a pretty stage looking string."""
    key = FUWWY_BEATMAP_STAGES[fuwwy_type]
    if fuwwy_type == FuwwyBeatmapIDs.FULL:
        "Fluff (Jacks) - Stage 1"
        return f"{key['name']} [{key['stage']}]"
    else:
        return f"{key['name']} [{fuwwy_type.name if len(fuwwy_type.name) == 2 else fuwwy_type.name.capitalize()}] - {key['stage']}"


class Embeds(MixinMeta):
    """Embed builders."""

    async def fuwwy_profile_embed(
        self,
        ctx: commands.Context,
        data: Dict[FuwwyBeatmapIDs, FuwwyScore],
        osu_user_id: int,
        join_date: datetime = None,
        new_member: bool = False,
    ) -> discord.Embed:
        embed = discord.Embed(
            color=await self.bot.get_embed_color(ctx),
        )

        description = []

        all_users = await self.osu_config.all_users()
        member_count = 0
        for user_data in all_users.values():
            if user_data["fuwwy_clan"]["member"]:
                member_count += 1

        if new_member:
            embed.title = f"Welcome to the FUWWY clan {ctx.author.display_name}"
            embed.set_author(
                name=f"You are member number #{member_count}",
                icon_url=ctx.bot.user.display_avatar.url,
            )
        else:
            embed.title = f"[FUWWY]{ctx.author.display_name}"
            embed.set_author(
                name=f"You're 1 of {member_count} degenerates that played this exam!",
                icon_url=ctx.bot.user.display_avatar.url,
            )
            description.append(
                f"You've been a member since <t:{int(join_date.timestamp())}:D> <t:{int(join_date.timestamp())}:R>"
            )

        embed.url = f"{OsuUrls.USER.value}{osu_user_id}"

        embed.set_thumbnail(url=ctx.author.display_avatar.url)

        description.append(
            "\n".join(
                [
                    "This is your current line-up of scores.",
                    "Keep submitting more to fill the list and improve on the leaderboard!",
                ]
            )
        )
        embed.description = "\n\n".join(description)

        temp_dict = deepcopy(data)
        worst_stage = {"stage": None, "accuracy": 100}
        for fuwwy_map, score in data.items():
            if score.score is None:
                embed.add_field(
                    name=fuwwy_to_stage_string(fuwwy_map),
                    value="Not submitted yet.",
                    inline=False,
                )
                temp_dict.pop(fuwwy_map, None)
                continue
            else:
                embed.add_field(
                    name=fuwwy_to_stage_string(fuwwy_map),
                    value=self.fuwwy_score_entry_builder(score),
                    inline=False,
                )
                if fuwwy_map == FuwwyBeatmapIDs.FULL:
                    continue
                if worst_stage["stage"] is None or worst_stage["accuracy"] > score.accuracy:
                    worst_stage = {"stage": fuwwy_map, "accuracy": score.accuracy}

        temp_dict.pop(FuwwyBeatmapIDs.FULL, None)
        if len(temp_dict) >= 3:
            if len(temp_dict) == 4:
                temp_dict.pop(worst_stage["stage"], None)

            total_accuracy = 0
            for score in temp_dict.values():
                total_accuracy += score.accuracy
            embed.set_footer(
                text=f"The average accuracy on your best {len(temp_dict)} stage maps is {'{:.2%}'.format(total_accuracy / len(temp_dict))}"
            )

        return embed

    def fuwwy_score_entry_builder(self, score: FuwwyScore) -> str:
        mods = ""
        if score.mods != OsuMod.NM:
            mods = f" {bold('+' + score.mods.short_name())}"

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

        return "\n".join(
            [
                f"{bold(humanize_number(score.score))} ◈ ({'{:.2%}'.format(score.accuracy)}) ◈ [{hits}]",
                f"{EMOJI[score.rank.value]}{mods} ◈ {humanize_number(score.max_combo)}x ◈ <t:{int(score.created_at.timestamp())}:R>",
            ]
        )

    async def requirements_embed(self, ctx: commands.Context) -> discord.Embed:
        embed = discord.Embed(
            color=await self.bot.get_embed_color(ctx),
        )
        home_guild = self.bot.get_guild(inkopolis)
        embed.set_author(
            name=f"{ctx.bot.user.display_name}'s home server invite!",
            url=inkopolis_invite,
            icon_url=home_guild.icon.url,
        )
        embed.title = "[FUWWY] Clan Entry Requirements"

        embed.description = "\n\n".join(
            [
                "First of all. This clan is just a meme. Don't expect anything out of it.",
                "\n".join(
                    [
                        f"To submit scores you'll have to be in {ctx.bot.user.display_name}'s home server.",
                        f"In there you can submit scores using {inline(ctx.clean_prefix + 'fuwwyclan submit')}",
                        "Once you become a member you're able to submit improved scores anywhere the bot is.",
                    ]
                ),
                f"You'll have to set some scores on maps in this [FUWWY Clan Entrance Exam]({OsuUrls.BEATMAP.value}{FuwwyBeatmapIDs.FULL.value})",
            ]
        )
        embed.add_field(
            name="You have two options for becoming eligible as a member.",
            value="\n".join(
                [
                    f"- Get an {bold(FUWWY_GRADE_THRESHOLD.value)} on the full "
                    f"{inline(FUWWY_BEATMAP_STAGES[FuwwyBeatmapIDs.FULL]['name'])} ",
                    f"- {bold('Get an average accuracy')} of {bold('{:.2%}'.format(FUWWY_ACC_THRESHOLD))} on three of the four stage maps.",
                ]
            ),
            inline=False,
        )

        mods = []
        mods.append("Acronym: Name")

        line_string = ""
        for _ in range(len(mods[0])):
            line_string += "-"
        mods.append(line_string)

        for mod in FUWWY_ALLOWED_MODS:
            mods.append(f"{mod.short_name()}: {mod.long_name()}")

        mods = "\n".join(mods)

        embed.add_field(
            name="You're allowed to use any combination of these mods when playing.",
            value=box(mods, "apache"),
            inline=False,
        )

        embed.add_field(
            name="If one of the maps get updated with a mapping change.",
            value="You'll be given a grace period of 2 weeks "
            "where you have to re-submit your score before having your saved scores deleted "
            "and be removed from the clan.",
        )

        return embed

    async def improvement_embed(
        self,
        ctx: commands.Context,
        osu_user_id: int,
        fuwwy_map: FuwwyBeatmapIDs,
        join_date: datetime,
        new_score: FuwwyScore,
        old_score: Optional[FuwwyScore] = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            color=await self.bot.get_embed_color(ctx),
        )
        embed.title = f"[FUWWY]{ctx.author.display_name}"
        embed.url = f"{OsuUrls.USER.value}{osu_user_id}"

        embed.set_thumbnail(url=ctx.author.display_avatar.url)

        improvement_string = "added"
        if old_score is not None:
            embed.add_field(
                name="Old Score", value=self.fuwwy_score_entry_builder(old_score), inline=False
            )
            improvement_string = "improvement"

        embed.add_field(
            name="New Score", value=self.fuwwy_score_entry_builder(new_score), inline=False
        )

        embed.set_author(
            name=f"Score {improvement_string} on {fuwwy_to_stage_string(fuwwy_map)}",
            icon_url=ctx.bot.user.display_avatar.url,
        )

        embed.set_footer(text="You've been a member since")
        embed.timestamp = join_date

        return embed

    async def new_member_scores_embed(
        self,
        ctx: commands.Context,
        fuwwy_data: FuwwyUser,
        osu_user_id: int,
        worst_stage: FuwwyBeatmapIDs,
    ) -> None:
        embed = discord.Embed(
            color=await self.bot.get_embed_color(ctx),
        )
        embed.title = f"{ctx.author.display_name}"
        embed.url = f"{OsuUrls.USER.value}{osu_user_id}"

        embed.set_thumbnail(url=ctx.author.display_avatar.url)

        embed.set_author(
            name=f"Currently submitted scores.",
            icon_url=ctx.bot.user.display_avatar.url,
        )

        submitted_stages = 0
        accuracy = 0

        for stage, score in fuwwy_data.get_stages(
            [
                FuwwyBeatmapIDs.JACKS,
                FuwwyBeatmapIDs.STREAMS,
                FuwwyBeatmapIDs.LN,
                FuwwyBeatmapIDs.JACKS,
            ]
        ).items():
            submitted_stages += 1
            if stage != worst_stage:
                accuracy += score.accuracy
            embed.add_field(
                name=fuwwy_to_stage_string(stage),
                value=self.fuwwy_score_entry_builder(score),
                inline=False,
            )

        text_list = []
        if submitted_stages < 3:
            text_list.append(f"play {bold(str(3 - submitted_stages))} more stages")

        if submitted_stages == 4:
            if accuracy / 3 < FUWWY_ACC_THRESHOLD:
                text_list.append(
                    f"get {bold('{:.2%}'.format(accuracy / 3))} higher average accuracy"
                )
            text_add1 = f"{bold('{:.2%}'.format(accuracy / 3))} with "
            f"{bold('4')} stages submitted."
        else:
            if accuracy / submitted_stages < FUWWY_ACC_THRESHOLD:
                text_list.append(
                    f"get {bold('{:.2%}'.format(accuracy / submitted_stages))} higher average accuracy"
                )
            text_add1 = f"{bold('{:.2%}'.format(accuracy / submitted_stages))} on "
            f"{bold(str(submitted_stages))} stage{'s' if len(submitted_stages) > 1 else ''}."

        text_add2 = " and ".join(text_list)

        embed.description = "\n\n".join(
            [
                f"Your average accuracy on the top 3 stages is currently at {text_add1}",
                f"You'll need to {text_add2} to join the clan.",
            ]
        )

        embed.set_footer(
            f"Entry requirements: Get an "
            f"{FUWWY_GRADE_THRESHOLD.value} on "
            f"{fuwwy_to_stage_string(FuwwyBeatmapIDs.FULL)} "
            f"or {'{:.2%}'.format(FUWWY_ACC_THRESHOLD)} average accuracy on 3 of the 4 stages"
        )

    async def fuwwy_leaderboard_embed(
        self,
        ctx: commands.Context,
        members: List[Dict[str, Union[FuwwyScore, int, str, None]]],
        beatmap_data: Beatmap,
        author_id: int,
    ) -> List[discord.Embed]:
        guild = self.bot.get_guild(inkopolis)

        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        base_embed.set_author(
            name=f"[FUWWY] Clan leaderboard",
            icon_url=ctx.bot.user.display_avatar.url,
        )

        base_embed.set_thumbnail(url=guild.icon.url)

        base_embed.title = re.sub(r"^\S*\s", "", beatmap_data.version)
        base_embed.url = beatmap_data.url

        embed_list: List[discord.Embed] = []

        index = 0
        page_num = 1
        while page_num <= ceil(len(members) / 5):
            start_index = (page_num - 1) * 5
            end_index = start_index + 5
            score_entries = []
            for score in members[start_index:end_index]:
                score_entries.append(
                    "\n".join(
                        [
                            f"{bold(str(index + 1) + '.')} "
                            f"{bold(score['username']) if score['user_id'] == author_id else score['username']} ◈ "
                            f"[Profile]({OsuUrls.USER.value}{score['user_id']})",
                            self.fuwwy_score_entry_builder(score["data"]),
                        ]
                    )
                )

                index += 1

            embed = base_embed.copy()

            embed.set_footer(
                text=f"Page {page_num}/{ceil(len(members) / 5)} ◈ Total Scores: {len(members)}"
            )

            embed.description = "\n\n".join(score_entries)

            embed_list.append(embed)
            page_num += 1

        return embed_list


class Functions(Embeds):
    """Utiility functions."""

    async def submit_score(
        self, ctx: commands.Context, data: OsuScore, stage: FuwwyBeatmapIDs
    ) -> None:
        """Submit a members score."""

        async with self.osu_config.user(ctx.author).fuwwy_clan() as config:
            config[stage.name] = FuwwyScore(data).to_dict()

    async def get_member_scores(
        self, ctx: commands.Context, new_member: bool = False
    ) -> Dict[FuwwyBeatmapIDs, FuwwyScore]:
        data = None
        async with self.osu_config.user(ctx.author).fuwwy_clan() as config:
            if new_member:
                clan_data = await self.osu_config.fuwwy_clan()
                if clan_data["role_id"] is not None:
                    role = ctx.guild.get_role(clan_data["role_id"])
                    if role is not None:
                        try:
                            await ctx.author.add_roles(role, reason="New FUWWY member")
                        except:
                            pass
                config["member"] = True
                config["join_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
            data = {
                FuwwyBeatmapIDs.FULL: FuwwyScore(config[FuwwyBeatmapIDs.FULL.name]),
                FuwwyBeatmapIDs.JACKS: FuwwyScore(config[FuwwyBeatmapIDs.JACKS.name]),
                FuwwyBeatmapIDs.STREAMS: FuwwyScore(config[FuwwyBeatmapIDs.STREAMS.name]),
                FuwwyBeatmapIDs.LN: FuwwyScore(config[FuwwyBeatmapIDs.LN.name]),
                FuwwyBeatmapIDs.TECH: FuwwyScore(config[FuwwyBeatmapIDs.TECH.name]),
            }

        return data


class Commands(Functions, Embeds):
    """Command logic."""

    async def reference_command(self, ctx: commands.Context) -> None:
        clan_data = await self.osu_config.user(ctx.author).fuwwy_clan()

        if not clan_data[FuwwyBeatmapIDs.FULL.name]:
            return await del_message(ctx, "You don't have a score submitted on the full map.")

        reference_score = await self.osu_config.fuwwy_clan()

        if clan_data[FuwwyBeatmapIDs.FULL.name]["score"] <= reference_score["score"]:
            return await del_message(
                ctx, "Your submitted score isn't better than the reference score."
            )

        await self.osu_config.fuwwy_clan.set(
            {
                "score": clan_data[FuwwyBeatmapIDs.FULL.name]["score"],
                "map_version": clan_data[FuwwyBeatmapIDs.FULL.name]["map_version"],
            }
        )

        await ctx.send(
            f'Updated reference score from {reference_score["score"]} to {clan_data[FuwwyBeatmapIDs.FULL.name]["score"]}'
        )

    async def remove_member_command(self, ctx: commands.Context, user: discord.User) -> None:
        clan_data = await self.osu_config.fuwwy_clan()

        if clan_data["role_id"] is None:
            guild = self.bot.get_guild(inkopolis)
            if guild is not None:
                member = guild.get_member(user.id)
                if member is not None:
                    role = guild.get_role(clan_data["role_id"])
                    if role is not None:
                        try:
                            await member.remove_roles(role, reason="Manually removed FUWWY member")
                        except:
                            await ctx.send("Failed to remove FUWWY role.")

        async with self.osu_config.user(user).all() as data:
            data.pop("fuwwy_clan", None)
        await ctx.send("Done!")

    async def submit_command(self, ctx: commands.Context) -> None:
        user_data = await self.osu_config.user(ctx.author).all()

        fuwwy_data = FuwwyUser(user_data["fuwwy_clan"])

        # Needs account linked
        if user_data["user_id"] is None:
            return await self.profile_linking_onboarding(ctx)

        # If not a member already and not in home guild.
        # You can only submit scores as a non-member in the home guild.
        if not fuwwy_data.member:
            if ctx.guild is None or ctx.guild.id != inkopolis:
                guild = self.bot.get_guild(inkopolis)
                return await del_message(
                    ctx,
                    "\n\n".join(
                        [
                            "Until you're a clan member you can only submit scores in the "
                            f"{self.bot.user.name} home guild {inline(guild.name)}",
                            f"Here's an invite to the server {inkopolis_invite}",
                        ]
                    ),
                )

        data = await self.api.user_scores(
            user_data["user_id"], ScoreType.RECENT, limit=1, mode=GameMode.MANIA
        )

        if not data:
            return await del_message(
                ctx, f"Looks like you don't have any recent plays in that mode."
            )

        data = data[0]

        # Check if the beatmap is actually one of the exam maps
        fuwwy_ids = set(item.value for item in FuwwyBeatmapIDs)
        if not data.beatmap.id in fuwwy_ids:
            return await del_message(
                ctx,
                "Your most recent play doesn't seem to be on one of the the exam maps. "
                "Play one of them then try again!",
            )

        # Re-use my logic from osubeat for checking if any mod used isn't in whitelist
        split_mods = data.mods.decompose()
        for mod in split_mods:
            if not OsuMod(mod.short_name()) in FUWWY_ALLOWED_MODS:
                return await del_message(
                    ctx,
                    f"The {inline(OsuMod(mod).long_name())} mod isn't allowed for score submissions!",
                )

        # First if else here is just to differentiate between the full map and stage maps
        if data.beatmap.id == FuwwyBeatmapIDs.FULL.value:
            # Score isn't better than grade requirement
            if data.rank not in FUWWY_GRADES:
                return await del_message(
                    ctx,
                    "\n".join(
                        [
                            f"Your score doesn't have at least an {bold(FUWWY_GRADE_THRESHOLD.value)} rank.",
                            "Try again and see if you can improve!",
                        ]
                    ),
                )

            # If user isn't a member we make them one here and end the chain.
            if not fuwwy_data.member:
                await self.submit_score(ctx, data, FuwwyBeatmapIDs.FULL)
                return await ctx.send(
                    embed=await self.fuwwy_profile_embed(
                        ctx,
                        await self.get_member_scores(ctx, new_member=True),
                        user_data["user_id"],
                        new_member=True,
                    )
                )

            # Since the user is a member we check their old score against the new one
            # and update it. If they don't have one just push the score right away
            if fuwwy_data.FULL is not None:
                # Old score better than new one. End chain.
                if data.score <= fuwwy_data.FULL.score:
                    return await del_message(
                        ctx,
                        f"Your score of {inline(humanize_number(data.score))} isn't better than "
                        f"your last submitted one {inline(humanize_number(fuwwy_data.FULL.score))}.",
                    )

                # Submit score. Send profile embed. Send change embed. End chain
                await self.submit_score(ctx, data, FuwwyBeatmapIDs.FULL)
                return await ctx.send(
                    embed=await self.improvement_embed(
                        ctx,
                        user_data["user_id"],
                        FuwwyBeatmapIDs.FULL,
                        fuwwy_data.join_date,
                        FuwwyScore(data),
                        fuwwy_data.FULL,
                    )
                )

            await self.submit_score(ctx, data, FuwwyBeatmapIDs.FULL)
            return await ctx.send(
                embed=await self.improvement_embed(
                    ctx,
                    user_data["user_id"],
                    FuwwyBeatmapIDs.FULL,
                    fuwwy_data.join_date,
                    FuwwyScore(data),
                )
            )
        else:
            # Extract the map out of the different stages first
            if data.beatmap.id == FuwwyBeatmapIDs.JACKS.value:
                fuwwy_map = FuwwyBeatmapIDs.JACKS
            elif data.beatmap.id == FuwwyBeatmapIDs.STREAMS.value:
                fuwwy_map = FuwwyBeatmapIDs.STREAMS
            elif data.beatmap.id == FuwwyBeatmapIDs.LN.value:
                fuwwy_map = FuwwyBeatmapIDs.LN
            else:
                fuwwy_map = FuwwyBeatmapIDs.TECH

            # Handling non-members is easier so we seperate it right away
            if not fuwwy_data.member:
                try:
                    # Only do accuracy comparison for new user since we're only worried about
                    # making sure the user reaches the average acc threshold.
                    if fuwwy_data.get_stage(fuwwy_map).accuracy > data.accuracy:
                        return await del_message(
                            ctx,
                            f"Your old score on {inline(data.beatmap.version)} has better accuracy than this play.",
                        )
                    await self.submit_score(ctx, data, fuwwy_map)
                except KeyError:
                    # If we get exception here the user doesn't have a score on the map so just submit score
                    await self.submit_score(ctx, data, fuwwy_map)

                setattr(fuwwy_data, fuwwy_map.name, FuwwyScore(data))
                stages: Dict[FuwwyBeatmapIDs, FuwwyScore] = fuwwy_data.get_stages(
                    [
                        FuwwyBeatmapIDs.JACKS,
                        FuwwyBeatmapIDs.STREAMS,
                        FuwwyBeatmapIDs.LN,
                        FuwwyBeatmapIDs.JACKS,
                    ]
                )
                worst_stage: Dict[
                        str, Union[Optional[FuwwyBeatmapIDs], Optional[FuwwyScore]]
                    ] = {
                        "stage": None,
                        "data": None,
                    }
                if len(stages) == 4:
                    for stage, play_data in stages.items():
                        if (
                            worst_stage["stage"] is None
                            or worst_stage["data"].accuracy > play_data.accuracy
                        ):
                            worst_stage = {"stage": stage, "data": play_data}

                stages.pop(worst_stage["stage"], None)

                accuracy = 0
                for stage_data in stages.values():
                    accuracy += stage_data.accuracy

                accuracy = accuracy / 3

                if accuracy > FUWWY_ACC_THRESHOLD:
                    await ctx.send(
                        embed=await self.fuwwy_profile_embed(
                            ctx,
                            await self.get_member_scores(ctx, new_member=True),
                            user_data["user_id"],
                            new_member=True,
                        )
                    )
                else:
                    await ctx.send(
                        embed=await self.new_member_scores_embed(
                            ctx, fuwwy_data, user_data["user_id"], worst_stage["stage"]
                        )
                    )
                return

            # Grab old play and see if they have one at all. Since we're already a member
            # we just submit if they haven't set one.
            old_play = fuwwy_data.get_stage(fuwwy_map)
            if old_play is None:
                await self.submit_score(ctx, data, fuwwy_map)
                return await ctx.send(
                    embed=await self.improvement_embed(
                        ctx,
                        user_data["user_id"],
                        fuwwy_map,
                        fuwwy_data.join_date,
                        FuwwyScore(data),
                    )
                )

            # At this point things get a little weird. The entry requirement for stages is
            # based on acc. But our leaderboard is score based so we want to do a score
            # comparison first.
            if old_play.score > data.score:
                return await del_message(
                    ctx,
                    f"Your old score {bold(str(old_play['score']))} on {inline(data.beatmap.version)} is better than this play.",
                )

            # If both score and acc is better. Just submit.
            if old_play.accuracy < data.accuracy:
                await self.submit_score(ctx, data, fuwwy_map)
                return await ctx.send(
                    embed=await self.improvement_embed(
                        ctx,
                        user_data["user_id"],
                        fuwwy_map,
                        fuwwy_data.join_date,
                        FuwwyScore(data),
                        old_play,
                    )
                )
            # Alternatively if we have a score on the full map.
            # Still just submit since we're not worried about user becoming ineligable.
            # This just means the new score will be better but with worse acc.
            #
            # XXX: This might change in the future if we allow score submission for
            # the full map no matter the score if you're eligable through stage acc.
            if fuwwy_data.FULL is not None:
                await self.submit_score(ctx, data, fuwwy_map)
                return await ctx.send(
                    embed=await self.improvement_embed(
                        ctx,
                        user_data["user_id"],
                        fuwwy_map,
                        fuwwy_data.join_date,
                        FuwwyScore(data),
                        old_play,
                    )
                )

            # A couple of things needed here
            # 1. The top 3 scores based on acc
            # 2. That isn't the full map
            stages: Dict[FuwwyBeatmapIDs, FuwwyScore] = fuwwy_data.get_stages(
                [
                    FuwwyBeatmapIDs.JACKS,
                    FuwwyBeatmapIDs.STREAMS,
                    FuwwyBeatmapIDs.LN,
                    FuwwyBeatmapIDs.JACKS,
                ]
            )

            # Writing this before I write the code for my own sanity.
            #
            # We know the user relies on it's top 3 scores accuracy to stay in the clan.
            # So we need to make sure that submitting a score with lower acc here
            # won't drop them below the average needed.
            if len(stages) == 4:
                worst_stage: Dict[str, Union[Optional[FuwwyBeatmapIDs], Optional[FuwwyScore]]] = {
                    "stage": None,
                    "data": None,
                }
                for stage, play_data in stages.items():
                    if (
                        worst_stage["stage"] is None
                        or worst_stage["data"].accuracy > play_data.accuracy
                    ):
                        worst_stage = {"stage": stage, "data": play_data}

                # We have 4 submitted scores and the worst is the same stage as our new one. Submit.
                if worst_stage["stage"] == fuwwy_map:
                    await self.submit_score(ctx, data, fuwwy_map)
                    return await ctx.send(
                        embed=await self.improvement_embed(
                            ctx,
                            user_data["user_id"],
                            fuwwy_map,
                            fuwwy_data.join_date,
                            FuwwyScore(data),
                            old_play,
                        )
                    )

                # Figure out which score should be used with the top 2
                # If our new score is worse than the old "worst score"
                # then the "worst score" is promoted to the top 3 for
                # comparison
                stages.pop(fuwwy_map, None)
                stages.pop(worst_stage["stage"], None)

                accuracy = (
                    worst_stage["data"].accuracy
                    if data.accuracy < worst_stage["data"].accuracy
                    else data.accuracy
                )

                for stage_data in stages.values():
                    accuracy += stage_data.accuracy

                accuracy = accuracy / 3

                if accuracy < FUWWY_ACC_THRESHOLD:
                    return await del_message(
                        f"Your score on the map was improved but "
                        f"the accuracy got worse and submitting it "
                        f"would drop you below the {'{:.2%}'.format(FUWWY_ACC_THRESHOLD)} "
                        f"average accuracy threshold."
                    )

                await self.submit_score(ctx, data, fuwwy_map)
                return await ctx.send(
                    embed=await self.improvement_embed(
                        ctx,
                        user_data["user_id"],
                        fuwwy_map,
                        fuwwy_data.join_date,
                        FuwwyScore(data),
                        old_play,
                    )
                )

            accuracy = data.accuracy

            for stage_data in stages.values():
                accuracy += stage_data.accuracy

            accuracy = accuracy / 3

            if accuracy < FUWWY_ACC_THRESHOLD:
                return await del_message(
                    f"Your score on the map was improved but "
                    f"the accuracy got worse and submitting it "
                    f"would drop you below the {'{:.2%}'.format(FUWWY_ACC_THRESHOLD)} "
                    f"average accuracy threshold."
                )

            await self.submit_score(ctx, data, fuwwy_map)
            return await ctx.send(
                embed=await self.improvement_embed(
                    ctx,
                    user_data["user_id"],
                    fuwwy_map,
                    fuwwy_data.join_date,
                    FuwwyScore(data),
                    old_play,
                )
            )

    async def fuwwy_profile_command(self, ctx: commands.Context) -> None:
        user_data = await self.osu_config.user(ctx.author).all()

        if not user_data["fuwwy_clan"]["member"]:
            return await del_message(
                ctx,
                "\n\n".join(
                    [
                        "You're not a member of the clan!",
                        f"To see the requirements for joining, use {inline(ctx.clean_prefix + 'fuwwyclan requirements')}",
                    ]
                ),
            )
        await ctx.send(
            embed=await self.fuwwy_profile_embed(
                ctx,
                await self.get_member_scores(ctx),
                user_data["user_id"],
                datetime.strptime(user_data["fuwwy_clan"]["join_date"], "%Y-%m-%dT%H:%M:%S%z"),
            )
        )

    async def set_role_command(self, ctx: commands.Context, role: discord.Role) -> None:
        if ctx.guild is None or ctx.guild.id != inkopolis:
            return del_message(ctx, "Please only run the command in the home server.")

        async with self.osu_config.fuwwy_clan() as data:
            data["role_id"] = role.id

        await ctx.send(f"Set the new role id to {inline(str(role.id))}.")

    async def claim_role_command(self, ctx: commands.Context) -> None:
        user_data = await self.osu_config.user(ctx.author).fuwwy_clan()
        if not user_data["member"]:
            return await ctx.maybe_send_embed("You're not a clan member!")

        clan_data = await self.osu_config.fuwwy_clan()

        if clan_data["role_id"] is not None:
            role = ctx.guild.get_role(clan_data["role_id"])

            if role is not None:
                try:
                    await ctx.author.add_roles(role)
                    return await ctx.maybe_send_embed("Role granted!")
                except:
                    pass
        await ctx.maybe_send_embed(
            f"There was an issue trying to give you the role. Ask the owner about it."
        )

    async def leaderboard_command(self, ctx: commands.Context, stage: str):
        author_data = await self.osu_config.user(ctx.author).all()
        if not author_data["user_id"]:
            return await del_message("You're not a clan member!")

        stage_l = stage.lower()
        clean_stage = None
        # I know this isn't pretty. But I wanna be generous to the user.
        #
        # Order here is important so we don't match with something by accident.
        if "full" in stage_l or "true" in stage_l or "nuzzle" in stage_l:
            clean_stage = FuwwyBeatmapIDs.FULL
        elif "1" in stage_l or "jack" in stage_l or "fluff" in stage_l:
            clean_stage = FuwwyBeatmapIDs.JACKS
        elif "2" in stage_l or "stream" in stage_l or "maw" in stage_l:
            clean_stage = FuwwyBeatmapIDs.STREAMS
        elif "3" in stage_l or "ln" in stage_l or "paws" in stage_l:
            clean_stage = FuwwyBeatmapIDs.LN
        elif "4" in stage_l or "tech" in stage_l or "beans" in stage_l:
            clean_stage = FuwwyBeatmapIDs.TECH

        if clean_stage is None:
            return await del_message(ctx, f"I couldn't figure out a stage that matched {stage}")

        beatmap_data = await self.api.beatmap(clean_stage.value)
        if beatmap_data is None:
            return await del_message(
                ctx, "An unknown error occured with the api. Maybe try again later?"
            )

        all_user_data = await self.osu_config.all_users()

        members: List[Dict[str, Union[FuwwyScore, int, str, None]]] = []
        for data in all_user_data.values():
            if data["fuwwy_clan"]["member"]:
                if data["fuwwy_clan"][clean_stage.name]:
                    members.append(
                        {
                            "username": data["username"],
                            "user_id": data["user_id"],
                            "data": FuwwyScore(data["fuwwy_clan"][clean_stage.name]),
                        }
                    )

        if len(members) == 0:
            return await del_message(
                ctx, "For some reason nobody has set any scores on that stage yet."
            )

        if len(members) > 1:
            members = sorted(members, key=lambda item: item["data"].score, reverse=True)

        await menu(
            ctx,
            await self.fuwwy_leaderboard_embed(ctx, members, beatmap_data, author_data["user_id"]),
        )


class Fuwwy(Commands):
    "Fuwwy clan commands."

    @commands.group(name="fuwwyclan", aliases=["fc"])
    async def fuwwy_clan(self, ctx: commands.Context):
        """Welcome to the new home of the osu! fuwwy clan.

        (Yes this is still just a meme. Don't take this seriously.)

        As a member. You get absolutely nothing!
        But your score gets saved on a leaderboard
        and you get a fancy role in my server.

        To get started with joining. Use `[p]fuwwyclan requirements`.
        """

    @commands.is_owner()
    @fuwwy_clan.command(name="reference", aliases=["ref"])
    async def _reference(self, ctx: commands.Context):
        """Set reference score used for full map."""

        await self.reference_command(ctx)

    @commands.is_owner()
    @fuwwy_clan.command(name="setrole")
    async def _set_role(self, ctx: commands.Context, role: discord.Role):
        """Set the FUWWY role."""

        await self.set_role_command(ctx, role)

    @commands.is_owner()
    @fuwwy_clan.command(name="resetreference", aliases=["rref"])
    async def _reset_reference(self, ctx: commands.Context):
        """Reset the reference score user for full map."""

        await self.osu_config.fuwwy_clan.clear()
        await ctx.send("Reset!")

    @commands.is_owner()
    @fuwwy_clan.command(name="removemember", aliases=["rm"])
    async def _remove_member(self, ctx: commands.Context, user: discord.User):
        """Manually remove a member from the clan."""

        await self.remove_member_command(ctx, user)

    @fuwwy_clan.command(name="requirements", aliases=["req"])
    async def _requirements(self, ctx: commands.Context):
        """See the score requirements for joining the clan."""

        await ctx.send(embed=await self.requirements_embed(ctx))

    @inkopolis_server_check()
    @fuwwy_clan.command(name="claimrole")
    async def _claim_role(self, ctx: commands.Context):
        """If for whatever reason you lost the clan role, use
        this to reclaim it.
        """

        await self.claim_role_command(ctx)

    @fuwwy_clan.command(name="submit")
    async def _submit(self, ctx: commands.Context):
        """Submit scores to join the clan.

        You'll need to be in my server to submit scores.

        To see the requirements for joining the clan, do `[p]fuwwyclan requirements`
        """

        await self.submit_command(ctx)

    @fuwwy_clan.command(name="profile", aliases=["scores"])
    async def _profile(self, ctx: commands.Context):
        """Show your clan profile with all your scores."""

        await self.fuwwy_profile_command(ctx)

    @fuwwy_clan.command(name="leaderboard", aliases=["lb", "standings"])
    async def _leaderboard(self, ctx: commands.Context, *, stage: str):
        """See the leaderboard for one of the maps in the exam.

        The stage is any keyword associated with the stage like:
        `stage 1`, `Nuzzles`, `LN` or `Full Exam`"""

        await self.leaderboard_command(ctx, stage)
