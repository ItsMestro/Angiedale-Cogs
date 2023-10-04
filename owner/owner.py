import asyncio
import logging
import os
import re
import shutil
from datetime import date, datetime, timedelta
from random import choice
from typing import Optional
from zipfile import ZipFile

import discord
import requests
from dateutil.easter import easter
from redbot.core import Config, bank, checks, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import (
    box,
    humanize_list,
    humanize_number,
    humanize_timedelta,
    inline,
    pagify,
)
from redbot.core.utils.menus import DEFAULT_CONTROLS, close_menu, menu
from redbot.core.utils.tunnel import Tunnel

log = logging.getLogger("red.angiedale.owner")


RUNNING_ANNOUNCEMENT = (
    "I am already announcing something. If you would like to make a"
    " different announcement please use `{prefix}announce cancel`"
    " first."
)


def is_owner_if_bank_global():
    """
    Command decorator. If the bank is global, it checks if the author is
    bot owner, otherwise it only checks
    if command was used in guild - it DOES NOT check any permissions.

    When used on the command, this should be combined
    with permissions check like `guildowner_or_permissions()`.
    """

    async def pred(ctx):
        author = ctx.author
        if not await bank.is_global():
            if not ctx.guild:
                return False
            return True
        else:
            return await ctx.bot.is_owner(author)

    return commands.check(pred)


