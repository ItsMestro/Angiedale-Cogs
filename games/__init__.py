from redbot.core.bot import Red
from .games import Games


async def setup(bot: Red) -> None:
    cog = Games(bot)
    await bot.add_cog(cog)
    await cog.initialise()
