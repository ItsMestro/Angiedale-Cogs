from .general import General


def setup(bot):
    cog = General(bot)
    bot.add_cog(cog)
