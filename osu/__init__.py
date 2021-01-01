from .osu import osu


def setup(bot):
    cog = osu(bot)
    bot.add_cog(cog)
