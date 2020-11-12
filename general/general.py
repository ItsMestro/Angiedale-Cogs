import datetime
import time
from enum import Enum
from random import randint, choice
from typing import Final
import urllib.parse
import aiohttp
import discord
import asyncio
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from redbot.core.utils.common_filters import filter_mass_mentions
from redbot.core.utils.chat_formatting import (
    bold,
    escape,
    italics,
    humanize_number,
    humanize_timedelta,
)


class General(commands.Cog):
    """General commands."""

    def __init__(self, bot: Red):
        super().__init__()
        self.stopwatches = {}
        self.bot = bot
        self.channels = {}

    @commands.command()
    async def choose(self, ctx, *choices):
        """Choose between multiple options.

        To denote options which include whitespace, you should use
        double quotes.
        """
        choices = [escape(c, mass_mentions=True) for c in choices if c]
        if len(choices) < 2:
            await ctx.send(("Not enough options to pick from."))
        else:
            await ctx.send(choice(choices))

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
        await ctx.send("https://lmgtfy.com/?q={}".format(search_terms))

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
                "PARTNERED": ("Partnered"),
                "VERIFIED": ("Verified"),
                "DISCOVERABLE": ("Server Discovery"),
                "FEATURABLE": ("Featurable"),
                "COMMUNITY": ("Community"),
                "PUBLIC_DISABLED": ("Public disabled"),
                "INVITE_SPLASH": ("Splash Invite"),
                "VIP_REGIONS": ("VIP Voice Servers"),
                "VANITY_URL": ("Vanity URL"),
                "MORE_EMOJI": ("More Emojis"),
                "COMMERCE": ("Commerce"),
                "NEWS": ("News Channels"),
                "ANIMATED_ICON": ("Animated Icon"),
                "BANNER": ("Banner Image"),
                "MEMBER_LIST_DISABLED": ("Member list disabled"),
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
            f"Everyone, let's pay respects to **{filter_mass_mentions(answer)}**! Press the f reaction on the this message to pay respects."
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

        await ctx.send("{}'s Avatar URL : {}".format(user.name, url))