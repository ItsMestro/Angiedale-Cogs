import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Mapping, Optional, Set, Tuple, Union

import discord
from ossapi import Beatmap, GameMode
from ossapi import Mod as OsuMod
from ossapi import OssapiAsync
from ossapi import Score as OsuScore
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.menus import _ControlCallable

from .utils.beatmapparser import DatabaseBeatmap
from .utils.classes import CommandArgs, CommandParams, DatabaseLeaderboard, DoubleArgs, SingleArgs


class MixinMeta(ABC):
    """
    Base class for well behaved type hint detection with composite class.

    Basically, to keep developers sane when not all attributes are defined in each mixin.
    """

    def __init__(self, *_args):
        self.api: Union[OssapiAsync, None]
        self.bot: Red
        self.osu_config: Config
        self.db_connected: bool
        self.osubeat_maps: Dict[
            int, Dict[int, Dict[str, Union[datetime, List[Union[OsuMod, str]], GameMode]]]
        ]
        self.leaderboard_tasks: Set[Optional[asyncio.Task]]
        self.osubeat_check_tasks: Set[Optional[asyncio.Task]]
        self.tracking_init_task: asyncio.Task

    @abstractmethod
    def toggle_page(self, bot: Red) -> Mapping[str, _ControlCallable]:
        raise NotImplementedError()

    @abstractmethod
    async def user_id_extractor(
        self,
        ctx: commands.Context,
        user: Optional[Union[discord.Member, str]],
        check_leaderboard: bool = False,
    ) -> Union[Optional[int], Tuple[Optional[int], bool]]:
        raise NotImplementedError()

    @abstractmethod
    async def user_and_parameter_extractor(
        self,
        ctx: commands.Context,
        params: Tuple[str],
        single_args: List[SingleArgs] = None,
        double_args: List[DoubleArgs] = None,
        skip_user: bool = False,
    ) -> Optional[CommandParams]:
        raise NotImplementedError()

    @abstractmethod
    async def queue_osubeat_check(self, ctx: commands.Context, data: List[OsuScore]) -> None:
        raise NotImplementedError()

    @abstractmethod
    def queue_leaderboard(self, data: List[OsuScore], mode: GameMode):
        raise NotImplementedError()

    @abstractmethod
    async def extra_beatmap_info(self, beatmap: Beatmap) -> DatabaseBeatmap:
        raise NotImplementedError()

    @abstractmethod
    async def message_history_lookup(
        self, ctx: commands.Context
    ) -> Tuple[Optional[int], OsuMod, GameMode]:
        raise NotImplementedError()

    @abstractmethod
    async def osu_link(self, ctx: commands.Context, *, username: str):
        raise NotImplementedError()

    @abstractmethod
    def beatmap_converter(self, search_string: Union[int, str]) -> Optional[int]:
        raise NotImplementedError()

    @abstractmethod
    async def argument_extractor(
        self, ctx: commands.Context, args: Tuple[str]
    ) -> Optional[CommandArgs]:
        raise NotImplementedError()

    @abstractmethod
    async def get_unranked_leaderboard(
        self, map_id: int, mode: GameMode
    ) -> Optional[DatabaseLeaderboard]:
        raise NotImplementedError()

    @abstractmethod
    def prettify_mode(self, mode: GameMode) -> str:
        raise NotImplementedError()

    async def profile_linking_onboarding(ctx: commands.Context) -> None:
        raise NotImplementedError()
