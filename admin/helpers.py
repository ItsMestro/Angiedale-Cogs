import asyncio

from redbot.core import commands
from redbot.core.commands.requires import PrivilegeLevel
from redbot.core.utils.predicates import MessagePredicate


def get_command_from_input(bot, userinput: str):
    com = None
    orig = userinput
    while com is None:
        com = bot.get_command(userinput)
        if com is None:
            userinput = " ".join(userinput.split(" ")[:-1])
        if len(userinput) == 0:
            break
    if com is None:
        return None, ("I could not find a command from that input!")

    if com.requires.privilege_level >= PrivilegeLevel.BOT_OWNER:
        return (
            None,
            ("That command requires bot owner. I can't allow you to use that for an action"),
        )
    return "{prefix}" + orig, None


async def get_command_for_exceeded_points(ctx: commands.Context):
    """Gets the command to be executed when the user is at or exceeding
    the points threshold for the action"""
    await ctx.send(
        (
            "Enter the command to be run when the user **exceeds the points for "
            "this action to occur.**\n**If you do not wish to have a command run, enter** "
            "`none`.\n\nEnter it exactly as you would if you were "
            "actually trying to run the command, except don't put a prefix and "
            "use `{user}` in place of any user/member arguments\n\n"
            "WARNING: The command entered will be run without regard to checks or cooldowns. "
            "Commands requiring bot owner are not allowed for security reasons.\n\n"
            "Please wait 15 seconds before entering your response."
        )
    )
    await asyncio.sleep(15)

    await ctx.send(("You may enter your response now."))

    try:
        msg = await ctx.bot.wait_for(
            "message", check=MessagePredicate.same_context(ctx), timeout=30
        )
    except asyncio.TimeoutError:
        return None
    else:
        if msg.content == "none":
            return None

    command, m = get_command_from_input(ctx.bot, msg.content)
    if command is None:
        await ctx.send(m)
        return None

    return command


async def get_command_for_dropping_points(ctx: commands.Context):
    """
    Gets the command to be executed when the user drops below the points
    threshold

    This is intended to be used for reversal of the action that was executed
    when the user exceeded the threshold
    """
    await ctx.send(
        (
            "Enter the command to be run when the user **returns to a value below "
            "the points for this action to occur.** Please note that this is "
            "intended to be used for reversal of the action taken when the user "
            "exceeded the action's point value.\n**If you do not wish to have a command run "
            "on dropping points, enter** `none`.\n\nEnter it exactly as you would "
            "if you were actually trying to run the command, except don't put a prefix "
            "and use `{user}` in place of any user/member arguments\n\n"
            "WARNING: The command entered will be run without regard to checks or cooldowns. "
            "Commands requiring bot owner are not allowed for security reasons.\n\n"
            "Please wait 15 seconds before entering your response."
        )
    )
    await asyncio.sleep(15)

    await ctx.send(("You may enter your response now."))

    try:
        msg = await ctx.bot.wait_for(
            "message", check=MessagePredicate.same_context(ctx), timeout=30
        )
    except asyncio.TimeoutError:
        return None
    else:
        if msg.content == "none":
            return None
    command, m = get_command_from_input(ctx.bot, msg.content)
    if command is None:
        await ctx.send(m)
        return None

    return command
