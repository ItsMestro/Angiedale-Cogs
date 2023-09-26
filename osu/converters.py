from redbot.core import commands
from redbot.core.utils.chat_formatting import inline

class BeatMode:
    NORMAL = 1
    TUNNELVISION = 2
    SECRET = 3

class BeatModeConverter(commands.Converter):
    """
    Tries to convert given string to a osubeat mode.
    """
    async def convert(self, ctx:commands.Context, arg: str) -> BeatMode:
        if arg.upper() == "NORMAL" or arg == "1" or arg == 1:
            return BeatMode.NORMAL
        elif arg.upper() == "TUNNELVISION" or arg == "2" or arg == 2:
            return BeatMode.TUNNELVISION
        elif arg.upper() == "SECRET" or arg == "3" or arg == 3:
            return BeatMode.SECRET
        else:
            modestr = ""
            for m in [
                attr
                for attr in dir(BeatMode)
                if not callable(getattr(BeatMode, attr)) and not attr.startswith("__")
            ]:
                modestr += f"{inline(m)}, "
            raise commands.BadArgument(f"The mode has to be one of {modestr[:len(modestr) - 2]}")