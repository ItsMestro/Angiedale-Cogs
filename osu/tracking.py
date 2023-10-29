import asyncio
import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import discord
from ossapi import GameMode
from ossapi import Score as OsuScore
from ossapi import ScoreType
from redbot.core import commands
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import humanize_number, inline
from redbot.core.utils.menus import menu

from .abc import MixinMeta
from .utilities import EMOJI, OsuUrls, del_message
from .utils.classes import _GAMEMODES, ValueFound

log = logging.getLogger("red.angiedale.osu")


class APIFailingError(Exception):
    """
    Osu API isn't sending any data.
    """


class Embeds(MixinMeta):
    """Embed builders."""

    async def tracking_list_embed(
        self,
        ctx: commands.Context,
        tracking_entries: List[
            Dict[
                str,
                Union[
                    int,
                    discord.TextChannel,
                    discord.VoiceChannel,
                    discord.StageChannel,
                    discord.Thread,
                    GameMode,
                ],
            ]
        ],
    ) -> List[discord.Embed]:
        tracked_string = []

        for tracking in tracking_entries:
            tracked_string.append(
                f'{tracking["id"]} ◈ {tracking["mode"].name.capitalize()} ◈ {tracking["channel"].mention}'
            )

        tracked_string = "\n".join(tracked_string)

        embeds = []
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_author(
            name=f"{len(tracking_entries)} players are being tracked in this server.",
            icon_url=self.bot.user.avatar.url,
        )

        embed.description = tracked_string

        embeds.append(embed)
        return embeds

    async def tracking_embed(self, data: dict) -> discord.Embed:
        if data["mode"] == "mania":
            combo_ratio = "Combo / Ratio"
            version = re.sub(r"^\S*\s", "", data["beatmap"]["version"])
            try:
                ratio = round(
                    data["statistics"]["count_geki"] / data["statistics"]["count_300"], 2
                )
            except:
                ratio = "Perfect"
            combo = f'**{data["max_combo"]:,}x** / {ratio}'
            hits = (
                f'{humanize_number(data["statistics"]["count_geki"])}/'
                f'{humanize_number(data["statistics"]["count_300"])}/'
                f'{humanize_number(data["statistics"]["count_katu"])}/'
                f'{humanize_number(data["statistics"]["count_100"])}/'
                f'{humanize_number(data["statistics"]["count_50"])}/'
                f'{humanize_number(data["statistics"]["count_miss"])}'
            )
            stats = f'OD: `{data["beatmap"]["accuracy"]}` | ' f'HP: `{data["beatmap"]["drain"]}`'
        else:
            version = data["beatmap"]["version"]
            combo_ratio = "Combo"
            combo = f'**{data["max_combo"]:,}x**'
            hits = (
                f'{humanize_number(data["statistics"]["count_300"])}/'
                f'{humanize_number(data["statistics"]["count_100"])}/'
                f'{humanize_number(data["statistics"]["count_50"])}/'
                f'{humanize_number(data["statistics"]["count_miss"])}'
            )
            stats = (
                f'CS: `{data["beatmap"]["cs"]}` | '
                f'AR: `{data["beatmap"]["ar"]}` | '
                f'OD: `{data["beatmap"]["accuracy"]}` | '
                f'HP: `{data["beatmap"]["drain"]}`'
            )

        mods = ""
        if data["mods"] != "NM":
            mods = f' +{data["mods"]}'

        status = inline(
            data["beatmapset"]["status"].capitalize()
            if data["beatmapset"]["status"] != "WIP"
            else data["beatmapset"]["status"]
        )

        pp_addon = ""
        accuracy_addon = ""
        embed_title = f'New #{data["index"] + 1} for {data["user"]["username"]}'
        embed_color = discord.Color.green()
        try:
            performance = f'{humanize_number(round(data["pp"], 2))}'
        except TypeError:
            performance = 0
        try:
            if data["old_pp"]:
                checked_pp = round(data["pp"] - data["old_pp"], 2)
                checked_accuracy = data["accuracy"] - data["old_accuracy"]
                pp_addon = f" ({humanize_number(checked_pp)})"
                accuracy_addon = f' ({"{:.2%}".format(checked_accuracy)})'
                if checked_pp > 0:
                    pp_addon = f" (+{humanize_number(checked_pp)})"
                if checked_accuracy > 0:
                    accuracy_addon = f' (+{"{:.2%}".format(checked_accuracy)})'
                if data["index"] < data["old_index"]:
                    embed_title = f'Improved #{data["index"] + 1} from #{data["old_index"] + 1} for {data["user"]["username"]}'
                    embed_color = discord.Color.blue()
                else:
                    embed_title = f'Changed #{data["index"] + 1} from #{data["old_index"] + 1} for {data["user"]["username"]}'
                    embed_color = discord.Color.yellow()
        except KeyError:
            pass

        embed = discord.Embed(color=embed_color)

        embed.set_author(
            name=f'{data["beatmapset"]["artist"]} - {data["beatmapset"]["title"]} [{version}] [{str(data["beatmap"]["difficulty_rating"])}★]',
            url=data["beatmap"]["url"],
            icon_url=data["user"]["avatar_url"],
        )

        embed.title = embed_title

        embed.set_image(url=data["beatmapset"]["cover"])

        embed.add_field(name="Grade", value=f'{EMOJI[data["rank"]]}{mods}', inline=True)
        embed.add_field(name="Score", value=humanize_number(data["score"]), inline=True)
        embed.add_field(
            name="Accuracy", value="{:.2%}{}".format(data["accuracy"], accuracy_addon), inline=True
        )
        embed.add_field(name="PP", value=f"**{performance}pp{pp_addon}**", inline=True)
        embed.add_field(name=combo_ratio, value=combo, inline=True)
        embed.add_field(name="Hits", value=hits, inline=True)
        embed.add_field(
            name="Map Info",
            value="\n".join(
                [
                    f'Mapper: [{data["beatmapset"]["creator"]}]({OsuUrls.USER.value}{data["beatmapset"]["creator_id"]}) | '
                    f'{EMOJI["BPM"]} `{data["beatmap"]["bpm"]}` | '
                    f'Objects: `{humanize_number(data["beatmap"]["count_circles"] + data["beatmap"]["count_sliders"] + data["beatmap"]["count_spinners"])}` ',
                    f"Status: {status} | {stats}",
                ]
            ),
            inline=False,
        )

        embed.set_footer(
            text=f'{data["user"]["username"]} | osu!{self.prettify_mode(GameMode(data["mode"])).capitalize()} | Played'
        )

        embed.timestamp = datetime.strptime(data["created_at"], "%Y-%m-%dT%H:%M:%S%z")

        return embed


