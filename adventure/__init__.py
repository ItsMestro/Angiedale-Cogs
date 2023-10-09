from redbot.core.bot import Red

from .adventure import Adventure


async def setup(bot: Red) -> None:
    await bot.add_cog(Adventure(bot))
