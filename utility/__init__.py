from redbot.core.bot import Red

from .utility import Utility


async def setup(bot: Red) -> None:
    await bot.add_cog(Utility(bot))
