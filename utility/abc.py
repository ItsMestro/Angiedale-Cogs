import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from redbot.core import Config
from redbot.core.bot import Red


class MixinMeta(ABC):
    """
    Base class for well behaved type hint detection with composite class.

    Basically, to keep developers sane when not all attributes are defined in each mixin.
    """

    def __init__(self, *_args):
        self.bot: Red
        self.raffle_config: Config
        self.poll_config: Config
        self.active_poll_tasks: List[asyncio.Task]
        self.active_raffle_tasks: List[asyncio.Task]
        self.poll_cache_task: Optional[asyncio.Task]
        self.polls: Dict[int, Dict[int, Any]]

    @abstractmethod
    async def update_cache(self, guild_id: int, message_id: int, poll: Any) -> None:
        raise NotImplementedError