class Functions(Embeds):
    """Utiility functions."""

    def __init__(self):
        self.tracking_cache: Dict[GameMode, Dict[int, List[int]]] = {}

    async def initialize_tracking(self):
        """First time tracking logic when starting up."""

        await self.bot.wait_until_red_ready()

        log.info("Initializing osu! tracking.")

        await self.refresh_tracking_cache()

        count = 0
        for mode, users in self.tracking_cache.items():
            count += len(users)

        if count == 0 and self.tracking_task:
            return log.info("Tracking initialization stopped due to empty cache.")

        path = Path(f"{cog_data_path(self)}/tracking")
        path.mkdir(exist_ok=True)

        # Since bot is just starting up. Update every user once without sending embed just in case
        # to avoid accidental spam on boot.
        #
        # This has been a problem in the past so now there is a fail safe.

        await asyncio.sleep(20)
        active_cache = deepcopy(self.tracking_cache)

        for mode, users in active_cache.items():
            for user_id, channels in users.items():
                user_path = f"{path}/{user_id}_{mode.value}.json"

                fresh_data = await self.api.user_scores(
                    user_id, ScoreType.BEST, mode=mode, limit=100
                )
                if fresh_data:
                    fresh_data = self.scores_to_dict(fresh_data)
                    with open(user_path, "w+") as data:
                        json.dump(fresh_data, data, indent=4)
                    await asyncio.sleep(1)
                await asyncio.sleep(4)

        self.tracking_task = asyncio.create_task(self.update_tracking(path))

    async def ping_api(self) -> bool:
        """Pings the api with a long cooldown to test if it's alive."""
        await asyncio.sleep(600)
        data = (
            await self.api.seasonal_backgrounds()
        )  # I'll probably regret using this endpoint in the future but I thought it was funny
        if data is None:
            return False
        return True

    async def restart_tracking(self, exception: Exception = None, api_fail: bool = False):
        """Restarts tracking in case of an error.

        Will wait on api to become alive again if it detected the api as down.
        """
        if exception:
            log.warning("Tracking loop failed and will restart shortly.", exc_info=exception)
        await asyncio.sleep(300)

        if api_fail:
            log.warning(
                "Tracking stopped from api having issues. Will ping api and restart when back up."
            )
            while True:
                if await self.ping_api():
                    break

        path = Path(f"{cog_data_path(self)}/tracking")
        path.mkdir(exist_ok=True)

        self.tracking_init_task = asyncio.create_task(self.initialize_tracking())

    async def update_tracking(self, path: Path):
        """Top score tracking loop (The magnum opus of awful code).

        Checks a users top scores and compares with stored data
        and sends any changes to subscribed servers.
        """

        log.info("Starting tracking loop.")

        retry_attempt = False  # For running the loop one extra time in case it was api error.
        remove_fails = False  # Allow the loop to delete entries.

        while True:
            try:
                await asyncio.sleep(60)  # Arbitrary break for the api. Will make dynamic some day.

                fail_count = 0
                active_cache = deepcopy(
                    self.tracking_cache
                )  # Make a copy each run that doesn't change during it.

                for mode, users in active_cache.items():
                    for user_id, channels in users.items():
                        stored_data = {}
                        user_path = f"{path}/{user_id}_{mode.value}.json"

                        fresh_data = await self.api.user_scores(
                            user_id, ScoreType.BEST, mode=mode, limit=100
                        )
                        if fresh_data:
                            fresh_data = self.scores_to_dict(fresh_data)

                            if not os.path.exists(
                                user_path
                            ):  # Must be new user. Cache without sending embeds.
                                with open(user_path, "w+") as data:
                                    json.dump(fresh_data, data, indent=4)
                            else:
                                try:
                                    with open(user_path) as data:  # Try to get the users data.
                                        stored_data = json.load(data)
                                except FileNotFoundError:
                                    pass

                                if (
                                    not stored_data == fresh_data
                                ):  # Data isn't the same. Time to send embeds.
                                    with open(user_path, "w+") as data:
                                        json.dump(fresh_data, data, indent=4)

                                    await self.tracking_payload(channels, stored_data, fresh_data)
                                    await asyncio.sleep(10)

                            await asyncio.sleep(5)
                        else:
                            if (
                                not remove_fails
                            ):  # We don't remove entries until we're sure it's not the api having issues.
                                fail_count += 1
                                continue
                            await self.update_tracking_config(
                                user=user_id, mode=mode, remove_only=True
                            )
                            await self.refresh_tracking_cache()

                count = 0
                for mode, users in self.tracking_cache.items():  # Count users in warm cache.
                    count += len(users)
                if count == 0:
                    log.info("Stopping tracking loop due to empty cache.")
                    break

                count = 0
                for mode, users in active_cache.items():  # Count users in cold cache.
                    count += len(users)
                if fail_count > 0:
                    if not remove_fails and retry_attempt:  # We've already retried once.
                        if fail_count >= count:  # Stop the loop and double check the api.
                            raise APIFailingError
                        else:  # Remove entries next run.
                            remove_fails = True
                    else:  # Give the loop one more attempt.
                        retry_attempt = True
                else:  # Reset.
                    remove_fails = False
                    retry_attempt = False
            except asyncio.CancelledError:  # Most likely cog unloading.
                break
            except APIFailingError:
                self.tracking_restart_task = asyncio.create_task(
                    self.restart_tracking(api_fail=True)
                )
            except (
                Exception
            ) as e:  # I've had so many issues with this that I'm just gonna catch all and restart at this point.
                self.tracking_restart_task = asyncio.create_task(
                    self.restart_tracking(exception=e)
                )
                break

    async def refresh_tracking_cache(self) -> None:
        """Tracking cache instantiation.

        Should be called after every config change to flush the cache with new data.
        """

        async with self.osu_config.tracking() as data:
            new_cache = {}
            for mode_string, users in data.items():
                mode = GameMode(mode_string)
                new_cache[mode] = {}
                for user, channels in users.items():
                    new_cache[mode][int(user)] = channels

        self.tracking_cache = new_cache

        if self.tracking_task:  # Restart tracking if needed
            if self.tracking_task.done():
                self.tracking_init_task = asyncio.create_task(self.initialize_tracking())

    async def tracking_payload(
        self,
        channels: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        stored_data: List[dict],
        fresh_data: List[dict],
    ) -> None:
        """Builds score embed and sends to subscribed servers."""
        for stored_index, _ in enumerate(stored_data):
            for fresh_index, _ in enumerate(fresh_data):
                if (
                    fresh_data[fresh_index]["beatmap"]["id"]
                    == stored_data[stored_index]["beatmap"]["id"]
                ):
                    if (
                        fresh_data[fresh_index]["created_at"]
                        == stored_data[stored_index]["created_at"]
                    ):
                        fresh_data.pop(fresh_index)
                        break
                    fresh_data[fresh_index]["old_pp"] = stored_data[stored_index]["pp"]
                    fresh_data[fresh_index]["old_index"] = stored_data[stored_index]["index"]
                    fresh_data[fresh_index]["old_accuracy"] = stored_data[stored_index]["accuracy"]

        bad_channels = []

        for data in fresh_data:
            embed = await self.tracking_embed(data)

            for channel_id in channels:
                channel = self.bot.get_channel(channel_id)

                if channel is not None:
                    try:
                        await channel.send(embed=embed)
                    except discord.errors.Forbidden:
                        log.warning(
                            f"Failed to send tracking embed to {channel.name} ({channel.id})"
                        )
                    await asyncio.sleep(1)
                else:
                    bad_channels.append(channel_id)
            await asyncio.sleep(1)

        if len(bad_channels) == 0:
            return

        log.info(f"Missing tracking channels found. Removing them from config: {bad_channels}")

        async with self.osu_config.tracking() as data:
            for channel_id in bad_channels:
                for mode, users in data.items():
                    for user, channel_list in users.items():
                        if channel_id in channel_list:
                            data[mode][user].remove(channel_id)
                        if len(data[mode][user]) == 0:
                            data[mode].remove(user)

            log.info(f"New config: {data}")

    def scores_to_dict(
        self, scores: List[OsuScore]
    ) -> dict:  # This returns just a generic dict but is formatted same as ossapi.Score
        """Converts ossapi.Score to dict for storing locally as json."""
        output = []
        index = 0

        for score in scores:
            data = {}

            data["index"] = index

            data["beatmap"] = {}  # Beatmap data
            data["beatmap"]["id"] = score.beatmap.id
            data["beatmap"]["version"] = score.beatmap.version
            data["beatmap"]["difficulty_rating"] = score.beatmap.difficulty_rating
            data["beatmap"]["ar"] = score.beatmap.ar
            data["beatmap"]["cs"] = score.beatmap.cs
            data["beatmap"]["accuracy"] = score.beatmap.accuracy
            data["beatmap"]["drain"] = score.beatmap.drain
            data["beatmap"]["url"] = score.beatmap.url
            data["beatmap"]["bpm"] = score.beatmap.bpm
            data["beatmap"]["count_circles"] = score.beatmap.count_circles
            data["beatmap"]["count_sliders"] = score.beatmap.count_sliders
            data["beatmap"]["count_spinners"] = score.beatmap.count_spinners

            data["mode"] = score.mode.value
            data["created_at"] = score.created_at.strftime("%Y-%m-%dT%H:%M:%S%z")
            data["score"] = score.score
            data["pp"] = score.pp
            data["accuracy"] = score.accuracy
            data["max_combo"] = score.max_combo
            data["mods"] = score.mods.short_name()
            data["rank"] = score.rank.value

            data["statistics"] = {}  # Hit counts
            data["statistics"]["count_geki"] = score.statistics.count_geki
            data["statistics"]["count_katu"] = score.statistics.count_katu
            data["statistics"]["count_300"] = score.statistics.count_300
            data["statistics"]["count_100"] = score.statistics.count_100
            data["statistics"]["count_50"] = score.statistics.count_50
            data["statistics"]["count_miss"] = score.statistics.count_miss

            data["user"] = {}  # User data
            user = score.user()
            data["user"]["username"] = user.username
            data["user"]["avatar_url"] = user.avatar_url

            data["beatmapset"] = {}  # Set data
            data["beatmapset"]["id"] = score.beatmapset.id
            data["beatmapset"]["title"] = score.beatmapset.title
            data["beatmapset"]["artist"] = score.beatmapset.artist
            data["beatmapset"]["cover"] = score.beatmapset.covers.cover
            data["beatmapset"]["creator_id"] = score.beatmapset.user_id
            data["beatmapset"]["creator"] = score.beatmapset.creator
            data["beatmapset"]["status"] = score.beatmapset.status.name

            index += 1

            output.append(data)

        return output

    async def get_player(
        self, ctx: commands.Context, user: Union[discord.Member, str]
    ) -> Optional[Tuple[int, str]]:
        """Gets a users id and username from a string or mention.

        This is just a slightly modified version of `utilities.Utilities.user_id_extractor()`
        that is less persistant on trying to find a functional user.
        """
        if isinstance(user, discord.Member):
            user_id = await self.osu_config.user(user).userid()
            if user_id is not None:
                username = await self.osu_config.user(user).username()
        else:
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
                user_id: str = clean_user.rsplit("/", 1)[-1]
            else:
                try:
                    user_id = int(user)
                except ValueError:
                    user_id = user

            data = await self.api.user(user_id)

            if data is None:
                await del_message(ctx, f"Could not find the user {user}.")
                return None, None

            user_id: int = data.id
            username = data.username

        if user_id is None:
            await del_message(ctx, f"Could not find the user {user}.")
            return None, None

        return user_id, username

    async def count_tracking(
        self,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ] = None,
        user: str = None,
        guild: discord.Guild = None,
    ) -> Union[
        int,
        List[
            Dict[
                str,
                Union[
                    int,
                    discord.TextChannel,
                    discord.VoiceChannel,
                    discord.StageChannel,
                    discord.Thread,
                    GameMode,
                ],
            ]
        ],
    ]:
        """Counts how many instances of tracking are happening in a server.

        Will return a list with data in a weird edge case where we re-use this function for
        listing the tracked users instead of counting them.
        """

        count = 0
        channel_list = []

        async with self.osu_config.tracking() as data:
            for mode, users in data.items():
                for user_id, channels in users.items():
                    if channel is not None:
                        if user_id == user:
                            count -= 1
                        for channel_id in channels:
                            # If channel is in guild count it
                            if channel.guild.get_channel_or_thread(channel_id):
                                count += 1
                                break
                    elif user is not None and user == user_id:
                        count += 1
                        break
                    elif guild is not None:
                        for channel_id in channels:
                            guild_channel = guild.get_channel_or_thread(channel_id)
                            if guild_channel is not None:
                                channel_list.append(
                                    {
                                        "id": user_id,
                                        "channel": guild_channel,
                                        "mode": GameMode(mode),
                                    }
                                )
                                break
        if guild is not None:
            return channel_list

        return count

    async def update_tracking_config(
        self,
        user: int,
        mode: GameMode,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ] = None,
        remove_only: bool = False,
    ) -> None:
        """Updates our top score tracking config with new data.

        Has logic for finding and removing old entries first before
        updating our config or to just clear the data.
        """
        async with self.osu_config.tracking() as data:
            users: Dict[str, List[int]] = data[mode.value]  # Get users of the given mode

            channels = []

            try:
                if channel is not None:
                    channels = users[str(user)]  # Get user channel from dict of users
                    for channel_id in channels:
                        if channel.guild.get_channel_or_thread(
                            channel_id
                        ):  # If channel is in guild
                            channels.remove(channel_id)  # Remove it from list to allow new channel
                            raise ValueFound
            except (ValueFound, KeyError):
                pass
            if remove_only:
                try:
                    if channel is None or len(users[str(user)]) == 0:
                        del data[mode.value][str(user)]
                except KeyError:
                    pass
                try:
                    os.remove(f"{cog_data_path(self)}/tracking/{user}_{mode.value}.json")
                except:
                    pass
                return
            channels.append(channel.id)

            data[mode.value][str(user)] = channels


