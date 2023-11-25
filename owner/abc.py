from abc import ABC, abstractmethod
from typing import List, Union

import discord
from redbot.core import Config
from redbot.core.bot import Red


class MixinMeta(ABC):
    """
    Base class for well behaved type hint detection with composite class.

    Basically, to keep developers sane when not all attributes are defined in each mixin.
    """

    def __init__(self, *_args):
        self.bot: Red
        self.interaction: List[Union[discord.Member, discord.User]]
        self.admin_config: Config

    @abstractmethod
    async def stop_interaction(self, user: Union[discord.Member, discord.User]) -> None:
        raise NotImplementedError
