from redbot.core.bot import Red

from .games import Games


async def setup(bot: Red) -> None:
    await bot.add_cog(Games(bot))
