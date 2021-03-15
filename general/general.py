import asyncio
import datetime
import logging
import random
import time
import urllib.parse
from typing import Optional, Tuple, Union

import discord
from dateutil.relativedelta import relativedelta
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.commands import command
from redbot.core.utils.chat_formatting import (
    bold, box, escape, humanize_number, humanize_timedelta, pagify
)
from redbot.core.utils.common_filters import (
    escape_spoilers_and_mass_mentions, filter_invites, filter_mass_mentions
)
from redbot.core.utils.menus import close_menu, menu

from .converters import SelfRole
from .reports import Reports

log = logging.getLogger("red.angiedale.general")

KAOMOJI_JOY = [" (* ^ ω ^)", " (o^▽^o)", " (≧◡≦)", ' ☆⌒ヽ(*"､^*)chu', " ( ˘⌣˘)♡(˘⌣˘ )", " xD"]
KAOMOJI_EMBARRASSED = [" (⁄ ⁄>⁄ ▽ ⁄<⁄ ⁄)..", " (*^.^*)..,", "..,", ",,,", "... ", ".. ", " mmm..", "O.o"]
KAOMOJI_CONFUSE = [" (o_O)?", " (°ロ°) !?", " (ーー;)?", " owo?"]
KAOMOJI_SPARKLES = [" *:･ﾟ✧*:･ﾟ✧ ", " ☆*:・ﾟ ", "〜☆ ", " uguu.., ", "-.-"]

fur = {
        "ahh": "*murr*",
        "love": "wuv",
        "loves": "wuvs",
        "awesome": "pawsome",
        "awful": "pawful",
        "bite": "nom",
        "bites": "noms",
        "butthole": "tailhole",
        "buttholes": "tailholes",
        "bulge": "bulgy-wulgy",
        "bye": "bai",
        "celebrity": "popufur",
        "celebrities": "popufurs",
        "cheese": "sergal",
        "child": "cub",
        "children": "cubs",
        "computer": "protogen",
        "computers": "protogens",
        "confuse": "confuzzle",
        "confused": "confuzzled",
        "disease": "pathOwOgen",
        "dog": "good boy",
        "dogs": "good boys",
        "dragon": "derg",
        "dragons": "dergs",
        "eat": "vore",
        "everyone": "everyfur",
        "foot": "footpaw",
        "feet": "footpaws",
        "for": "fur",
        "fuck": "yiff",
        "fucking": "yiffing",
        "fucked": "yiffed",
        "hand": "paw",
        "hands": "paws",
        "hi": "hai",
        "human": "hyooman",
        "humans": "hyoomans",
        "hyena": "yeen",
        "hyenas": "yeens",
        "innocent": "furocent",
        "kiss": "lick",
        "kisses": "licks",
        "lmao": "hehe~",
        "masturbate": "paw off",
        "mouth": "maw",
        "naughty": "knotty",
        "not": "knot",
        "perfect": "purfect",
        "persona": "fursona",
        "personas": "fursonas",
        "pervert": "furvert",
        "perverts": "furverts",
        "porn": "yiff",
        "roar": "rawr",
        "shout": "awoo",
        "someone": "somefur",
        "source": "sauce",
        "sexy": "yiffy",
        "tale": "tail",
        "the": "teh",
        "this": "dis",
        "what": "wat",
        "with": "wif",
        "you": "chu",
        ":)": ":3",
        ":o": "OwO",
        ":D": "UwU",
        "XD": "X3",
    }

default_guild_settings_r = {"output_channel": None, "active": False, "next_ticket": 1}

default_report = {"report": {}}

