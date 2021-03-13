from .mod import Mod

__red_end_user_data_statement__ = (
    "This cog stores data on user "
    "that are meant to aid server staff "
    "in moderating their servers. "
    "It also keeps track of punishments "
    "set by said staff on users. "
    "To prevent punishment evasion data "
    "can not be deleted per request."
)

async def setup(bot):
    cog = Mod(bot)
    bot.add_cog(cog)
    await cog.initialize()
