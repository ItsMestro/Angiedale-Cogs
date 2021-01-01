from .osu import Osu


def setup(bot):
    cog = Osu(bot)
    bot.add_cog(cog)
