from redbot.core.bot import Red
from .ttools import TTools


async def setup(bot: Red) -> None:
    await bot.add_cog(TTools(bot))
