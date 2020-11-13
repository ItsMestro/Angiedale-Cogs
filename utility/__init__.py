from .utility import Utility


def setup(bot):
    cog = Utility(bot)
    bot.add_cog(cog)