from .management import Management, is_owner_if_bank_global


async def setup(bot):
    bot.add_cog(Management(bot))
    Management(bot).sync_init()