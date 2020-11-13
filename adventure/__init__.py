from .adventure import Adventure


async def setup(bot):
    cog = Adventure(bot)
    bot.add_cog(cog)
