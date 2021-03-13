from datetime import timedelta

from redbot.core import checks, commands
from redbot.core.utils.chat_formatting import humanize_timedelta

from .abc import MixinMeta


class Slowmode(MixinMeta):
    """
    Commands regarding channel slowmode management.
    """

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(manage_channels=True)
    @checks.mod_or_permissions(manage_channels=True)
    async def slowmode(
        self,
        ctx,
        *,
        interval: commands.TimedeltaConverter(
            minimum=timedelta(seconds=0), maximum=timedelta(hours=6), default_unit="seconds"
        ) = timedelta(seconds=0),
    ):
        """Changes channel's slowmode setting.

        Interval can be anything from 0 seconds to 6 hours.
        Use without parameters to disable.
        """
        seconds = interval.total_seconds()
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds > 0:
            await ctx.send(
                ("Slowmode interval is now {interval}.").format(
                    interval=humanize_timedelta(timedelta=interval)
                )
            )
        else:
            await ctx.send(("Slowmode has been disabled."))