class General(Reports, commands.Cog):
    """General commands."""

    default_member_settings_m = {"past_nicks": [], "perms_cache": {}, "banned_until": False}

    default_user_settings_m = {"past_names": []}

    def __init__(self, bot: Red):
        super().__init__()
        self.stopwatches = {}
        self.bot = bot
        self.channels = {}

        self.warnconfig = Config.get_conf(self, identifier=1387000, force_registration=True, cog_name="Warnings")

        self.modconfig = Config.get_conf(self, identifier=1387000, cog_name="Mod")
        self.modconfig.register_member(**self.default_member_settings_m)
        self.modconfig.register_user(**self.default_user_settings_m)

        self.adminconfig = Config.get_conf(self, identifier=1387000, force_registration=True, cog_name="OwnerAdmin")

    @staticmethod
    def pass_hierarchy_check(ctx: commands.Context, role: discord.Role) -> bool:
        """
        Determines if the bot has a higher role than the given one.
        :param ctx:
        :param role: Role object.
        :return:
        """
        return ctx.guild.me.top_role > role

    @staticmethod
    def pass_user_hierarchy_check(ctx: commands.Context, role: discord.Role) -> bool:
        """
        Determines if a user is allowed to add/remove/edit the given role.
        :param ctx:
        :param role:
        :return:
        """
        return ctx.author.top_role > role or ctx.author == ctx.guild.owner

    @commands.command(aliases=["sw"])
    async def stopwatch(self, ctx):
        """Start or stop the stopwatch."""
        author = ctx.author
        if author.id not in self.stopwatches:
            self.stopwatches[author.id] = int(time.perf_counter())
            await ctx.send(author.mention + (" Stopwatch started!"))
        else:
            tmp = abs(self.stopwatches[author.id] - int(time.perf_counter()))
            tmp = str(datetime.timedelta(seconds=tmp))
            await ctx.send(
                author.mention + (" Stopwatch stopped! Time: **{seconds}**").format(seconds=tmp)
            )
            self.stopwatches.pop(author.id, None)

    @commands.command()
    async def lmgtfy(self, ctx, *, search_terms: str):
        """Create a lmgtfy link."""
        search_terms = escape(urllib.parse.quote_plus(search_terms), mass_mentions=True)
        await ctx.send("https://lmgtfy.app/?q={}".format(search_terms))

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def serverinfo(self, ctx, details: bool = False):
        """
        Show server information.

        `details`: Shows more information when set to `True`.
        Default to False.
        """
        guild = ctx.guild
        passed = (ctx.message.created_at - guild.created_at).days
        created_at = ("Created on {date}. That's over {num} days ago!").format(
            date=guild.created_at.strftime("%d %b %Y %H:%M"),
            num=humanize_number(passed),
        )
        online = humanize_number(
            len([m.status for m in guild.members if m.status != discord.Status.offline])
        )
        total_users = humanize_number(guild.member_count)
        text_channels = humanize_number(len(guild.text_channels))
        voice_channels = humanize_number(len(guild.voice_channels))
        if not details:
            data = discord.Embed(description=created_at, colour=await ctx.embed_colour())
            data.add_field(name=("Region"), value=str(guild.region))
            data.add_field(name=("Users online"), value=f"{online}/{total_users}")
            data.add_field(name=("Text Channels"), value=text_channels)
            data.add_field(name=("Voice Channels"), value=voice_channels)
            data.add_field(name=("Roles"), value=humanize_number(len(guild.roles)))
            data.add_field(name=("Owner"), value=str(guild.owner))
            data.set_footer(
                text=("Server ID: ")
                + str(guild.id)
                + ("  •  Use {command} for more info on the server.").format(
                    command=f"{ctx.clean_prefix}serverinfo 1"
                )
            )
            if guild.icon_url:
                data.set_author(name=guild.name, url=guild.icon_url)
                data.set_thumbnail(url=guild.icon_url)
            else:
                data.set_author(name=guild.name)
        else:

            def _size(num: int):
                for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
                    if abs(num) < 1024.0:
                        return "{0:.1f}{1}".format(num, unit)
                    num /= 1024.0
                return "{0:.1f}{1}".format(num, "YB")

            def _bitsize(num: int):
                for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
                    if abs(num) < 1000.0:
                        return "{0:.1f}{1}".format(num, unit)
                    num /= 1000.0
                return "{0:.1f}{1}".format(num, "YB")

            shard_info = (
                ("\nShard ID: **{shard_id}/{shard_count}**").format(
                    shard_id=humanize_number(guild.shard_id + 1),
                    shard_count=humanize_number(ctx.bot.shard_count),
                )
                if ctx.bot.shard_count > 1
                else ""
            )
            # Logic from: https://github.com/TrustyJAID/Trusty-cogs/blob/master/serverstats/serverstats.py#L159
            online_stats = {
                ("Humans: "): lambda x: not x.bot,
                (" • Bots: "): lambda x: x.bot,
                "\N{LARGE GREEN CIRCLE}": lambda x: x.status is discord.Status.online,
                "\N{LARGE ORANGE CIRCLE}": lambda x: x.status is discord.Status.idle,
                "\N{LARGE RED CIRCLE}": lambda x: x.status is discord.Status.do_not_disturb,
                "\N{MEDIUM WHITE CIRCLE}\N{VARIATION SELECTOR-16}": lambda x: (
                    x.status is discord.Status.offline
                ),
                "\N{LARGE PURPLE CIRCLE}": lambda x: any(
                    a.type is discord.ActivityType.streaming for a in x.activities
                ),
                "\N{MOBILE PHONE}": lambda x: x.is_on_mobile(),
            }
            member_msg = ("Users online: **{online}/{total_users}**\n").format(
                online=online, total_users=total_users
            )
            count = 1
            for emoji, value in online_stats.items():
                try:
                    num = len([m for m in guild.members if value(m)])
                except Exception as error:
                    print(error)
                    continue
                else:
                    member_msg += f"{emoji} {bold(humanize_number(num))} " + (
                        "\n" if count % 2 == 0 else ""
                    )
                count += 1

            vc_regions = {
                "vip-us-east": ("__VIP__ US East ") + "\U0001F1FA\U0001F1F8",
                "vip-us-west": ("__VIP__ US West ") + "\U0001F1FA\U0001F1F8",
                "vip-amsterdam": ("__VIP__ Amsterdam ") + "\U0001F1F3\U0001F1F1",
                "eu-west": ("EU West ") + "\U0001F1EA\U0001F1FA",
                "eu-central": ("EU Central ") + "\U0001F1EA\U0001F1FA",
                "europe": ("Europe ") + "\U0001F1EA\U0001F1FA",
                "london": ("London ") + "\U0001F1EC\U0001F1E7",
                "frankfurt": ("Frankfurt ") + "\U0001F1E9\U0001F1EA",
                "amsterdam": ("Amsterdam ") + "\U0001F1F3\U0001F1F1",
                "us-west": ("US West ") + "\U0001F1FA\U0001F1F8",
                "us-east": ("US East ") + "\U0001F1FA\U0001F1F8",
                "us-south": ("US South ") + "\U0001F1FA\U0001F1F8",
                "us-central": ("US Central ") + "\U0001F1FA\U0001F1F8",
                "singapore": ("Singapore ") + "\U0001F1F8\U0001F1EC",
                "sydney": ("Sydney ") + "\U0001F1E6\U0001F1FA",
                "brazil": ("Brazil ") + "\U0001F1E7\U0001F1F7",
                "hongkong": ("Hong Kong ") + "\U0001F1ED\U0001F1F0",
                "russia": ("Russia ") + "\U0001F1F7\U0001F1FA",
                "japan": ("Japan ") + "\U0001F1EF\U0001F1F5",
                "southafrica": ("South Africa ") + "\U0001F1FF\U0001F1E6",
                "india": ("India ") + "\U0001F1EE\U0001F1F3",
                "dubai": ("Dubai ") + "\U0001F1E6\U0001F1EA",
                "south-korea": ("South Korea ") + "\U0001f1f0\U0001f1f7",
            }
            verif = {
                "none": ("0 - None"),
                "low": ("1 - Low"),
                "medium": ("2 - Medium"),
                "high": ("3 - High"),
                "extreme": ("4 - Extreme"),
            }

            features = {
                "ANIMATED_ICON": ("Animated Icon"),
                "BANNER": ("Banner Image"),
                "COMMERCE": ("Commerce"),
                "COMMUNITY": ("Community"),
                "DISCOVERABLE": ("Server Discovery"),
                "FEATURABLE": ("Featurable"),
                "INVITE_SPLASH": ("Splash Invite"),
                "MEMBER_LIST_DISABLED": ("Member list disabled"),
                "MEMBER_VERIFICATION_GATE_ENABLED": ("Membership Screening enabled"),
                "MORE_EMOJI": ("More Emojis"),
                "NEWS": ("News Channels"),
                "PARTNERED": ("Partnered"),
                "PREVIEW_ENABLED": ("Preview enabled"),
                "PUBLIC_DISABLED": ("Public disabled"),
                "VANITY_URL": ("Vanity URL"),
                "VERIFIED": ("Verified"),
                "VIP_REGIONS": ("VIP Voice Servers"),
                "WELCOME_SCREEN_ENABLED": ("Welcome Screen enabled"),
            }
            guild_features_list = [
                f"\N{WHITE HEAVY CHECK MARK} {name}"
                for feature, name in features.items()
                if feature in guild.features
            ]

            joined_on = (
                "{bot_name} joined this server on {bot_join}. That's over {since_join} days ago!"
            ).format(
                bot_name=ctx.bot.user.name,
                bot_join=guild.me.joined_at.strftime("%d %b %Y %H:%M:%S"),
                since_join=humanize_number((ctx.message.created_at - guild.me.joined_at).days),
            )

            data = discord.Embed(
                description=(f"{guild.description}\n\n" if guild.description else "") + created_at,
                colour=await ctx.embed_colour(),
            )
            data.set_author(
                name=guild.name,
                icon_url="https://cdn.discordapp.com/emojis/457879292152381443.png"
                if "VERIFIED" in guild.features
                else "https://cdn.discordapp.com/emojis/508929941610430464.png"
                if "PARTNERED" in guild.features
                else discord.Embed.Empty,
            )
            if guild.icon_url:
                data.set_thumbnail(url=guild.icon_url)
            data.add_field(name=("Members:"), value=member_msg)
            data.add_field(
                name=("Channels:"),
                value=(
                    "\N{SPEECH BALLOON} Text: {text}\n"
                    "\N{SPEAKER WITH THREE SOUND WAVES} Voice: {voice}"
                ).format(text=bold(text_channels), voice=bold(voice_channels)),
            )
            data.add_field(
                name=("Utility:"),
                value=(
                    "Owner: {owner}\nVoice region: {region}\nVerif. level: {verif}\nServer ID: {id}{shard_info}"
                ).format(
                    owner=bold(str(guild.owner)),
                    region=f"**{vc_regions.get(str(guild.region)) or str(guild.region)}**",
                    verif=bold(verif[str(guild.verification_level)]),
                    id=bold(str(guild.id)),
                    shard_info=shard_info,
                ),
                inline=False,
            )
            data.add_field(
                name=("Misc:"),
                value=(
                    "AFK channel: {afk_chan}\nAFK timeout: {afk_timeout}\nCustom emojis: {emoji_count}\nRoles: {role_count}"
                ).format(
                    afk_chan=bold(str(guild.afk_channel))
                    if guild.afk_channel
                    else bold(("Not set")),
                    afk_timeout=bold(humanize_timedelta(seconds=guild.afk_timeout)),
                    emoji_count=bold(humanize_number(len(guild.emojis))),
                    role_count=bold(humanize_number(len(guild.roles))),
                ),
                inline=False,
            )
            if guild_features_list:
                data.add_field(name=("Server features:"), value="\n".join(guild_features_list))
            if guild.premium_tier != 0:
                nitro_boost = (
                    "Tier {boostlevel} with {nitroboosters} boosts\n"
                    "File size limit: {filelimit}\n"
                    "Emoji limit: {emojis_limit}\n"
                    "VCs max bitrate: {bitrate}"
                ).format(
                    boostlevel=bold(str(guild.premium_tier)),
                    nitroboosters=bold(humanize_number(guild.premium_subscription_count)),
                    filelimit=bold(_size(guild.filesize_limit)),
                    emojis_limit=bold(str(guild.emoji_limit)),
                    bitrate=bold(_bitsize(guild.bitrate_limit)),
                )
                data.add_field(name=("Nitro Boost:"), value=nitro_boost)
            if guild.splash:
                data.set_image(url=guild.splash_url_as(format="png"))
            data.set_footer(text=joined_on)

        await ctx.send(embed=data)

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    async def respecc(self, ctx, *, user: discord.User = None):
        """Pay respects by pressing F"""
        if str(ctx.channel.id) in self.channels:
            return await ctx.send(
                "Oops! I'm still paying respects in this channel, you'll have to wait until I'm done."
            )
        self.channels[str(ctx.channel.id)] = {}

        if user:
            answer = user.display_name
        else:
            await ctx.send("What do you want to pay respects to?")

            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            try:
                respecc = await ctx.bot.wait_for("message", timeout=120.0, check=check)
            except asyncio.TimeoutError:
                del self.channels[str(ctx.channel.id)]
                return await ctx.send("You took too long to reply.")

            answer = respecc.content[:1900]

        message = await ctx.send(
            f"Everyone, let's pay respects to **{filter_mass_mentions(answer)}**! Press the f reaction on this message to pay respects."
        )
        await message.add_reaction("\U0001f1eb")
        self.channels[str(ctx.channel.id)] = {"msg_id": message.id, "reacted": []}
        await asyncio.sleep(120)
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass
        amount = len(self.channels[str(ctx.channel.id)]["reacted"])
        word = "person has" if amount == 1 else "people have"
        await ctx.send(f"**{amount}** {word} paid respects to **{filter_mass_mentions(answer)}**.")
        del self.channels[str(ctx.channel.id)]

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if str(reaction.message.channel.id) not in self.channels:
            return
        if self.channels[str(reaction.message.channel.id)]["msg_id"] != reaction.message.id:
            return
        if user.id == self.bot.user.id:
            return
        if user.id not in self.channels[str(reaction.message.channel.id)]["reacted"]:
            if str(reaction.emoji) == "\U0001f1eb":
                await reaction.message.channel.send(f"**{user.name}** has paid their respects.")
                self.channels[str(reaction.message.channel.id)]["reacted"].append(user.id)

    @commands.command(Aliases=["pfp"])
    async def avatar(self, ctx, *, user: discord.Member=None):
        """Returns user avatar URL.

        User argument can be user mention, nickname, username, user ID.
        Default to yourself when no argument is supplied.
        """
        author = ctx.author

        if not user:
            user = author

        if user.is_avatar_animated():
            url = user.avatar_url_as(format="gif")
        if not user.is_avatar_animated():
            url = user.avatar_url_as(static_format="png")
        embed = discord.Embed(
            color=await self.bot.get_embed_color(ctx),
            title=f"{user.name}'s Avatar"
        )
        embed.set_image(
            url=url
        )

        await ctx.send(embed=embed)

    @commands.command()
    async def fuwwy(self, ctx: commands.Context, *, text: str = None):
        """Fuwwyize the pwevious message, ow youw own text."""
        if not text:
            text = (await ctx.channel.history(limit=2).flatten())[
                1
            ].content or "I can't translate that!"
        fuwwytext = self.fuwwyize_string(text)
        await ctx.send(fuwwytext[:2000] if len(fuwwytext) > 2000 else fuwwytext, allowed_mentions=discord.AllowedMentions(users=False))

    def fuwwyize_string(self, string: str):
        """Uwuize and wetuwn a stwing."""
        converted = ""
        current_word = ""
        for letter in string:
            if letter.isprintable() and not letter.isspace():
                current_word += letter
            elif current_word:
                converted += self.fuwwyize_word(current_word) + letter
                current_word = ""
            else:
                converted += letter
        if current_word:
            converted += self.fuwwyize_word(current_word)
        return converted

    def fuwwyize_word(self, word: str):
        """Uwuize and wetuwn a wowd.

        Thank you to the following for inspiration:
        https://github.com/senguyen1011/UwUinator
        """
        word = word.lower()
        uwu = word.rstrip(".?!,")
        punctuations = word[len(uwu) :]
        final_punctuation = punctuations[-1] if punctuations else ""
        extra_punctuation = punctuations[:-1] if punctuations else ""

        # Process punctuation
        if final_punctuation == "." and not random.randint(0, 3):
            final_punctuation = random.choice(KAOMOJI_JOY)
        if final_punctuation == "?" and not random.randint(0, 2):
            final_punctuation = random.choice(KAOMOJI_CONFUSE)
        if final_punctuation == "!" and not random.randint(0, 2):
            final_punctuation = random.choice(KAOMOJI_JOY)
        if final_punctuation == "," and not random.randint(0, 3):
            final_punctuation = random.choice(KAOMOJI_EMBARRASSED)
        if final_punctuation and not random.randint(0, 4):
            final_punctuation = random.choice(KAOMOJI_SPARKLES)

        # Full Words Extra
        if uwu == "ahh": uwu = fur["ahh"]
        if uwu == "love": uwu = fur["love"]
        if uwu == "awesome": uwu = fur["awesome"]
        if uwu == "awful": uwu = fur["awful"]
        if uwu == "bite": uwu = fur["bite"]
        if uwu == "bites": uwu = fur["bites"]
        if uwu == "butthole": uwu = fur["butthole"]
        if uwu == "buttholes": uwu = fur["buttholes"]
        if uwu == "bulge": uwu = fur["bulge"]
        if uwu == "bye": uwu = fur["bye"]
        if uwu == "celebrity": uwu = fur["celebrity"]
        if uwu == "celebrities": uwu = fur["celebrities"]
        if uwu == "cheese": uwu = fur["cheese"]
        if uwu == "child" or uwu == "kid" or uwu == "infant": uwu = fur["child"]
        if uwu == "children" or uwu == "kids" or uwu == "infants": uwu = fur["children"]
        if uwu == "robot" or uwu == "cyborg" or uwu == "computer": uwu = fur["computer"]
        if uwu == "robots" or uwu == "cyborgs" or uwu == "computers": uwu = fur["computers"]
        if uwu == "disease": uwu = fur["disease"]
        if uwu == "dog": uwu = fur["dog"]
        if uwu == "dogs": uwu = fur["dogs"]
        if uwu == "dragon": uwu = fur["dragon"]
        if uwu == "dragons": uwu = fur["dragons"]
        if uwu == "eat": uwu = fur["eat"]
        if uwu == "everyone": uwu = fur["everyone"]
        if uwu == "foot": uwu = fur["foot"]
        if uwu == "feet": uwu = fur["feet"]
        if uwu == "for": uwu = fur["for"]
        if uwu == "fuck": uwu = fur["fuck"]
        if uwu == "fucking": uwu = fur["fucking"]
        if uwu == "fucked": uwu = fur["fucked"]
        if uwu == "hand": uwu = fur["hand"]
        if uwu == "hands": uwu = fur["hands"]
        if uwu == "hi": uwu = fur["hi"]
        if uwu == "human": uwu = fur["human"]
        if uwu == "humans": uwu = fur["humans"]
        if uwu == "hyena": uwu = fur["hyena"]
        if uwu == "hyenas": uwu = fur["hyenas"]
        if uwu == "innocent": uwu = fur["innocent"]
        if uwu == "kiss": uwu = fur["kiss"]
        if uwu == "kisses": uwu = fur["kisses"]
        if uwu == "lmao": uwu = fur["lmao"]
        if uwu == "masturbate" or uwu == "fap": uwu = fur["masturbate"]
        if uwu == "mouth": uwu = fur["mouth"]
        if uwu == "naughty": uwu = fur["naughty"]
        if uwu == "not": uwu = fur["not"]
        if uwu == "perfect": uwu = fur["perfect"]
        if uwu == "persona": uwu = fur["persona"]
        if uwu == "personas": uwu = fur["personas"]
        if uwu == "pervert": uwu = fur["pervert"]
        if uwu == "perverts": uwu = fur["perverts"]
        if uwu == "porn": uwu = fur["porn"]
        if uwu == "roar": uwu = fur["roar"]
        if uwu == "shout": uwu = fur["shout"]
        if uwu == "someone": uwu = fur["someone"]
        if uwu == "source": uwu = fur["source"]
        if uwu == "sexy": uwu = fur["sexy"]
        if uwu == "tale": uwu = fur["tale"]
        if uwu == "the": uwu = fur["the"]
        if uwu == "this": uwu = fur["this"]
        if uwu == "what": uwu = fur["what"]
        if uwu == "with": uwu = fur["with"]
        if uwu == "you": uwu = fur["you"]
        if uwu == ":)": uwu = fur[":)"]
        if uwu == ":o" or uwu == ":O": uwu = fur[":o"]
        if uwu == ":D": uwu = fur[":D"]
        if uwu == "XD" or uwu == "xD" or uwu == "xd": uwu = fur["XD"]

        # L -> W and R -> W
        if not uwu in fur.values():
            protected = ""
            if (
                uwu.endswith("le")
                or uwu.endswith("ll")
                or uwu.endswith("er")
                or uwu.endswith("re")
            ):
                protected = uwu[-2:]
                uwu = uwu[:-2]
            elif (
                uwu.endswith("les")
                or uwu.endswith("lls")
                or uwu.endswith("ers")
                or uwu.endswith("res")
            ):
                protected = uwu[-3:]
                uwu = uwu[:-3]
            uwu = uwu.replace("l", "w").replace("r", "w") + protected

        # Full words
        uwu = uwu.replace("you're", "ur")
        uwu = uwu.replace("youre", "ur")
        uwu = uwu.replace("shit", "poopoo")
        uwu = uwu.replace("bitch", "meanie")
        uwu = uwu.replace("asshole", "b-butthole")
        uwu = uwu.replace("dick", "peenie")
        uwu = uwu.replace("penis", "peenie")
        uwu = "spooge" if uwu in ("cum", "semen") else uwu
        uwu = "boi pussy" if uwu == "ass" else uwu
        uwu = "daddy" if uwu in ("dad", "father") else uwu

        # Add back punctuations
        uwu += extra_punctuation + final_punctuation

        # Add occasional stutter
        if (
            len(uwu) > 2
            and uwu[0].isalpha()
            and "-" not in uwu
            and not random.randint(0, 6)
        ):
            uwu = f"{uwu[0]}-{uwu}"

        return uwu

    @commands.command()
    async def utc(self, ctx, time_or_offset = None):
        """Shows the current UTC time and can convert to local.
        
        **Examples:**
        - `[p]utc` will show the current time at UTC+0
        - `[p]utc 20:00` shows how long until UTC+0 is `20:00`
        - `[p]utc -3` shows the local time at UTC-3
        - `[p]utc +8` shows the local time at UTC+8
        """
        current_time = datetime.datetime.utcnow()

        embed = discord.Embed(
            color=await self.bot.get_embed_color(ctx),
        )
        embed.set_author(
            name="Find your UTC offset here",
            url="https://www.timeanddate.com/time/map/"
        )

        try:
            if time_or_offset:
                if "+" in time_or_offset or "-" in time_or_offset:
                    if "+" in time_or_offset:
                        cleanoffset = time_or_offset.replace("+", "")
                    else:
                        cleanoffset = time_or_offset
                    if ":" in cleanoffset:
                        time = cleanoffset.split(":")
                        if int(time[1]) >= 60 or int(time[1]) < 0:
                            await self.del_message(ctx, "Please only use minutes between 0 and 59")
                            return
                        elif int(time[0]) > 14 or int(time[0]) < -12:
                            await self.del_message(ctx, "Please only use hours between -12 and +14")
                            return
                        else:
                            newtime = current_time + datetime.timedelta(hours=int(time[0]), minutes=int(time[1]))
                    else:
                        if int(cleanoffset) > 14 or int(cleanoffset) < -12:
                            await self.del_message(ctx, "Please only use hours between -12 and +14")
                            return
                        else:
                            newtime = current_time + datetime.timedelta(hours=int(cleanoffset))
                    embedtitle = f'{newtime.strftime("%H:%M")}'
                    embeddescription = f'The local time at UTC{time_or_offset} is {newtime.strftime("%H:%M")}'
                    
                elif ":" in time_or_offset:
                    time = time_or_offset.split(":")
                    if int(time[0]) >= 24 or int(time[0]) < 0:
                        await self.del_message(ctx, "Please only use hours between 0 and 23")
                        return
                    else:
                        if int(time[1]) >= 60 or int(time[1]) < 0:
                            await self.del_message(ctx, "Please only use minutes between 0 and 59")
                            return
                        else:
                            newtime = current_time.replace(hour=int(time[0]), minute=int(time[1]))
                            if newtime < current_time:
                                newtime = newtime + datetime.timedelta(hours=24)
                            timeanswer = relativedelta(newtime, current_time)
                            if timeanswer.hours:
                                embedtitle = f"{timeanswer.hours} hours and {timeanswer.minutes} minutes"
                                embeddescription = f"In {timeanswer.hours} hours and {timeanswer.minutes} minutes the clock will be {time[0]}:{time[1]} in UTC+0"
                            elif timeanswer.minutes:
                                embedtitle = f"{timeanswer.minutes} minutes"
                                embeddescription = f"In {timeanswer.minutes} minutes the clock will be {time[0]}:{time[1]} in UTC+0"
                            else:
                                embedtitle = f"{time[0]}:{time[1]} is the current time!"
                                embeddescription = ""


                embed.set_footer(
                    text=f'Current UTC+0 Time ◈ {current_time.strftime("%H:%M")}'
                )
                embed.title = embedtitle
                embed.description = embeddescription
                await ctx.send(embed=embed)
            else:
                embed.add_field(
                    name="Current UTC+0 Time",
                    value=current_time.strftime("%H:%M"),
                    inline=False
                )
                await ctx.send(embed=embed)
        except:
            await self.del_message(ctx, f"I couldn't understand your time format. Do `{ctx.clean_prefix}help utc` for examples on using the command.")

    @commands.command(aliases=["uinfo"])
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def userinfo(self, ctx, *, user: discord.Member = None):
        """Show information about a user.

        This includes fields for status, discord join date, server
        join date, voice state and previous names/nicknames.

        If the user has no roles, previous names or previous nicknames,
        these fields will be omitted.
        """
        author = ctx.author
        guild = ctx.guild

        if not user:
            user = author

        roles = user.roles[-1:0:-1]
        names = await self.modconfig.user(user).past_names()
        nicks = await self.modconfig.member(user).past_nicks()
        if names:
            names = [escape_spoilers_and_mass_mentions(name) for name in names if name]
        if nicks:
            nicks = [escape_spoilers_and_mass_mentions(nick) for nick in nicks if nick]

        joined_at = user.joined_at
        since_created = (ctx.message.created_at - user.created_at).days
        if joined_at is not None:
            since_joined = (ctx.message.created_at - joined_at).days
            user_joined = joined_at.strftime("%d %b %Y %H:%M")
        else:
            since_joined = "?"
            user_joined = ("Unknown")
        user_created = user.created_at.strftime("%d %b %Y %H:%M")
        voice_state = user.voice
        member_number = (
            sorted(guild.members, key=lambda m: m.joined_at or ctx.message.created_at).index(user)
            + 1
        )

        created_on = ("{}\n({} days ago)").format(user_created, since_created)
        joined_on = ("{}\n({} days ago)").format(user_joined, since_joined)

        if any(a.type is discord.ActivityType.streaming for a in user.activities):
            statusemoji = "\N{LARGE PURPLE CIRCLE}"
        elif user.status.name == "online":
            statusemoji = "\N{LARGE GREEN CIRCLE}"
        elif user.status.name == "offline":
            statusemoji = "\N{MEDIUM WHITE CIRCLE}\N{VARIATION SELECTOR-16}"
        elif user.status.name == "dnd":
            statusemoji = "\N{LARGE RED CIRCLE}"
        elif user.status.name == "idle":
            statusemoji = "\N{LARGE ORANGE CIRCLE}"
        activity = ("User is currently {}").format(user.status)
        status_string = self.get_status_string(user)
        if user.id == ctx.guild.owner.id:
            status_string += "\n\nIs the owner of this server"
        if user.id == 128853022200561665:
            status_string += "\nCreated me!  - Angiedale OwO"

        if roles:

            role_str = ", ".join([x.mention for x in roles])
            # 400 BAD REQUEST (error code: 50035): Invalid Form Body
            # In embed.fields.2.value: Must be 1024 or fewer in length.
            if len(role_str) > 1024:
                # Alternative string building time.
                # This is not the most optimal, but if you're hitting this, you are losing more time
                # to every single check running on users than the occasional user info invoke
                # We don't start by building this way, since the number of times we hit this should be
                # infinitesimally small compared to when we don't across all uses of Red.
                continuation_string = (
                    "and {numeric_number} more roles not displayed due to embed limits."
                )
                available_length = 1024 - len(continuation_string)  # do not attempt to tweak, i18n

                role_chunks = []
                remaining_roles = 0

                for r in roles:
                    chunk = f"{r.mention}, "
                    chunk_size = len(chunk)

                    if chunk_size < available_length:
                        available_length -= chunk_size
                        role_chunks.append(chunk)
                    else:
                        remaining_roles += 1

                role_chunks.append(continuation_string.format(numeric_number=remaining_roles))

                role_str = "".join(role_chunks)

        else:
            role_str = None

        data = discord.Embed(description=status_string or activity, colour=user.colour)

        data.add_field(name=("Joined Discord on"), value=created_on)
        data.add_field(name=("Joined this server on"), value=joined_on)
        if role_str is not None:
            data.add_field(
                name=("Roles") if len(roles) > 1 else ("Role"), value=role_str, inline=False
            )
        if names:
            # May need sanitizing later, but mentions do not ping in embeds currently
            val = filter_invites(", ".join(names))
            data.add_field(
                name=("Previous Names") if len(names) > 1 else ("Previous Name"),
                value=val,
                inline=False,
            )
        if nicks:
            # May need sanitizing later, but mentions do not ping in embeds currently
            val = filter_invites(", ".join(nicks))
            data.add_field(
                name=("Previous Nicknames") if len(nicks) > 1 else ("Previous Nickname"),
                value=val,
                inline=False,
            )
        if voice_state and voice_state.channel:
            data.add_field(
                name=("Current voice channel"),
                value="{0.mention} ID: {0.id}".format(voice_state.channel),
                inline=False,
            )
        data.set_footer(text=("Member #{} | User ID: {}").format(member_number, user.id))

        name = str(user)
        name = " ◈ ".join((name, user.nick)) if user.nick else name
        name = filter_invites(name)

        avatar = user.avatar_url_as(static_format="png")
        data.set_author(name=f"{statusemoji} {name}", url=avatar)
        data.set_thumbnail(url=avatar)

        await ctx.send(embed=data)

    def get_status_string(self, user):
        string = ""
        for a in [
            self.handle_custom(user),
            self.handle_playing(user),
            self.handle_listening(user),
            self.handle_streaming(user),
            self.handle_watching(user),
            self.handle_competing(user),
        ]:
            status_string, status_type = a
            if status_string is None:
                continue
            string += f"{status_string}\n"
        string += f"\nShares servers with bot: {str(len(set([member.guild.name for member in self.bot.get_all_members() if member.id == user.id])))}"
        return string

    def handle_custom(self, user):
        a = [c for c in user.activities if c.type == discord.ActivityType.custom]
        if not a:
            return None, discord.ActivityType.custom
        a = a[0]
        c_status = None
        if not a.name and not a.emoji:
            return None, discord.ActivityType.custom
        elif a.name and a.emoji:
            c_status = ("Custom Status: {emoji} {name}").format(emoji=a.emoji, name=a.name)
        elif a.emoji:
            c_status = ("Custom Status: {emoji}").format(emoji=a.emoji)
        elif a.name:
            c_status = ("Custom Status: {name}").format(name=a.name)
        return c_status, discord.ActivityType.custom

    def handle_playing(self, user):
        p_acts = [c for c in user.activities if c.type == discord.ActivityType.playing]
        if not p_acts:
            return None, discord.ActivityType.playing
        p_act = p_acts[0]
        act = ("Playing: {name}").format(name=p_act.name)
        return act, discord.ActivityType.playing

    def handle_streaming(self, user):
        s_acts = [c for c in user.activities if c.type == discord.ActivityType.streaming]
        if not s_acts:
            return None, discord.ActivityType.streaming
        s_act = s_acts[0]
        if isinstance(s_act, discord.Streaming):
            act = ("Streaming: [{name}{sep}{game}]({url})").format(
                name=discord.utils.escape_markdown(s_act.name),
                sep=" | " if s_act.game else "",
                game=discord.utils.escape_markdown(s_act.game) if s_act.game else "",
                url=s_act.url,
            )
        else:
            act = ("Streaming: {name}").format(name=s_act.name)
        return act, discord.ActivityType.streaming

    def handle_listening(self, user):
        l_acts = [c for c in user.activities if c.type == discord.ActivityType.listening]
        if not l_acts:
            return None, discord.ActivityType.listening
        l_act = l_acts[0]
        if isinstance(l_act, discord.Spotify):
            act = ("Listening to: [{title}{sep}{artist}]({url})").format(
                title=discord.utils.escape_markdown(l_act.title),
                sep=" | " if l_act.artist else "",
                artist=discord.utils.escape_markdown(l_act.artist) if l_act.artist else "",
                url=f"https://open.spotify.com/track/{l_act.track_id}",
            )
        else:
            act = ("Listening to: {title}").format(title=l_act.name)
        return act, discord.ActivityType.listening

    def handle_watching(self, user):
        w_acts = [c for c in user.activities if c.type == discord.ActivityType.watching]
        if not w_acts:
            return None, discord.ActivityType.watching
        w_act = w_acts[0]
        act = ("Watching: {name}").format(name=w_act.name)
        return act, discord.ActivityType.watching

    def handle_competing(self, user):
        w_acts = [c for c in user.activities if c.type == discord.ActivityType.competing]
        if not w_acts:
            return None, discord.ActivityType.competing
        w_act = w_acts[0]
        act = ("Competing in: {competing}").format(competing=w_act.name)
        return act, discord.ActivityType.competing

    @commands.guild_only()
    @commands.command(aliases=["chinfo"])
    async def channelinfo(self, ctx, channel: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel] = None):
        """Shows channel information. Defaults to current text channel."""
        if channel is None:
            channel = ctx.channel

        if channel is None:
            return await ctx.send("Not a valid channel.")

        cembed = []

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(name=channel.name, icon_url=channel.guild.icon_url)

        embed.set_footer(text=f'Channel ID: {channel.id} ◈ Created at')

        embed.add_field(name="Type", value=str(channel.type).capitalize(), inline=True)
        embed.add_field(name="Position", value=channel.position, inline=True)

        if isinstance(channel, discord.VoiceChannel):
            embed.add_field(name="Bitrate", value=f"{int(channel.bitrate / 1000)}kbps", inline=True)
            embed.add_field(name="Category", value=channel.category, inline=False)
            embed.add_field(name="Users In Channel", value=len(channel.members), inline=True)
            embed.add_field(name="User Limit", value=channel.user_limit, inline=True)
        else:
            embed.add_field(name="NSFW", value=channel.is_nsfw(), inline=True)

        if isinstance(channel, discord.TextChannel):
            if channel.topic:
                embed.description = channel.topic
            embed.add_field(name="Category", value=channel.category, inline=False)
            embed.add_field(name="Users With Access", value=len(channel.members), inline=True)
            embed.add_field(name="Announcement Channel", value=channel.is_news(), inline=True)
        elif isinstance(channel, discord.CategoryChannel):
            embed.add_field(name="Text Channels", value=len(channel.text_channels), inline=True)
            embed.add_field(name="Voice Channels", value=len(channel.voice_channels), inline=True)

        embed.timestamp = channel.created_at

        cembed.append(embed)

        await menu(ctx, cembed, {"\N{CROSS MARK}": close_menu})

    @commands.guild_only()
    @commands.command(aliases=["einfo", "emoteinfo"])
    async def emojiinfo(self, ctx, emoji: Optional[discord.Emoji] = None):
        """Emoji information. Only works for servers the bot is in"""
        if not emoji:
            await self.del_message(ctx, "Please use a custom emoji from a server that I'm in")
        else:
            e = emoji

            theembed = []

            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

            text = f"{e}\n\n"
            text += f"From Server: **{e.guild}**\n"
            text += f"Animated: **{e.animated}**\n"
            text += f"Twitch Sub Emote: **{e.managed}**\n\n"
            text += f"**[Link To Image]({e.url})**"

            embed.description = text
            embed.title = e.name
            embed.set_thumbnail(url=e.url)

            embed.set_footer(text=f'Emote ID: {e.id} ◈ Created at')

            embed.timestamp = e.created_at

            theembed.append(embed)

            await menu(ctx, theembed, {"\N{CROSS MARK}": close_menu})

    @commands.command(aliases=["re"], hidden=True)
    async def randomemote(self, ctx, all_servers: bool = False):
        """Sends a random emote."""
        if await self.bot.is_owner(ctx.author) and all_servers:
            bad_guilds = 678278754497200139
            guilds = self.bot.guilds
            guilds.remove(self.bot.get_guild(bad_guilds))
            find_guild = True
            while find_guild:
                g = random.choice(guilds)
                el = g.emojis
                if len(el) > 0:
                    twitch = True
                    while twitch and len(el) > 0:
                        e = random.choice(el)
                        if not e.managed:
                            twitch = False
                        else:
                            el.remove(e)
                    find_guild = False
                else:
                    guilds.remove(g)
            await ctx.send(e)
        else:
            try:
                twitch = True
                el = ctx.guild.emojis
                while twitch and len(el) > 0:
                    e = random.choice(el)
                    if not e.managed:
                        twitch = False
                    else:
                        el.remove(e)
                await ctx.send(e)
            except:
                await self.del_message(ctx, "I can find any emojis to pick from in this server")

    async def del_message(self, ctx, message_text):
        message = await ctx.maybe_send_embed(message_text)
        await asyncio.sleep(10)
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass

    @commands.guild_only()
    @commands.group(invoke_without_command=True)
    async def selfrole(self, ctx: commands.Context, *, selfrole: SelfRole):
        """
        Add or remove a selfrole from yourself.

        Server admins must have configured the role as user settable.
        NOTE: The role is case sensitive!
        """
        if selfrole in ctx.author.roles:
            return await self._removerole(ctx, ctx.author, selfrole, check_user=False)
        else:
            return await self._addrole(ctx, ctx.author, selfrole, check_user=False)

    @selfrole.command(name="add", hidden=True)
    async def selfrole_add(self, ctx: commands.Context, *, selfrole: SelfRole):
        """
        Add a selfrole to yourself.

        Server admins must have configured the role as user settable.
        NOTE: The role is case sensitive!
        """
        # noinspection PyTypeChecker
        await self._addrole(ctx, ctx.author, selfrole, check_user=False)

    @selfrole.command(name="remove", hidden=True)
    async def selfrole_remove(self, ctx: commands.Context, *, selfrole: SelfRole):
        """
        Remove a selfrole from yourself.

        Server admins must have configured the role as user settable.
        NOTE: The role is case sensitive!
        """
        # noinspection PyTypeChecker
        await self._removerole(ctx, ctx.author, selfrole, check_user=False)

    @selfrole.command(name="list")
    async def selfrole_list(self, ctx: commands.Context):
        """
        Lists all available selfroles.
        """
        selfroles = await self._valid_selfroles(ctx.guild)
        fmt_selfroles = "\n".join(["+ " + r.name for r in selfroles])

        if not fmt_selfroles:
            await ctx.send("There are currently no selfroles.")
            return

        msg = ("Available Selfroles:\n{selfroles}").format(selfroles=fmt_selfroles)
        await ctx.send(box(msg, "diff"))

    async def _addrole(
        self, ctx: commands.Context, member: discord.Member, role: discord.Role, *, check_user=True
    ):
        if role in member.roles:
            await ctx.send(
                ("{member.display_name} already has the role {role.name}.").format(
                    role=role, member=member
                )
            )
            return
        if check_user and not self.pass_user_hierarchy_check(ctx, role):
            await ctx.send((
                "I can not let you give {role.name} to {member.display_name}"
                " because that role is higher than or equal to your highest role"
                " in the Discord hierarchy."
                ).format(role=role, member=member))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((
                "I can not give {role.name} to {member.display_name}"
                " because that role is higher than or equal to my highest role"
                " in the Discord hierarchy."
                ).format(role=role, member=member))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send(("I need manage roles permission to do that."))
            return
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            await ctx.send((
                "I attempted to do something that Discord denied me permissions for."
                " Your command failed to successfully complete."))
        else:
            await ctx.send(
                ("I successfully added {role.name} to {member.display_name}").format(
                    role=role, member=member
                )
            )

    async def _removerole(
        self, ctx: commands.Context, member: discord.Member, role: discord.Role, *, check_user=True
    ):
        if role not in member.roles:
            await ctx.send(
                ("{member.display_name} does not have the role {role.name}.").format(
                    role=role, member=member
                )
            )
            return
        if check_user and not self.pass_user_hierarchy_check(ctx, role):
            await ctx.send((
                "I can not let you remove {role.name} from {member.display_name}"
                " because that role is higher than or equal to your highest role"
                " in the Discord hierarchy."
                ).format(role=role, member=member))
            return
        if not self.pass_hierarchy_check(ctx, role):
            await ctx.send((
                "I can not remove {role.name} from {member.display_name}"
                " because that role is higher than or equal to my highest role"
                " in the Discord hierarchy."
                ).format(role=role, member=member))
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send(("I need manage roles permission to do that."))
            return
        try:
            await member.remove_roles(role)
        except discord.Forbidden:
            await ctx.send((
                "I attempted to do something that Discord denied me permissions for."
                " Your command failed to successfully complete."))
        else:
            await ctx.send(
                ("I successfully removed {role.name} from {member.display_name}").format(
                    role=role, member=member
                )
            )

    async def _valid_selfroles(self, guild: discord.Guild) -> Tuple[discord.Role]:
        """
        Returns a tuple of valid selfroles
        :param guild:
        :return:
        """
        selfrole_ids = set(await self.adminconfig.guild(guild).selfroles())
        guild_roles = guild.roles

        valid_roles = tuple(r for r in guild_roles if r.id in selfrole_ids)
        valid_role_ids = set(r.id for r in valid_roles)

        if selfrole_ids != valid_role_ids:
            await self.adminconfig.guild(guild).selfroles.set(list(valid_role_ids))

        # noinspection PyTypeChecker
        return valid_roles

    @commands.command()
    @commands.guild_only()
    async def mywarnings(self, ctx: commands.Context):
        """List warnings for yourself."""

        user = ctx.author

        msg = ""
        member_settings = self.warnconfig.member(user)
        async with member_settings.warnings() as user_warnings:
            if not user_warnings.keys():  # no warnings for the user
                await ctx.send(("You have no warnings!"))
            else:
                for key in user_warnings.keys():
                    mod_id = user_warnings[key]["mod"]
                    if mod_id == 0xDE1:
                        mod = ("Deleted Moderator")
                    else:
                        bot = ctx.bot
                        mod = bot.get_user(mod_id) or ("Unknown Moderator ({})").format(mod_id)
                    msg += (
                        "{num_points} point warning {reason_name} issued by {user} for "
                        "{description}\n"
                    ).format(
                        num_points=user_warnings[key]["points"],
                        reason_name=key,
                        user=mod,
                        description=user_warnings[key]["description"],
                    )
                await ctx.send_interactive(
                    pagify(msg, shorten_by=58),
                    box_lang=("Warnings for {user}").format(user=user),
                )

    @command.command()
    async def support(self, ctx):
        """Sends invite to the support server."""

        await ctx.send("Here's an invite link. Mestro should be able to help you in there.\n\nhttps://discord.gg/xxjdXmR")