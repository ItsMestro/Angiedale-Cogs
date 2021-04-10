from .games import Games

__red_end_user_data_statement__ = (
    "This cog stores data provided by users "
    "for the express purpose of redisplaying. "
    "It does not store user data which was not "
    "provided through a command. "
    "This cog does not support data requests, "
    "but will respect deletion requests."
)


async def setup(bot):
    cog = Games(bot)
    bot.add_cog(cog)
    await cog.initialise()
