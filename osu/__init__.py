from redbot.core.bot import Red
from .osu import Osu


async def setup(bot: Red) -> None:
    await bot.add_cog(Osu(bot))
