from .owner import Owner

__red_end_user_data_statement__ = "This cog does not store any End User Data."


def setup(bot):
    cog = Owner(bot)
    bot.add_cog(cog)
