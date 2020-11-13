from .games import Games
from .session import *
from .log import *


async def setup(bot):
    cog = Games(bot)
    bot.add_cog(cog)
    await cog.initialise()