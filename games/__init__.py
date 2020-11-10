from .games import Games
from .session import *
from .log import *


def setup(bot):
    bot.add_cog(Games())