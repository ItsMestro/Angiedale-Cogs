from .interactions import Interactions


def setup(bot):
    cog = Interactions(bot)
    bot.add_cog(cog)