class Owner(commands.Cog):
    """Bot set-up commands."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.interaction = []

        self.adminconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="OwnerAdmin"
        )
        self.adminconfig.register_global(serverlocked=False, schema_version=0)

        self.mutesconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Mutes"
        )

        self.statsconfig = Config.get_conf(
            self, identifier=1387000, force_registration=True, cog_name="Stats"
        )
        self.statsconfig.register_global(Channel=None, Message=None, bonk=0)

        self.__current_announcer = None
        self.statschannel = None
        self.statsmessage = None
        self.statstask: Optional[asyncio.Task] = None

        self.presence_task = asyncio.create_task(self.maybe_update_presence())

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    async def cog_load(self) -> None:
        await self.bot.wait_until_ready()
        lock = self.adminconfig.get_guilds_lock()
        async with lock:
            # This prevents the edge case of someone loading admin,
            # unloading it, loading it again during a migration
            current_schema = await self.adminconfig.schema_version()

            if current_schema == 0:
                await self.migrate_config_from_0_to_1()
                await self.adminconfig.schema_version.set(1)

        async with self.statsconfig.all() as sconfig:
            self.statschannel = sconfig["Channel"]
            self.statsmessage = sconfig["Message"]
        if self.statschannel:
            self.statstask = asyncio.create_task(self._update_stats())

    async def migrate_config_from_0_to_1(self) -> None:
        all_guilds = await self.adminconfig.all_guilds()

        for guild_id, guild_data in all_guilds.items():
            if guild_data.get("announce_ignore", False):
                async with self.adminconfig.guild_from_id(guild_id).all(
                    acquire_lock=False
                ) as guild_config:
                    guild_config.pop("announce_channel", None)
                    guild_config.pop("announce_ignore", None)

    def cog_unload(self):
        self.presence_task.cancel()
        if self.statstask:
            self.statstask.cancel()
        try:
            self.__current_announcer.cancel()
        except AttributeError:
            pass
        for user in self.interaction:
            asyncio.create_task(self.stop_interaction(user))

    async def _update_stats(self):
        await asyncio.sleep(30 * 1)
        while True:
            try:
                await self.check_statsembed()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(e, exc_info=e)
            await asyncio.sleep(60 * 10)

    async def check_statsembed(self):
        total_users = len(self.bot.users)
        servers = len(self.bot.guilds)
        commands = len(self.bot.commands)
        emojis = len(self.bot.emojis)
        bonkedusers = await self.statsconfig.bonk()
        latencies = self.bot.latencies
        uptime = humanize_timedelta(timedelta=datetime.utcnow() - self.bot.uptime)

        latencymsg = ""
        for shard, pingt in latencies:
            latencymsg += "Shard **{}/{}**: `{}ms`\n".format(
                shard + 1, len(latencies), round(pingt * 1000)
            )

        channel = self.bot.get_channel(self.statschannel)
        message = await channel.fetch_message(self.statsmessage)
        embed = message.embeds[0]

        embed.set_field_at(0, name="Serving Users", value=total_users)
        embed.set_field_at(1, name="In Servers", value=servers)
        embed.set_field_at(2, name="Commands", value=commands)
        embed.set_field_at(3, name="Emojis", value=emojis)
        embed.set_field_at(4, name="Bonked Users", value=bonkedusers)
        embed.set_field_at(5, name="Uptime", value=uptime, inline=False)
        embed.set_field_at(6, name="Latency", value=latencymsg, inline=False)

        embed.timestamp = datetime.utcnow()

        await message.edit(embed=embed)

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def setstatschannel(self, ctx, channel: discord.TextChannel = None):
        """Set a channel for displaying bot stats."""
        if not self.statsmessage and not channel:
            return await ctx.send("Please provide a channel to start displaying bot stats in.")
        response = []
        if self.statsmessage:
            if self.statstask:
                self.statstask.cancel()
            schannel = self.bot.get_channel(self.statschannel)
            smessage = await schannel.fetch_message(self.statsmessage)
            try:
                await smessage.delete()
            except:
                pass
            await self.statsconfig.clear_all()
            response.append("deleted the previous stats message")
        if channel:
            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
            embed.title = f"Statistics for {self.bot.user.name}"
            embed.set_thumbnail(url=self.bot.user.avatar_url)
            embed.add_field(name="Serving Users", value=0)
            embed.add_field(name="In Servers", value=0)
            embed.add_field(name="Commands", value=0)
            embed.add_field(name="Emojis", value=0)
            embed.add_field(name="Bonked Users", value=0)
            embed.add_field(name="Uptime", value=0, inline=False)
            embed.add_field(name="Latency", value=0, inline=False)
            embed.timestamp = datetime.utcnow()
            embed.set_footer(text="Updated")

            message = await channel.send(embed=embed)

            await self.statsconfig.Channel.set(channel.id)
            await self.statsconfig.Message.set(message.id)

            self.statschannel = channel.id
            self.statsmessage = message.id
            self.statstask = asyncio.create_task(self._update_stats())

            response.append(f"started displaying stats for {self.bot.user.name} in {channel.name}")

        await ctx.send(" and ".join(response).capitalize())

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user in self.interaction:
            channel = reaction.message.channel
            if isinstance(channel, discord.DMChannel):
                await self.stop_interaction(user)

    async def stop_interaction(self, user):
        self.interaction.remove(user)
        await user.send(("Session closed"))

    def is_announcing(self) -> bool:
        """
        Is the bot currently announcing something?
        :return:
        """
        if self.__current_announcer is None:
            return False

        return self.__current_announcer.active or False

    @commands.group(invoke_without_command=True)
    @commands.is_owner()
    async def announce(self, ctx: commands.Context, *, message: str):
        """Announce a message to all servers the bot is in."""
        if not self.is_announcing():
            announcer = Announcer(ctx, message, config=self.adminconfig)
            announcer.start()

            self.__current_announcer = announcer

            await ctx.send(("The announcement has begun."))
        else:
            prefix = ctx.clean_prefix
            await ctx.send((RUNNING_ANNOUNCEMENT).format(prefix=prefix))

    @announce.command(name="cancel")
    async def announce_cancel(self, ctx):
        """Cancel a running announce."""
        if not self.is_announcing():
            await ctx.send(("There is no currently running announcement."))
            return
        self.__current_announcer.cancel()
        await ctx.send(("The current announcement has been cancelled."))

    @commands.command()
    @commands.is_owner()
    async def serverlock(self, ctx: commands.Context):
        """Lock a bot to its current servers only."""
        serverlocked = await self.adminconfig.serverlocked()
        await self.adminconfig.serverlocked.set(not serverlocked)

        if serverlocked:
            await ctx.send(("The bot is no longer serverlocked."))
        else:
            await ctx.send(("The bot is now serverlocked."))

    async def say(self, ctx, channel: Optional[discord.TextChannel], text: str, files: list,mentions: discord.AllowedMentions = None,
        delete: int = None,):
        if not channel:
            channel = ctx.channel
        if not text and not files:
            await ctx.send_help()
            return

        # preparing context info in case of an error
        if files != []:
            error_message = (
                "Has files: yes\n"
                f"Number of files: {len(files)}\n"
                f"Files URL: " + ", ".join([x.url for x in ctx.message.attachments])
            )
        else:
            error_message = "Has files: no"

        # sending the message
        try:
            await channel.send(text, files=files, allowed_mentions=mentions, delete_after=delete)
        except discord.errors.HTTPException as e:
            if not ctx.guild.me.permissions_in(channel).send_messages:
                try:
                    await ctx.send(
                        ("I am not allowed to send messages in ") + channel.mention,
                        delete_after=2,
                    )
                except discord.errors.Forbidden:
                    await ctx.author.send(
                        ("I am not allowed to send messages in ") + channel.mention,
                        delete_after=15,
                    )
                    # If this fails then fuck the command author
            elif not ctx.guild.me.permissions_in(channel).attach_files:
                try:
                    await ctx.send(
                        ("I am not allowed to upload files in ") + channel.mention, delete_after=2
                    )
                except discord.errors.Forbidden:
                    await ctx.author.send(
                        ("I am not allowed to upload files in ") + channel.mention,
                        delete_after=15,
                    )
            else:
                log.error(
                    f"Unknown permissions error when sending a message.\n{error_message}",
                    exc_info=e,
                )

    @commands.command(name="say")
    @commands.is_owner()
    async def _say(self, ctx, channel: Optional[discord.TextChannel], *, text: str = ""):
        """
        Make the bot say what you want in the desired channel.

        If no channel is specified, the message will be send in the current channel.
        You can attach some files to upload them to Discord.

        Example usage :
        - `!say #general hello there`
        - `!say owo I have a file` (a file is attached to the command message)
        """

        files = await Tunnel.files_from_attatch(ctx.message)
        await self.say(ctx, channel, text, files)

    @commands.command(name="sayd", aliases=["sd"])
    @commands.is_owner()
    async def _saydelete(self, ctx, channel: Optional[discord.TextChannel], *, text: str = ""):
        """
        Same as say command, except it deletes your message.

        If the message wasn't removed, then I don't have enough permissions.
        """

        # download the files BEFORE deleting the message
        author = ctx.author
        files = await Tunnel.files_from_attatch(ctx.message)

        try:
            await ctx.message.delete()
        except discord.errors.Forbidden:
            try:
                await ctx.send(("Not enough permissions to delete messages."), delete_after=2)
            except discord.errors.Forbidden:
                await author.send(("Not enough permissions to delete messages."), delete_after=15)

        await self.say(ctx, channel, text, files)

    @commands.command(name="interact")
    @commands.is_owner()
    async def _interact(self, ctx, channel: discord.TextChannel = None):
        """Start receiving and sending messages as the bot through DM"""

        u = ctx.author
        if channel is None:
            if isinstance(ctx.channel, discord.DMChannel):
                await ctx.send(
                    (
                        "You need to give a channel to enable this in DM. You can "
                        "give the channel ID too."
                    )
                )
                return
            else:
                channel = ctx.channel

        if u in self.interaction:
            await ctx.send(("A session is already running."))
            return

        message = await u.send(
            (
                "I will start sending you messages from {0}.\n"
                "Just send me any message and I will send it in that channel.\n"
                "React with ❌ on this message to end the session.\n"
                "If no message was send or received in the last 5 minutes, "
                "the request will time out and stop."
            ).format(channel.mention)
        )
        await message.add_reaction("❌")
        self.interaction.append(u)

        while True:

            if u not in self.interaction:
                return

            try:
                message = await self.bot.wait_for("message", timeout=300)
            except asyncio.TimeoutError:
                await u.send(("Request timed out. Session closed"))
                self.interaction.remove(u)
                return

            if message.author == u and isinstance(message.channel, discord.DMChannel):
                files = await Tunnel.files_from_attatch(message)
                if message.content.startswith(tuple(await self.bot.get_valid_prefixes())):
                    return
                await channel.send(message.content, files=files)
            elif (
                message.channel != channel
                or message.author == channel.guild.me
                or message.author == u
            ):
                pass

            else:
                embed = discord.Embed()
                embed.set_author(
                    name="{} | {}".format(str(message.author), message.author.id),
                    icon_url=message.author.avatar_url,
                )
                embed.set_footer(text=message.created_at.strftime("%d %b %Y %H:%M"))
                embed.description = message.content
                embed.colour = message.author.color

                if message.attachments != []:
                    embed.set_image(url=message.attachments[0].url)

                await u.send(embed=embed)

    @commands.command(name="listguilds", aliases=["listservers", "guildlist", "serverlist"])
    @commands.is_owner()
    async def listguilds(self, ctx):
        """List the servers the bot is in."""
        guilds = sorted(self.bot.guilds, key=lambda g: -g.member_count)

        base_embed = discord.Embed(color=await ctx.embed_colour())

        base_embed.set_author(
            name=f"{self.bot.user.name} is in {len(guilds)} servers",
            icon_url=self.bot.user.avatar_url,
        )

        guild_list = []
        for g in guilds:
            entry = f"**{g.name}** ◈ {humanize_number(g.member_count)} Users ◈ {g.id}"
            guild_list.append(entry)

        final = "\n".join(guild_list)

        page_list = []
        pages = list(pagify(final, delims=["\n"], page_length=1000))

        i = 1
        for page in pages:
            embed = base_embed.copy()
            embed.description = page
            embed.set_footer(text=f"Page {i}/{len(pages)}")

            page_list.append(embed)
            i += 1

        await menu(
            ctx,
            page_list,
            DEFAULT_CONTROLS if len(page_list) > 1 else {"\N{CROSS MARK}": close_menu},
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if await self.adminconfig.serverlocked():
            if len(self.bot.guilds) == 1:  # will be 0 once left
                log.warning(
                    f"Leaving guild '{guild.name}' ({guild.id}) due to serverlock. You can "
                    "temporarily disable serverlock by starting up the bot with the --no-cogs flag."
                )
            else:
                log.info(f"Leaving guild '{guild.name}' ({guild.id}) due to serverlock.")
            await guild.leave()

    async def maybe_update_presence(self):
        await self.bot.wait_until_red_ready()
        delay = 90
        while True:
            try:
                await self.presence_updater()
                await asyncio.sleep(int(delay))
            except asyncio.CancelledError:
                break
            except:
                pass
            # except Exception as e:
            #     log.exception(e, exc_info=e)

    async def presence_updater(self):
        pattern = re.compile(rf"<@!?{self.bot.user.id}>")
        guilds = self.bot.guilds
        guild = next(g for g in guilds if not g.unavailable)
        try:
            current_game = str(guild.me.activity.name)
        except AttributeError:
            current_game = None
        _type = 0

        url = f"https://www.twitch.tv/itsmestro"
        prefix = await self.bot.get_valid_prefixes()
        status = discord.Status.online

        me = self.bot.user
        clean_prefix = pattern.sub(f"@{me.name}", prefix[0])
        total_users = len(self.bot.users)
        servers = str(len(self.bot.guilds))
        helpaddon = f"{clean_prefix}help"
        usersstatus = f"with {total_users} users"
        serversstatus = f"in {servers} servers"
        datetoday = date.today()
        wheneaster = easter(datetoday.year)
        if datetoday >= wheneaster and datetoday <= wheneaster + timedelta(days=7):
            statuses = [
                "with you <3",
                "with things",
                "with ink",
                "Splatoon",
                "in the bot channel",
                "with my owner",
                "Happy Easter",
                "Happy Easter",
                "with colored eggs",
                "with bunnies",
                "egghunt",
                usersstatus,
                serversstatus,
            ]
        elif datetoday.month == 2 and datetoday.day >= 14 and datetoday.day <= 15:
            statuses = [
                "with you <3",
                "with things",
                "with ink",
                "Splatoon",
                "in the bot channel",
                "with my owner",
                "Happy Valentine",
                "Happy Valentine",
                "cupid",
                "with love",
                "with a box of heart chocolate",
                "with my lover",
                "with my valentine",
                usersstatus,
                serversstatus,
            ]
        elif datetoday.month == 12 and datetoday.day >= 24 and datetoday.day < 31:
            statuses = [
                "with you <3",
                "with things",
                "with ink",
                "Splatoon",
                "in the bot channel",
                "with my owner",
                "Merry Christmas",
                "Happy Holidays",
                "Merry Squidmas",
                "the christmas tree",
                "with santa",
                "with gifts",
                "in the snow",
                usersstatus,
                serversstatus,
            ]
        elif (
            datetoday.month == 12
            and datetoday.day == 31
            or datetoday.month == 1
            and datetoday.day <= 7
        ):
            statuses = [
                "with you <3",
                "with things",
                "with ink",
                "Splatoon",
                "in the bot channel",
                "with my owner",
                "Happy New Year",
                "Happy New Year",
                "with fireworks",
                usersstatus,
                serversstatus,
            ]
        elif (
            datetoday.month == 11
            and datetoday.day == 31
            or datetoday.month == 11
            and datetoday.day <= 7
        ):
            statuses = [
                "with you <3",
                "with things",
                "with ink",
                "Splatoon",
                "in the bot channel",
                "with my owner",
                "Happy Halloween",
                "Happy Splatoween",
                "trick or treat",
                "with candy",
                "spooky",
                "with pumpkins",
                usersstatus,
                serversstatus,
            ]
        else:
            statuses = [
                "with you <3",
                "with things",
                "with ink",
                "Splatoon",
                "in the bot channel",
                "with my owner",
                "with Pearl",
                "with Marina",
                "with Callie",
                "with Marie",
                "with Agent 3",
                "with Agent 4",
                usersstatus,
                serversstatus,
            ]
        new_status = self.random_status(guild, statuses, helpaddon)
        new_status = " | ".join((new_status, helpaddon))
        if (current_game != new_status) or (current_game is None):
            await self.bot.change_presence(
                activity=discord.Activity(name=new_status, type=_type), status=status
            )

    def random_status(self, guild, statuses, helpaddon):
        try:
            current = str(guild.me.activity.name)
        except AttributeError:
            current = None
        new_statuses = [s for s in statuses if " | ".join((s, helpaddon)) != current]
        if len(new_statuses) > 1:
            return choice(new_statuses)
        elif len(new_statuses) == 1:
            return new_statuses[0]
        return current

    @is_owner_if_bank_global()
    @commands.guildowner_or_permissions(administrator=True)
    @commands.group()
    async def bankset(self, ctx):
        """Base command for bank settings."""

    @bankset.command(name="showsettings")
    async def bankset_showsettings(self, ctx):
        """Show the current bank settings."""
        cur_setting = await bank.is_global()
        if cur_setting:
            group = bank._config
        else:
            if not ctx.guild:
                return
            group = bank._config.guild(ctx.guild)
        group_data = await group.all()
        bank_name = group_data["bank_name"]
        bank_scope = ("Global") if cur_setting else ("Server")
        currency_name = group_data["currency"]
        default_balance = group_data["default_balance"]
        max_balance = group_data["max_balance"]

        settings = (
            "Bank settings:\n\nBank name: {bank_name}\nBank scope: {bank_scope}\n"
            "Currency: {currency_name}\nDefault balance: {default_balance}\n"
            "Maximum allowed balance: {maximum_bal}\n"
        ).format(
            bank_name=bank_name,
            bank_scope=bank_scope,
            currency_name=currency_name,
            default_balance=humanize_number(default_balance),
            maximum_bal=humanize_number(max_balance),
        )
        await ctx.send(box(settings))

    @bankset.command(name="toggleglobal")
    @commands.is_owner()
    async def bankset_toggleglobal(self, ctx, confirm: bool = False):
        """Toggle whether the bank is global or not.

        If the bank is global, it will become per-server.
        If the bank is per-server, it will become global.
        """
        cur_setting = await bank.is_global()

        word = ("per-server") if cur_setting else ("global")
        if confirm is False:
            await ctx.send(
                (
                    "This will toggle the bank to be {banktype}, deleting all accounts "
                    "in the process! If you're sure, type `{command}`"
                ).format(banktype=word, command=f"{ctx.clean_prefix}bankset toggleglobal yes")
            )
        else:
            await bank.set_global(not cur_setting)
            await ctx.send(("The bank is now {banktype}.").format(banktype=word))

    @is_owner_if_bank_global()
    @commands.guildowner_or_permissions(administrator=True)
    @bankset.command(name="bankname")
    async def bankset_bankname(self, ctx, *, name: str):
        """Set the bank's name."""
        await bank.set_bank_name(name, ctx.guild)
        await ctx.send(("Bank name has been set to: {name}").format(name=name))

    @is_owner_if_bank_global()
    @commands.guildowner_or_permissions(administrator=True)
    @bankset.command(name="creditsname")
    async def bankset_creditsname(self, ctx, *, name: str):
        """Set the name for the bank's currency."""
        await bank.set_currency_name(name, ctx.guild)
        await ctx.send(("Currency name has been set to: {name}").format(name=name))

    @is_owner_if_bank_global()
    @commands.guildowner_or_permissions(administrator=True)
    @bankset.command(name="maxbal")
    async def bankset_maxbal(self, ctx, *, amount: int):
        """Set the maximum balance a user can get."""
        try:
            await bank.set_max_balance(amount, ctx.guild)
        except ValueError:
            # noinspection PyProtectedMember
            return await ctx.send(
                ("Amount must be greater than zero and less than {max}.").format(
                    max=humanize_number(bank._MAX_BALANCE)
                )
            )
        await ctx.send(
            ("Maximum balance has been set to: {amount}").format(amount=humanize_number(amount))
        )

    @commands.command(name="forcerolemutes")
    @commands.is_owner()
    async def force_role_mutes(self, ctx: commands.Context, true_or_false: bool):
        """
        Whether or not to force role only mutes on the bot
        """
        await self.config.force_role_mutes.set(true_or_false)
        if true_or_false:
            await ctx.send(("Okay I will enforce role mutes before muting users."))
        else:
            await ctx.send(("Okay I will allow channel overwrites for muting users."))

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def dumpemotes(self, ctx, guild: int = None):
        """Dumps emotes from a server."""
        if guild:
            g = self.bot.get_guild(guild)
        else:
            g = ctx.guild
        path = f"{cog_data_path(self)}/{g.id}"
        message = await ctx.send("Give me a moment...")

        if not os.path.exists(path):
            os.makedirs(path)

        for emote in g.emojis:
            r = requests.get(emote.url)
            if emote.animated:
                with open(f"{path}/{emote.name}.gif", "wb") as f:
                    f.write(r.content)
            else:
                with open(f"{path}/{emote.name}.png", "wb") as f:
                    f.write(r.content)
            await asyncio.sleep(0.2)
        try:
            await message.delete()
        except:
            pass
        with ZipFile(f"{path}.zip", "w") as zip:
            for file in os.listdir(path):
                zip.write(f"{path}/{file}", file)

        with open(f"{path}.zip", "rb") as fp:
            await ctx.send(
                content="Here's your emotes!", file=discord.File(fp, f"{g.name} Emotes.zip")
            )

        os.remove(f"{path}.zip")
        shutil.rmtree(path)


