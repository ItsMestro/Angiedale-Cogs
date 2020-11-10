import math

from redbot.core import commands

__all__ = ("finite_float",)


def finite_float(arg: str) -> float:
    try:
        ret = float(arg)
    except ValueError:
        raise commands.BadArgument(("`{arg}` is not a number.").format(arg=arg))
    if not math.isfinite(ret):
        raise commands.BadArgument(("`{arg}` is not a finite number.").format(arg=ret))
    return ret
