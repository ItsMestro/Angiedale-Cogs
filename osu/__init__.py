from .osu import Osu

__red_end_user_data_statement__ = (
    "This cog stores data provided by users "
    "for the express purpose of redisplaying. "
    "It does not store user data which was not "
    "provided through a command. "
    "This cog does not support data requests, "
    "but will respect deletion requests."
)


def setup(bot):
    cog = Osu(bot)
    bot.add_cog(cog)
