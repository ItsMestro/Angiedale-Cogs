from .management import Management, is_owner_if_bank_global


async def setup(bot):
    cog = Management(bot)
    bot.add_cog(cog)
    cog.sync_init()