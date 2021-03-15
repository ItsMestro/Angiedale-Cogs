import logging
import re

import discord
import gspread
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path
from redbot.core.utils.chat_formatting import humanize_number
from redbot.core.utils.menus import menu

log = logging.getLogger("red.angiedale.tournamenttools")


class TournamentTools(commands.Cog):
    """Tools for osu! Tournaments.
    """

    def __init__(self, bot: Red):
        self.bot = bot

        self.config: Config = Config.get_conf(self, identifier=1387000, cog_name="TTools", force_registration=True)
        guild_defaults = {"sheet": None, "enabled": False, "regsopen": False, "mode": "osu"}
        self.config.register_guild(**guild_defaults)

        self.gs = gspread.service_account(filename=f"{bundled_data_path(self)}/key.json")

    async def red_delete_data_for_user(self, **kwargs):
        """ Nothing to delete """
        return

    @commands.group(hidden=True)
    @commands.guild_only()
    @checks.guildowner()
    async def ttools(self, ctx):
        """"""
        pass

    @ttools.command()
    @checks.is_owner()
    async def toggleserver(self, ctx):
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
    async def sheet(self, ctx, key = None):
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
    async def mode(self, ctx, mode: str):
        """"""
        mode = mode.lower()
        if mode == "osu" or mode == "taiko" or mode == "fruits" or mode == "mania":
            await self.config.guild(ctx.guild).mode.set(mode)
            await ctx.send(f"Mode for this tournament now set to `{mode}`")
        else:
            await ctx.send("Invalid mode. Please use one of: `osu, taiko, fruits, mania`")

    @ttools.command(aliases=["openregistration"])
    async def openregs(self, ctx):
        """"""
        regsopen = await self.config.guild(ctx.guild).regsopen()
        regsopen = not regsopen
        await self.config.guild(ctx.guild).regsopen.set(regsopen)
        if regsopen:
            await ctx.send(("Registrations are now open."))
        else:
            await ctx.send(("Registrations are now open."))

    @commands.max_concurrency(1, per=commands.BucketType.user)
    @commands.command(hidden=True)
    async def register(self, ctx, username):
        """"""
        if not await self.isenabled(ctx):
            return
        if not await self.config.guild(ctx.guild).regsopen():
            return
        serverkey = await self.serverkey(ctx)
        if not serverkey:
            return

        sh = self.gs.open_by_key(serverkey)

        listofregs = sh.worksheet("Signups").get("C2:C")
        for team in listofregs:
            for r in team:
                if ctx.author.id == int(r):
                    await ctx.message.delete()
                    return await ctx.send("You are already registered. If you need to edit your registration, contact an organizer.", delete_after=10)

        if "osu.ppy.sh" in username:
            username = re.sub("[^0-9]", "", username.rsplit('/', 1)[-1])
        mode = await self.config.guild(ctx.guild).mode()
        data = await self.useosufetch(f"users/{username}/{mode}")
        await ctx.message.delete()
        if not data:
            return await ctx.send(f"Tried looking for the user `{username}` but couldn't find them. Maybe try with your profile link instead.", delete_after=20)

        embeds = []
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f'Is this you?',
            icon_url=f'https://osu.ppy.sh/images/flags/{data["country_code"]}.png'
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

        done = False

        async def failedregistration(ctx, _, __, message, *args):
            nonlocal done
            done = True

            await message.clear_reactions()
            await message.edit(content="Registration Cancelled.", embed=None, delete_after=10)

        async def processregistration(ctx, _, __, message, *args):
            nonlocal done
            done = True

            existingteams = sh.worksheet("Signups").get("A2:A")
            rank4k = None
            try:
                rank4k = data["statistics"]["variants"][0]["global_rank"]
                sh.worksheet("Data").update(f"BD{len(existingteams)+2}", rank4k)
            except:
                pass
            sh.worksheet("Signups").update(f"A{len(existingteams)+2}:G{len(existingteams)+2}", [[data["id"], f"{ctx.author.name}#{ctx.author.discriminator}", str(ctx.author.id), data["username"], data["statistics"]["global_rank"], data["country_code"], rank4k]])

            await message.delete()
            await ctx.send(f'{ctx.author.mention} is now registered to the tournament as `{data["username"]}`')

        await menu(ctx, embeds, {"\N{WHITE HEAVY CHECK MARK}": processregistration, "\N{CROSS MARK}": failedregistration})

        if not done:
            async for m in ctx.channel.history(limit=20):
                if m.author.id == self.bot.user.id:
                    try:
                        if m.embeds[0].url == titleurl:
                            await m.delete()
                    except:
                        pass

    async def useosufetch(self, api):
        osucog = self.bot.get_cog("Osu")
        if osucog:
            return await osucog.fetch_api(api)
        else:
            log.error("Osu cog not loaded")

    async def serverkey(self, ctx):
        return await self.config.guild(ctx.guild).sheet()

    async def isenabled(self, ctx):
        return await self.config.guild(ctx.guild).enabled()