class Announcer:
    def __init__(self, ctx: commands.Context, message: str, config=None):
        """
        :param ctx:
        :param message:
        :param config: Used to determine channel overrides
        """
        self.ctx = ctx
        self.message = message
        self.config = config

        self.active = None

    def start(self):
        """
        Starts an announcement.
        :return:
        """
        if self.active is None:
            self.active = True
            asyncio.create_task(self.announcer())

    def cancel(self):
        """
        Cancels a running announcement.
        :return:
        """
        self.active = False

    async def _get_announce_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        if await self.ctx.bot.cog_disabled_in_guild_raw("Admin", guild.id):
            return
        channel_id = await self.config.guild(guild).announce_channel()
        return guild.get_channel(channel_id)

    async def announcer(self):
        guild_list = self.ctx.bot.guilds
        failed = []
        async for g in AsyncIter(guild_list, delay=0.5):
            if not self.active:
                return

            channel = await self._get_announce_channel(g)

            if channel:
                if channel.permissions_for(g.me).send_messages:
                    try:
                        await channel.send(self.message)
                    except discord.Forbidden:
                        failed.append(str(g.id))
                else:
                    failed.append(str(g.id))

        if failed:
            msg = (
                ("I could not announce to the following server: ")
                if len(failed) == 1
                else ("I could not announce to the following servers: ")
            )
            msg += humanize_list(tuple(map(inline, failed)))
            await self.ctx.bot.send_to_owners(msg)
        self.active = False
