import logging
import re
from datetime import datetime
from math import ceil
from typing import Optional, Union

import discord
from redbot.core import checks, commands
from redbot.core.utils.chat_formatting import bold, humanize_timedelta, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, close_menu, menu

from .abc import MixinMeta

log = logging.getLogger("red.angiedale.mod.info")


class Info(MixinMeta):
    """Information commands for moderators."""

    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    @commands.group()
    async def access(self, ctx: commands.Context):
        """Check channel access"""

    @access.command()
    async def compare(self, ctx: commands.Context, user: discord.Member, guild: int = None):
        """Compare channel access with [user]."""
        if user is None:
            return
        if guild is None:
            guild = ctx.guild
        else:
            guild = self.bot.get_guild(guild)

        try:
            tcs = guild.text_channels
            vcs = guild.voice_channels
        except AttributeError:
            return await ctx.send(
                "User is not in that guild or I do not have access to that guild."
            )

        author_text_channels = [
            c for c in tcs if c.permissions_for(ctx.author).read_messages is True
        ]
        author_voice_channels = [c for c in vcs if c.permissions_for(ctx.author).connect is True]

        user_text_channels = [c for c in tcs if c.permissions_for(user).read_messages is True]
        user_voice_channels = [c for c in vcs if c.permissions_for(user).connect is True]

        author_only_t = set(author_text_channels) - set(
            user_text_channels
        )  # text channels only the author has access to
        author_only_v = set(author_voice_channels) - set(
            user_voice_channels
        )  # voice channels only the author has access to

        user_only_t = set(user_text_channels) - set(
            author_text_channels
        )  # text channels only the user has access to
        user_only_v = set(user_voice_channels) - set(
            author_voice_channels
        )  # voice channels only the user has access to

        common_t = list(
            set([c for c in tcs]) - author_only_t - user_only_t
        )  # text channels that author and user have in common
        common_v = list(
            set([c for c in vcs]) - author_only_v - user_only_v
        )  # voice channels that author and user have in common

        theembed = []

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(name=f"Comparing {ctx.author} with {user}", icon_url=user.avatar_url)
        embed.add_field(
            name=f"Text channels in common ◈ {len(common_t)}",
            value=(f"{' ◈ '.join([c.name for c in common_t])}" if common_t else "~"),
            inline=False,
        )
        embed.add_field(
            name=f"Text channels {user} can exclusively access ◈ {len(user_only_t)}",
            value=(f"{' ◈ '.join([c.name for c in user_only_t])}" if user_only_t else "~"),
            inline=False,
        )
        embed.add_field(
            name=f"Text channels you can exclusively access ◈ {len(author_only_t)}",
            value=(f"{' ◈ '.join([c.name for c in author_only_t])}" if author_only_t else "~"),
            inline=False,
        )
        embed.add_field(
            name=f"Voice channels in common ◈ {len(common_v)}",
            value=(f"{' ◈ '.join([c.name for c in common_v])}" if common_v else "~"),
            inline=False,
        )
        embed.add_field(
            name=f"Voice channels {user} can exclusively access ◈ {len(user_only_v)}",
            value=(f"{' ◈ '.join([c.name for c in user_only_v])}" if user_only_v else "~"),
            inline=False,
        )
        embed.add_field(
            name=f"Voice channels you can exclusively access ◈ {len(author_only_v)}",
            value=(f"{' ◈ '.join([c.name for c in author_only_v])}" if author_only_v else "~"),
            inline=False,
        )

        theembed.append(embed)

        await menu(ctx, theembed, {"\N{CROSS MARK}": close_menu})

    @access.command(name="text")
    async def _text(self, ctx: commands.Context, user: discord.Member = None, guild: int = None):
        """Check text channel access."""
        if user is None:
            user = ctx.author
        if guild is None:
            guild = ctx.guild
        else:
            guild = self.bot.get_guild(guild)

        try:
            can_access = [
                c.name
                for c in guild.text_channels
                if c.permissions_for(user).read_messages == True
            ]
            text_channels = [c.name for c in guild.text_channels]
        except AttributeError:
            return await ctx.send(
                "User is not in that guild or I do not have access to that guild."
            )

        theembed = []

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f'{("You have" if user.id == ctx.author.id else str(user) + " has")} access to {len(can_access)} out of {len(text_channels)} text channels',
            icon_url=user.avatar_url,
        )
        embed.add_field(
            name="Can Access",
            value=(f"{' ◈ '.join(can_access)}" if can_access else "~"),
            inline=False,
        )
        embed.add_field(
            name="Can Not Access",
            value=(
                f"{' ◈ '.join(list(set(text_channels) - set(can_access)))}"
                if not len(list(set(text_channels) - set(can_access))) == 0
                else "~"
            ),
            inline=False,
        )

        theembed.append(embed)

        await menu(ctx, theembed, {"\N{CROSS MARK}": close_menu})

    @access.command()
    async def voice(self, ctx: commands.Context, user: discord.Member = None, guild: int = None):
        """Check voice channel access."""
        if user is None:
            user = ctx.author
        if guild is None:
            guild = ctx.guild
        else:
            guild = self.bot.get_guild(guild)

        try:
            can_access = [
                c.name for c in guild.voice_channels if c.permissions_for(user).connect is True
            ]
            voice_channels = [c.name for c in guild.voice_channels]
        except AttributeError:
            return await ctx.send(
                "User is not in that guild or I do not have access to that guild."
            )

        theembed = []

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
        embed.set_author(
            name=f'{("You have" if user.id == ctx.author.id else str(user) + " has")} access to {len(can_access)} out of {len(voice_channels)} voice channels',
            icon_url=user.avatar_url,
        )
        embed.add_field(
            name="Can Access",
            value=(f"{' ◈ '.join(can_access)}" if can_access else "~"),
            inline=False,
        )
        embed.add_field(
            name="Can Not Access",
            value=(
                f"{' ◈ '.join(list(set(voice_channels) - set(can_access)))}"
                if not len(list(set(voice_channels) - set(can_access))) == 0
                else "~"
            ),
            inline=False,
        )

        theembed.append(embed)

        await menu(ctx, theembed, {"\N{CROSS MARK}": close_menu})

    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    @commands.group(name="user")
    async def _user(self, ctx: commands.Context):
        """Check user information."""

    @_user.command()
    async def inrole(self, ctx: commands.Context, *, role: discord.Role):
        """Check members in the role specified."""
        guild = ctx.guild

        users_in_role = "\n".join(sorted(m.display_name for m in guild.members if role in m.roles))
        embed_list = []
        base_embed = discord.Embed(colour=await self.bot.get_embed_color(ctx))

        if len(users_in_role) == 0:
            embed = base_embed.copy()
            embed.description = bold(f"0 users found with the {role.mention} role")
            embed_list.append(embed)
            await menu(ctx, embed_list, {"\N{CROSS MARK}": close_menu})
        else:
            base_embed.description = bold(
                f"{len([m for m in guild.members if role in m.roles])} users found with the {role.mention} role\n"
            )
            for page in pagify(users_in_role, delims=["\n"], page_length=200):
                embed = base_embed.copy()
                embed.add_field(name="Users", value=page)
                embed_list.append(embed)
            final_embed_list = []
            for i, embed in enumerate(embed_list):
                embed.set_footer(text=f"Page {i + 1}/{len(embed_list)}")
                final_embed_list.append(embed)

            await menu(
                ctx,
                final_embed_list,
                DEFAULT_CONTROLS if len(final_embed_list) > 1 else {"\N{CROSS MARK}": close_menu},
            )

    @_user.command()
    async def joined(self, ctx: commands.Context, user: discord.Member = None):
        """Show when a user joined the guild."""
        if not user:
            user = ctx.author
        if user.joined_at:
            user_joined = user.joined_at.strftime("%d %b %Y %H:%M")
            since_joined = (ctx.message.created_at - user.joined_at).days
            joined_on = f"{user_joined} ({since_joined} days ago)"
        else:
            joined_on = "a mysterious date that not even Discord knows."

        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed(
                description=f"{user.mention} joined this guild on {joined_on}.",
                color=await ctx.embed_colour(),
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{user.display_name} joined this guild on {joined_on}.")

    @_user.command()
    async def perms(
        self,
        ctx: commands.Context,
        user: Optional[discord.Member] = None,
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel] = None,
    ):
        """Fetch a specific user's permissions."""
        if user is None:
            user = ctx.author
        if channel is None:
            channel = ctx.channel

        perms = channel.permissions_for(user)
        perms_we_have = []
        perms_we_dont = []
        for x, y in perms:
            if y == True:
                perms_we_have.append(x)
            else:
                perms_we_dont.append(x)

        if len(perms_we_have) == 0:
            hasperms = "+\tNothing"
        else:
            hasperms = ""
            for p in perms_we_have:
                hasperms += f'+\t{p.replace("_", " ").title().replace("Guild", "Server")}\n'
        if len(perms_we_dont) == 0:
            nothasperms = "-\tNothing"
        else:
            nothasperms = ""
            for p in perms_we_dont:
                nothasperms += f'+\t{p.replace("_", " ").title().replace("Guild", "Server")}\n'

        page = []
        embed = discord.Embed(color=await ctx.embed_colour())
        embed.set_author(
            name=f"Permissions for {user.name} in {channel.name}", icon_url=user.avatar_url
        )
        embed.add_field(name="\N{WHITE HEAVY CHECK MARK}", value=hasperms, inline=True)
        embed.add_field(name="\N{CROSS MARK}", value=nothasperms, inline=True)
        page.append(embed)

        await menu(ctx, page, {"\N{CROSS MARK}": close_menu})

    @_user.command()
    async def new(self, ctx: commands.Context, count: int = 5):
        """Lists the newest 5 members."""
        guild = ctx.guild
        count = max(min(count, 25), 5)
        members = sorted(guild.members, key=lambda m: m.joined_at, reverse=True)[:count]

        base_embed = discord.Embed(color=await ctx.embed_colour())
        base_embed.set_author(
            name=f"{count} newest members in {ctx.guild.name}", icon_url=self.bot.user.avatar_url
        )
        base_embed.set_thumbnail(url=ctx.guild.icon_url)

        n = 0
        p = 0
        embed_list = []
        timenow = datetime.utcnow()
        for m in members:
            jlist = humanize_timedelta(timedelta=timenow - m.joined_at).split(", ")
            clist = humanize_timedelta(timedelta=timenow - m.created_at).split(", ")
            joined = f"{jlist[0]}, {jlist[1]}" if len(jlist) > 1 else jlist[0]
            created = f"{clist[0]}, {clist[1]}" if len(clist) > 1 else clist[0]
            if n == 0:
                embed = base_embed.copy()
                p += 1
                embed.set_footer(text=f"Page {p}/{ceil(len(members) / 5)}")
            if n < 4:
                embed.add_field(
                    name=f"{m.name} ({m.id})",
                    value=f"Joined Server: {joined} ago\nJoined Discord: {created}\n\u200B",
                    inline=False,
                )
                n += 1
            else:
                embed.add_field(
                    name=f"{m.name} ({m.id})",
                    value=f"Joined Server: {joined} ago\nJoined Discord: {created}",
                    inline=False,
                )
                embed_list.append(embed)
                n = 0

        await menu(
            ctx,
            embed_list,
            DEFAULT_CONTROLS if len(embed_list) > 1 else {"\N{CROSS MARK}": close_menu},
        )

    @commands.group(aliases=["server"])
    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    async def guild(self, ctx: commands.Context):
        """Check guild information."""

    @guild.command(aliases=["rinfo"])
    async def roleinfo(self, ctx: commands.Context, role: discord.Role):
        """Shows role info."""
        page = []
        embed = discord.Embed(color=(role.color if role.color else await ctx.embed_colour()))
        embed.set_author(
            name=f"Role info for role ◈ {role.name}", icon_url=self.bot.user.avatar_url
        )
        embed.set_thumbnail(url=role.guild.icon_url)

        perms = role.permissions
        perms_we_have = []
        perms_we_dont = []
        for x, y in perms:
            if y == True:
                perms_we_have.append(x)
            else:
                perms_we_dont.append(x)

        if len(perms_we_have) == 0:
            hasperms = "+\tNothing"
        else:
            hasperms = ""
            for p in perms_we_have:
                hasperms += f'+\t{p.replace("_", " ").title().replace("Guild", "Server")}\n'
        if len(perms_we_dont) == 0:
            nothasperms = "-\tNothing"
        else:
            nothasperms = ""
            for p in perms_we_dont:
                nothasperms += f'+\t{p.replace("_", " ").title().replace("Guild", "Server")}\n'

        if role.managed:
            if role.is_integration():
                embed.description = "This role is managed by an integration."
            elif role.is_premium_subscriber():
                embed.description = (
                    f"This is the {self.bot.get_emoji(810817144824004608)} nitro booster role."
                )
            elif role.is_bot_managed():
                embed.description = "This role is related to a bot."

        embed.add_field(
            name="ID",
            value=role.id,
            inline=True,
        )
        embed.add_field(
            name="Color",
            value=role.color,
            inline=True,
        )
        embed.add_field(
            name="Users",
            value=len(role.members),
            inline=True,
        )
        embed.add_field(
            name="Permissions \N{WHITE HEAVY CHECK MARK}",
            value=hasperms,
            inline=True,
        )
        embed.add_field(
            name="Permissions \N{CROSS MARK}",
            value=nothasperms,
            inline=True,
        )
        embed.set_footer(text=f"Position in role list: {int(role.position) + 1} ◈ Created")
        embed.timestamp = role.created_at

        page.append(embed)

        await menu(ctx, page, {"\N{CROSS MARK}": close_menu})

    @guild.command()
    async def inviteinfo(
        self, ctx: commands.Context, invite: Union[discord.Member, discord.TextChannel, str] = None
    ):
        """Show invite info for specific invite or for all."""
        if not ctx.me.permissions_in(ctx.channel).manage_guild:
            return await ctx.maybe_send_embed(
                'I need the "Manage Server" permission to use this command.'
            )

        if isinstance(invite, discord.Member):
            invites = []
            allinvites = await ctx.guild.invites()
            for inv in allinvites:
                if inv.inviter == invite:
                    invites.append(inv)
        elif isinstance(invite, discord.TextChannel):
            invites = []
            allinvites = await ctx.guild.invites()
            for inv in allinvites:
                if inv.channel == invite:
                    invites.append(inv)
        elif invite:
            if "/" in invite:
                invite = invite.rsplit("/", 1)[1]

            invites = []
            for inv in await ctx.guild.invites():
                if inv.code == invite:
                    invites.append(inv)

            if len(invites) < 1:
                return await ctx.maybe_send_embed("The provided invite seems to be invalid.")
        else:
            invites = await ctx.guild.invites()

        if len(invites) < 1:
            return await ctx.maybe_send_embed("Can't find any invites to show.")

        p = 1
        embeds = []
        for i in invites:
            maxuses = i.max_uses
            if maxuses == 0:
                maxuses = "\N{INFINITY}"
            if i.max_age == 0:
                maxage = ""
            else:
                maxage = re.split(r",\s", humanize_timedelta(seconds=i.max_age))
                try:
                    maxage = f"Expires: {maxage[0]} {maxage[1]}"
                except ValueError:
                    pass
                except IndexError:
                    maxage = f"Expires: {maxage[0]}"

            embed = discord.Embed(color=await self.bot.get_embed_color(ctx))
            embed.title = f"Invites for {ctx.guild.name}"
            msg = f"{bold(i.url)}\n\n"
            msg += f"Uses: {i.uses}/{maxuses}\n"
            msg += f"Target Channel: {i.channel.mention}\n"
            msg += f"Created by: {i.inviter.mention}\n"
            msg += f"Created at: {i.created_at.strftime('%m-%d-%Y @ %H:%M:%S UTC')}\n"
            if i.temporary:
                msg += "Temporary invite\n"
            msg += maxage
            embed.description = msg
            if len(invites) > 1:
                embed.set_footer(text=f"Page {p}/{len(invites)}")
                p += 1

            embeds.append(embed)

        await menu(
            ctx, embeds, DEFAULT_CONTROLS if len(embeds) > 1 else {"\N{CROSS MARK}": close_menu}
        )

    @guild.group()
    async def list(self, ctx: commands.Context):
        """List out different things."""

    @list.command()
    @checks.mod_or_permissions(ban_members=True)
    async def bans(self, ctx: commands.Context):
        """Displays the server's banlist."""
        try:
            banlist = await ctx.guild.bans()
        except discord.errors.Forbidden:
            await ctx.send("I do not have the `Ban Members` permission.")
            return
        bancount = len(banlist)
        if bancount == 0:
            msg = "No users are banned from this server."
        else:
            msg = ""
            for user_obj in banlist:
                user_name = f"{user_obj.user.name}#{user_obj.user.discriminator}"
                msg += f"`{user_obj.user.id} - {user_name}`\n"

        banlist = sorted(msg)
        embed_list = []
        for page in pagify(msg, shorten_by=1400):
            embed = discord.Embed(
                description="**Total bans:** {}\n\n{}".format(bancount, page),
                color=await self.bot.get_embed_color(ctx),
            )
            embed_list.append(embed)
        await menu(
            ctx,
            embed_list,
            DEFAULT_CONTROLS if len(embed_list) > 1 else {"\N{CROSS MARK}": close_menu},
        )

    @list.command()
    async def channels(self, ctx: commands.Context):
        """
        List the channels of the current server
        """
        channels = ctx.guild.channels

        temp = dict()
        channels = sorted(channels, key=lambda c: c.position)
        for c in channels[:]:
            if isinstance(c, discord.CategoryChannel):
                channels.pop(channels.index(c))
                temp[c] = list()
        for c in channels[:]:
            if c.category:
                channels.pop(channels.index(c))
                temp[c.category].append(c)
        category_channels = sorted(
            [(cat, sorted(chans, key=lambda c: c.position)) for cat, chans in temp.items()],
            key=lambda t: t[0].position,
        )

        no_category_list = []
        for n in channels:
            no_category_list.append(n.name.replace("-", " ").title())

        channels_desc = "\n".join(no_category_list)

        thing = []
        for t in category_channels:
            newlinelist = ""
            for f in t[1]:
                newlinelist += (
                    f'\n{f.name.replace("-", " ").title()} ◈ **{str(f.type).title()}** ◈ {f.id}'
                )
            thing.append(f"{t[0].name} ◈ {t[0].id}\n" + newlinelist)
        categories_formed = "\a\a\a".join(thing)
        if channels_desc:
            final_string = f"{channels_desc}\t{categories_formed}"
        else:
            final_string = categories_formed

        embed_list = []

        base_embed = discord.Embed(color=await ctx.embed_colour())
        base_embed.set_author(
            name=f"{ctx.guild.name} has {len(ctx.guild.channels)} channel{'s' if len(ctx.guild.channels) > 1 else ''}",
            icon_url=self.bot.user.avatar_url,
        )
        base_embed.set_thumbnail(url=ctx.guild.icon_url)

        i = 1
        pages = list(pagify(final_string, delims=["\a\a\a", "\n"], page_length=1000))
        for page in pages:
            embed = base_embed.copy()
            if i == 1:
                if channels_desc:
                    page = page.split("\t")
                    embed.description = page[0]
                    page = page[1]
            if page.startswith("\a\a\a"):
                page = page[3:]
            entries = page.split("\a\a\a")
            for c in entries:
                if "\n\n" in c:
                    data = c.split("\n\n")
                    embed.add_field(name=data[0], value=data[1], inline=False)
                else:
                    embed.description = c
            embed.set_footer(text=f"Page {i}/{len(pages)}")
            embed_list.append(embed)
            i += 1

        await menu(
            ctx,
            embed_list,
            DEFAULT_CONTROLS if len(embed_list) > 1 else {"\N{CROSS MARK}": close_menu},
        )

    @list.command()
    async def roles(self, ctx: commands.Context):
        """Displays the server's roles."""
        form = "`{rpos:0{zpadding}}` ◈ `{rid}` ◈ `{rcolor}` ◈ {rment}"
        max_zpadding = max([len(str(r.position)) for r in ctx.guild.roles])
        rolelist = [
            form.format(
                rpos=r.position, zpadding=max_zpadding, rid=r.id, rment=r.mention, rcolor=r.color
            )
            for r in ctx.guild.roles
        ]

        rolelist = sorted(rolelist, reverse=True)
        rolelist = "\n".join(rolelist)
        embed_list = []
        pages = list(pagify(rolelist, shorten_by=1200))
        i = 1
        for page in pages:
            if page.startswith("\n"):
                page = page[1:]
            embed = discord.Embed(
                description=f"**Total roles:** {len(ctx.guild.roles)}\n\n{page}",
                colour=await ctx.embed_colour(),
            )
            embed.set_footer(text=f"Page {i}/{len(pages)}")
            embed_list.append(embed)
            i += 1

        await menu(
            ctx,
            embed_list,
            DEFAULT_CONTROLS if len(embed_list) > 1 else {"\N{CROSS MARK}": close_menu},
        )

    @list.command()
    async def invites(self, ctx: commands.Context):
        """List the servers invites."""
        invites = await ctx.guild.invites()

        if len(invites) < 1:
            return await ctx.maybe_send_embed("Can't find any invites to show.")

        invitedetails = ""
        i = 1
        for inv in sorted(invites, key=lambda u: u.uses, reverse=True):
            maxuses = inv.max_uses
            if maxuses == 0:
                maxuses = "\N{INFINITY}"
            invitedetails += f"{i}. {inv.url} [ {inv.uses} uses / {maxuses} max ]\n"
            i += 1

        p = 1
        embeds = []
        pages = list(pagify(invitedetails, delims=["\n"], shorten_by=16))
        for page in pages:
            embed = discord.Embed(title=f"Invites for {ctx.guild.name}", description=page)
            if len(pages) > 1:
                embed.set_footer(text=f"Page {p}/{len(pages)}")
                p += 1

            embeds.append(embed)

        await menu(
            ctx, embeds, DEFAULT_CONTROLS if len(embeds) > 1 else {"\N{CROSS MARK}": close_menu}
        )
