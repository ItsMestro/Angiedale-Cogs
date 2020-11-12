from .programming import Programming


def setup(bot):
    bot.add_cog(Programming(bot))