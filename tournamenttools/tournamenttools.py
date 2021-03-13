import discord
import logging
import gspread

from redbot.core import commands, Config

from redbot.core.bot import Red
from redbot.core.utils.menus import menu
from redbot.core.data_manager import bundled_data_path

log = logging.getLogger("red.angiedale.tournamenttools")


class TournamentTools(commands.Cog):
    """Tools for osu! Tournaments.
    """

    def __init__(self, bot: Red):
        self.bot = bot

        self.config: Config = Config.get_conf(self, identifier=1387000, cog_name="TTools", force_registration=True)

        self.gs = gspread.service_account(filename=f"{bundled_data_path(self)}/key.json")

    async def red_delete_data_for_user(self, **kwargs):
        """ Nothing to delete """
        return

    @commands.command()
    async def register(self, ctx, player):

        data = await self.useosufetch(f"users/{player}")
        embeds = []
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f'Is this you?',
            icon_url=f'https://osu.ppy.sh/images/flags/{data["country_code"]}.png'
        )
        embed.set_thumbnail(url=f'https://a.ppy.sh/{data["id"]}')
        embed.title = data["username"]
        embed.url = f'https://osu.ppy.sh/u/{data["id"]}'
        embeds.append(embed)

        async def failedregistration(*args, **kwargs):
            await ctx.send("Registration Failed")

        async def processregistration(*args, **kwargs):
            serverkey = 
            sh = self.gs.open_by_key("1U8byQILpyGrCNqkRN9Tq7t8Rg2cowROudvIRzpl-vuE")
            listofregs = sh.worksheet("Sheet1").get("A:A")
            log.error(listofregs)
            sh.worksheet("Sheet1").update(f"A{len(listofregs)+1}", data["username"])
            sh.worksheet("Sheet1").update(f"B{len(listofregs)+1}", f"{ctx.author.name}#{ctx.author.discriminator}")


        await menu(ctx, embeds, {"\N{WHITE HEAVY CHECK MARK}": processregistration, "\N{CROSS MARK}": failedregistration})

        # sh = self.gs.open_by_key("1U8byQILpyGrCNqkRN9Tq7t8Rg2cowROudvIRzpl-vuE")

        # await ctx.send(sh.worksheet("Sheet1").get("A:Q"))

    async def useosufetch(self, api):
        osucog = self.bot.get_cog("Osu")
        if osucog:
            return await osucog.fetch_api(api)
        else:
            log.error("Osu cog not loaded")

    def serverkey(self, ctx):


    # async def failedregistration(self,
    #     ctx: commands.Context,
    #     pages: list,
    #     controls: dict,
    #     message: discord.Message,
    #     page: int,
    #     timeout: float,
    #     emoji: str,
    # ):
    #     await ctx.send("Registration Failed")

    # async def processregistration(self,
    #     ctx: commands.Context,
    #     pages: list,
    #     controls: dict,
    #     message: discord.Message,
    #     page: int,
    #     timeout: float,
    #     emoji: str,
    #     user,
    # ):
    #     sh = self.gs.open_by_key("1U8byQILpyGrCNqkRN9Tq7t8Rg2cowROudvIRzpl-vuE")
    #     sh.worksheet("Sheet1").update("A1", user)
    #     sh.worksheet("Sheet1").update("B1", ctx.author.id)