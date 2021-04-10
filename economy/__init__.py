from .economy import Economy

__red_end_user_data_statement__ = (
    "This cog stores dates associated with "
    "the last usage of certain commands. "
    "To avoid timer evasions, "
    "data can not be deleted per request."
)


def setup(bot):
    cog = Economy(bot)
    bot.add_cog(cog)
