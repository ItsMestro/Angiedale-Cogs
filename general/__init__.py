from .general import General

__red_end_user_data_statement__ = (
    "This cog stores data on user reports "
    "sent to server owners. Nothing gets "
    "saved unless a report has been sent. "
    "To allow server staff the full "
    "ability to take action against these "
    "reports it is not possible to "
    "have the data deleted per request."
)


def setup(bot):
    cog = General(bot)
    bot.add_cog(cog)
