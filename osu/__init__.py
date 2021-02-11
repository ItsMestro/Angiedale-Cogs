from .osu import Osu
from .tools import *
from .embeds import *

def setup(bot):
    cog = Osu(bot)
    bot.add_cog(cog)
