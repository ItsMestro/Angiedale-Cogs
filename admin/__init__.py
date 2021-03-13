from .admin import Admin

__red_end_user_data_statement__ = (
    "This cog stores data on users who break "
    "filtering rules set up by server "
    "owners. It also holds data on users that "
    "are warned in servers by server staff. "
    "By the nature of this data it can not be deleted "
    "per request to prevent rule evasion."
)

async def setup(bot):
    cog = Admin(bot)
    bot.add_cog(cog)
