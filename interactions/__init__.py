from redbot.core.bot import Red

from .interactions import Interactions


async def setup(bot: Red) -> None:
    await bot.add_cog(Interactions(bot))
