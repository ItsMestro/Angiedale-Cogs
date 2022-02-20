from copy import copy

import discord
from redbot.core import Config, commands
from redbot.core.commands.requires import PrivilegeLevel


async def warning_points_add_check(
    config: Config, ctx: commands.Context, user: discord.Member, points: int
):
    """Handles any action that needs to be taken or not based on the points"""
    guild = ctx.guild
    guild_settings = config.guild(guild)
    act = {}
    async with guild_settings.actions() as registered_actions:
        for a in registered_actions:
            # Actions are sorted in decreasing order of points.
            # The first action we find where the user is above the threshold will be the
            # highest action we can take.
            if points >= a["points"]:
                act = a
                break
    if act and act["exceed_command"] is not None:  # some action needs to be taken
        await create_and_invoke_context(ctx, act["exceed_command"], user)


async def warning_points_remove_check(
    config: Config, ctx: commands.Context, user: discord.Member, points: int
):
    guild = ctx.guild
    guild_settings = config.guild(guild)
    act = {}
    async with guild_settings.actions() as registered_actions:
        for a in registered_actions:
            if points >= a["points"]:
                act = a
            else:
                break
    if act and act["drop_command"] is not None:  # some action needs to be taken
        await create_and_invoke_context(ctx, act["drop_command"], user)


async def create_and_invoke_context(
    realctx: commands.Context, command_str: str, user: discord.Member
):
    m = copy(realctx.message)
    m.content = command_str.format(user=user.mention, prefix=realctx.prefix)
    fctx = await realctx.bot.get_context(m, cls=commands.Context)
    try:
        await realctx.bot.invoke(fctx)
    except (commands.CheckFailure, commands.CommandOnCooldown):
        # reinvoke bypasses checks and we don't want to run bot owner only commands here
        privilege_level = fctx.command.requires.privilege_level
        if privilege_level is None or privilege_level < PrivilegeLevel.BOT_OWNER:
            await fctx.reinvoke()
