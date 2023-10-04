from redbot.core.bot import Red
from .owner import Owner


async def setup(bot: Red) -> None:
    await bot.add_cog(Owner(bot))
