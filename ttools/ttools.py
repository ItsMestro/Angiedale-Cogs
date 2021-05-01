import asyncio
import logging
import os
from osu.tools import del_message
import re
from datetime import datetime, timezone
from random import choice
from typing import Optional

import discord
import gspread
from PIL import Image, ImageDraw, ImageFont
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path, cog_data_path
from redbot.core.utils.chat_formatting import humanize_number, humanize_timedelta
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate

log = logging.getLogger("red.angiedale.ttools")

pingphrase1 = [
    "Get yourselves ready.",
    "I hope you're warmed up.",
    "You better be prepared.",
    "The time has come.",
    "I hope you're ready.",
    "You better be ready.",
]

pingphrase2 = [
    "You have a match coming up in",
    "You will face each other in",
    "Your match starts in",
    "The time of your match is in",
    "You'll be playing in",
]


class TTools(commands.Cog):
    """Tools for osu! Tournaments."""

    def __init__(self, bot: Red):
        self.bot = bot

        self.config: Config = Config.get_conf(
            self, identifier=1387000, cog_name="TTools", force_registration=True
        )
        guild_defaults = {
            "sheet": None,
            "enabled": False,
            "regsopen": False,
            "mode": "osu",
            "referee": None,
            "useimg": False,
            "customimg": False,
            "playerrole": None,
        }
        self.config.register_guild(**guild_defaults)

        self.gs = gspread.service_account(
            filename=f"{bundled_data_path(self)}/key.json"
        )

    async def red_delete_data_for_user(self, **kwargs):
        """ Nothing to delete """
        return

    @commands.group(hidden=True)
    @commands.guild_only()
    @checks.guildowner()
    async def ttools(self, ctx: commands.Context):
        """"""
        pass

    @ttools.command()
    @checks.is_owner()
    async def toggleserver(self, ctx: commands.Context):
        """"""
        enabled = await self.config.guild(ctx.guild).enabled()
        enabled = not enabled
        await self.config.guild(ctx.guild).enabled.set(enabled)
        if enabled:
            await ctx.send(("TTools enabled in this server."))
        else:
            await ctx.send(("TTools disable in this server."))

    @ttools.command()
    @checks.is_owner()
    async def sheet(self, ctx: commands.Context, key: str = None):
        """"""
        await ctx.message.delete()
        if key:
            await self.config.guild(ctx.guild).sheet.set(key)
            await ctx.send("Sheet key set.")
        else:
            await self.config.guild(ctx.guild).sheet.set(None)
            await ctx.send("Sheet key removed.")

    @ttools.command()
    @checks.is_owner()
    async def mode(self, ctx: commands.Context, mode: str):
        """"""
        mode = mode.lower()
        if mode == "osu" or mode == "taiko" or mode == "fruits" or mode == "mania":
            await self.config.guild(ctx.guild).mode.set(mode)
            await ctx.send(f"Mode for this tournament now set to `{mode}`")
        else:
            await ctx.send(
                "Invalid mode. Please use one of: `osu, taiko, fruits, mania`"
            )

    @ttools.command(name="referee")
    @checks.is_owner()
    async def set_referee_role(self, ctx: commands.Context, role: discord.Role):
        """"""
        if role:
            await self.config.guild(ctx.guild).referee.set(role.id)
            await ctx.send("Referee role set.")
        else:
            await self.config.guild(ctx.guild).referee.set(None)
            await ctx.send("Referee role removed.")

    @ttools.command()
    @checks.is_owner()
    async def useimage(self, ctx: commands.Context):
        """"""
        useimg = await self.config.guild(ctx.guild).useimg()
        useimg = not useimg
        await self.config.guild(ctx.guild).useimg.set(useimg)
        if useimg:
            await ctx.send("Referee pings will now use images.")
        else:
            await ctx.send("Referee pings will no longer use images.")

    @ttools.command()
    @checks.is_owner()
    async def setimage(self, ctx: commands.Context):
        """"""
        if len(ctx.message.attachments) == 1:
            img = ctx.message.attachments[0]
            if (
                not img.filename.lower().endswith(".png")
                and not img.filename.lower().endswith(".jpg")
                and not img.filename.lower().endswith(".jpeg")
            ):
                return await ctx.send("Please provide a `.png` or `.jpg` image.")
            if not img.width == 1600 and not img.height == 400:
                return await ctx.send("Please use an image that is `1600x400` in size.")
            with open(
                f"{cog_data_path(raw_name='TTools')}/{ctx.guild.id}.png", "wb"
            ) as f:
                await img.save(f)
            await ctx.send("Will now use the provided image for ref pings.")
            await self.config.guild(ctx.guild).customimg.set(True)
        else:
            await ctx.send("Reverting to using the default image for ref pings.")
            await self.config.guild(ctx.guild).customimg.set(False)
            try:
                os.remove(f"{cog_data_path(raw_name='TTools')}/{ctx.guild.id}.png")
            except:
                pass

    @ttools.command(aliases=["toggleregistration"])
    async def toggleregs(self, ctx: commands.Context):
        """"""
        regsopen = await self.config.guild(ctx.guild).regsopen()
        regsopen = not regsopen
        await self.config.guild(ctx.guild).regsopen.set(regsopen)
        if regsopen:
            await ctx.send(("Registrations are now open."))
        else:
            await ctx.send(("Registrations closed."))

    @ttools.command()
    async def playerrole(self, ctx: commands.Context, role: Optional[discord.Role]):
        """"""
        if role:
            await self.config.guild(ctx.guild).playerrole.set(role.id)
            await ctx.send(
                (f"Players will now be given the {role} role when registering.")
            )
        else:
            await self.config.guild(ctx.guild).playerrole.set(None)
            await ctx.send(("Cleared the player role in this server."))

    @commands.group(hidden=True, aliases=["ref"])
    @commands.guild_only()
    async def referee(self, ctx: commands.Context):
        """"""

    @referee.command()
    async def ping(self, ctx: commands.Context, matchid: str):
        """"""
        if not await self.isenabled(ctx):
            return
        if not await self.isref(ctx):
            return
        serverkey = await self.serverkey(ctx)
        if not serverkey:
            return

        sh = self.gs.open_by_key(serverkey)

        matches = sh.worksheet("Schedule").get("C6:I")

        matchexists = False
        for m in matches:
            if str(m[3]).lower() == matchid.lower():
                redteam = m[5]
                blueteam = m[6]
                matchtime = m[1].split(":")
                matchdate = datetime.strptime(m[0].split(", ")[1], "%d %b")
                matchdatetime = matchdate.replace(
                    year=datetime.utcnow().year,
                    hour=int(matchtime[0]),
                    minute=int(matchtime[1]),
                    tzinfo=timezone.utc,
                )
                matchexists = True
                break

        if not matchexists:
            return await ctx.send(f"Found no match with the id: `{matchid}`")

        teams = sh.worksheet("Signups").get("B2:F")
        reduserid = None
        blueuserid = None
        for p in teams:
            if reduserid and blueuserid:
                break
            if p[2] == redteam:
                redreserve = p[0]
                reduserid = p[1]
                redflag = p[4]
            elif p[2] == blueteam:
                bluereserve = p[0]
                blueuserid = p[1]
                blueflag = p[4]

        reduser = ctx.guild.get_member(int(reduserid))
        blueuser = ctx.guild.get_member(int(blueuserid))
        if reduser:
            redping = f"{reduser.mention} "
        else:
            redping = f"{redreserve} "
        if blueuser:
            blueping = f"{blueuser.mention} "
        else:
            blueping = f"{bluereserve} "

        time = matchdatetime - datetime.now(timezone.utc).replace(second=59)
        timestring = humanize_timedelta(timedelta=time)

        phrase1 = choice(pingphrase1)
        phrase2 = choice(pingphrase2)

        if timestring:
            msg = f"{redping}{blueping}{phrase1}\n{phrase2}: **{timestring}**"
        else:
            msg = f"{redping}{blueping}{phrase1}\nIt's time for you to face each other in match."

        if await self.pingimage(ctx):
            if await self.config.guild(ctx.guild).customimg():
                imgpath = f"{cog_data_path(raw_name='TTools')}/{ctx.guild.id}.png"
            else:
                imgpath = f"{bundled_data_path(self)}/image.png"
            img = Image.open(imgpath).convert("RGBA")
            imgred = Image.open(
                f"{bundled_data_path(self)}/flags/{redflag}.png", formats=["PNG"]
            ).convert("RGBA")
            imgblue = Image.open(
                f"{bundled_data_path(self)}/flags/{blueflag}.png", formats=["PNG"]
            ).convert("RGBA")
            imgred = imgred.resize((imgred.size[0] * 2, imgred.size[1] * 2))
            imgblue = imgblue.resize((imgblue.size[0] * 2, imgblue.size[1] * 2))

            width, height = img.size

            matchtimetext = matchdatetime.strftime("%A, %-d %b | %-H:%M")

            drawimage = ImageDraw.Draw(img)

            font = ImageFont.truetype(f"{bundled_data_path(self)}/Exo2.0-Bold.otf", 52)
            timefont = ImageFont.truetype(
                f"{bundled_data_path(self)}/Exo2.0-Bold.otf", 32
            )

            redwidth, redheight = drawimage.textsize(redteam, font)
            rx = (width / 2) / 2 - (redwidth / 2)
            bluewidth, blueheight = drawimage.textsize(blueteam, font)
            bx = (width / 2) / 2 - (bluewidth / 2) + (width / 2)
            timewidth, timeheight = drawimage.textsize(matchtimetext, timefont)
            tx = (width / 2) - (timewidth / 2)
            rfx = (width / 2) / 2 - (imgred.size[0] / 2)
            bfx = (width / 2) / 2 - (imgblue.size[0] / 2) + (width / 2)

            drawimage.text((rx, 220), redteam, font=font, fill=(255, 255, 255))
            drawimage.text((bx, 220), blueteam, font=font, fill=(255, 255, 255))
            drawimage.text(
                (tx, 320), matchtimetext, font=timefont, fill=(160, 160, 160)
            )

            img.paste(imgred, (int(rfx), 100), imgred)
            img.paste(imgblue, (int(bfx), 100), imgblue)

            if not os.path.exists(f"{cog_data_path(raw_name='TTools')}/ping"):
                os.makedirs(f"{cog_data_path(raw_name='TTools')}/ping")

            img.save(f"{cog_data_path(raw_name='TTools')}/ping/{matchid}.png")

            with open(
                f"{cog_data_path(raw_name='TTools')}/ping/{matchid}.png", "rb"
            ) as image:
                await ctx.send(
                    content=msg, file=discord.File(image, filename=f"{matchid}.png")
                )

            os.remove(f"{cog_data_path(raw_name='TTools')}/ping/{matchid}.png")
        else:
            await ctx.send(content=msg)

    @commands.max_concurrency(1, per=commands.BucketType.member)
    @commands.command(hidden=True)
    async def register(self, ctx: commands.Context, username: str = None):
        """"""
        if not await self.isenabled(ctx):
            return
        if not await self.config.guild(ctx.guild).regsopen():
            return
        serverkey = await self.serverkey(ctx)
        if not serverkey:
            return

        if not username:
            await ctx.message.delete()
            return await del_message(ctx, f"Provide a username or link with the command like: `{ctx.clean_prefix}register <username>`")

        sh = self.gs.open_by_key(serverkey)

        listofregs = sh.worksheet("Signups").get("C2:C")
        for team in listofregs:
            for r in team:
                if ctx.author.id == int(r):
                    await ctx.send(
                        "You are already registered. If you need to edit your registration, contact an organizer.",
                        delete_after=10,
                    )
                    return await ctx.message.delete()

        if "osu.ppy.sh" in username:
            username = re.sub("[^0-9]", "", username.rsplit("/", 1)[-1])
        mode = await self.config.guild(ctx.guild).mode()
        data = await self.useosufetch(f"users/{username}/{mode}")
        await ctx.message.delete()
        if not data:
            return await ctx.send(
                f"Tried looking for the user `{username}` but couldn't find them. Maybe try with your profile link instead.",
                delete_after=20,
            )

        embeds = []
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f"You'll be signing up as this user. Are you sure?",
            icon_url=f'https://osu.ppy.sh/images/flags/{data["country_code"]}.png',
        )
        embed.set_thumbnail(url=f'https://a.ppy.sh/{data["id"]}')
        embed.title = data["username"]
        try:
            if mode == "osu":
                cmode = "Standard"
            elif mode == "fruits":
                cmode = "Catch"
            else:
                cmode = mode.capitalize()
            embed.description = f'#{humanize_number(data["statistics"]["global_rank"])} ({humanize_number(data["statistics"]["pp"])}pp) in osu!{cmode}'
        except:
            pass
        titleurl = f'https://osu.ppy.sh/u/{data["id"]}'
        embed.url = titleurl
        embeds.append(embed)

        embedmsg: discord.Message = await ctx.send(embed=embed)
        start_adding_reactions(embedmsg, ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = ReactionPredicate.yes_or_no(embedmsg, ctx.author)
        try:
            await ctx.bot.wait_for("reaction_add", check=pred, timeout=20)
        except asyncio.TimeoutError:
            await embedmsg.clear_reactions()
            return await embedmsg.edit(content="Took too long to respond. Cancelling registration.", embed=None, delete_after=10)
        if not pred.result:
            await embedmsg.clear_reactions()
            return await embedmsg.edit(content="Cancelling registration.", embed=None, delete_after=10)

        existingteams = sh.worksheet("Signups").get("A2:A")
        rank4k = 0
        try:
            rank4k = data["statistics"]["variants"][0]["global_rank"]
        except:
            pass
        sh.worksheet("Signups").update(
            f"A{len(existingteams)+2}:G{len(existingteams)+2}",
            [
                [
                    data["id"],
                    f"{ctx.author.name}#{ctx.author.discriminator}",
                    str(ctx.author.id),
                    data["username"],
                    data["statistics"]["global_rank"],
                    data["country_code"],
                    rank4k,
                ]
            ],
        )

        await embedmsg.delete()
        player_role = await self.config.guild(ctx.guild).playerrole()
        if player_role:
            try:
                ctx.author.add_roles(
                    player_role,
                    reason=f'Registered to the tournament as {data["username"]}.',
                )
            except:
                pass
        await ctx.send(
            f'{ctx.author.mention} is now registered to the tournament as `{data["username"]}`'
        )

    async def useosufetch(self, api: str):
        osucog = self.bot.get_cog("Osu")
        if osucog:
            return await osucog.fetch_api(api)
        else:
            log.error("Osu cog not loaded")

    async def serverkey(self, ctx: commands.Context):
        return await self.config.guild(ctx.guild).sheet()

    async def isenabled(self, ctx: commands.Context):
        return await self.config.guild(ctx.guild).enabled()

    async def isref(self, ctx: commands.Context):
        referees = await self.config.guild(ctx.guild).referee()
        refrole = ctx.guild.get_role(referees)
        if refrole in ctx.author.roles:
            return True
        else:
            return False

    async def pingimage(self, ctx: commands.Context):
        return await self.config.guild(ctx.guild).useimg()