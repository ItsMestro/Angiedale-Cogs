import datetime
import time
import calendar
import logging
import random
from datetime import datetime
from enum import Enum
from random import randint, choice
from typing import Final
import urllib.parse
import aiohttp
import discord
import asyncio
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from redbot.core.utils.predicates import MessagePredicate
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

    raffle_defaults = {"Channel": None, "Raffles": {}}

    def __init__(self, bot: Red):
        super().__init__()
        self.stopwatches = {}
        self.bot = bot
        self.channels = {}
        self.config = Config.get_conf(self, 1387009, cog_name="GeneralRaffle", force_registration=True)
        self.config.register_guild(**self.raffle_defaults)
        self.load_check = self.bot.loop.create_task(self.raffle_worker())

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

    @commands.group(autohelp=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def raffle(self, ctx):
        """Raffle group command"""
        pass

    @raffle.command()
    async def version(self, ctx):
        """Displays the currently installed version of raffle."""
        await ctx.send(f"You are running raffle version {__version__}")

    @raffle.command(hidden=True)
    @commands.is_owner()
    async def clear(self, ctx):
        await self.config.guild(ctx.guild).Raffles.clear()
        await ctx.send("Raffle data cleared out.")

    @raffle.command()
    async def start(self, ctx, timer, *, title: str):
        """Starts a raffle.

        Timer accepts a integer input that represents seconds or it will
        take the format of HH:MM:SS. For example:

        80       - 1 minute and 20 seconds or 80 seconds
        30:10    - 30 minutes and 10 seconds
        24:00:00 - 1 day or 24 hours

        Title should not be longer than 35 characters.
        Only one raffle can be active per server.
        """
        timer = await self.start_checks(ctx, timer, title)
        if timer is None:
            return

        try:
            description, winners, dos, roles = await self.raffle_setup(ctx)
        except asyncio.TimeoutError:
            return await ctx.send("Response timed out. A raffle failed to start.")
        str_roles = [r[0] for r in roles]
        description = f"{description}\n\nReact to this message with \U0001F39F to enter.\n\n"

        channel = await self._get_channel(ctx)
        end = calendar.timegm(ctx.message.created_at.utctimetuple()) + timer
        fmt_end = time.strftime("%a %d %b %Y %H:%M:%S", time.gmtime(end))

        try:
            embed = discord.Embed(
                description=description, title=title, color=self.bot.color
            )  ### old compat, i think ?
        except:
            color = await self.bot.get_embed_color(ctx)
            embed = discord.Embed(description=description, title=title, color=color)  ### new code
        embed.add_field(name="Days on Server", value=f"{dos}")
        role_info = f'{", ".join(str_roles) if roles else "@everyone"}'
        embed.add_field(name="Allowed Roles", value=role_info)
        msg = await channel.send(embed=embed)
        embed.set_footer(
            text=(
                f"Started by: {ctx.author.name} | Winners: {winners} | Ends at {fmt_end} UTC | Raffle ID: {msg.id}"
            )
        )
        await msg.edit(embed=embed)
        await msg.add_reaction("\U0001F39F")

        async with self.config.guild(ctx.guild).Raffles() as r:
            new_raffle = {
                "Channel": channel.id,
                "Timestamp": end,
                "DOS": dos,
                "Roles": roles,
                "ID": msg.id,
                "Title": title,
            }
            r[msg.id] = new_raffle

        await self.raffle_timer(ctx.guild, new_raffle, timer)

    @raffle.command()
    async def end(self, ctx, message_id: int = None):
        """Ends a raffle early. A winner will still be chosen."""
        if message_id is None:
            try:
                message_id = await self._menu(ctx)
            except ValueError:
                return await ctx.send("There are no active raffles to end.")
            except asyncio.TimeoutError:
                return await ctx.send("Response timed out.")

        try:
            await self.raffle_teardown(ctx.guild, message_id)
        except discord.NotFound:
            await ctx.send("The message id provided could not be found.")
        else:
            await ctx.send("The raffle has been ended.")

    @raffle.command()
    async def cancel(self, ctx, message_id: int = None):
        """Cancels an on-going raffle. No winner is chosen."""
        if message_id is None:
            try:
                message_id = await self._menu(ctx, end="cancel")
            except ValueError:
                return await ctx.send("There are no active raffles to cancel.")
            except asyncio.TimeoutError:
                return await ctx.send("Response timed out.")

        try:
            await self.raffle_removal(ctx, message_id)
        except discord.NotFound:
            await ctx.send("The message id provided could not be found.")
        else:
            await ctx.send("The raffle has been canceled.")
        finally:
            # Attempt to cleanup if a message was deleted and it's still stored in config.
            async with self.config.guild(ctx.guild).Raffles() as r:
                try:
                    del r[str(message_id)]
                except KeyError:
                    pass

    async def _menu(self, ctx, end="end"):
        title = f"Which of the following **Active** Raffles would you like to {end}?"
        async with self.config.guild(ctx.guild).Raffles() as r:
            if not r:
                raise ValueError
            raffles = list(r.items())
        try:
            # pre-3.2 compatibility layer
            embed = self.embed_builder(raffles, ctx.bot.color, title)
        except AttributeError:
            color = await self.bot.get_embed_color(ctx)
            embed = self.embed_builder(raffles, color, title)
        msg = await ctx.send(embed=embed)

        def predicate(m):
            if m.channel == ctx.channel and m.author == ctx.author:
                return int(m.content) in range(1, 11)

        resp = await ctx.bot.wait_for("message", timeout=60, check=predicate)
        message_id = raffles[int(resp.content) - 1][0]
        await resp.delete()
        await msg.delete()
        return message_id

    def embed_builder(self, raffles, color, title):
        embeds = []
        # FIXME Come back and make this more dynamic
        truncate = raffles[:10]
        emojis = (
            ":one:",
            ":two:",
            ":three:",
            ":four:",
            ":five:",
            ":six:",
            ":seven:",
            ":eight:",
            ":nine:",
            ":ten:",
        )
        e = discord.Embed(colour=color, title=title)
        description = ""
        for raffle, number_emoji in zip(truncate, emojis):
            description += f"{number_emoji} - {raffle[1]['Title']}\n"
            e.description = description
            embeds.append(e)
        return e

    @raffle.command()
    async def reroll(self, ctx, channel: discord.TextChannel, messageid: int):
        """Reroll the winner for a raffle. Requires the channel and message id."""
        try:
            msg = await channel.get_message(messageid)
        except AttributeError:
            try:
                msg = await channel.fetch_message(messageid)
            except discord.HTTPException:
                return await ctx.send("Invalid message id.")
        except discord.HTTPException:
            return await ctx.send("Invalid message id.")
        try:
            await self.pick_winner(ctx.guild, channel, msg)
        except AttributeError:
            return await ctx.send("This is not a raffle message.")
        except IndexError:
            return await ctx.send(
                "Nice try slim. You can't add a reaction to a random msg "
                "and think that I am stupid enough to say you won something."
            )

    @commands.group(autohelp=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setraffle(self, ctx):
        """Set Raffle group command"""
        pass

    @setraffle.command()
    async def channel(self, ctx, channel: discord.TextChannel = None):
        """Set the output channel for raffles."""
        if channel:
            await self.config.guild(ctx.guild).Channel.set(channel.id)
            return await ctx.send(f"Raffle output channel set to {channel.mention}.")
        await self.config.guild(ctx.guild).Channel.clear()
        await ctx.send("Raffles will now be started where they were created.")

    def cog_unload(self):
        self.__unload()

    def __unload(self):
        self.load_check.cancel()

    async def start_checks(self, ctx, timer, title):
        timer = self.time_converter(timer)
        if len(title) > 35:
            await ctx.send("Title is too long. Must be 35 characters or less.")
            return None
        elif timer is None:
            await ctx.send("Incorrect time format. Please use help on this command for more information.")
            return None
        else:
            return timer

    async def _get_response(self, ctx, question, predicate):
        question = await ctx.send(question)
        resp = await ctx.bot.wait_for(
            "message",
            timeout=60,
            check=lambda m: (m.author == ctx.author and m.channel == ctx.channel and predicate(m)),
        )
        if ctx.channel.permissions_for(ctx.me).manage_messages:
            await resp.delete()
        await question.delete()
        return resp.content

    async def _get_roles(self, ctx):
        q = await ctx.send(
            "What role or roles are allowed to enter? Use commas to separate "
            "multiple entries. For example: `Admin, Patrons, super mod, helper`"
        )

        def predicate(m):
            if m.author == ctx.author and m.channel == ctx.channel:
                given = set(m.content.split(", "))
                guild_roles = {r.name for r in ctx.guild.roles}
                return guild_roles.issuperset(given)
            else:
                return False

        resp = await ctx.bot.wait_for("message", timeout=60, check=predicate)
        roles = []
        for name in resp.content.split(", "):
            for role in ctx.guild.roles:
                if name == role.name:
                    roles.append((name, role.id))
        await q.delete()
        if ctx.channel.permissions_for(ctx.me).manage_messages:
            await resp.delete()
        return roles

    async def _get_channel(self, ctx):
        channel_id = await self.config.guild(ctx.guild).Channel()
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = ctx.channel
        return channel

    async def raffle_setup(self, ctx):
        predicate1 = lambda m: len(m.content) <= 200

        def predicate2(m):
            try:
                if int(m.content) >= 1:
                    return True
                return False
            except ValueError:
                return False

        predicate3 = MessagePredicate.yes_or_no(ctx, ctx.channel, ctx.author)

        def predicate4(m):
            try:
                if int(m.content) >= 0:
                    return True
                return False
            except ValueError:
                return False

        q1 = "Please set a brief description (200 chars max)"
        q2 = (
            "Please set how many winners are pulled.\n**Note**: If there are "
            "more winners than entries, I will make everyone a winner."
        )
        q3 = "Would you like to set a 'days on server' requirement?"
        q4 = "Do you want to limit this raffle to specific roles?"

        description = await self._get_response(ctx, q1, predicate1)
        winners = await self._get_response(ctx, q2, predicate2)
        dos = 0
        roles = []

        if await self._get_response(ctx, q3, predicate3) == "yes":
            dos = await self._get_response(ctx, "How many days on the server are required?", predicate4)

        if await self._get_response(ctx, q4, predicate3) == "yes":
            roles = await self._get_roles(ctx)

        return description, int(winners), int(dos), roles

    async def raffle_worker(self):
        """Restarts raffle timers
        This worker will attempt to restart raffle timers incase of a cog reload or
        if the bot has been restart or shutdown. The task is only created when the cog
        is loaded, and is destroyed when it has finished.
        """
        try:
            await self.bot.wait_until_ready()
            guilds = [self.bot.get_guild(guild) for guild in await self.config.all_guilds()]
            coros = []
            for guild in guilds:
                raffles = await self.config.guild(guild).Raffles.all()
                if raffles:
                    now = calendar.timegm(datetime.utcnow().utctimetuple())
                    for key, value in raffles.items():
                        remaining = raffles[key]["Timestamp"] - now
                        if remaining <= 0:
                            await self.raffle_teardown(guild, raffles[key]["ID"])
                        else:
                            coros.append(self.raffle_timer(guild, raffles[key], remaining))
            await asyncio.gather(*coros)
        except Exception as e:
            print(e)

    async def raffle_timer(self, guild, raffle: dict, remaining: int):
        """Helper function for starting the raffle countdown.

        This function will silently pass when the unique raffle id is not found or
        if a raffle is empty. It will call `raffle_teardown` if the ID is still
        current when the sleep call has completed.

        Parameters
        ----------
        guild : Guild
            The guild object
        raffle : dict
            All of the raffle information gained from the config to include:
            ID, channel, message, timestamp, and entries.
        remaining : int
            Number of seconds remaining until the raffle should end
        """
        await asyncio.sleep(remaining)
        async with self.config.guild(guild).Raffles() as r:
            data = r.get(str(raffle["ID"]))
        if data:
            await self.raffle_teardown(guild, raffle["ID"])

    async def raffle_teardown(self, guild, message_id):
        raffles = await self.config.guild(guild).Raffles.all()
        channel = self.bot.get_channel(raffles[str(message_id)]["Channel"])

        errored = False
        try:
            msg = await channel.get_message(raffles[str(message_id)]["ID"])
        except AttributeError:
            try:
                msg = await channel.fetch_message(raffles[str(message_id)]["ID"])
            except discord.NotFound:
                errored = True
        except discord.errors.NotFound:
            errored = True
        if not errored:
            await self.pick_winner(guild, channel, msg)

        async with self.config.guild(guild).Raffles() as r:
            try:
                del r[str(message_id)]
            except KeyError:
                pass

    async def pick_winner(self, guild, channel, msg):
        reaction = next(filter(lambda x: x.emoji == "\U0001F39F", msg.reactions), None)
        if reaction is None:
            return await channel.send(
                "It appears there were no valid entries, so a winner for the raffle could not be picked."
            )
        users = [user for user in await reaction.users().flatten() if guild.get_member(user.id)]
        users.remove(self.bot.user)
        try:
            amt = int(msg.embeds[0].footer.text.split("Winners: ")[1][0])
        except AttributeError:  # the footer was not set in time
            return await channel.send("An error occurred, so a winner for the raffle could not be picked.")
        valid_entries = await self.validate_entries(users, msg)
        winners = random.sample(valid_entries, min(len(valid_entries), amt))
        if not winners:
            await channel.send(
                "It appears there were no valid entries, so a winner for the raffle could not be picked."
            )
        else:
            display = ", ".join(winner.mention for winner in winners)
            await channel.send(f"Congratulations {display}! You have won the {msg.embeds[0].title} raffle!")

    async def validate_entries(self, users, msg):
        dos, roles = msg.embeds[0].fields
        dos = int(dos.value)
        roles = roles.value.split(", ")

        try:
            if dos:
                users = [user for user in users if dos < (user.joined_at.now() - user.joined_at).days]

            if roles:
                users = [user for user in users if any(role in [r.name for r in user.roles] for role in roles)]
        except AttributeError:
            return None
        return users

    async def raffle_removal(self, ctx, message_id):
        async with self.config.guild(ctx.guild).Raffles() as r:
            try:
                del r[str(message_id)]
            except KeyError:
                pass

    @staticmethod
    def time_converter(units):
        try:
            return sum(int(x) * 60 ** i for i, x in enumerate(reversed(units.split(":"))))
        except ValueError:
            return None