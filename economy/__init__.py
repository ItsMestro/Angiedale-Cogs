from .economy import Economy


def setup(bot):
    cog = Economy(bot)
    bot.add_cog(cog)