class Commands(Functions, Embeds):
    """Command logic."""

    async def tracking_add_command(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        mode: str,
        user: Union[discord.Member, str],
        dev: bool = False,
    ) -> None:
        mode = mode.lower()
        for key, temp_mode in _GAMEMODES.items():
            if mode in temp_mode:
                mode = GameMode(key)
                break
        if not isinstance(mode, GameMode):
            return await del_message(ctx, "Mode given is invalid.")

        user_id, username = await self.get_player(ctx, user)

        if user_id is None:
            return

        if not dev:
            count = await self.count_tracking(channel=channel, user=str(user_id))

            if count >= 25:
                return await del_message(
                    ctx,
                    "Already tracking 25 users in this server. Please remove some before adding more.",
                )

        await self.update_tracking_config(user=user_id, mode=mode, channel=channel)
        await self.refresh_tracking_cache()
        await ctx.maybe_send_embed(
            f"Now tracking top 100 plays for {username} in {channel.mention}"
        )

    async def tracking_remove_command(
        self, ctx: commands.Context, mode: str, user: Union[discord.Member, str]
    ):
        mode = mode.lower()
        for key, temp_mode in _GAMEMODES.items():
            if mode in temp_mode:
                mode = GameMode(key)
                break
        if not isinstance(mode, GameMode):
            return await del_message(ctx, "Mode given is invalid.")

        user_id, username = await self.get_player(ctx, user)

        if user_id is None:
            return

        count = await self.count_tracking(user=str(user_id))

        if count == 0:
            return await del_message(ctx, f"{username} isn't being tracked in this server.")

        await self.update_tracking_config(
            user=user_id, mode=mode, channel=ctx.channel, remove_only=True
        )
        await self.refresh_tracking_cache()
        await ctx.maybe_send_embed(f"Stopped tracking {username} in osu!{mode.name.capitalize()}")

    async def tracking_list_command(self, ctx: commands.Context) -> None:
        count = await self.count_tracking(guild=ctx.guild)

        if len(count) == 0:
            return await del_message(ctx, "Nobody is being tracked in this server.")

        count = sorted(count, key=lambda item: item["mode"].value)

        embeds = await self.tracking_list_embed(ctx, count)

        await menu(ctx, embeds)


class Tracking(Commands):
    """Top score tracking."""

    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    @commands.group(name="osutrack")
    async def osu_track(self, ctx: commands.Context):
        """Top play tracking"""

    @osu_track.command(name="add")
    async def _tracking_add(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        mode: str,
        *,
        user: Union[discord.Member, str],
    ):
        """Track a players top scores.

        Max 25 players in a server.
        """

        await self.tracking_add_command(ctx, channel, mode, user)

    @osu_track.command(name="remove")
    async def _tracking_remove(
        self, ctx: commands.Context, mode: str, *, user: Union[discord.Member, str]
    ):
        """Remove a tracked player."""

        await self.tracking_remove_command(ctx, mode, user)

    @osu_track.command(name="list")
    async def _tracking_list(self, ctx: commands.Context):
        """Lists currently tracked users in this server."""

        await self.tracking_list_command(ctx)

    @commands.is_owner()
    @osu_track.command(name="dev")
    async def _tracking_dev(
        self,
        ctx: commands.Context,
        channel: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread
        ],
        mode: str,
        *,
        user: Union[discord.Member, str],
    ):
        """Track a players top scores.

        Max 25 players in a server.
        """

        await self.tracking_add_command(ctx, channel, mode, user, dev=True)
