from redbot.core.bot import Red
from .api import API


async def setup(bot: Red) -> None:
    await bot.add_cog(API(bot))
