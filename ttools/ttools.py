import asyncio
import logging
import os
from osu.tools import del_message
import re
from datetime import datetime, timezone, timedelta
from random import choice
from typing import Optional, Union

import discord
import gspread
from PIL import Image, ImageDraw, ImageFont
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path, cog_data_path
from redbot.core.utils.chat_formatting import humanize_number, humanize_timedelta
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate, MessagePredicate

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
        self.listenchannels = {}
        self.listenlock = set()

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
            "teamsize": 1,
            "regcategory": None,
            "regchannel": None,
        }
        member_defaults = {"regchannel": None}
        global_defaults = {"regchannels": {}}
        channel_defaults = {"message": None, "teamname": None, "players": []}
        self.config.register_guild(**guild_defaults)
        self.config.register_member(**member_defaults)
        self.config.register_global(**global_defaults)
        self.config.register_channel(**channel_defaults)

        self.gs = gspread.service_account(filename=f"{bundled_data_path(self)}/key.json")

        self.initialize_task: asyncio.Task = self.bot.loop.create_task(self.initialize())

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    async def initialize(self):
        await self.bot.wait_until_ready()
        await self._update_listenchannels()

    async def _update_listenchannels(self):
        async with self.config.regchannels() as channels:
            bad_channels = []
            for ch, u in channels.items():
                channel = self.bot.get_channel(int(ch))
                if not channel:
                    bad_channels.append(ch)
                    continue
                self.listenchannels[ch] = u

            for c in bad_channels:
                await self.config.channel_from_id(c).clear()
                channels.pop(str(c))

    @commands.group(hidden=True)
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def ttools(self, ctx: commands.Context):
        """"""
        pass

    @ttools.group()
    @commands.guild_only()
    @checks.is_owner()
    async def debug(self, ctx: commands.Context):
        """"""
        pass

    @debug.command()
    async def listenchs(self, ctx: commands.Context):
        """"""
        text = f"Listening to {len(self.listenchannels)} channels"
        for ch, u in self.listenchannels.items():
            text += f"\n{ch}: {u}"
        await ctx.send(text)

    @debug.command()
    async def regchs(self, ctx: commands.Context):
        """"""
        await ctx.send(await self.config.regchannels())

    @debug.command()
    async def clearreg(
        self, ctx: commands.Context, member_or_channel: Union[discord.Member, discord.TextChannel]
    ):
        """"""
        if isinstance(member_or_channel, discord.Member):
            member_data = await self.config.member(member_or_channel).regchannel()
            if member_data:
                channel = ctx.guild.get_channel(member_data)
                await self.config.channel_from_id(member_data).clear()
                await self.config.member(member_or_channel).clear()
                if channel:
                    async with self.config.regchannels() as channels:
                        channels.pop(str(channel.id))
                    self.listenchannels.pop(str(channel.id))
                    await channel.delete()
                await ctx.send("Cleared member.")
        else:
            channel_data = await self.config.channel(member_or_channel).all()
            if channel_data:
                try:
                    await self.config.member_from_ids(
                        guild_id=ctx.guild.id, member_id=channel_data["players"][0]["discord"]
                    ).clear()
                except:
                    pass
                await self.config.channel(member_or_channel).clear()
                try:
                    async with self.config.regchannels() as channels:
                        channels.pop(str(member_or_channel.id))
                    self.listenchannels.pop(str(member_or_channel.id))
                except:
                    pass
                await member_or_channel.delete()
                await ctx.send("Cleared channel.")

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
    async def resetserver(self, ctx: commands.Context):
        """"""
        can_react = ctx.channel.permissions_for(ctx.me).add_reactions
        msg: discord.Message = await ctx.send(
            (f"This will reset all TTools settings in this server. Are you sure?")
        )

        if can_react:
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            event = "reaction_add"
        else:
            pred = MessagePredicate.yes_or_no(ctx)
            event = "message"
        try:
            await ctx.bot.wait_for(event, check=pred, timeout=30)
        except asyncio.TimeoutError:
            await msg.delete()
        if not pred.result:
            return await msg.delete()

        guild_settings = await self.config.guild(ctx.guild).all()
        registration_category: discord.CategoryChannel = ctx.guild.get_channel(
            guild_settings["regcategory"]
        )
        for ch in registration_category.channels:
            await ch.delete()
        await registration_category.delete()

        await self.config.guild(ctx.guild).clear()
        await self.config.clear_all_members(ctx.guild)
        await ctx.send("Cleared server settings.")
        await self.settings(ctx)

    @ttools.command()
    @checks.is_owner()
    async def settings(self, ctx: commands.Context):
        """"""
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_author(name=f"TTools settings for {ctx.guild.name}", icon_url=ctx.guild.icon_url)

        guild_settings = await self.config.guild(ctx.guild).all()

        embed.title = f"Enabled in server: {guild_settings['enabled']}"

        text = f"Sheet: {'Set' if guild_settings['sheet'] else '**None**'}"
        text += f"\nMode: {guild_settings['mode']}"
        text += f"\nReferee Role: {ctx.guild.get_role(guild_settings['referee']).mention if guild_settings['referee'] else '**None**'}"
        text += f"\nUse Ping Image: {guild_settings['useimg']}"
        text += f"\nCustom Image Set: {'True' if guild_settings['customimg'] else 'False'}"
        text += f"\nPlayer Role: {ctx.guild.get_role(guild_settings['playerrole']).mention if guild_settings['playerrole'] else '**None**'}"
        text += f"\nTeam Size: {guild_settings['teamsize']}"
        text += f"\nRegistration Category: {ctx.guild.get_channel(guild_settings['regcategory']).mention if guild_settings['regcategory'] else '**None**'}"
        text += f"\nRegistration Channel: {ctx.guild.get_channel(guild_settings['regchannel']).mention if guild_settings['regchannel'] else '**None**'}"

        embed.description = text

        await ctx.send(embed=embed)

    @ttools.command()
    @checks.is_owner()
    async def setup(self, ctx: commands.Context):
        """"""
        if not ctx.guild.me.guild_permissions.manage_channels:
            return await ctx.send(
                'I need "Manage Channels" permission to set up registration channels.'
            )
        userperms = discord.PermissionOverwrite()
        adminperms = discord.PermissionOverwrite()
        userperms.view_channel = False
        userperms.send_messages = True
        userperms.read_message_history = True
        adminperms.view_channel = True
        adminperms.send_messages = True

        overwrites = {ctx.guild.default_role: userperms}
        overwrites[ctx.guild.me] = adminperms
        for ar in await self.bot.get_admin_roles(ctx.guild):
            overwrites[ar] = adminperms

        category_channel: discord.CategoryChannel = await ctx.guild.create_category(
            name="Registration Channels", overwrites=overwrites
        )

        channel = await category_channel.create_text_channel(
            name="registration",
            overwrites=overwrites,
            topic=f"To begin your registration type {ctx.clean_prefix}register",
        )

        await self.config.guild(ctx.guild).regcategory.set(category_channel.id)
        await self.config.guild(ctx.guild).regchannel.set(channel.id)

        await self.settings(ctx)

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
            await ctx.send("Invalid mode. Please use one of: `osu, taiko, fruits, mania`")

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
            with open(f"{cog_data_path(raw_name='TTools')}/{ctx.guild.id}.png", "wb") as f:
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

    @ttools.command()
    @checks.is_owner()
    async def setteamsize(self, ctx: commands.Context, teamsize: int):
        """"""
        if teamsize < 1 or teamsize > 4:
            return await ctx.send("Teamsize has to be between 1 and 4 players")

        await self.config.guild(ctx.guild).teamsize.set(teamsize)
        await ctx.send(f"Teamsize for tourney now set to {teamsize}")

    @ttools.command()
    @checks.is_owner()
    async def setregcategory(self, ctx: commands.Context, category: discord.CategoryChannel):
        """"""
        await self.config.guild(ctx.guild).regcategory.set(category.id)

        await ctx.send("Registration category updated.")

    @ttools.command()
    @checks.is_owner()
    async def setregchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """"""
        await self.config.guild(ctx.guild).regchannel.set(channel.id)

        await ctx.send("Registration channel updated.")

    @ttools.command(aliases=["toggleregistration"])
    async def toggleregs(self, ctx: commands.Context):
        """"""
        guild_settings = await self.config.guild(ctx.guild).all()

        if not guild_settings["enabled"]:
            return await ctx.send("TTools not enabled in the server.")
        if not guild_settings["sheet"]:
            return await ctx.send("There is no sheet key set in the server.")
        if not guild_settings["playerrole"]:
            return await ctx.send("There is no player role set in the server.")
        if not guild_settings["regcategory"]:
            return await ctx.send("There is no registration category set in the server.")
        if not guild_settings["regchannel"]:
            return await ctx.send("There is no registration channel set in the server.")

        player_role = ctx.guild.get_role(guild_settings["playerrole"])
        if not ctx.guild.me.guild_permissions.manage_roles or player_role >= ctx.guild.me.top_role:
            return await ctx.send(
                'I either don\'t have "Manage Roles" permission or the player role is above mine.'
            )

        regsopen = guild_settings["regsopen"]
        regsopen = not regsopen
        await self.config.guild(ctx.guild).regsopen.set(regsopen)

        perms = discord.PermissionOverwrite()
        perms.send_messages = True
        perms.read_message_history = True
        registration_channel = ctx.guild.get_channel(guild_settings["regchannel"])
        if regsopen:
            perms.view_channel = True
            await registration_channel.set_permissions(ctx.guild.default_role, overwrite=perms)
            await ctx.send(("Registrations are now open."))
        else:
            perms.view_channel = False
            await registration_channel.set_permissions(ctx.guild.default_role, overwrite=perms)
            await ctx.send(("Registrations closed."))

    @ttools.command()
    @checks.is_owner()
    async def playerrole(self, ctx: commands.Context, role: Optional[discord.Role]):
        """"""
        if role:
            await self.config.guild(ctx.guild).playerrole.set(role.id)
            await ctx.send((f"Players will now be given the {role} role when registering."))
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
            timefont = ImageFont.truetype(f"{bundled_data_path(self)}/Exo2.0-Bold.otf", 32)

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
            drawimage.text((tx, 320), matchtimetext, font=timefont, fill=(160, 160, 160))

            img.paste(imgred, (int(rfx), 100), imgred)
            img.paste(imgblue, (int(bfx), 100), imgblue)

            if not os.path.exists(f"{cog_data_path(raw_name='TTools')}/ping"):
                os.makedirs(f"{cog_data_path(raw_name='TTools')}/ping")

            img.save(f"{cog_data_path(raw_name='TTools')}/ping/{matchid}.png")

            with open(f"{cog_data_path(raw_name='TTools')}/ping/{matchid}.png", "rb") as image:
                await ctx.send(content=msg, file=discord.File(image, filename=f"{matchid}.png"))

            os.remove(f"{cog_data_path(raw_name='TTools')}/ping/{matchid}.png")
        else:
            await ctx.send(content=msg)

    @commands.max_concurrency(1, per=commands.BucketType.member)
    @commands.guild_only()
    @commands.command(hidden=True)
    async def register(self, ctx: commands.Context):
        """"""
        if not await self.isenabled(ctx):
            return
        if not await self.config.guild(ctx.guild).regsopen():
            return
        serverkey = await self.serverkey(ctx)
        if not serverkey:
            return

        player_reg_channel = await self.config.member(ctx.author).regchannel()

        if player_reg_channel:
            regchannel = ctx.guild.get_channel(player_reg_channel)
            if regchannel:
                return await del_message(
                    ctx,
                    f"You already have an ongoing registration to the tourney in {regchannel.mention}",
                )

        player_roleid = await self.config.guild(ctx.guild).playerrole()
        player_role = ctx.guild.get_role(player_roleid)
        if player_role in ctx.author.roles:
            return await del_message(ctx, "You're already signed up for the tournament.")

        regcategory = ctx.guild.get_channel(await self.regcategory(ctx))

        perms = discord.PermissionOverwrite()
        defaultperms = discord.PermissionOverwrite()
        defaultperms.view_channel = False
        perms.view_channel = True
        perms.send_messages = True
        perms.read_message_history = True

        permusers = {}
        permusers[ctx.author] = perms
        permusers[ctx.guild.me] = perms
        permusers[ctx.guild.default_role] = defaultperms
        for ar in await self.bot.get_admin_roles(ctx.guild):
           permusers[ar] = perms

        regchannel = await regcategory.create_text_channel(
            name=f"{ctx.author.name}",
            overwrites=permusers,
            reason=f"{ctx.author.name} registration",
        )
        await self.config.member(ctx.author).regchannel.set(regchannel.id)
        async with self.config.regchannels() as rc:
            rc[regchannel.id] = ctx.author.id
        self.listenchannels[str(regchannel.id)] = ctx.author.id

        teamsize = await self.teamsize(ctx)

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(name=f"{ctx.author.name} registration", icon_url=ctx.guild.icon_url)

        embed.description = (
            f"Welcome to your registration!\n\n"
            f"This embed will show your current registration.\n"
            f"Once you're happy with it, type `{ctx.clean_prefix}submitreg` to finish signing up!"
        )

        if teamsize == 1:
            embed.add_field(name="Discord", value=ctx.author.mention, inline=True)
        else:
            embed.add_field(name="P1 Discord", value=ctx.author.mention, inline=True)

        embed.set_footer(
            text=f"If something breaks or you need help. Type {ctx.clean_prefix}reghelp"
        )

        embedmsg = await regchannel.send(embed=embed)

        players = [{"discord": ctx.author.id}]
        while not len(players) == teamsize:
            players.append({})

        await self.config.channel(regchannel).message.set(embedmsg.id)
        await self.config.channel(regchannel).players.set(players)

        if teamsize == 1:
            await regchannel.send(
                f"{ctx.author.mention} to get started, type `{ctx.clean_prefix}player <username or profile link>` to add your profile to the registration."
            )
        elif teamsize == 2:
            await regchannel.send(
                (
                    f"{ctx.author.mention} to get started, type `{ctx.clean_prefix}player1 <username or profile link>` to add your profile to the registration.\n"
                    f"Afterwards you add your teammate with `player2`"
                )
            )
        await del_message(
            ctx,
            f"A channel has been created for you to complete your registration. {regchannel.mention}",
            30,
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

    async def teamsize(self, ctx: commands.Context):
        return await self.config.guild(ctx.guild).teamsize()

    async def regcategory(self, ctx: commands.Context):
        return await self.config.guild(ctx.guild).regcategory()

    async def isref(self, ctx: commands.Context):
        referees = await self.config.guild(ctx.guild).referee()
        refrole = ctx.guild.get_role(referees)
        if refrole in ctx.author.roles:
            return True
        else:
            return False

    async def pingimage(self, ctx: commands.Context):
        return await self.config.guild(ctx.guild).useimg()

    async def prepare_user(
        self, ctx: commands.Context, embed_msg: discord.Message, mode: str, username: str
    ):
        marked_msgs = []
        async for msg in ctx.history(before=ctx.message, after=embed_msg):
            marked_msgs.append(msg)
        await ctx.channel.delete_messages(marked_msgs)

        return await self.request_user(ctx, username, mode)

    async def prepare_member(
        self, ctx: commands.Context, embed_msg: discord.Message, member: discord.Member
    ):
        marked_msgs = []
        async for msg in ctx.history(before=ctx.message, after=embed_msg):
            marked_msgs.append(msg)
        await ctx.channel.delete_messages(marked_msgs)

        return await self.request_member(ctx, member)

    async def prepare_channel(self, ctx: commands.Context, embed_msg: discord.Message):
        marked_msgs = []
        manual_marked_msgs = []
        two_weeks_ago = datetime.utcnow() - timedelta(days=14, minutes=-5)

        async for msg in ctx.history(before=ctx.message, after=embed_msg):
            if msg.created_at < two_weeks_ago:
                manual_marked_msgs.append(msg)
            else:
                marked_msgs.append(msg)

        if len(marked_msgs) > 0:
            await ctx.channel.delete_messages(marked_msgs)
        if len(manual_marked_msgs) > 0:
            for message in manual_marked_msgs:
                await message.delete()
                await asyncio.sleep(0.5)

    async def request_user(self, ctx: commands.Context, user: str, mode: str):
        if "osu.ppy.sh" in user:
            user = re.sub("[^0-9]", "", user.rsplit("/", 1)[-1])
        data = await self.useosufetch(f"users/{user}/{mode}")
        if not data:
            self.listenlock.discard(ctx.channel.id)
            await ctx.channel.send(
                "Couldn't find a player with that username or ID. Make sure it's correct and try again",
                delete_after=10,
            )
            return None, None

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx.channel))
        embed.set_author(
            name=f"Is this the correct player?",
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

        embedmsg: discord.Message = await ctx.channel.send(embed=embed)
        start_adding_reactions(embedmsg, ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = ReactionPredicate.yes_or_no(embedmsg, ctx.author)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=20)
        except asyncio.TimeoutError:
            self.listenlock.discard(ctx.channel.id)
            await embedmsg.clear_reactions()
            await embedmsg.edit(
                content="Took too long to respond. Try again.", embed=None, delete_after=10
            )
            return None, None
        if not pred.result:
            self.listenlock.discard(ctx.channel.id)
            await embedmsg.delete()
            return None, None

        await embedmsg.delete()

        return data["username"], data["id"]

    async def request_member(self, ctx: commands.Context, member: discord.Member):
        question = f"{member.mention}\n\nIs this the correct discord user?"

        msg: discord.Message = await ctx.channel.send(question)
        start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = ReactionPredicate.yes_or_no(msg, ctx.author)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=20)
        except asyncio.TimeoutError:
            self.listenlock.discard(ctx.channel.id)
            await msg.clear_reactions()
            await msg.edit(
                content="Took too long to respond. Try again.", embed=None, delete_after=10
            )
            return False
        if not pred.result:
            self.listenlock.discard(ctx.channel.id)
            await msg.delete()
            return False

        return True

    async def set_registration(
        self,
        embed_msg: discord.Message,
        player: int,
        name: str = None,
        id: str = None,
        member: discord.Member = None,
    ):
        embed = embed_msg.embeds[0]
        if member:
            i = 0
            for embed_field in embed.fields:
                if embed_field.name.startswith(f"P{player}"):
                    embed.set_field_at(
                        i, name=f"P{player} Discord", value=member.mention, inline=True
                    )
                    return embed
                i += 1
            i = 0
            for embed_field in embed.fields:
                if embed_field.name.endswith(f"{player} ID") and len(embed.fields) > i + 1:
                    embed.insert_field_at(
                        i + 1, name=f"P{player} Discord", value=member.mention, inline=True
                    )
                    return embed
                i += 1
            embed.add_field(name=f"P{player} Discord", value=member.mention, inline=True)
        else:
            if player == 0:
                if len(embed.fields) == 3:
                    embed.set_field_at(0, name="Player", value=name, inline=True)
                    embed.set_field_at(1, name="Player ID", value=id, inline=True)
                else:
                    embed.insert_field_at(0, name="Player ID", value=id, inline=True)
                    embed.insert_field_at(0, name="Player", value=name, inline=True)
            else:
                i = 0
                for embed_field in embed.fields:
                    if embed_field.name.endswith(str(player)):
                        embed.set_field_at(i, name=f"Player {player}", value=name, inline=True)
                        embed.set_field_at(
                            i + 1, name=f"Player {player} ID", value=id, inline=True
                        )
                        return embed
                    elif embed_field.name.startswith(f"P{player}"):
                        embed.insert_field_at(
                            i, name=f"Player {player} ID", value=id, inline=True
                        )
                        embed.insert_field_at(
                            i, name=f"Player {player}", value=name, inline=True
                        )
                        return embed
                    i += 1
                
                if embed.fields[-1].name.startswith("Player"):
                    new_player = int(embed.fields[-1].name[-4]) + 1
                else:
                    new_player = int(embed.fields[-1].name[1]) + 1
                embed.add_field(name=f"Player {new_player}", value=name, inline=True)
                embed.add_field(name=f"Player {new_player} ID", value=id, inline=True)

        return embed

    def lockcheck(self, ctx: commands.Context):
        if not str(ctx.channel.id) in self.listenchannels:
            return True
        if not self.listenchannels[str(ctx.channel.id)] == ctx.author.id:
            return True
        if ctx.channel.id in self.listenlock:
            return True

    def response_string(self, ctx: commands.Context, reg_status: int, end_string: str):
        end_string
        if reg_status == 0:
            end_string += (
                "\nIf everything looks good with the registration, type `-submitreg` to sign up."
            )
        elif reg_status == 1:
            end_string += (
                f"\nAdd your teammate with `{ctx.clean_prefix}player2 <username or profile link>`"
            )
        elif reg_status == 2:
            end_string += (
                f"\nSet your teammates discord with `{ctx.clean_prefix}discord2 <username or id>`"
            )
        elif reg_status == 3:
            end_string += f"\nNow give your team a name with `{ctx.clean_prefix}teamname <name>`"

        return end_string

    @commands.guild_only()
    @commands.command(hidden=True, usage="<username or profile link>")
    async def player(self, ctx: commands.Context, *, username=None):
        """"""
        if self.lockcheck(ctx):
            return

        self.listenlock.add(ctx.channel.id)

        guild_settings = await self.config.guild(ctx.guild).all()
        if not guild_settings["teamsize"] == 1:
            return self.listenlock.discard(ctx.channel.id)

        if not username:
            await ctx.send(
                f"You need to provide a username or profile link after the command! `{ctx.clean_prefix}player <username or profile link>`"
            )
            return self.listenlock.discard(ctx.channel.id)

        async with self.config.channel(ctx.channel).all() as channel_settings:
            embed_msg = await ctx.fetch_message(channel_settings["message"])

            player_name, player_id = await self.prepare_user(
                ctx, embed_msg, guild_settings["mode"], username
            )
            if not player_id:
                return

            channel_settings["players"][0]["id"] = player_id
            channel_settings["players"][0]["name"] = player_name
            new_embed = await self.set_registration(embed_msg, 0, name=player_name, id=player_id)
            await embed_msg.edit(embed=new_embed)

        self.listenlock.discard(ctx.channel.id)

        return await ctx.channel.send(
            f"Your profile has been added to the registration. If everything looks good with the registration, type `-submitreg` to sign up."
        )

    @commands.guild_only()
    @commands.command(hidden=True, usage="<username or profile link>")
    async def player1(self, ctx: commands.Context, *, username=None):
        """"""
        if self.lockcheck(ctx):
            return

        self.listenlock.add(ctx.channel.id)

        guild_settings = await self.config.guild(ctx.guild).all()
        if not guild_settings["teamsize"] > 1:
            return self.listenlock.discard(ctx.channel.id)

        if not username:
            await ctx.send(
                f"You need to provide a username or profile link after the command! `{ctx.clean_prefix}player <username or profile link>`"
            )
            return self.listenlock.discard(ctx.channel.id)

        async with self.config.channel(ctx.channel).all() as channel_settings:
            embed_msg = await ctx.fetch_message(channel_settings["message"])

            player_name, player_id = await self.prepare_user(
                ctx, embed_msg, guild_settings["mode"], username
            )
            if not player_id:
                return

            channel_settings["players"][0]["id"] = player_id
            channel_settings["players"][0]["name"] = player_name
            new_embed = await self.set_registration(
                embed_msg, 1, name=player_name, id=player_id
            )
            await embed_msg.edit(embed=new_embed)

            reg_status = 0
            for p in channel_settings["players"]:
                if len(p) <= 1:
                    reg_status = 1
                    break
            if reg_status == 0:
                for p in channel_settings["players"]:
                    if len(p) == 2:
                        reg_status = 2
                        break
            if reg_status == 0 and not channel_settings["teamname"]:
                reg_status = 3

        self.listenlock.discard(ctx.channel.id)

        text = "Player profile has been added to the registration."

        end_string = self.response_string(ctx, reg_status, text)

        return await ctx.channel.send(end_string)

    @commands.guild_only()
    @commands.command(hidden=True, usage="<username or profile link>")
    async def player2(self, ctx: commands.Context, *, username=None):
        """"""
        if self.lockcheck(ctx):
            return

        self.listenlock.add(ctx.channel.id)

        guild_settings = await self.config.guild(ctx.guild).all()
        if not guild_settings["teamsize"] >= 2:
            return self.listenlock.discard(ctx.channel.id)

        if not username:
            await ctx.send(
                f"You need to provide a username or profile link after the command! `{ctx.clean_prefix}player <username or profile link>`"
            )
            return self.listenlock.discard(ctx.channel.id)

        async with self.config.channel(ctx.channel).all() as channel_settings:
            embed_msg = await ctx.fetch_message(channel_settings["message"])

            player_name, player_id = await self.prepare_user(
                ctx, embed_msg, guild_settings["mode"], username
            )
            if not player_id:
                return

            channel_settings["players"][1]["id"] = player_id
            channel_settings["players"][1]["name"] = player_name
            new_embed = await self.set_registration(embed_msg, 2, name=player_name, id=player_id)
            await embed_msg.edit(embed=new_embed)

            reg_status = 0
            for p in channel_settings["players"]:
                if len(p) <= 1:
                    reg_status = 1
                    break
            if reg_status == 0:
                for p in channel_settings["players"]:
                    if len(p) == 2:
                        reg_status = 2
                        break
            if reg_status == 0 and not channel_settings["teamname"]:
                reg_status = 3

        self.listenlock.discard(ctx.channel.id)

        text = "Player profile has been added to the registration."

        end_string = self.response_string(ctx, reg_status, text)

        return await ctx.channel.send(end_string)

    @commands.guild_only()
    @commands.command(hidden=True, usage="<discord name or id>")
    async def discord2(self, ctx: commands.Context, *, discord: Optional[discord.Member] = None):
        """"""
        if self.lockcheck(ctx):
            return

        self.listenlock.add(ctx.channel.id)

        guild_settings = await self.config.guild(ctx.guild).all()
        if not guild_settings["teamsize"] >= 2:
            return self.listenlock.discard(ctx.channel.id)

        if not discord:
            await ctx.send(
                f"Couldn't find a user in the server by that name. Either use their nickname in this server or username (Name#xxxx)"
            )
            return self.listenlock.discard(ctx.channel.id)

        async with self.config.channel(ctx.channel).all() as channel_settings:
            embed_msg = await ctx.fetch_message(channel_settings["message"])

            result = await self.prepare_member(ctx, embed_msg, discord)
            if not result:
                return

            channel_settings["players"][1]["discord"] = discord.id
            new_embed = await self.set_registration(embed_msg, 2, member=discord)
            await embed_msg.edit(embed=new_embed)

            reg_status = 0
            for p in channel_settings["players"]:
                if len(p) <= 1:
                    reg_status = 1
                    break
            if reg_status == 0:
                for p in channel_settings["players"]:
                    if len(p) == 2:
                        reg_status = 2
                        break
            if reg_status == 0 and not channel_settings["teamname"]:
                reg_status = 3

            self.listenlock.discard(ctx.channel.id)

        text = "Discord user has been added to the registration."

        end_string = self.response_string(ctx, reg_status, text)

        return await ctx.channel.send(end_string)

    @commands.guild_only()
    @commands.command(hidden=True, usage="<teamname>")
    async def teamname(self, ctx: commands.Context, *, name: str):
        """"""
        if self.lockcheck(ctx):
            return

        guild_settings = await self.config.guild(ctx.guild).all()
        if not guild_settings["teamsize"] > 1:
            return

        if len(name) > 30:
            return await ctx.send("Name can't be longer than 30 characters")

        async with self.config.channel(ctx.channel).all() as channel_settings:
            embed_msg = await ctx.fetch_message(channel_settings["message"])

            await self.prepare_channel(ctx, embed_msg)

            embed = embed_msg.embeds[0]
            embed.title = name

            await embed_msg.edit(embed=embed)

            channel_settings["teamname"] = name

            reg_status = 0
            for p in channel_settings["players"]:
                if len(p) <= 1:
                    reg_status = 1
                    break
            if reg_status == 0:
                for p in channel_settings["players"]:
                    if len(p) == 2:
                        reg_status = 2
                        break
            if reg_status == 0 and not channel_settings["teamname"]:
                reg_status = 3

        text = "Team name has been set."

        end_string = self.response_string(ctx, reg_status, text)

        return await ctx.channel.send(end_string)

    @commands.guild_only()
    @commands.command(hidden=True)
    @commands.cooldown(rate=1, per=3600, type=commands.BucketType.member)
    async def reghelp(self, ctx: commands.Context):
        """"""
        if not str(ctx.channel.id) in self.listenchannels:
            return
        if not self.listenchannels[str(ctx.channel.id)] == ctx.author.id:
            return

        await self.bot.send_to_owners(f"[TTools] Help Request: {ctx.channel.mention}")
        return await ctx.channel.send(
            f"Request for help has been sent! Hold tight until help is available."
        )

    @commands.guild_only()
    @commands.command(hidden=True)
    async def submitreg(self, ctx: commands.Context):
        """"""
        if self.lockcheck(ctx):
            return

        guild_settings = await self.config.guild(ctx.guild).all()
        channel_settings = await self.config.channel(ctx.channel).all()

        if not len(channel_settings["players"]) == guild_settings["teamsize"]:
            return await ctx.send(
                f"You haven't added your teammate to the registration yet. Do so with `{ctx.clean_prefix}player2 <username or profile link>`"
            )
        i = 1
        for p in channel_settings["players"]:
            if len(p) == 1:
                if guild_settings["teamsize"] == 1:
                    return await ctx.send(
                        f"You need to add a profile to your registration. Do so with `{ctx.clean_prefix}player <username or profile link>`"
                    )
                return await ctx.send(
                    f"Player {i} doesn't have a profile added to their registration yet. Add one with `{ctx.clean_prefix}player{i} <username or profile link>`"
                )
            if len(p) == 2:
                return await ctx.send(
                    f"Player {i} is missing a discord profile. Add one with `{ctx.clean_prefix}discord{i} <username or id>`"
                )
            i += 1

        if guild_settings["teamsize"] > 1 and not channel_settings["teamname"]:
            return await ctx.send(
                f"Your team doesn't have a team name yet. Set one using `{ctx.clean_prefix}teamname <name>`"
            )
        
        processing_msg = await ctx.send("Processing...")

        serverkey = await self.serverkey(ctx)
        if not serverkey:
            return

        sh = self.gs.open_by_key(serverkey)

        if guild_settings["teamsize"] == 1:
            signup_discord_ids = sh.worksheet("Signups").get("R2:R", major_dimension="COLUMNS")
            signup_player_ids = sh.worksheet("Signups").get("B2:B", major_dimension="COLUMNS")
            if (
                str(channel_settings["players"][0]["discord"]) in signup_discord_ids[0]
                or str(channel_settings["players"][0]["id"]) in signup_player_ids[0]
            ):
                await self.bot.send_to_owners(
                    f"[TTools] already signed up error: {ctx.channel.mention}"
                )
                await processing_msg.delete()
                return await ctx.send(
                    f"You're already signed up to the tournament **somehow**. This is most likely a bug which I've reported for you. Hold tight and help will be here soon."
                )

        else:
            signup_teamnames = sh.worksheet("Signups").get("A2:A", major_dimension="COLUMNS")

            teamname = channel_settings["teamname"].lower()
            if len(signup_teamnames) == 0:
                signup_discord_ids = []
            else:
                for name in signup_teamnames[0]:
                    if teamname == name.lower():
                        await processing_msg.delete()
                        return await ctx.send(
                            f"Your teams name is already in use by another team. Set a new one using `{ctx.clean_prefix}teamname <name>`"
                        )

                signup_discord_ids = sh.worksheet("Signups").get("R2:Y")

                i = 1
                for p in channel_settings["players"]:
                    for t in signup_discord_ids:
                        if str(p["discord"]) in t:
                            await processing_msg.delete()
                            return await ctx.send(
                                f"Player {i} is already signed up for the tournament in a different team."
                            )
                    i += 1

                signup_player_ids = sh.worksheet("Signups").get("B2:I")

                i = 1
                for p in channel_settings["players"]:
                    for t in signup_discord_ids:
                        if str(p["id"]) in t:
                            await processing_msg.delete()
                            return await ctx.send(
                                f"Player {i} is already signed up for the tournament in a different team."
                            )
                    i += 1

        players = []
        for p in channel_settings["players"]:
            data = await self.useosufetch(f"users/{p['id']}/{guild_settings['mode']}")
            players.append(data)

        members = []
        for p in channel_settings["players"]:
            members.append(ctx.guild.get_member(p["discord"]))

        new_signup = []

        # Col A - Team Name
        if guild_settings["teamsize"] == 1:
            new_signup.append(str(players[0]["username"]))
        else:
            new_signup.append(str(channel_settings["teamname"]))

        # Col B:I - Player ID
        i = 0
        while i < 8:
            try:
                new_signup.append(str(players[i]["id"]))
            except IndexError:
                new_signup.append(None)
            i += 1
        # Col J:Q - Discord Name
        i = 0
        while i < 8:
            try:
                new_signup.append(str(f"{members[i].name}#{members[i].discriminator}"))
            except IndexError:
                new_signup.append(None)
            i += 1
        # Col R:Y - Discord ID
        i = 0
        while i < 8:
            try:
                new_signup.append(str(channel_settings["players"][i]["discord"]))
            except IndexError:
                new_signup.append(None)
            i += 1
        # Col Z:AG - Player Name
        i = 0
        while i < 8:
            try:
                new_signup.append(str(players[i]["username"]))
            except IndexError:
                new_signup.append(None)
            i += 1
        # Col AH:AO - Player Rank
        i = 0
        while i < 8:
            try:
                new_signup.append(str(players[i]["statistics"]["global_rank"]))
            except IndexError:
                new_signup.append(None)
            i += 1
        # Col AP:AW - Player Flag
        i = 0
        while i < 8:
            try:
                new_signup.append(players[i]["country_code"])
            except IndexError:
                new_signup.append(None)
            i += 1
        # Col AX:BE - Player 4k Mania Rank
        i = 0
        while i < 8:
            try:
                new_signup.append(str(players[i]["statistics"]["variants"][0]["global_rank"]))
            except IndexError:
                new_signup.append(None)
            i += 1

        sh.worksheet("Signups").insert_row(
            new_signup,
            index=len(
                signup_discord_ids if guild_settings["teamsize"] > 1 else signup_discord_ids[0]
            )
            + 2,
            value_input_option="RAW",
        )

        playerrole = ctx.guild.get_role(guild_settings["playerrole"])
        for m in members:
            await m.add_roles(playerrole, reason="Tournament Signup")

        marked_msgs = []
        async for msg in ctx.history(after=await ctx.fetch_message(channel_settings["message"])):
            marked_msgs.append(msg)
        await ctx.channel.delete_messages(marked_msgs)

        perms = discord.PermissionOverwrite()
        perms.view_channel = True
        perms.send_messages = False
        perms.read_message_history = True

        await ctx.channel.set_permissions(ctx.author, overwrite=perms)

        await ctx.send(
            "You've now been signed up to the tournament!\n\nThis channel will be deleted shortly. Good luck in your matches!"
        )

        await self.config.channel(ctx.channel).clear()
        await self.config.member(ctx.author).clear()
        async with self.config.regchannels() as channels:
            channels.pop(str(ctx.channel.id))
        self.listenchannels.pop(str(ctx.channel.id))

        await asyncio.sleep(30)

        await ctx.channel.delete()
