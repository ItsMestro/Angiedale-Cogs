from .management import Management, is_owner_if_bank_global
from redbot.core.bot import Red


async def setup(bot: Red):
    cog = Management(bot)
    bot.add_cog(cog)
    cog.sync_init()