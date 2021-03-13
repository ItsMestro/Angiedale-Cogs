from .tournamenttools import TournamentTools


def setup(bot):
    cog = TournamentTools(bot)
    bot.add_cog(cog)
