from .api import API


def setup(bot):
    cog = API(bot)
    bot.add_cog(cog)