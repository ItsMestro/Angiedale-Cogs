import logging

import discord
from redbot.core import commands

from .abc import MixinMeta
from .polls import Poll, VoteType

log = logging.getLogger("red.angiedale.utility")


class Events(MixinMeta):
    """Listener events for Utility cog."""

    @commands.Cog.listener("on_raw_reaction_add")
    async def poll_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return

        if self.polls.get(payload.guild_id, None) is None:
            return

        if self.polls[payload.guild_id].get(payload.message_id, None) is None:
            return

        if await self.bot.cog_disabled_in_guild_raw(self.qualified_name, payload.guild_id):
            return

        poll: Poll = self.polls[payload.guild_id][payload.message_id]

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        poll.guild = guild

        member = payload.member
        if member is None or member.bot:
            return

        if not str(payload.emoji) in poll.options_to_emojis(return_as_string=True):
            if poll.channel.permissions_for(guild.me).manage_messages:
                message = await poll.fetch_message()
                try:
                    await message.remove_reaction(str(payload.emoji), member)
                except:
                    pass
                return

        if len(poll.roles) != 0:
            if not any(role in member.roles for role in poll.roles):
                try:
                    await member.send(
                        "You don't have any of the required roles to interact with this poll!"
                    )
                except:
                    pass
                return

        message = await poll.fetch_message()
        if message is None:
            return

        removed_vote = None
        text = ""
        for i, option in enumerate(poll.options):
            if poll.vote_type == VoteType.single_vote and str(option.emoji) != str(payload.emoji):
                try:
                    self.polls[payload.guild_id][payload.message_id].options[i].votes.remove(
                        member.id
                    )
                    removed_vote = option.to_string()
                    await message.remove_reaction(option.emoji, member)
                except ValueError:
                    pass

            if str(option.emoji) == str(payload.emoji):
                if payload.user_id not in poll.options[i].votes:
                    text = f"Successfully counted your vote for: {option.to_string()}"
                    self.polls[payload.guild_id][payload.message_id].options[i].votes.append(
                        payload.user_id
                    )
                else:
                    text = f"Removed your vote for: {option.to_string()}"
                    self.polls[payload.guild_id][payload.message_id].options[i].votes.remove(
                        payload.user_id
                    )

        if poll.vote_type == VoteType.single_vote and removed_vote is not None:
            text += f"\n\nAnd removed your previous vote for: {removed_vote}"

        await member.send(text)

        await self.update_cache(
            guild_id=guild.id,
            message_id=message.id,
            poll=self.polls[payload.guild_id][payload.message_id],
        )

    @commands.Cog.listener("on_raw_reaction_remove")
    async def poll_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return

        if self.polls.get(payload.guild_id, None) is None:
            return

        if self.polls[payload.guild_id].get(payload.message_id, None) is None:
            return

        if await self.bot.cog_disabled_in_guild_raw(self.qualified_name, payload.guild_id):
            return

        poll: Poll = self.polls[payload.guild_id][payload.message_id]

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        poll.guild = guild

        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        if not str(payload.emoji) in poll.options_to_emojis(return_as_string=True):
            return

        message = await poll.fetch_message()
        if message is None:
            return

        for i, option in enumerate(poll.options):
            if str(option.emoji) == str(payload.emoji):
                try:
                    self.polls[payload.guild_id][payload.message_id].options[i].votes.remove(
                        member.id
                    )
                    await member.send(f"Removed your vote for: {option.to_string()}")
                except ValueError:
                    pass
                return
