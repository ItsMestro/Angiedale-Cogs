from .ttools import TTools


def setup(bot):
    cog = TTools(bot)
    bot.add_cog(cog)
