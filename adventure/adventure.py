# -*- coding: utf-8 -*-
import asyncio
import contextlib
import json
import logging
import os
import random
import re
import time
from datetime import date, datetime, timedelta
from math import ceil
from operator import itemgetter
from types import SimpleNamespace
from typing import List, Literal, MutableMapping, Optional, Union

import discord
from beautifultable import ALIGN_LEFT, BeautifulTable
from discord.ext.commands import CheckFailure
from discord.ext.commands.errors import BadArgument
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands import check, get_dict_converter
from redbot.core.data_manager import bundled_data_path, cog_data_path
from redbot.core.errors import BalanceTooHigh
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import box, escape, humanize_list, humanize_number, humanize_timedelta, pagify
from redbot.core.utils.common_filters import filter_various_mentions
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu, start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate

import adventure.charsheet
from . import bank
from .charsheet import (
    DEV_LIST,
    ORDER,
    RARITIES,
    BackpackFilterParser,
    Character,
    DayConverter,
    EquipableItemConverter,
    EquipmentConverter,
    GameSession,
    Item,
    ItemConverter,
    ItemsConverter,
    PercentageConverter,
    RarityConverter,
    SlotConverter,
    Stats,
    ThemeSetMonterConverter,
    ThemeSetPetConverter,
    calculate_sp,
    can_equip,
    equip_level,
    has_funds,
    no_dev_prompt,
    parse_timedelta,
)
from .menus import (
    BackpackMenu,
    BaseMenu,
    LeaderboardMenu,
    LeaderboardSource,
    NVScoreboardSource,
    ScoreBoardMenu,
    ScoreboardSource,
    SimpleSource,
    WeeklyScoreboardSource,
)

_ = Translator("Adventure", __file__)

log = logging.getLogger("red.angiedale.adventure")

REBIRTH_LVL = 20
REBIRTH_STEP = 10
_SCHEMA_VERSION = 4
_config: Config = None
TaxesConverter = get_dict_converter(delims=[" ", ",", ";"])


async def smart_embed(ctx, message, success=None, image=None):
    if ctx.guild:
        use_embeds = await _config.guild(ctx.guild).embed()
    else:
        use_embeds = True
    if use_embeds:
        if await ctx.embed_requested():
            if success is True:
                colour = discord.Colour.dark_green()
            elif success is False:
                colour = discord.Colour.dark_red()
            else:
                colour = await ctx.embed_colour()
            embed = discord.Embed(description=message, color=colour)
            if image:
                embed.set_thumbnail(url=image)
            return await ctx.send(embed=embed)
        else:
            return await ctx.send(message)
    return await ctx.send(message)


def check_global_setting_admin():
    """
    Command decorator. If the bank is not global, it checks if the author is 
    either a bot admin or has the manage_guild permission.
    """

    async def pred(ctx: commands.Context):
        author = ctx.author
        if not await bank.is_global():
            if not isinstance(ctx.channel, discord.abc.GuildChannel):
                return False
            if await ctx.bot.is_owner(author):
                return True
            if author == ctx.guild.owner:
                return True
            if ctx.channel.permissions_for(author).manage_guild:
                return True
            admin_role_ids = await ctx.bot.get_admin_role_ids(ctx.guild.id)
            for role in author.roles:
                if role.id in admin_role_ids:
                    return True
        else:
            return await ctx.bot.is_owner(author)

    return commands.check(pred)


def has_separated_economy():
    async def predicate(ctx):
        if not (ctx.cog and getattr(ctx.cog, "_separate_economy", False)):
            raise CheckFailure
        return True

    return check(predicate)


class AdventureResults:
    """Object to store recent adventure results."""

    def __init__(self, num_raids):
        self._num_raids = num_raids
        self._last_raids: MutableMapping[int, List] = {}

    def add_result(self, ctx: commands.Context, main_action, amount, num_ppl, success):
        """Add result to this object.
        :main_action: Main damage action taken by the adventurers
            (highest amount dealt). Should be either "attack" or
            "talk". Running will just be notated by a 0 amount.
        :amount: Amount dealt.
        :num_ppl: Number of people in adventure.
        :success: Whether adventure was successful or not.
        """
        if ctx.guild.id not in self._last_raids:
            self._last_raids[ctx.guild.id] = []

        if len(self._last_raids.get(ctx.guild.id, [])) >= self._num_raids:
            if ctx.guild.id in self._last_raids:
                self._last_raids[ctx.guild.id].pop(0)
        raid_dict = {}
        for var in ("main_action", "amount", "num_ppl", "success"):
            raid_dict[var] = locals()[var]
        self._last_raids[ctx.guild.id].append(raid_dict)

    def get_stat_range(self, ctx: commands.Context):
        """Return reasonable stat range for monster pool to have based
        on last few raids' damage.

        :returns: Dict with stat_type, min_stat and max_stat.
        """
        # how much % to increase damage for solo raiders so that they
        # can't just solo every monster based on their own average
        # damage
        if ctx.guild.id not in self._last_raids:
            self._last_raids[ctx.guild.id] = []
        SOLO_RAID_SCALE = 0.25
        if len(self._last_raids.get(ctx.guild.id, [])) == 0:
            return {"stat_type": "hp", "min_stat": 0, "max_stat": 0}

        # tally up stats for raids
        num_attack = 0
        dmg_amount = 0
        num_talk = 0
        talk_amount = 0
        num_wins = 0
        stat_type = "hp"
        avg_amount = 0
        raids = self._last_raids.get(ctx.guild.id, [])
        raid_count = len(raids)
        if raid_count == 0:
            num_wins = self._num_raids // 2
            raid_count = self._num_raids
            win_percent = 0.5
        else:
            for raid in raids:
                if raid["main_action"] == "attack":
                    num_attack += 1
                    dmg_amount += raid["amount"]
                    if raid["num_ppl"] == 1:
                        dmg_amount += raid["amount"] * SOLO_RAID_SCALE
                else:
                    num_talk += 1
                    talk_amount += raid["amount"]
                    if raid["num_ppl"] == 1:
                        talk_amount += raid["amount"] * SOLO_RAID_SCALE
                log.debug(f"raid dmg: {raid['amount']}")
                if raid["success"]:
                    num_wins += 1
            if num_attack > 0:
                avg_amount = dmg_amount / num_attack
            if dmg_amount < talk_amount:
                stat_type = "dipl"
                avg_amount = talk_amount / num_talk
            win_percent = num_wins / raid_count
            min_stat = avg_amount * 0.75
            max_stat = avg_amount * 2
            # want win % to be at least 50%, even when solo
            # if win % is below 50%, scale back min/max for easier mons
            if win_percent < 0.5:
                min_stat = avg_amount * win_percent
                max_stat = avg_amount * 1.5

        stats_dict = {}
        for var in ("stat_type", "min_stat", "max_stat", "win_percent"):
            stats_dict[var] = locals()[var]
        return stats_dict

    def __str__(self):
        return str(self._last_raids)


@cog_i18n(_)
class Adventure(commands.Cog):
    """Adventure, derived from the Goblins Adventure cog by locastan."""

    async def red_delete_data_for_user(
        self, *, requester: Literal["discord", "owner", "user", "user_strict"], user_id: int,
    ):
        await self.config.user_from_id(user_id).clear()
        await bank._config.user_from_id(
            user_id
        ).clear()  # This will only ever touch the separate currency, leaving bot economy to be handled by core.

    __version__ = "3.4.3.2"

    def __init__(self, bot: Red):
        self.bot = bot
        bank._init(bot)
        self._last_trade = {}
        self._adv_results = AdventureResults(20)
        self.emojis = SimpleNamespace()
        self.emojis.fumble = "\N{EXCLAMATION QUESTION MARK}\N{VARIATION SELECTOR-16}"
        self.emojis.level_up = "\N{BLACK UP-POINTING DOUBLE TRIANGLE}"
        self.emojis.rebirth = "\N{BABY SYMBOL}"
        self.emojis.attack = "\N{DAGGER KNIFE}\N{VARIATION SELECTOR-16}"
        self.emojis.magic = "\N{SPARKLES}"
        self.emojis.talk = "\N{LEFT SPEECH BUBBLE}\N{VARIATION SELECTOR-16}"
        self.emojis.pray = "\N{PERSON WITH FOLDED HANDS}"
        self.emojis.run = "\N{RUNNER}\N{ZERO WIDTH JOINER}\N{MALE SIGN}\N{VARIATION SELECTOR-16}"
        self.emojis.crit = "\N{COLLISION SYMBOL}"
        self.emojis.magic_crit = "\N{HIGH VOLTAGE SIGN}"
        self.emojis.berserk = "\N{RIGHT ANGER BUBBLE}\N{VARIATION SELECTOR-16}"
        self.emojis.dice = "\N{GAME DIE}"
        self.emojis.yes = "\N{WHITE HEAVY CHECK MARK}"
        self.emojis.no = "\N{NEGATIVE SQUARED CROSS MARK}"
        self.emojis.sell = "\N{MONEY BAG}"
        self.emojis.skills = SimpleNamespace()
        self.emojis.skills.bless = "\N{SCROLL}"
        self.emojis.skills.psychic = "\N{SIX POINTED STAR WITH MIDDLE DOT}"
        self.emojis.skills.berserker = self.emojis.berserk
        self.emojis.skills.wizzard = self.emojis.magic_crit
        self.emojis.skills.bard = "\N{EIGHTH NOTE}\N{BEAMED EIGHTH NOTES}\N{BEAMED SIXTEENTH NOTES}"
        self.emojis.hp = "\N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16}"
        self.emojis.dipl = self.emojis.talk

        self._adventure_actions = [
            self.emojis.attack,
            self.emojis.magic,
            self.emojis.talk,
            self.emojis.pray,
            self.emojis.run,
        ]
        self._adventure_controls = {
            "fight": self.emojis.attack,
            "magic": self.emojis.magic,
            "talk": self.emojis.talk,
            "pray": self.emojis.pray,
            "run": self.emojis.run,
        }
        self._order = [
            "head",
            "neck",
            "chest",
            "gloves",
            "belt",
            "legs",
            "boots",
            "left",
            "right",
            "two handed",
            "ring",
            "charm",
        ]
        self._treasure_controls = {
            self.emojis.yes: "equip",
            self.emojis.no: "backpack",
            self.emojis.sell: "sell",
        }
        self._yes_no_controls = {self.emojis.yes: "yes", self.emojis.no: "no"}

        self._adventure_countdown = {}
        self._rewards = {}
        self._reward_message = {}
        self._loss_message = {}
        self._trader_countdown = {}
        self._current_traders = {}
        self._curent_trader_stock = {}
        self._sessions: MutableMapping[int, GameSession] = {}
        self._react_messaged = []
        self.tasks = {}
        self.locks: MutableMapping[int, asyncio.Lock] = {}
        self.gb_task = None

        self.config = Config.get_conf(self, 1387005, cog_name="Adventure", force_registration=True)
        self._daily_bonus = {}
        self._separate_economy = None

        default_user = {
            "exp": 0,
            "lvl": 1,
            "att": 0,
            "cha": 0,
            "int": 0,
            "last_skill_reset": 0,
            "last_known_currency": 0,
            "last_currency_check": 0,
            "treasure": [0, 0, 0, 0, 0, 0],
            "items": {
                "head": {},
                "neck": {},
                "chest": {},
                "gloves": {},
                "belt": {},
                "legs": {},
                "boots": {},
                "left": {},
                "right": {},
                "ring": {},
                "charm": {},
                "backpack": {},
            },
            "loadouts": {},
            "class": {"name": _("Hero"), "ability": False, "desc": _("Your basic adventuring hero."), "cooldown": 0,},
            "skill": {"pool": 0, "att": 0, "cha": 0, "int": 0},
            "adventures": {
                "wins": 0,
                "loses": 0,
                "fight": 0,
                "spell": 0,
                "talk": 0,
                "pray": 0,
                "run": 0,
                "fumbles": 0,
            },
            "nega": {"wins": 0, "loses": 0, "xp__earnings": 0, "gold__losses": 0,},
        }

        default_guild = {
            "cart_channels": [],
            "god_name": "Kanna Kamui",
            "cart_name": "Angiedale's Supplies",
            "embed": True,
            "cooldown": 0,
            "cartroom": None,
            "cart_timeout": 10800,
            "cooldown_timer_manual": 120,
            "rebirth_cost": 100.0,
            "disallow_withdraw": True,
            "max_allowed_withdraw": 50000,
        }
        default_global = {
            "god_name": _("Kanna Kamui"),
            "cart_name": _("Angiedale's Supplies"),
            "theme": "default",
            "restrict": False,
            "embed": True,
            "enable_chests": True,
            "currentweek": date.today().isocalendar()[1],
            "schema_version": 1,
            "rebirth_cost": 100.0,
            "themes": {},
            "daily_bonus": {"1": 0, "2": 0.25, "3": 0, "4": 0.25, "5": 0.5, "6": 1.0, "7": 1.0},
            "tax_brackets": {},
            "separate_economy": False,
            "to_conversion_rate": 5,
            "from_conversion_rate": 10,
            "max_allowed_withdraw": 50000,
            "disallow_withdraw": False,
            "easy_mode": False,
        }
        self.RAISINS: list = None
        self.THREATEE: list = None
        self.TR_GEAR_SET: dict = None
        self.ATTRIBS: dict = None
        self.MONSTERS: dict = None
        self.AS_MONSTERS: dict = None
        self.MONSTER_NOW: dict = None
        self.LOCATIONS: list = None
        self.PETS: dict = None

        self.config.register_guild(**default_guild)
        self.config.register_global(**default_global)
        self.config.register_user(**default_user)
        self.cleanup_loop = self.bot.loop.create_task(self.cleanup_tasks())
        log.debug("Creating Task")
        self._init_task = self.bot.loop.create_task(self.initialize())
        self._ready_event = asyncio.Event()

    async def cog_before_invoke(self, ctx: commands.Context):
        await self._ready_event.wait()
        if ctx.author.id in self.locks and self.locks[ctx.author.id].locked():
            raise CheckFailure(f"There's an active lock for this user ({ctx.author.id})")
        return True

    @staticmethod
    def is_dev(user: Union[discord.User, discord.Member]):
        return user.id in DEV_LIST

    async def initialize(self):
        """This will load all the bundled data into respective variables."""
        await self.bot.wait_until_red_ready()
        try:
            global _config
            _config = self.config
            theme = await self.config.theme()
            self._separate_economy = await self.config.separate_economy()
            if theme in {"default"}:
                get_path = bundled_data_path
            else:
                get_path = cog_data_path

            as_monster_fp = get_path(self) / f"{theme}" / "as_monsters.json"
            attribs_fp = get_path(self) / f"{theme}" / "attribs.json"
            locations_fp = get_path(self) / f"{theme}" / "locations.json"
            monster_fp = get_path(self) / f"{theme}" / "monsters.json"
            pets_fp = get_path(self) / f"{theme}" / "pets.json"
            raisins_fp = get_path(self) / f"{theme}" / "raisins.json"
            threatee_fp = get_path(self) / f"{theme}" / "threatee.json"
            tr_set_fp = get_path(self) / f"{theme}" / "tr_set.json"
            prefixes_fp = get_path(self) / f"{theme}" / "prefixes.json"
            materials_fp = get_path(self) / f"{theme}" / "materials.json"
            equipment_fp = get_path(self) / f"{theme}" / "equipment.json"
            suffixes_fp = get_path(self) / f"{theme}" / "suffixes.json"
            set_bonuses = get_path(self) / f"{theme}" / "set_bonuses.json"
            files = {
                "pets": pets_fp,
                "attr": attribs_fp,
                "monster": monster_fp,
                "location": locations_fp,
                "raisins": raisins_fp,
                "threatee": threatee_fp,
                "set": tr_set_fp,
                "as_monsters": as_monster_fp,
                "prefixes": prefixes_fp,
                "materials": materials_fp,
                "equipment": equipment_fp,
                "suffixes": suffixes_fp,
                "set_bonuses": set_bonuses,
            }
            for (name, file) in files.items():
                if not file.exists():
                    files[name] = bundled_data_path(self) / "default" / f"{file.name}"

            with files["pets"].open("r") as f:
                self.PETS = json.load(f)
            with files["attr"].open("r") as f:
                self.ATTRIBS = json.load(f)
            with files["monster"].open("r") as f:
                self.MONSTERS = json.load(f)
            with files["as_monsters"].open("r") as f:
                self.AS_MONSTERS = json.load(f)
            with files["location"].open("r") as f:
                self.LOCATIONS = json.load(f)
            with files["raisins"].open("r") as f:
                self.RAISINS = json.load(f)
            with files["threatee"].open("r") as f:
                self.THREATEE = json.load(f)
            with files["set"].open("r") as f:
                self.TR_GEAR_SET = json.load(f)
            with files["prefixes"].open("r") as f:
                self.PREFIXES = json.load(f)
            with files["materials"].open("r") as f:
                self.MATERIALS = json.load(f)
            with files["equipment"].open("r") as f:
                self.EQUIPMENT = json.load(f)
            with files["suffixes"].open("r") as f:
                self.SUFFIXES = json.load(f)
            with files["set_bonuses"].open("r") as f:
                self.SET_BONUSES = json.load(f)

            if not all(
                i
                for i in [
                    len(self.PETS) > 0,
                    len(self.ATTRIBS) > 0,
                    len(self.MONSTERS) > 0,
                    len(self.LOCATIONS) > 0,
                    len(self.RAISINS) > 0,
                    len(self.THREATEE) > 0,
                    len(self.TR_GEAR_SET) > 0,
                    len(self.PREFIXES) > 0,
                    len(self.MATERIALS) > 0,
                    len(self.EQUIPMENT) > 0,
                    len(self.SUFFIXES) > 0,
                    len(self.SET_BONUSES) > 0,
                ]
            ):
                log.critical(f"{theme} theme is invalid, resetting it to the default theme.")
                await self.config.theme.set("default")
                await self.initialize()
                return
            adventure.charsheet.TR_GEAR_SET = self.TR_GEAR_SET
            adventure.charsheet.PETS = self.PETS
            adventure.charsheet.REBIRTH_LVL = REBIRTH_LVL
            adventure.charsheet.REBIRTH_STEP = REBIRTH_STEP
            adventure.charsheet.SET_BONUSES = self.SET_BONUSES
            await self._migrate_config(from_version=await self.config.schema_version(), to_version=_SCHEMA_VERSION)
            self._daily_bonus = await self.config.daily_bonus.all()
        except Exception as err:
            log.exception("There was an error starting up the cog", exc_info=err)
        else:
            self._ready_event.set()
            self.gb_task = self.bot.loop.create_task(self._garbage_collection())

    async def cleanup_tasks(self):
        await self._ready_event.wait()
        while self is self.bot.get_cog("Adventure"):
            to_delete = []
            for (msg_id, task) in self.tasks.items():
                if task.done():
                    to_delete.append(msg_id)
            for task in to_delete:
                del self.tasks[task]
            await asyncio.sleep(300)

    async def _migrate_config(self, from_version: int, to_version: int) -> None:
        log.debug(f"from_version: {from_version} to_version:{to_version}")
        if from_version == to_version:
            return
        if from_version < 2 <= to_version:
            group = self.config._get_base_group(self.config.USER)
            accounts = await group.all()
            tmp = accounts.copy()
            async with group.all() as adventurers_data:
                for user in tmp:
                    new_backpack = {}
                    new_loadout = {}
                    user_equipped_items = adventurers_data[user]["items"]
                    for slot in user_equipped_items.keys():
                        if user_equipped_items[slot]:
                            for (slot_item_name, slot_item) in list(user_equipped_items[slot].items())[:1]:
                                new_name, slot_item = self._convert_item_migration(slot_item_name, slot_item)
                                adventurers_data[user]["items"][slot] = {new_name: slot_item}
                    if "backpack" not in adventurers_data[user]:
                        adventurers_data[user]["backpack"] = {}
                    for (backpack_item_name, backpack_item) in adventurers_data[user]["backpack"].items():
                        new_name, backpack_item = self._convert_item_migration(backpack_item_name, backpack_item)
                        new_backpack[new_name] = backpack_item
                    adventurers_data[user]["backpack"] = new_backpack
                    if "loadouts" not in adventurers_data[user]:
                        adventurers_data[user]["loadouts"] = {}
                    try:
                        for (loadout_name, loadout) in adventurers_data[user]["loadouts"].items():
                            for (slot, equipped_loadout) in loadout.items():
                                new_loadout[slot] = {}
                                for (loadout_item_name, loadout_item) in equipped_loadout.items():

                                    new_name, loadout_item = self._convert_item_migration(
                                        loadout_item_name, loadout_item
                                    )
                                    new_loadout[slot][new_name] = loadout_item
                        adventurers_data[user]["loadouts"] = new_loadout
                    except Exception:
                        adventurers_data[user]["loadouts"] = {}
            await self.config.schema_version.set(2)
            from_version = 2
        if from_version < 3 <= to_version:
            group = self.config._get_base_group(self.config.USER)
            accounts = await group.all()
            tmp = accounts.copy()
            async with group.all() as adventurers_data:
                for user in tmp:
                    new_loadout = {}
                    if "loadouts" not in adventurers_data[user]:
                        adventurers_data[user]["loadouts"] = {}
                    try:
                        for (loadout_name, loadout) in adventurers_data[user]["loadouts"].items():
                            if loadout_name in {
                                "head",
                                "neck",
                                "chest",
                                "gloves",
                                "belt",
                                "legs",
                                "boots",
                                "left",
                                "right",
                                "ring",
                                "charm",
                            }:
                                continue
                            new_loadout[loadout_name] = loadout
                        adventurers_data[user]["loadouts"] = new_loadout
                    except Exception:
                        adventurers_data[user]["loadouts"] = {}
            await self.config.schema_version.set(3)

        if from_version < 4 <= to_version:
            group = self.config._get_base_group(self.config.USER)
            accounts = await group.all()
            tmp = accounts.copy()
            async with group.all() as adventurers_data:
                async for user in AsyncIter(tmp, steps=100):
                    if "items" in tmp[user]:
                        equipped = tmp[user]["items"]
                        for slot, item in equipped.items():
                            for item_name, item_data in item.items():
                                if "King Solomos" in item_name:
                                    del adventurers_data[user]["items"][slot][item_name]
                                    item_name = item_name.replace("Solomos", "Solomons")
                                    adventurers_data[user]["items"][slot][item_name] = item_data
                    if "loadouts" in tmp[user]:
                        loadout = tmp[user]["loadouts"]
                        for loadout_name, loadout_data in loadout.items():
                            for slot, item in equipped.items():
                                for item_name, item_data in item.items():
                                    if "King Solomos" in item_name:
                                        del adventurers_data[user]["loadouts"][loadout_name][slot][item_name]
                                        item_name = item_name.replace("Solomos", "Solomons")
                                        adventurers_data[user]["loadouts"][loadout_name][slot][item_name] = item_data
                    if "backpack" in tmp[user]:
                        backpack = tmp[user]["backpack"]
                        async for item_name, item_data in AsyncIter(backpack.items(), steps=100):
                            if "King Solomos" in item_name:
                                del adventurers_data[user]["backpack"][item_name]
                                item_name = item_name.replace("Solomos", "Solomons")
                                adventurers_data[user]["backpack"][item_name] = item_data
            await self.config.schema_version.set(4)

    def _convert_item_migration(self, item_name, item_dict):
        new_name = item_name
        if "name" in item_dict:
            del item_dict["name"]
        if "rarity" not in item_dict:
            item_dict["rarity"] = "common"
        if item_dict["rarity"] == "legendary":
            new_name = item_name.replace("{Legendary:'", "").replace("legendary:'", "").replace("'}", "")
        if item_dict["rarity"] == "epic":
            new_name = item_name.replace("[", "").replace("]", "")
        if item_dict["rarity"] == "rare":
            new_name = item_name.replace("_", " ").replace(".", "")
        if item_dict["rarity"] == "set":
            new_name = (
                item_name.replace("{Gear_Set:'", "")
                .replace("{gear_set:'", "")
                .replace("{Gear Set:'", "")
                .replace("'}", "")
            )
        if item_dict["rarity"] != "set":
            if "bonus" in item_dict:
                del item_dict["bonus"]
            if "parts" in item_dict:
                del item_dict["parts"]
            if "set" in item_dict:
                del item_dict["set"]
        return (new_name, item_dict)

    def in_adventure(self, ctx=None, user=None):
        author = user or ctx.author
        sessions = self._sessions
        if not sessions:
            return False
        participants_ids = set(
            [
                p.id
                for _loop, session in self._sessions.items()
                for p in [*session.fight, *session.magic, *session.pray, *session.talk, *session.run,]
            ]
        )
        return bool(author.id in participants_ids)

    async def allow_in_dm(self, ctx):
        """Checks if the bank is global and allows the command in dm."""
        if ctx.guild is not None:
            return True
        return bool(ctx.guild is None and await bank.is_global())

    def get_lock(self, member: discord.User):
        if member.id not in self.locks:
            self.locks[member.id] = asyncio.Lock()
        return self.locks[member.id]

    @staticmethod
    def escape(t: str) -> str:
        return escape(filter_various_mentions(t), mass_mentions=True, formatting=True)

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    @commands.is_owner()
    async def makecart(self, ctx: commands.Context):
        """[Dev] Force a cart to appear."""
        if not await no_dev_prompt(ctx):
            return
        await self._trader(ctx, True)

    async def _genitem(self, rarity: str = None, slot: str = None):
        """Generate an item."""
        if rarity == "set":
            items = list(self.TR_GEAR_SET.items())
            items = (
                [
                    i
                    for i in items
                    if i[1]["slot"] == [slot] or (slot == "two handed" and i[1]["slot"] == ["left", "right"])
                ]
                if slot
                else items
            )
            item_name, item_data = random.choice(items)
            return Item.from_json({item_name: item_data})

        RARE_INDEX = RARITIES.index("rare")
        EPIC_INDEX = RARITIES.index("epic")
        PREFIX_CHANCE = {"rare": 0.5, "epic": 0.75, "legendary": 0.9, "ascended": 1.0, "set": 0}
        SUFFIX_CHANCE = {"epic": 0.5, "legendary": 0.75, "ascended": 0.5}

        if rarity not in RARITIES:
            rarity = "normal"
        if slot is None:
            slot = random.choice(ORDER)
        name = ""
        stats = {"att": 0, "cha": 0, "int": 0, "dex": 0, "luck": 0}

        def add_stats(word_stats):
            """Add stats in word's dict to local stats dict."""
            for stat in stats.keys():
                if stat in word_stats:
                    stats[stat] += word_stats[stat]

        # only rare and above should have prefix with PREFIX_CHANCE
        if RARITIES.index(rarity) >= RARE_INDEX and random.random() <= PREFIX_CHANCE[rarity]:
            #  log.debug(f"Prefix %: {PREFIX_CHANCE[rarity]}")
            prefix, prefix_stats = random.choice(list(self.PREFIXES.items()))
            name += f"{prefix} "
            add_stats(prefix_stats)

        material, material_stat = random.choice(list(self.MATERIALS[rarity].items()))
        name += f"{material} "
        for stat in stats.keys():
            stats[stat] += material_stat

        equipment, equipment_stats = random.choice(list(self.EQUIPMENT[slot].items()))
        name += f"{equipment}"
        add_stats(equipment_stats)

        # only epic and above should have suffix with SUFFIX_CHANCE
        if RARITIES.index(rarity) >= EPIC_INDEX and random.random() <= SUFFIX_CHANCE[rarity]:
            #  log.debug(f"Suffix %: {SUFFIX_CHANCE[rarity]}")
            suffix, suffix_stats = random.choice(list(self.SUFFIXES.items()))
            of_keyword = "of" if "the" not in suffix_stats else "of the"
            name += f" {of_keyword} {suffix}"
            add_stats(suffix_stats)

        slot_list = [slot] if slot != "two handed" else ["left", "right"]
        return Item(
            name=name,
            slot=slot_list,
            rarity=rarity,
            att=stats["att"],
            int=stats["int"],
            cha=stats["cha"],
            dex=stats["dex"],
            luck=stats["luck"],
            owned=1,
            parts=1,
        )

    @commands.command()
    @commands.is_owner()
    async def genitems(self, ctx: commands.Context, rarity: str, slot: str, num: int = 1):
        """[Dev] Generate random items."""
        if not await no_dev_prompt(ctx):
            return
        user = ctx.author
        rarity = rarity.lower()
        slot = slot.lower()
        if rarity not in RARITIES:
            return await smart_embed(
                ctx, _("Invalid rarity; choose one of {list}.").format(list=humanize_list(RARITIES)),
            )
        elif slot not in ORDER:
            return await smart_embed(ctx, _("Invalid slot; choose one of {list}.").format(list=humanize_list(ORDER)))
        async with self.get_lock(user):
            try:
                c = await Character.from_json(self.config, user, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            for _loop_counter in range(num):
                await c.add_to_backpack(await self._genitem(rarity, slot))
            await self.config.user(ctx.author).set(await c.to_json(self.config))
        await ctx.invoke(self._backpack)

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    @commands.is_owner()
    async def copyuser(self, ctx: commands.Context, user_id: int):
        """[Owner] Copy another members data to yourself.

        Note this overrides your current data.
        """
        user_data = await self.config.user_from_id(user_id).all()
        await self.config.user(ctx.author).set(user_data)
        await ctx.tick()

    @commands.command(name="ebackpack")
    @commands.bot_has_permissions(add_reactions=True)
    async def commands_equipable_backpack(
        self,
        ctx: commands.Context,
        show_diff: Optional[bool] = False,
        rarity: Optional[RarityConverter] = None,
        *,
        slot: Optional[SlotConverter] = None,
    ):
        """This shows the contents of your backpack that can be equipped.

        Give it a rarity and/or slot to filter what backpack items to show.

        Note: An item **degrade** level is how many rebirths it will last, before it is broken down.
        """
        assert isinstance(rarity, str) or rarity is None
        assert isinstance(slot, str) or slot is None
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        if not ctx.invoked_subcommand:
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if rarity:
                rarity = rarity.lower()
                if rarity not in RARITIES:
                    return await smart_embed(
                        ctx, _("{} is not a valid rarity, select one of {}").format(rarity, humanize_list(RARITIES)),
                    )
            if slot:
                slot = slot.lower()
                if slot not in ORDER:
                    return await smart_embed(
                        ctx, _("{} is not a valid slot, select one of {}").format(slot, humanize_list(ORDER)),
                    )

            backpack_pages = await c.get_backpack(rarity=rarity, slot=slot, show_delta=show_diff, equippable=True)
            if backpack_pages:
                await BackpackMenu(
                    source=SimpleSource(backpack_pages),
                    help_command=self.commands_equipable_backpack,
                    delete_message_after=True,
                    clear_reactions_after=True,
                    timeout=60,
                ).start(ctx=ctx)
            else:
                return await smart_embed(ctx, _("You have no equippable items that match this query."),)

    @commands.group(name="cbackpack")
    @commands.bot_has_permissions(add_reactions=True)
    async def commands_cbackpack(
        self, ctx: commands.Context,
    ):
        """Complex backpack management tools.

        Please read the usage instructions [here](https://github.com/aikaterna/gobcog/blob/master/docs/cbackpack.md)
        """

    @commands_cbackpack.command(name="show")
    async def commands_cbackpack_show(
        self, ctx: commands.Context, *, query: BackpackFilterParser,
    ):
        """This shows the contents of your backpack.

        Please read the usage instructions [here](https://github.com/aikaterna/gobcog/blob/master/docs/cbackpack.md)
        """
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        try:
            c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            return
        backpack_pages = await c.get_argparse_backpack(query)
        if backpack_pages:
            await BackpackMenu(
                source=SimpleSource(backpack_pages),
                help_command=self.commands_cbackpack,
                delete_message_after=True,
                clear_reactions_after=True,
                timeout=60,
            ).start(ctx=ctx)
        else:
            return await smart_embed(ctx, _("You have no items that match this query."),)

    @commands_cbackpack.command(name="disassemble")
    async def commands_cbackpack_disassemble(self, ctx: commands.Context, *, query: BackpackFilterParser):
        """
        Disassemble items from your backpack.

        This will provide a chance for a chest,
        or the item might break while you are handling it...

        Please read the usage instructions [here](https://github.com/aikaterna/gobcog/blob/master/docs/cbackpack.md)
        """
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to disassemble an item but the monster ahead of you commands your attention."),
            )
        query.pop("degrade", None)  # Disallow selling by degrade levels
        async with self.get_lock(ctx.author):
            try:
                character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            slots = await character.get_argparse_backpack_items(query, rarity_exclude=["forged"])
            if (total_items := sum(len(i) for s, i in slots)) > 2:

                msg = await ctx.send(
                    "Are you sure you want to disassemble {count} unique items and their duplicates?".format(
                        count=humanize_number(total_items)
                    )
                )
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                try:
                    await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                except asyncio.TimeoutError:
                    await self._clear_react(msg)
                    return

                if not pred.result:
                    await ctx.send("Not disassembling those items.")
                    return
        failed = 0
        success = 0
        disassembled = set()

        async for slot_name, slot_group in AsyncIter(slots, steps=100):
            async for item_name, item in AsyncIter(slot_group, steps=100):
                try:
                    item = character.backpack[item.name]
                except KeyError:
                    continue
                if item.name in disassembled:
                    continue
                if item.rarity in ["forged"]:
                    failed += 1
                    continue
                index = min(RARITIES.index(item.rarity), 4)
                disassembled.add(item.name)
                owned = item.owned
                async for _loop_counter in AsyncIter(range(0, owned), steps=100):
                    if character.heroclass["name"] != "Tinkerer":
                        roll = random.randint(0, 5)
                        chests = 1
                    else:
                        roll = random.randint(0, 3)
                        chests = random.randint(1, 2)
                    if roll != 0:
                        item.owned -= 1
                        if item.owned <= 0 and item.name in character.backpack:
                            del character.backpack[item.name]
                        failed += 1
                    else:
                        item.owned -= 1
                        if item.owned <= 0 and item.name in character.backpack:
                            del character.backpack[item.name]
                        character.treasure[index] += chests
                        success += 1
        if (not failed) and (not success):
            return await smart_embed(ctx, _("No items matched your query.").format(),)
        else:

            await self.config.user(ctx.author).set(await character.to_json(self.config))
            return await smart_embed(
                ctx,
                _("You attempted to disassemble multiple items: {succ} were successful and {fail} failed.").format(
                    succ=humanize_number(success), fail=humanize_number(failed)
                ),
            )

    @commands_cbackpack.command(name="sell", cooldown_after_parsing=True)
    @commands.cooldown(rate=3, per=60, type=commands.BucketType.user)
    async def commands_cbackpack_sell(self, ctx: commands.Context, *, query: BackpackFilterParser):
        """Sell items from your backpack.

        Forged items cannot be sold using this command.

        Please read the usage instructions [here](https://github.com/aikaterna/gobcog/blob/master/docs/cbackpack.md)
        """

        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to go sell your items but the monster ahead is not allowing you to leave."),
            )
        query.pop("degrade", None)  # Disallow selling by degrade levels
        async with self.get_lock(ctx.author):
            try:
                character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            slots = await character.get_argparse_backpack_items(query, rarity_exclude=["forged"])
            if (total_items := sum(len(i) for s, i in slots)) > 2:
                msg = await ctx.send(
                    "Are you sure you want to sell {count} items in your inventory that match this query?".format(
                        count=humanize_number(total_items)
                    )
                )
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                try:
                    await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                except asyncio.TimeoutError:
                    await self._clear_react(msg)
                    return

                if not pred.result:
                    await ctx.send("Not selling those items.")
                    return
            total_price = 0
            msg = ""
            async with ctx.typing():
                async for slot_name, slot_group in AsyncIter(slots, steps=100):
                    async for item_name, item in AsyncIter(slot_group, steps=100):
                        old_owned = item.owned
                        item_price = 0
                        async for _loop_counter in AsyncIter(range(0, old_owned), steps=100):
                            item.owned -= 1
                            item_price += self._sell(character, item)
                            if item.owned <= 0 and item.name in character.backpack:
                                del character.backpack[item.name]
                        item_price = max(item_price, 0)
                        msg += _("{old_item} sold for {price}.\n").format(
                            old_item=str(old_owned) + " " + str(item), price=humanize_number(item_price),
                        )
                        total_price += item_price
                if total_price > 0:
                    try:
                        await bank.deposit_credits(ctx.author, total_price)
                    except BalanceTooHigh as e:
                        await bank.set_balance(ctx.author, e.max_balance)
                character.last_known_currency = await bank.get_balance(ctx.author)
                character.last_currency_check = time.time()
                await self.config.user(ctx.author).set(await character.to_json(self.config))
            if total_price == 0:
                return await smart_embed(ctx, _("No items matched your query.").format(),)
            if msg:
                msg_list = []
                new_msg = _("{author} sold {number} items and their duplicates for {price}.\n\n{items}").format(
                    author=self.escape(ctx.author.display_name),
                    number=humanize_number(total_items),
                    price=humanize_number(total_price),
                    items=msg,
                )
                for page in pagify(new_msg, shorten_by=10, page_length=1900):
                    msg_list.append(box(page, lang="css"))
                await BaseMenu(
                    source=SimpleSource(msg_list), delete_message_after=True, clear_reactions_after=True, timeout=60,
                ).start(ctx=ctx)

    @commands.group(name="backpack", autohelp=False)
    @commands.bot_has_permissions(add_reactions=True)
    async def _backpack(
        self,
        ctx: commands.Context,
        show_diff: Optional[bool] = False,
        rarity: Optional[RarityConverter] = None,
        *,
        slot: Optional[SlotConverter] = None,
    ):
        """This shows the contents of your backpack.

        Give it a rarity and/or slot to filter what backpack items to show.

        Selling:     `[p]backpack sell item_name`
        Trading:     `[p]backpack trade @user price item_name`
        Equip:       `[p]backpack equip item_name`
        Sell All:    `[p]backpack sellall rarity slot`
        Disassemble: `[p]backpack disassemble item_name`

        Note: An item **degrade** level is how many rebirths it will last, before it is broken down.
        """
        assert isinstance(rarity, str) or rarity is None
        assert isinstance(slot, str) or slot is None
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        if not ctx.invoked_subcommand:
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if rarity:
                rarity = rarity.lower()
                if rarity not in RARITIES:
                    return await smart_embed(
                        ctx, _("{} is not a valid rarity, select one of {}").format(rarity, humanize_list(RARITIES)),
                    )
            if slot:
                slot = slot.lower()
                if slot not in ORDER:
                    return await smart_embed(
                        ctx, _("{} is not a valid slot, select one of {}").format(slot, humanize_list(ORDER)),
                    )

            msgs = await c.get_backpack(rarity=rarity, slot=slot, show_delta=show_diff)
            if not msgs:
                return await smart_embed(ctx, _("You have no items in your backpack."),)
            await BackpackMenu(
                source=SimpleSource(msgs),
                help_command=self._backpack,
                delete_message_after=True,
                clear_reactions_after=True,
                timeout=60,
            ).start(ctx=ctx)

    @_backpack.command(name="equip")
    async def backpack_equip(self, ctx: commands.Context, *, equip_item: EquipableItemConverter):
        """Equip an item from your backpack."""
        assert isinstance(equip_item, Item)
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to equip an item but the monster ahead of you commands your attention."),
            )
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            equiplevel = equip_level(c, equip_item)
            if self.is_dev(ctx.author):  # FIXME:
                equiplevel = 0

            if not can_equip(c, equip_item):
                return await smart_embed(
                    ctx, _("You need to be level `{level}` to equip this item.").format(level=equiplevel),
                )

            equip = c.backpack.get(equip_item.name)
            if equip:
                slot = equip.slot[0]
                if len(equip.slot) > 1:
                    slot = "two handed"
                if not getattr(c, equip.slot[0]):
                    equip_msg = box(
                        _("{author} equipped {item} ({slot} slot).").format(
                            author=self.escape(ctx.author.display_name), item=str(equip), slot=slot
                        ),
                        lang="css",
                    )
                else:
                    equip_msg = box(
                        _("{author} equipped {item} ({slot} slot) and put {put} into their backpack.").format(
                            author=self.escape(ctx.author.display_name),
                            item=str(equip),
                            slot=slot,
                            put=getattr(c, equip.slot[0]),
                        ),
                        lang="css",
                    )
                await ctx.send(equip_msg)
                c = await c.equip_item(equip, True, self.is_dev(ctx.author))  # FIXME:
                await self.config.user(ctx.author).set(await c.to_json(self.config))

    @_backpack.command(name="eset", cooldown_after_parsing=True)
    @commands.cooldown(rate=1, per=600, type=commands.BucketType.user)
    async def backpack_eset(self, ctx: commands.Context, *, set_name: str):
        """Equip all parts of a set that you own."""
        if self.in_adventure(ctx):
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(
                ctx, _("You tried to magically equip multiple items at once, but the monster ahead nearly killed you."),
            )
        set_list = humanize_list(sorted([f"`{i}`" for i in self.SET_BONUSES.keys()], key=str.lower))
        if set_name is None:
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(
                ctx, _("Use this command with one of the following set names: \n{sets}").format(sets=set_list),
            )
        async with self.get_lock(ctx.author):
            try:
                character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                ctx.command.reset_cooldown(ctx)
                return

            pieces = await character.get_set_count(return_items=True, set_name=set_name.title())
            if not pieces:
                ctx.command.reset_cooldown(ctx)
                return await smart_embed(
                    ctx, _("You have no pieces of `{set_name}` that you can equip.").format(set_name=set_name),
                )
            for piece in pieces:
                character = await character.equip_item(piece, from_backpack=True)
            await self.config.user(ctx.author).set(await character.to_json(self.config))
            await smart_embed(
                ctx,
                _("I've equipped all pieces of `{set_name}` that you are able to equip.").format(set_name=set_name),
            )

    @_backpack.command(name="disassemble")
    async def backpack_disassemble(self, ctx: commands.Context, *, backpack_items: ItemsConverter):
        """
        Disassemble items from your backpack.

        This will provide a chance for a chest,
        or the item might break while you are handling it...
        """
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to disassemble an item but the monster ahead of you commands your attention."),
            )

        async with self.get_lock(ctx.author):
            if len(backpack_items[1]) > 2:
                msg = await ctx.send(
                    "Are you sure you want to disassemble {count} unique items and their duplicates?".format(
                        count=humanize_number(len(backpack_items[1]))
                    )
                )
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                try:
                    await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                except asyncio.TimeoutError:
                    await self._clear_react(msg)
                    return

                if not pred.result:
                    await ctx.send("Not disassembling those items.")
                    return

            try:
                character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            failed = 0
            success = 0
            op = backpack_items[0]
            disassembled = set()
            async for item in AsyncIter(backpack_items[1], steps=100):
                try:
                    item = character.backpack[item.name]
                except KeyError:
                    continue
                if item.name in disassembled:
                    continue
                if item.rarity in ["forged"]:
                    continue
                index = min(RARITIES.index(item.rarity), 4)
                if op == "single":
                    if character.heroclass["name"] != "Tinkerer":
                        roll = random.randint(0, 5)
                        chests = 1
                    else:
                        roll = random.randint(0, 3)
                        chests = random.randint(1, 2)
                    if roll != 0:
                        item.owned -= 1
                        if item.owned <= 0:
                            del character.backpack[item.name]
                        await self.config.user(ctx.author).set(await character.to_json(self.config))
                        return await smart_embed(
                            ctx,
                            _("Your attempt at disassembling `{}` failed and it has been destroyed.").format(item.name),
                        )
                    else:
                        item.owned -= 1
                        if item.owned <= 0:
                            del character.backpack[item.name]
                        character.treasure[index] += chests
                        await self.config.user(ctx.author).set(await character.to_json(self.config))
                        return await smart_embed(
                            ctx,
                            _("Your attempt at disassembling `{}` was successful and you have received {} {}.").format(
                                item.name, chests, _("chests") if chests > 1 else _("chest")
                            ),
                        )
                elif op == "all":
                    disassembled.add(item.name)
                    owned = item.owned
                    async for _loop_counter in AsyncIter(range(0, owned), steps=100):
                        if character.heroclass["name"] != "Tinkerer":
                            roll = random.randint(0, 5)
                            chests = 1
                        else:
                            roll = random.randint(0, 3)
                            chests = random.randint(1, 2)
                        if roll != 0:
                            item.owned -= 1
                            if item.owned <= 0 and item.name in character.backpack:
                                del character.backpack[item.name]
                            failed += 1
                        else:
                            item.owned -= 1
                            if item.owned <= 0 and item.name in character.backpack:
                                del character.backpack[item.name]
                            character.treasure[index] += chests
                            success += 1
            await self.config.user(ctx.author).set(await character.to_json(self.config))
            return await smart_embed(
                ctx,
                _("You attempted to disassemble multiple items: {succ} were successful and {fail} failed.").format(
                    succ=humanize_number(success), fail=humanize_number(failed)
                ),
            )

    @_backpack.command(name="sellall")
    async def backpack_sellall(
        self, ctx: commands.Context, rarity: Optional[RarityConverter] = None, *, slot: Optional[SlotConverter] = None,
    ):
        """Sell all items in your backpack. Optionally specify rarity or slot."""
        assert isinstance(rarity, str) or rarity is None
        assert isinstance(slot, str) or slot is None
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to go sell your items but the monster ahead is not allowing you to leave."),
            )
        if rarity:
            rarity = rarity.lower()
            if rarity not in RARITIES:
                return await smart_embed(
                    ctx, _("{} is not a valid rarity, select one of {}").format(rarity, humanize_list(RARITIES)),
                )
            if rarity.lower() in ["forged"]:
                return await smart_embed(ctx, _("You cannot sell `{rarity}` rarity items.").format(rarity=rarity))
        if slot:
            slot = slot.lower()
            if slot not in ORDER:
                return await smart_embed(
                    ctx, _("{} is not a valid slot, select one of {}").format(slot, humanize_list(ORDER)),
                )

        async with self.get_lock(ctx.author):
            if rarity and slot:
                msg = await ctx.send(
                    "Are you sure you want to sell all {rarity} {slot} items in your inventory?".format(
                        rarity=rarity, slot=slot
                    )
                )
            elif rarity or slot:
                msg = await ctx.send(
                    "Are you sure you want to sell all{rarity}{slot} items in your inventory?".format(
                        rarity=f" {rarity}" if rarity else "", slot=f" {slot}" if slot else ""
                    )
                )
            else:
                msg = await ctx.send("Are you sure you want to sell all items in your inventory?")

            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            try:
                await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
            except asyncio.TimeoutError:
                await self._clear_react(msg)
                return

            if not pred.result:
                await ctx.send("Not selling those items.")
                return

            msg = ""
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            total_price = 0
            async with ctx.typing():
                items = [i for n, i in c.backpack.items() if i.rarity not in ["forged"]]
                count = 0
                async for item in AsyncIter(items, steps=100):
                    if rarity and item.rarity != rarity:
                        continue
                    if slot:
                        if len(item.slot) == 1 and slot != item.slot[0]:
                            continue
                        elif len(item.slot) == 2 and slot != "two handed":
                            continue
                    item_price = 0
                    old_owned = item.owned
                    async for _loop_counter in AsyncIter(range(0, old_owned), steps=100):
                        item.owned -= 1
                        item_price += self._sell(c, item)
                        if item.owned <= 0:
                            del c.backpack[item.name]
                    item_price = max(item_price, 0)
                    msg += _("{old_item} sold for {price}.\n").format(
                        old_item=str(old_owned) + " " + str(item), price=humanize_number(item_price),
                    )
                    total_price += item_price
                if total_price > 0:
                    try:
                        await bank.deposit_credits(ctx.author, total_price)
                    except BalanceTooHigh as e:
                        await bank.set_balance(ctx.author, e.max_balance)
                c.last_known_currency = await bank.get_balance(ctx.author)
                c.last_currency_check = time.time()
                await self.config.user(ctx.author).set(await c.to_json(self.config))
        msg_list = []
        new_msg = _("{author} sold all their{rarity} items for {price}.\n\n{items}").format(
            author=self.escape(ctx.author.display_name),
            rarity=f" {rarity}" if rarity else "",
            price=humanize_number(total_price),
            items=msg,
        )
        for page in pagify(new_msg, shorten_by=10, page_length=1900):
            msg_list.append(box(page, lang="css"))
        await BaseMenu(
            source=SimpleSource(msg_list), delete_message_after=True, clear_reactions_after=True, timeout=60,
        ).start(ctx=ctx)

    @_backpack.command(name="sell", cooldown_after_parsing=True)
    @commands.cooldown(rate=3, per=60, type=commands.BucketType.user)
    async def backpack_sell(self, ctx: commands.Context, *, item: ItemConverter):
        """Sell an item from your backpack."""

        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to go sell your items but the monster ahead is not allowing you to leave."),
            )
        if item.rarity in ["forged"]:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(
                box(
                    _("\n{author}, your {device} is refusing to be sold and bit your finger for trying.").format(
                        author=self.escape(ctx.author.display_name), device=str(item)
                    ),
                    lang="css",
                )
            )

        lock = self.get_lock(ctx.author)
        await lock.acquire()
        try:
            c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            ctx.command.reset_cooldown(ctx)
            log.exception("Error with the new character sheet", exc_info=exc)
            lock.release()
            return
        price_shown = self._sell(c, item)
        messages = [
            _("**{author}**, do you want to sell this item for {price} each? {item}").format(
                author=self.escape(ctx.author.display_name),
                item=box(str(item), lang="css"),
                price=humanize_number(price_shown),
            )
        ]
        try:
            item = c.backpack[item.name]
        except KeyError:
            return

        async def _backpack_sell_menu(
            ctx: commands.Context,
            pages: list,
            controls: dict,
            message: discord.Message,
            page: int,
            timeout: float,
            emoji: str,
        ):
            if message:
                with contextlib.suppress(discord.HTTPException):
                    await message.delete()
                await self._backpack_sell_button_action(ctx, emoji, page, item, price_shown, c)
                return None

        back_pack_sell_controls = {
            "\N{DIGIT ONE}\N{COMBINING ENCLOSING KEYCAP}": _backpack_sell_menu,
            "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}": _backpack_sell_menu,
            "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS WITH CIRCLED ONE OVERLAY}": _backpack_sell_menu,
            "\N{CROSS MARK}": _backpack_sell_menu,
        }

        await menu(ctx, messages, back_pack_sell_controls, timeout=60)

    async def _backpack_sell_button_action(self, ctx, emoji, page, item, price_shown, character):
        currency_name = await bank.get_currency_name(ctx.guild,)
        msg = ""
        try:
            if emoji == "\N{DIGIT ONE}\N{COMBINING ENCLOSING KEYCAP}":  # user reacted with one to sell.
                ctx.command.reset_cooldown(ctx)
                # sell one of the item
                price = 0
                item.owned -= 1
                price += price_shown
                msg += _("**{author}** sold one {item} for {price} {currency_name}.\n").format(
                    author=self.escape(ctx.author.display_name),
                    item=box(item, lang="css"),
                    price=humanize_number(price),
                    currency_name=currency_name,
                )
                if item.owned <= 0:
                    del character.backpack[item.name]
                price = max(price, 0)
                if price > 0:
                    try:
                        await bank.deposit_credits(ctx.author, price)
                    except BalanceTooHigh as e:
                        await bank.set_balance(ctx.author, e.max_balance)
            elif emoji == "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}":  # user wants to sell all owned.
                ctx.command.reset_cooldown(ctx)
                price = 0
                old_owned = item.owned
                count = 0
                for _loop_counter in range(0, item.owned):
                    item.owned -= 1
                    price += price_shown
                    if item.owned <= 0:
                        del character.backpack[item.name]
                    if not count % 10:
                        await asyncio.sleep(0.1)
                    count += 1
                msg += _("**{author}** sold all their {old_item} for {price} {currency_name}.\n").format(
                    author=self.escape(ctx.author.display_name),
                    old_item=box(str(item) + " - " + str(old_owned), lang="css"),
                    price=humanize_number(price),
                    currency_name=currency_name,
                )
                price = max(price, 0)
                if price > 0:
                    try:
                        await bank.deposit_credits(ctx.author, price)
                    except BalanceTooHigh as e:
                        await bank.set_balance(ctx.author, e.max_balance)
            elif (
                emoji == "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS WITH CIRCLED ONE OVERLAY}"
            ):  # user wants to sell all but one.
                if item.owned == 1:
                    ctx.command.reset_cooldown(ctx)
                    return await smart_embed(ctx, _("You already only own one of those items."))
                price = 0
                old_owned = item.owned
                count = 0
                for _loop_counter in range(1, item.owned):
                    item.owned -= 1
                    price += price_shown
                if not count % 10:
                    await asyncio.sleep(0.1)
                count += 1
                if price != 0:
                    msg += _("**{author}** sold all but one of their {old_item} for {price} {currency_name}.\n").format(
                        author=self.escape(ctx.author.display_name),
                        old_item=box(str(item) + " - " + str(old_owned - 1), lang="css"),
                        price=humanize_number(price),
                        currency_name=currency_name,
                    )
                    price = max(price, 0)
                    if price > 0:
                        try:
                            await bank.deposit_credits(ctx.author, price)
                        except BalanceTooHigh as e:
                            await bank.set_balance(ctx.author, e.max_balance)
            else:  # user doesn't want to sell those items.
                msg = _("Not selling those items.")
        finally:
            lock = self.get_lock(ctx.author)
            with contextlib.suppress(Exception):
                lock.release()

        if msg:
            character.last_known_currency = await bank.get_balance(ctx.author)
            character.last_currency_check = time.time()
            await self.config.user(ctx.author).set(await character.to_json(self.config))
            pages = [page for page in pagify(msg, delims=["\n"], page_length=1900)]
            await BaseMenu(
                source=SimpleSource(pages), delete_message_after=True, clear_reactions_after=True, timeout=60,
            ).start(ctx=ctx)

    @_backpack.command(name="trade")
    async def backpack_trade(
        self, ctx: commands.Context, buyer: discord.Member, asking: Optional[int] = 1000, *, item: ItemConverter,
    ):
        """Trade an item from your backpack to another user."""
        if ctx.author == buyer:
            return await smart_embed(
                ctx,
                _("You take the item and pass it from one hand to the other. Congratulations, you traded yourself."),
            )
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to trade an item to a party member but the monster ahead commands your attention."),
            )
        if self.in_adventure(user=buyer):
            return await smart_embed(
                ctx,
                _("**{buyer}** is currently in an adventure... you were unable to reach them via pigeon.").format(
                    buyer=self.escape(buyer.display_name)
                ),
            )
        try:
            c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            return
        try:
            buy_user = await Character.from_json(self.config, buyer, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            return

        if buy_user.is_backpack_full(is_dev=self.is_dev(buyer)):
            await ctx.send(
                _("**{author}**'s backpack is currently full.").format(author=self.escape(buyer.display_name))
            )
            return

        if not any([x for x in c.backpack if item.name.lower() == x.lower()]):
            return await smart_embed(
                ctx,
                _("**{author}**, you have to specify an item from your backpack to trade.").format(
                    author=self.escape(ctx.author.display_name)
                ),
            )
        lookup = list(x for n, x in c.backpack.items() if str(item) == str(x))
        if len(lookup) > 1:
            await smart_embed(
                ctx,
                _(
                    "**{author}**, I found multiple items ({items}) "
                    "matching that name in your backpack.\nPlease be more specific."
                ).format(author=self.escape(ctx.author.display_name), items=humanize_list([x.name for x in lookup]),),
            )
            return
        if any([x for x in lookup if x.rarity == "forged"]):
            device = [x for x in lookup if x.rarity == "forged"]
            return await ctx.send(
                box(
                    _("\n{author}, your {device} does not want to leave you.").format(
                        author=self.escape(ctx.author.display_name), device=str(device[0])
                    ),
                    lang="css",
                )
            )
        elif any([x for x in lookup if x.rarity == "set"]):
            return await ctx.send(
                box(
                    _("\n{character}, you cannot trade Set items as they are bound to your soul.").format(
                        character=self.escape(ctx.author.display_name)
                    ),
                    lang="css",
                )
            )
        else:
            item = lookup[0]
            hand = item.slot[0] if len(item.slot) < 2 else "two handed"
            currency_name = await bank.get_currency_name(ctx.guild,)
            if str(currency_name).startswith("<"):
                currency_name = "credits"
            trade_talk = box(
                _(
                    "{author} wants to sell {item}. "
                    "(ATT: {att_item} | "
                    "CHA: {cha_item} | "
                    "INT: {int_item} | "
                    "DEX: {dex_item} | "
                    "LUCK: {luck_item}) "
                    "[{hand}])\n{buyer}, "
                    "do you want to buy this item for {asking} {currency_name}?"
                ).format(
                    author=self.escape(ctx.author.display_name),
                    item=item,
                    att_item=str(item.att),
                    cha_item=str(item.cha),
                    int_item=str(item.int),
                    dex_item=str(item.dex),
                    luck_item=str(item.luck),
                    hand=hand,
                    buyer=self.escape(buyer.display_name),
                    asking=str(asking),
                    currency_name=currency_name,
                ),
                lang="css",
            )
            async with self.get_lock(ctx.author):
                trade_msg = await ctx.send(f"{buyer.mention}\n{trade_talk}")
                start_adding_reactions(trade_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(trade_msg, buyer)
                try:
                    await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                except asyncio.TimeoutError:
                    await self._clear_react(trade_msg)
                    return
                if pred.result:  # buyer reacted with Yes.
                    with contextlib.suppress(discord.errors.NotFound):
                        if await bank.can_spend(buyer, asking):
                            if buy_user.rebirths + 1 < c.rebirths:
                                return await smart_embed(
                                    ctx,
                                    _(
                                        "You can only trade with people that are the same "
                                        "rebirth level, one rebirth level less than you, "
                                        "or a higher rebirth level than yours."
                                    ),
                                )
                            try:
                                await bank.transfer_credits(buyer, ctx.author, asking)
                            except BalanceTooHigh as e:
                                await bank.withdraw_credits(buyer, asking)
                                await bank.set_balance(ctx.author, e.max_balance)
                            c.backpack[item.name].owned -= 1
                            newly_owned = c.backpack[item.name].owned
                            if c.backpack[item.name].owned <= 0:
                                del c.backpack[item.name]
                            async with self.get_lock(buyer):
                                if item.name in buy_user.backpack:
                                    buy_user.backpack[item.name].owned += 1
                                else:
                                    item.owned = 1
                                    buy_user.backpack[item.name] = item
                                await self.config.user(buyer).set(await buy_user.to_json(self.config))
                                item.owned = newly_owned
                                await self.config.user(ctx.author).set(await c.to_json(self.config))

                            await trade_msg.edit(
                                content=(
                                    box(
                                        _("\n{author} traded {item} to {buyer} for {asking} {currency_name}.").format(
                                            author=self.escape(ctx.author.display_name),
                                            item=item,
                                            buyer=self.escape(buyer.display_name),
                                            asking=asking,
                                            currency_name=currency_name,
                                        ),
                                        lang="css",
                                    )
                                )
                            )
                            await self._clear_react(trade_msg)
                        else:
                            await trade_msg.edit(
                                content=_("**{buyer}**, you do not have enough {currency_name}.").format(
                                    buyer=self.escape(buyer.display_name), currency_name=currency_name,
                                )
                            )
                else:
                    with contextlib.suppress(discord.HTTPException):
                        await trade_msg.delete()

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    @commands.guild_only()
    async def rebirth(self, ctx: commands.Context):
        """Resets your character level and increases your rebirths by 1."""
        if self.in_adventure(ctx):
            return await smart_embed(ctx, _("You tried to rebirth but the monster ahead is commanding your attention."))
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if c.lvl < c.maxlevel:
                return await smart_embed(ctx, _("You need to be level `{c.maxlevel}` to rebirth.").format(c=c))
            if not c.last_currency_check + 10 < time.time():
                return await smart_embed(ctx, _("You need to wait a little before rebirthing.").format(c=c))
            if not await bank.is_global():
                rebirth_cost = await self.config.guild(ctx.guild).rebirth_cost()
            else:
                rebirth_cost = await self.config.rebirth_cost()
            rebirthcost = 1000 * c.rebirths
            current_balance = c.bal
            last_known_currency = c.last_known_currency
            if last_known_currency and current_balance / last_known_currency < 0.25:
                currency_name = await bank.get_currency_name(ctx.guild,)
                return await smart_embed(
                    ctx,
                    _(
                        "You tried to get rid of all your {currency_name} -- tsk tsk, "
                        "once you get back up to {cur} {currency_name} try again."
                    ).format(currency_name=currency_name, cur=humanize_number(last_known_currency),),
                )
            else:
                has_fund = await has_funds(ctx.author, rebirthcost)
            if not has_fund:
                currency_name = await bank.get_currency_name(ctx.guild,)
                return await smart_embed(
                    ctx, _("You need more {currency_name} to be able to rebirth.").format(currency_name=currency_name),
                )
            space = "\N{EN SPACE}"
            open_msg = await smart_embed(
                ctx,
                _(
                    f"Rebirthing will:\n\n"
                    f"* cost {int(rebirth_cost)}% of your credits\n"
                    f"* cost all of your current gear\n"
                    f"{space*4}- Legendary items loose one degradation point per rebirth "
                    f"and are broken down when they have 0 left.\n"
                    f"{space*4}- Set items never disappear\n"
                    f"* set you back to level 1 while keeping your current class\n\n"
                    f"In turn, rebirthing will give you a higher stat base, a better chance "
                    f"for acquiring more powerful items, a higher max level, and the "
                    f"ability to convert chests to higher rarities after the second rebirth.\n\n"
                    f"Would you like to rebirth?"
                ),
            )
            start_adding_reactions(open_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(open_msg, ctx.author)
            try:
                await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
            except asyncio.TimeoutError:
                await self._clear_react(open_msg)
                return await smart_embed(ctx, "I can't wait forever, you know.")
            else:
                if not pred.result:
                    await open_msg.edit(
                        content=box(
                            _("{c} decided not to rebirth.").format(c=self.escape(ctx.author.display_name)), lang="css",
                        ),
                        embed=None,
                    )
                    return await self._clear_react(open_msg)

                try:
                    c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    return
                if c.lvl < c.maxlevel:
                    await open_msg.edit(
                        content=box(_("You need to be level `{c}` to rebirth.").format(c=c.maxlevel), lang="css",),
                        embed=None,
                    )
                    return
                bal = await bank.get_balance(ctx.author)
                if bal >= 1000:
                    withdraw = int((bal - 1000) * (rebirth_cost / 100.0))
                    await bank.withdraw_credits(ctx.author, withdraw)
                else:
                    withdraw = int(bal * (rebirth_cost / 100.0))
                    await bank.set_balance(ctx.author, 0)

                await open_msg.edit(
                    content=box(
                        _("{c}, congratulations on your rebirth.\nYou paid {bal}.").format(
                            c=self.escape(ctx.author.display_name), bal=humanize_number(withdraw),
                        ),
                        lang="css",
                    ),
                    embed=None,
                )
                await self.config.user(ctx.author).set(await c.rebirth())

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    @commands.is_owner()
    async def devrebirth(
        self,
        ctx: commands.Context,
        rebirth_level: int = 1,
        character_level: int = 1,
        users: commands.Greedy[discord.User] = None,
    ):
        """[Dev] Set multiple users rebirths and level."""
        if not await no_dev_prompt(ctx):
            return
        targets = users or [ctx.author]
        if not self.is_dev(ctx.author):
            if rebirth_level > 100:
                await ctx.send("Rebirth is too high.")
                await ctx.send_help()
                return
            elif character_level > 1000:
                await ctx.send("Level is too high.")
                await ctx.send_help()
                return
        for target in targets:
            async with self.get_lock(target):
                try:
                    c = await Character.from_json(self.config, target, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    continue
                bal = await bank.get_balance(target)
                if bal >= 1000:
                    withdraw = bal - 1000
                    await bank.withdraw_credits(target, withdraw)
                else:
                    withdraw = bal
                    await bank.set_balance(target, 0)
                character_data = await c.rebirth(dev_val=rebirth_level)
                await self.config.user(target).set(character_data)
                await ctx.send(
                    content=(
                        box(
                            _("{c}, congratulations on your rebirth.\nYou paid {bal}.").format(
                                c=self.escape(target.display_name), bal=humanize_number(withdraw)
                            ),
                            lang="css",
                        )
                    )
                )
            await self._add_rewards(ctx, target, int((character_level) ** 3.5) + 1, 0, False)
        await ctx.tick()

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    @commands.is_owner()
    async def devreset(self, ctx: commands.Context, users: commands.Greedy[discord.User]):
        """[Dev] Reset the skill cooldown for multiple users."""
        if not await no_dev_prompt(ctx):
            return
        targets = users or [ctx.author]
        for target in targets:
            async with self.get_lock(target):
                try:
                    c = await Character.from_json(self.config, target, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    continue
                c.heroclass["ability"] = False
                c.heroclass["cooldown"] = 0
                if "catch_cooldown" in c.heroclass:
                    c.heroclass["catch_cooldown"] = 0
                await self.config.user(target).set(await c.to_json(self.config))
        await ctx.tick()

    @commands.group(aliases=["loadouts"])
    async def loadout(self, ctx: commands.Context):
        """Set up gear sets or loadouts."""

    @loadout.command(name="save", aliases=["update"])
    async def save_loadout(self, ctx: commands.Context, name: str):
        """Save your current equipment as a loadout."""
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        name = name.lower()
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if name in c.loadouts:
                msg = await ctx.send("Are you sure you want to update your existing loadout: `{}`?".format(name))
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                try:
                    await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                except asyncio.TimeoutError:
                    await self._clear_react(msg)
                    return

                if not pred.result:
                    await ctx.send("I will not updated loadout: `{}`.".format(name))
                    return
            loadout = await Character.save_loadout(c)
            c.loadouts[name] = loadout
            await self.config.user(ctx.author).set(await c.to_json(self.config))
            await smart_embed(
                ctx,
                _("**{author}**, your current equipment has been saved to {name}.").format(
                    author=self.escape(ctx.author.display_name), name=name
                ),
            )

    @loadout.command(name="delete", aliases=["del", "rem", "remove"])
    async def remove_loadout(self, ctx: commands.Context, name: str):
        """Delete a saved loadout."""
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        async with self.get_lock(ctx.author):
            name = name.lower()
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if name not in c.loadouts:
                await smart_embed(
                    ctx,
                    _("**{author}**, you don't have a loadout named {name}.").format(
                        author=self.escape(ctx.author.display_name), name=name
                    ),
                )
            else:
                del c.loadouts[name]
                await self.config.user(ctx.author).set(await c.to_json(self.config))
                await smart_embed(
                    ctx,
                    _("**{author}**, loadout {name} has been deleted.").format(
                        author=self.escape(ctx.author.display_name), name=name
                    ),
                )

    @loadout.command(name="show")
    @commands.bot_has_permissions(add_reactions=True)
    async def show_loadout(self, ctx: commands.Context, name: str = None):
        """Show saved loadouts."""
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        try:
            c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            return
        if not c.loadouts:
            return await smart_embed(
                ctx,
                _("**{author}**, you don't have any loadouts saved.").format(
                    author=self.escape(ctx.author.display_name)
                ),
            )
        if name is not None and name.lower() not in c.loadouts:
            return await smart_embed(
                ctx,
                _("**{author}**, you don't have a loadout named {name}.").format(
                    author=self.escape(ctx.author.display_name), name=name
                ),
            )
        else:
            msg_list = []
            index = 0
            count = 0
            for (l_name, loadout) in c.loadouts.items():
                if name and name.lower() == l_name:
                    index = count
                stats = await self._build_loadout_display({"items": loadout}, rebirths=c.rebirths, index=count + 1)
                msg = _("{name} Loadout for {author}\n\n{stats}").format(
                    name=l_name, author=self.escape(ctx.author.display_name), stats=stats
                )
                msg_list.append(box(msg, lang="css"))
                count += 1
            if msg_list:
                await BaseMenu(
                    source=SimpleSource(msg_list), delete_message_after=True, clear_reactions_after=True, timeout=60,
                ).start(ctx=ctx, page=index)

    @loadout.command(name="equip", aliases=["load"], cooldown_after_parsing=True)
    @commands.cooldown(rate=1, per=600, type=commands.BucketType.user)
    async def equip_loadout(self, ctx: commands.Context, name: str):
        """Equip a saved loadout."""
        if self.in_adventure(ctx):
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(
                ctx, _("You tried to magically equip multiple items at once, but the monster ahead nearly killed you."),
            )
        if not await self.allow_in_dm(ctx):
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        name = name.lower()
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                ctx.command.reset_cooldown(ctx)
                return
            if name not in c.loadouts:
                ctx.command.reset_cooldown(ctx)
                return await smart_embed(
                    ctx,
                    _("**{author}**, you don't have a loadout named {name}.").format(
                        author=self.escape(ctx.author.display_name), name=name
                    ),
                )
            else:
                c = await c.equip_loadout(name)
                await self.config.user(ctx.author).set(await c.to_json(self.config))
                try:
                    c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    ctx.command.reset_cooldown(ctx)
                    return
                current_stats = box(
                    _(
                        "{author}'s new stats: "
                        "Attack: {stat_att} [{skill_att}], "
                        "Intelligence: {stat_int} [{skill_int}], "
                        "Diplomacy: {stat_cha} [{skill_cha}], "
                        "Dexterity: {stat_dex}, "
                        "Luck: {stat_luck}."
                    ).format(
                        author=self.escape(ctx.author.display_name),
                        stat_att=c.get_stat_value("att")[0],
                        skill_att=c.skill["att"],
                        stat_int=c.get_stat_value("int")[0],
                        skill_int=c.skill["int"],
                        stat_cha=c.get_stat_value("cha")[0],
                        skill_cha=c.skill["cha"],
                        stat_dex=c.get_stat_value("dex")[0],
                        stat_luck=c.get_stat_value("luck")[0],
                    ),
                    lang="css",
                )
                await ctx.send(current_stats)

    @commands.guildowner()
    @commands.group()
    @commands.guild_only()
    async def adventureset(self, ctx: commands.Context):
        """Setup various adventure settings."""

    @adventureset.command()
    @check_global_setting_admin()
    async def rebirthcost(self, ctx: commands.Context, percentage: float):
        """[Admin] Set what percentage of the user balance to charge for rebirths.

        Unless the user's balance is under 1k, users that rebirth will be left with the base of 1k credits plus the remaining credit percentage after the rebirth charge.
        """
        if percentage < 0 or percentage > 100:
            return await smart_embed(ctx, _("Percentage has to be between 0 and 100."))
        if not await bank.is_global():
            await self.config.guild(ctx.guild).rebirth_cost.set(percentage)
            await smart_embed(
                ctx, _("I will now charge {0:.0%} of the user's balance for a rebirth.").format(percentage / 100),
            )
        else:
            await self.config.rebirth_cost.set(percentage)
            await smart_embed(
                ctx,
                _("I will now charge {0:.0%} of the user's global balance for a rebirth.").format(percentage / 100),
            )

    @adventureset.command()
    @commands.admin_or_permissions(administrator=True)
    async def cartroom(self, ctx: commands.Context, room: discord.TextChannel = None):
        """[Admin] Lock carts to a specific text channel."""
        if room is None:
            await self.config.guild(ctx.guild).cartroom.set(None)
            return await smart_embed(ctx, _("Done, carts will be able to appear in any text channel the bot can see."))

        await self.config.guild(ctx.guild).cartroom.set(room.id)
        await smart_embed(ctx, _("Done, carts will only appear in {room.mention}.").format(room=room))

    @adventureset.group(name="locks")
    @commands.bot_has_permissions(add_reactions=True)
    @commands.admin_or_permissions(administrator=True)
    async def adventureset_locks(self, ctx: commands.Context):
        """[Admin] Reset Adventure locks."""

    @adventureset_locks.command(name="user")
    @commands.is_owner()
    async def adventureset_locks_user(self, ctx: commands.Context, users: commands.Greedy[discord.User]):
        """[Owner] Reset a multiple adventurers lock."""
        for user in users:
            lock = self.get_lock(user)
            with contextlib.suppress(Exception):
                lock.release()
        await ctx.tick()

    @adventureset.command(name="dailybonus")
    @commands.is_owner()
    async def adventureset_daily_bonus(self, ctx: commands.Context, day: DayConverter, percentage: PercentageConverter):
        """[Owner] Set the daily xp and currency bonus.

        **percentage** must be between 0% and 100%.
        """
        day_val, day_text = day
        async with self.config.daily_bonus.all() as daily_bonus_data:
            daily_bonus_data[day_val] = percentage
            self._daily_bonus = daily_bonus_data.copy()
        await smart_embed(
            ctx, _("Daily bonus for `{0}` has been set to: {1:.0%}").format(day_text.title(), percentage),
        )

    @commands.guild_only()
    @adventureset_locks.command(name="adventure")
    async def adventureset_locks_adventure(self, ctx: commands.Context):
        """[Admin] Reset the adventure game lock for the server."""
        while ctx.guild.id in self._sessions:
            del self._sessions[ctx.guild.id]
        await ctx.tick()

    async def _garbage_collection(self):
        await self.bot.wait_until_red_ready()
        delta = timedelta(minutes=6)
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                async for guild_id, session in AsyncIter(self._sessions.copy(), steps=100):
                    if session.start_time + delta > datetime.now():
                        if guild_id in self._sessions:
                            del self._sessions[guild_id]
                await asyncio.sleep(5)

    @adventureset.command()
    @commands.is_owner()
    async def restrict(self, ctx: commands.Context):
        """[Owner] Set whether or not adventurers are restricted to one adventure at a time."""
        toggle = await self.config.restrict()
        await self.config.restrict.set(not toggle)
        await smart_embed(ctx, _("Adventurers restricted to one adventure at a time: {}").format(not toggle))

    @adventureset.command()
    @commands.is_owner()
    async def easymode(self, ctx: commands.Context):
        """[Owner] Set whether or not Adventure will be in easy mode.

        Easy mode gives less rewards, but monster information is shown.
        """
        toggle = await self.config.easy_mode()
        await self.config.easy_mode.set(not toggle)
        await smart_embed(ctx, _("Adventure easy mode is now **{}**.").format("Enabled" if not toggle else "Disabled"))

    @adventureset.command()
    @commands.is_owner()
    async def sepcurrency(self, ctx: commands.Context):
        """[Owner] Toggle whether the currency should be separated from main bot currency."""
        toggle = await self.config.separate_economy()
        await self.config.separate_economy.set(not toggle)
        self._separate_economy = not toggle
        await smart_embed(
            ctx, _("Adventurer currency is: **{}**").format(_("Separated" if not toggle else _("Unified")))
        )

    @adventureset.group(name="economy")
    @check_global_setting_admin()
    @commands.guild_only()
    @has_separated_economy()
    async def commands_adventureset_economy(self, ctx: commands.Context):
        """[Admin] Manages the adventure economy."""

    @commands_adventureset_economy.command(name="tax", usage="<gold,tax ...>")
    @commands.is_owner()
    async def commands_adventureset_economy_tax(self, ctx: commands.Context, *, taxes: TaxesConverter):
        """[Owner] Set the tax thresholds.

        **gold** must be positive
        **tax** must be between 0 and 1.

        Example: `[p]adventureset economy tax 10000,0.1 20000,0.2 ...`

        """
        new_taxes = {}
        for k, v in taxes.items():
            if int(k) >= 0 and 0 <= float(v) <= 1:
                new_taxes[k] = float(v)
        new_taxes = {k: v for k, v in sorted(new_taxes.items(), key=lambda item: item[1])}
        await self.config.tax_brackets.set(new_taxes)

        taxes = await self.config.tax_brackets()
        table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
        table.set_style(BeautifulTable.STYLE_RST)
        table.columns.header = ["Tax %", "Tax Threshold"]
        for k, v in taxes.items():
            table.rows.append((f"[{v:.2%}]", humanize_number(int(k))))
        table.rows.sort("Tax %", reverse=True)
        await smart_embed(ctx, box(str(table), lang="css",))

    @commands.is_owner()
    @commands_adventureset_economy.command(name="rate")
    async def commands_adventureset_economy_conversion_rate(self, ctx: commands.Context, rate_in: int, rate_out: int):
        """[Owner] Set how much 1 bank credit is worth in adventure.

        **rate_in**: Is how much gold you will get for 1 bank credit. Default is 10
        **rate_out**: Is how much gold is needed to convert to 1 bank credit. Default is 11
        """
        if rate_in < 0 or rate_out < 0:
            return await smart_embed(ctx, _("You are evil ... please DM me your phone number we need to hangout."))
        await self.config.to_conversion_rate.set(rate_in)
        await self.config.from_conversion_rate.set(rate_out)
        await smart_embed(
            ctx,
            _("1 {name} will be worth {rate_in} {a_name}.\n{rate_out} {a_name} will convert into 1 {name}").format(
                name=await bank.get_currency_name(ctx.guild, _forced=True),
                rate_in=humanize_number(rate_in),
                rate_out=humanize_number(rate_out),
                a_name=await bank.get_currency_name(ctx.guild),
            ),
        )

    @commands_adventureset_economy.command(name="maxwithdraw")
    async def commands_adventureset_economy_maxwithdraw(self, ctx: commands.Context, *, amount: int):
        """[Admin] Set how much players are allowed to withdraw."""
        if amount < 0:
            return await smart_embed(ctx, _("You are evil ... please DM me your phone number we need to hangout."))
        if await bank.is_global(_forced=True):
            await self.config.max_allowed_withdraw.set(amount)
        else:
            await self.config.guild(ctx.guild).max_allowed_withdraw.set(amount)
        await smart_embed(
            ctx,
            _(
                "Adventurers will be able to withdraw up to {amount} {name} from their adventure bank and deposit into their bot economy."
            ).format(name=await bank.get_currency_name(ctx.guild, _forced=True), amount=humanize_number(amount),),
        )

    @commands_adventureset_economy.command(name="withdraw")
    async def commands_adventureset_economy_withdraw(self, ctx: commands.Context):
        """[Admin] Toggle whether users are allowed to withdraw from adventure currency to main currency."""

        if await bank.is_global(_forced=True):
            state = await self.config.disallow_withdraw()
            await self.config.disallow_withdraw.set(not state)
        else:
            state = await self.config.guild(ctx.guild).disallow_withdraw()
            await self.config.guild(ctx.guild).disallow_withdraw.set(not state)

        await smart_embed(
            ctx,
            _("Adventurers are now {state} to withdraw money from adventure currency.").format(
                state=_("allowed") if not state else _("disallowed")
            ),
        )

    @adventureset.command(name="advcooldown", hidden=True)
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def advcooldown(self, ctx: commands.Context, *, time_in_seconds: int):
        """[Admin] Changes the cooldown/gather time after an adventure.

        Default is 120 seconds.
        """
        if time_in_seconds < 30:
            return await smart_embed(ctx, _("Cooldown cannot be set to less than 30 seconds."))

        await self.config.guild(ctx.guild).cooldown_timer_manual.set(time_in_seconds)
        await smart_embed(
            ctx, _("Adventure cooldown set to {cooldown} seconds.").format(cooldown=time_in_seconds),
        )

    @adventureset.command()
    async def version(self, ctx: commands.Context):
        """Display the version of adventure being used."""
        await ctx.send(box(_("Adventure version: {}").format(self.__version__)))

    @adventureset.command()
    @commands.admin_or_permissions(administrator=True)
    async def god(self, ctx: commands.Context, *, name):
        """[Admin] Set the server's name of the god."""
        await self.config.guild(ctx.guild).god_name.set(name)
        await ctx.tick()

    @adventureset.command()
    @commands.is_owner()
    async def globalgod(self, ctx: commands.Context, *, name):
        """[Owner] Set the default name of the god."""
        await self.config.god_name.set(name)
        await ctx.tick()

    @adventureset.command(aliases=["embed"])
    @commands.admin_or_permissions(administrator=True)
    async def embeds(self, ctx: commands.Context):
        """[Admin] Set whether or not to use embeds for the adventure game."""
        toggle = await self.config.guild(ctx.guild).embed()
        await self.config.guild(ctx.guild).embed.set(not toggle)
        await smart_embed(ctx, _("Embeds: {}").format(not toggle))

    @adventureset.command(aliases=["chests"], enabled=False, hidden=True)
    @commands.is_owner()
    async def cartchests(self, ctx: commands.Context):
        """[Admin] Set whether or not to sell chests in the cart."""
        toggle = await self.config.enable_chests()
        await self.config.enable_chests.set(not toggle)
        await smart_embed(ctx, _("Carts can sell chests: {}").format(not toggle))

    @adventureset.command()
    @commands.admin_or_permissions(administrator=True)
    async def cartname(self, ctx: commands.Context, *, name):
        """[Admin] Set the server's name of the cart."""
        await self.config.guild(ctx.guild).cart_name.set(name)
        await ctx.tick()

    @adventureset.command()
    @commands.admin_or_permissions(administrator=True)
    async def carttime(self, ctx: commands.Context, *, time: str):
        """
        [Admin] Set the cooldown of the cart.
        Time can be in seconds, minutes, hours, or days.
        Examples: `1h 30m`, `2 days`, `300 seconds`
        """
        time_delta = parse_timedelta(time)
        if time_delta is None:
            return await smart_embed(ctx, _("You must supply a amount and time unit like `120 seconds`."))
        if time_delta.total_seconds() < 600:
            cartname = await self.config.guild(ctx.guild).cart_name()
            if not cartname:
                cartname = await self.config.cart_name()
            return await smart_embed(ctx, _("{} doesn't have the energy to return that often. Try 10 minutes or more.").format(cartname))
        await self.config.guild(ctx.guild).cart_timeout.set(int(time_delta.total_seconds()))
        await ctx.tick()

    @adventureset.command(name="clear")
    @commands.is_owner()
    async def clear_user(self, ctx: commands.Context, users: commands.Greedy[discord.User]):
        """[Owner] Lets you clear multiple users character sheets."""
        for user in users:
            await self.config.user(user).clear()
            await smart_embed(ctx, _("{user}'s character sheet has been erased.").format(user=user))

    @adventureset.command(name="remove")
    @commands.is_owner()
    async def remove_item(self, ctx: commands.Context, user: discord.User, *, full_item_name: str):
        """[Owner] Lets you remove an item from a user.

        Use the full name of the item including the rarity characters like . or []  or {}.
        """
        async with self.get_lock(user):
            item = None
            try:
                c = await Character.from_json(self.config, user, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            for slot in ORDER:
                if slot == "two handed":
                    continue
                equipped_item = getattr(c, slot)
                if equipped_item and equipped_item.name.lower() == full_item_name.lower():
                    item = equipped_item
            if item:
                with contextlib.suppress(Exception):
                    await c.unequip_item(item)
            else:
                try:
                    item = c.backpack[full_item_name]
                except KeyError:
                    return await smart_embed(
                        ctx, _("{} does not have an item named `{}`.").format(user, full_item_name)
                    )
            with contextlib.suppress(KeyError):
                del c.backpack[item.name]
            await self.config.user(user).set(await c.to_json(self.config))
        await ctx.send(_("{item} removed from {user}.").format(item=box(str(item), lang="css"), user=user))

    @adventureset.command()
    @commands.is_owner()
    async def globalcartname(self, ctx: commands.Context, *, name):
        """[Owner] Set the default name of the cart."""
        await self.config.cart_name.set(name)
        await ctx.tick()

    @adventureset.command()
    @commands.is_owner()
    async def theme(self, ctx: commands.Context, *, theme):
        """[Owner] Change the theme for adventure."""
        if theme == "default":
            await self.config.theme.set("default")
            await smart_embed(ctx, _("Going back to the default theme."))
            await self.initialize()
            return
        if theme not in os.listdir(cog_data_path(self)):
            await smart_embed(ctx, _("That theme pack does not exist!"))
            return
        good_files = [
            "as_monsters.json",
            "attribs.json",
            "locations.json",
            "monsters.json",
            "pets.json",
            "raisins.json",
            "threatee.json",
            "tr_set.json",
            "prefixes.json",
            "materials.json",
            "equipment.json",
            "suffixes.json",
            "set_bonuses.json",
        ]
        missing_files = set(good_files).difference(os.listdir(cog_data_path(self) / theme))

        if missing_files:
            await smart_embed(
                ctx, _("That theme pack is missing the following files: {}.").format(humanize_list(missing_files)),
            )
            return
        else:
            await self.config.theme.set(theme)
            await ctx.tick()
        await self.initialize()

    @commands.group()
    @commands.guild_only()
    @commands.is_owner()
    async def themeset(self, ctx: commands.Context):
        """[Admin] Modify themes."""

    @commands.is_owner()
    @themeset.group(name="add")
    async def themeset_add(self, ctx: commands.Context):
        """[Owner] Add/Update objects in the specified theme."""

    @themeset_add.command(name="monster")
    async def themeset_add_monster(self, ctx: commands.Context, *, theme_data: ThemeSetMonterConverter):
        """[Owner] Add/Update a monster object in the specified theme.

        Usage: `[p]themeset add monster theme++name++hp++dipl++pdef++mdef++cdef++boss++image`
        """
        assert isinstance(theme_data, dict)
        theme = theme_data.pop("theme", None)
        if theme != "default" and theme not in os.listdir(cog_data_path(self)):
            await smart_embed(ctx, _("That theme pack does not exist!"))
            return
        updated = False
        monster = theme_data.pop("name", None)
        async with self.config.themes.all() as config_data:
            if theme not in config_data:
                config_data[theme] = {"monsters": {}}
            if "monsters" not in config_data[theme]:
                config_data[theme]["monsters"] = {}
            if monster in config_data[theme]["monsters"]:
                updated = True
            config_data[theme]["monsters"][monster] = theme_data
        image = theme_data.pop("image", None)
        text = _(
            "Monster: `{monster}` has been {status} the `{theme}` theme\n"
            "```ini\n"
            "HP:                  [{hp}]\n"
            "Diplomacy:           [{dipl}]\n"
            "Physical defence:    [{pdef}]\n"
            "Magical defence:     [{mdef}]\n"
            "Persuasion defence:  [{cdef}]\n"
            "Is a boss:           [{boss}]```"
        ).format(monster=monster, theme=theme, status=_("added to") if not updated else _("updated in"), **theme_data,)

        embed = discord.Embed(description=text, colour=await ctx.embed_colour())
        embed.set_image(url=image)
        await ctx.send(embed=embed)

    @themeset_add.command(name="pet")
    async def themeset_add_pet(self, ctx: commands.Context, *, pet_data: ThemeSetPetConverter):
        """[Owner] Add/Update a pet object in the specified theme.

        Usage: `[p]themeset add pet theme++name++bonus_multiplier++required_cha++crit_chance++always_crit`
        """
        assert isinstance(pet_data, dict)
        theme = pet_data.pop("theme", None)
        if theme != "default" and theme not in os.listdir(cog_data_path(self)):
            await smart_embed(ctx, _("That theme pack does not exist!"))
            return
        updated = False
        pet = pet_data.pop("name", None)
        async with self.config.themes.all() as config_data:
            if theme not in config_data:
                config_data[theme] = {"pet": {}}
            if "pet" not in config_data[theme]:
                config_data[theme]["pet"] = {}
            if pet in config_data[theme]["pet"]:
                updated = True
            config_data[theme]["pet"][pet] = pet_data

        pet_bonuses = pet_data.pop("bonuses", {})
        text = _(
            "Pet: `{pet}` has been {status} the `{theme}` theme\n"
            "```ini\n"
            "Bonus Multiplier:  [{bonus}]\n"
            "Required Charisma: [{cha}]\n"
            "Pet always crits:  [{always}]\n"
            "Critical Chance:   [{crit}/100]```"
        ).format(
            pet=pet, theme=theme, status=_("added to") if not updated else _("updated in"), **pet_data, **pet_bonuses,
        )

        embed = discord.Embed(description=text, colour=await ctx.embed_colour())
        await ctx.send(embed=embed)

    @commands.is_owner()
    @themeset.group(name="delete", aliases=["del", "rem", "remove"])
    async def themeset_delete(self, ctx: commands.Context):
        """[Owner] Remove objects in the specified theme."""

    @themeset_delete.command(name="monster")
    async def themeset_delete_monster(self, ctx: commands.Context, theme: str, *, monster: str):
        """[Owner] Remove a monster object in the specified theme."""
        if theme != "default" and theme not in os.listdir(cog_data_path(self)):
            await smart_embed(ctx, _("That theme pack does not exist!"))
            return
        async with self.config.themes.all() as config_data:
            if theme not in config_data:
                config_data[theme] = {"monsters": {}}
            if "monsters" not in config_data[theme]:
                config_data[theme]["monsters"] = {}
            if monster in config_data[theme]["monsters"]:
                del config_data[theme]["monsters"][monster]
            else:
                text = _("Monster: `{monster}` does not exist in `{theme}` theme").format(monster=monster, theme=theme)
                await smart_embed(ctx, text)
                return

        text = _("Monster: `{monster}` has been deleted from the `{theme}` theme").format(monster=monster, theme=theme)
        await smart_embed(ctx, text)

    @themeset_delete.command(name="pet")
    async def themeset_delete_pet(self, ctx: commands.Context, theme: str, *, pet: str):
        """[Owner] Remove a pet object in the specified theme."""
        if theme != "default" and theme not in os.listdir(cog_data_path(self)):
            await smart_embed(ctx, _("That theme pack does not exist!"))
            return
        async with self.config.themes.all() as config_data:
            if theme not in config_data:
                config_data[theme] = {"pet": {}}
            if "pet" not in config_data[theme]:
                config_data[theme]["pet"] = {}
            if pet in config_data[theme]["pet"]:
                del config_data[theme]["pet"][pet]
            else:
                text = _("Pet: `{pet}` does not exist in `{theme}` theme").format(pet=pet, theme=theme)
                await smart_embed(ctx, text)
                return

        text = _("Pet: `{pet}` has been deleted from the `{theme}` theme").format(pet=pet, theme=theme)
        await smart_embed(ctx, text)

    @themeset.group(name="list", aliases=["show"])
    async def themeset_list(self, ctx: commands.Context):
        """[Admin] Show custom objects in the specified theme."""

    @themeset_list.command(name="monster")
    async def themeset_list_monster(self, ctx: commands.Context, *, theme: str):
        """[Admin] Show monster objects in the specified theme."""
        if theme != "default" and theme not in os.listdir(cog_data_path(self)):
            await smart_embed(ctx, _("That theme pack does not exist!"))
            return
        async with self.config.themes.all() as config_data:
            if theme not in config_data:
                return await smart_embed(ctx, _("No custom monsters exist in this theme"))
            monster_data = config_data.get(theme, {}).get("monsters", {})
        embed_list = []
        for monster, monster_stats in monster_data.items():
            image = monster_stats.get("image")
            monster_stats["cdef"] = monster_stats.get("cdef", 1.0)
            text = _(
                "```ini\n"
                "HP:                  [{hp}]\n"
                "Diplomacy:           [{dipl}]\n"
                "Physical defence:    [{pdef}]\n"
                "Magical defence:     [{mdef}]\n"
                "Persuasion defence:  [{cdef}]\n"
                "Is a boss:           [{boss}]```"
            ).format(**monster_stats)
            embed = discord.Embed(title=monster, description=text)
            embed.set_image(url=image)
            embed_list.append(embed)
        if embed_list:
            await BaseMenu(
                source=SimpleSource(embed_list), delete_message_after=True, clear_reactions_after=True, timeout=60,
            ).start(ctx=ctx)

    @themeset_list.command(name="pet")
    async def themeset_list_pet(self, ctx: commands.Context, *, theme: str):
        """[Admin] Show pet objects in the specified theme."""
        if theme != "default" and theme not in os.listdir(cog_data_path(self)):
            await smart_embed(ctx, _("That theme pack does not exist!"))
            return
        async with self.config.themes.all() as config_data:
            if theme not in config_data:
                return await smart_embed(ctx, _("No custom monsters exist in this theme"))
            monster_data = config_data.get(theme, {}).get("pet", {})
        embed_list = []
        for pet, pet_stats in monster_data.items():
            pet_bonuses = pet_stats.pop("bonuses", {})
            text = _(
                "```ini\n"
                "Bonus Multiplier:  [{bonus}]\n"
                "Required Charisma: [{cha}]\n"
                "Pet always crits:  [{always}]\n"
                "Critical Chance:   [{crit}/100]```"
            ).format(theme=theme, **pet_stats, **pet_bonuses)
            embed = discord.Embed(title=pet, description=text)
            embed_list.append(embed)
        if embed_list:
            await BaseMenu(
                source=SimpleSource(embed_list), delete_message_after=True, clear_reactions_after=True, timeout=60,
            ).start(ctx=ctx)

    @adventureset.command()
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def cart(self, ctx: commands.Context, *, channel: discord.TextChannel = None):
        """[Admin] Add or remove a text channel that the Trader cart can appear in.

        If the channel is already in the list, it will be removed.
        Use `[p]adventureset cart` with no arguments to show the channel list.
        """

        channel_list = await self.config.guild(ctx.guild).cart_channels()
        if not channel_list:
            channel_list = []
        if channel is None:
            msg = _("Active Cart Channels:\n")
            if not channel_list:
                msg += _("None.")
            else:
                name_list = []
                for chan_id in channel_list:
                    name_list.append(self.bot.get_channel(chan_id))
                msg += "\n".join(chan.name for chan in name_list)
            return await ctx.send(box(msg))
        elif channel.id in channel_list:
            new_channels = channel_list.remove(channel.id)
            await smart_embed(
                ctx, _("The {} channel has been removed from the cart delivery list.").format(channel),
            )
            return await self.config.guild(ctx.guild).cart_channels.set(new_channels)
        else:
            channel_list.append(channel.id)
            await smart_embed(ctx, _("The {} channel has been added to the cart delivery list.").format(channel))
            await self.config.guild(ctx.guild).cart_channels.set(channel_list)

    @commands.guild_only()
    @commands.command()
    @commands.cooldown(rate=1, per=4, type=commands.BucketType.guild)
    async def adventuresettings(self, ctx: commands.Context):
        """Display current settings."""
        global_data = await self.config.all()
        guild_data = await self.config.guild(ctx.guild).all()
        is_owner = await self.bot.is_owner(ctx.author)
        theme = global_data["theme"]
        god_name = global_data["god_name"] if not guild_data["god_name"] else guild_data["god_name"]
        cart_trader_name = global_data["cart_name"] if not guild_data["cart_name"] else guild_data["cart_name"]

        cart_channel_ids = guild_data["cart_channels"]
        if cart_channel_ids:
            cart_channels = humanize_list([f"{self.bot.get_channel(x).name}" for x in cart_channel_ids])
        else:
            cart_channels = _("None")

        cart_channel_lock_override_id = guild_data["cartroom"]
        if cart_channel_lock_override_id:
            cclo_channel_obj = self.bot.get_channel(cart_channel_lock_override_id)
            cart_channel_lock_override = f"{cclo_channel_obj.name}"
        else:
            cart_channel_lock_override = _("No channel lock present.")

        cart_timeout = parse_timedelta(f"{guild_data['cart_timeout']} seconds")
        # lootbox_in_carts = _("Allowed") if global_data["enable_chests"] else _("Not allowed")

        if not await bank.is_global():
            rebirth_cost = guild_data["rebirth_cost"]
        else:
            rebirth_cost = global_data["rebirth_cost"]
        rebirth_cost = _("{0:.0%} of bank balance").format(rebirth_cost / 100)

        single_adventure_restrict = _("Restricted") if global_data["restrict"] else _("Unlimited")
        adventure_in_embed = _("Allow embeds") if guild_data["embed"] else _("No embeds")
        time_after_adventure = parse_timedelta(f"{guild_data['cooldown_timer_manual']} seconds")

        separate_economy = global_data["separate_economy"]
        economy_string = _("\n# Economy Settings\n")
        economy_string += _("[Separated Currency]:                   {state}\n").format(
            state=_("Enabled") if separate_economy else _("Disabled")
        )

        if separate_economy:
            main_currency_name = await bank.get_currency_name(ctx.guild, _forced=True)
            adv_currency_name = await bank.get_currency_name(ctx.guild)
            if await bank.is_global(_forced=True):
                withdraw_state = global_data["disallow_withdraw"]
                max_allowed_withdraw = global_data["max_allowed_withdraw"]

            else:
                withdraw_state = guild_data["disallow_withdraw"]
                max_allowed_withdraw = guild_data["max_allowed_withdraw"]
            economy_string += _("[Withdraw to Bank]:                     {state}\n").format(
                state=_("Allowed") if withdraw_state else _("Disallowed")
            )
            if withdraw_state:
                economy_string += _("[Max withdraw allowed]:                 {state}\n").format(
                    state=humanize_number(max_allowed_withdraw)
                )
            to_conversion_rate = global_data["to_conversion_rate"]
            from_conversion_rate = global_data["from_conversion_rate"]

            economy_string += _(
                "[Bank to Adventure conversion rate]:    1 {main_name} will be worth {ratio} {adventure_name}\n"
            ).format(main_name=main_currency_name, ratio=1 * to_conversion_rate, adventure_name=adv_currency_name,)
            economy_string += _(
                "[Adventure to bank conversion rate]:    {ratio} {adventure_name} will be worth 1 {main_name}\n"
            ).format(main_name=main_currency_name, ratio=from_conversion_rate, adventure_name=adv_currency_name,)
            if is_owner:
                economy_string += _("\n# Tax Settings\n")
                taxes = global_data["tax_brackets"]
                for cur, tax in sorted(taxes.items(), key=lambda x: x[1]):
                    economy_string += _("[{tax:06.2%}]:                               {currency}\n").format(
                        tax=tax, currency=humanize_number(int(cur))
                    )

        daily_bonus = global_data["daily_bonus"]
        daily_bonus_string = "\n# Daily Bonuses\n"
        daily_bonus_string += _("[Monday]:                               {v:.2%}\n").format(v=daily_bonus.get("1", 0))
        daily_bonus_string += _("[Tuesday]:                              {v:.2%}\n").format(v=daily_bonus.get("2", 0))
        daily_bonus_string += _("[Wednesday]:                            {v:.2%}\n").format(v=daily_bonus.get("3", 0))
        daily_bonus_string += _("[Thursday]:                             {v:.2%}\n").format(v=daily_bonus.get("4", 0))
        daily_bonus_string += _("[Friday]:                               {v:.2%}\n").format(v=daily_bonus.get("5", 0))
        daily_bonus_string += _("[Saturday]:                             {v:.2%}\n").format(v=daily_bonus.get("6", 0))
        daily_bonus_string += _("[Sunday]:                               {v:.2%}\n").format(v=daily_bonus.get("7", 0))

        easy_mode = global_data["easy_mode"]
        msg = _("Adventure Settings\n\n")
        msg += _("# Main Settings\n")
        msg += _("[Easy Mode]:                            {state}\n").format(
            state=_("Enabled") if easy_mode else _("Disabled")
        )
        msg += _("[Theme]:                                {theme}\n").format(theme=theme)
        msg += _("[God name]:                             {god_name}\n").format(god_name=god_name)
        msg += _("[Base rebirth cost]:                    {rebirth_cost}\n").format(rebirth_cost=rebirth_cost)
        msg += _("[Adventure message style]:              {adventure_in_embed}\n").format(
            adventure_in_embed=adventure_in_embed
        )
        msg += _("[Multi-adventure restriction]:          {single_adventure_restrict}\n").format(
            single_adventure_restrict=single_adventure_restrict
        )
        msg += _("[Post-adventure cooldown (hh:mm:ss)]:   {time_after_adventure}\n\n").format(
            time_after_adventure=time_after_adventure
        )
        msg += _("# Cart Settings\n")
        msg += _("[Cart trader name]:                     {cart_trader_name}\n").format(
            cart_trader_name=cart_trader_name
        )
        msg += _("[Cart delivery channels]:               {cart_channels}\n").format(cart_channels=cart_channels)
        msg += _("[Cart channel lock override]:           {cart_channel_lock_override}\n").format(
            cart_channel_lock_override=cart_channel_lock_override
        )
        msg += _("[Cart timeout (hh:mm:ss)]:              {cart_timeout}\n").format(cart_timeout=cart_timeout)
        # msg += _("[Lootboxes in carts]:                   {lootbox_in_carts}\n").format(
        #     lootbox_in_carts=lootbox_in_carts
        # )
        msg += economy_string
        msg += daily_bonus_string
        if is_owner:
            with contextlib.suppress(discord.HTTPException):
                await ctx.author.send(box(msg, lang="ini"))
        else:
            await ctx.send(box(msg, lang="ini"))

    @commands.command()
    @commands.cooldown(rate=1, per=4, type=commands.BucketType.guild)
    async def convert(self, ctx: commands.Context, box_rarity: str, amount: int = 1):
        """Convert normal, rare or epic chests.

        Trade 25 normal chests for 1 rare chest.
        Trade 25 rare chests for 1 epic chest.
        Trade 25 epic chests for 1 legendary chest.
        """

        # Thanks to flare#0001 for the idea and writing the first instance of this
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx,
                _(
                    "You tried to magically combine some of your loot chests "
                    "but the monster ahead is commanding your attention."
                ),
            )
        normalcost = 25
        rarecost = 25
        epiccost = 25
        rebirth_normal = 2
        rebirth_rare = 8
        rebirth_epic = 10
        if amount < 1:
            return await smart_embed(ctx, _("Nice try :smirk:"))
        if amount > 1:
            plural = "s"
        else:
            plural = ""
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return

            if box_rarity.lower() == "rare" and c.rebirths < rebirth_rare:
                return await smart_embed(
                    ctx,
                    ("**{}**, you need to have {} or more rebirths to convert rare treasure chests.").format(
                        self.escape(ctx.author.display_name), rebirth_rare
                    ),
                )
            elif box_rarity.lower() == "epic" and c.rebirths < rebirth_epic:
                return await smart_embed(
                    ctx,
                    ("**{}**, you need to have {} or more rebirths to convert epic treasure chests.").format(
                        self.escape(ctx.author.display_name), rebirth_epic
                    ),
                )
            elif c.rebirths < 2:
                return await smart_embed(
                    ctx,
                    _("**{c}**, you need to 3 rebirths to use this.").format(c=self.escape(ctx.author.display_name)),
                )

            if box_rarity.lower() == "normal" and c.rebirths >= rebirth_normal:
                if c.treasure[0] >= (normalcost * amount):
                    c.treasure[0] -= normalcost * amount
                    c.treasure[1] += 1 * amount
                    await ctx.send(
                        box(
                            _(
                                "Successfully converted {converted} normal treasure "
                                "chests to {to} rare treasure chest{plur}.\n{author} "
                                "now owns {normal} normal, {rare} rare, {epic} epic, "
                                "{leg} legendary treasure chests, {asc} ascended and {set} set treasure chests."
                            ).format(
                                converted=humanize_number(normalcost * amount),
                                to=humanize_number(1 * amount),
                                plur=plural,
                                author=self.escape(ctx.author.display_name),
                                normal=c.treasure[0],
                                rare=c.treasure[1],
                                epic=c.treasure[2],
                                leg=c.treasure[3],
                                asc=c.treasure[4],
                                set=c.treasure[5],
                            ),
                            lang="css",
                        )
                    )
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                else:
                    await smart_embed(
                        ctx,
                        _("**{author}**, you do not have {amount} normal treasure chests to convert.").format(
                            author=self.escape(ctx.author.display_name), amount=humanize_number(normalcost * amount),
                        ),
                    )
            elif box_rarity.lower() == "rare" and c.rebirths >= rebirth_rare:
                if c.treasure[1] >= (rarecost * amount):
                    c.treasure[1] -= rarecost * amount
                    c.treasure[2] += 1 * amount
                    await ctx.send(
                        box(
                            _(
                                "Successfully converted {converted} rare treasure "
                                "chests to {to} epic treasure chest{plur}. \n{author} "
                                "now owns {normal} normal, {rare} rare, {epic} epic, "
                                "{leg} legendary treasure chests, {asc} ascended and {set} set treasure chests."
                            ).format(
                                converted=humanize_number(rarecost * amount),
                                to=humanize_number(1 * amount),
                                plur=plural,
                                author=self.escape(ctx.author.display_name),
                                normal=c.treasure[0],
                                rare=c.treasure[1],
                                epic=c.treasure[2],
                                leg=c.treasure[3],
                                asc=c.treasure[4],
                                set=c.treasure[5],
                            ),
                            lang="css",
                        )
                    )
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                else:
                    await smart_embed(
                        ctx,
                        _("{author}, you do not have {amount} rare treasure chests to convert.").format(
                            author=ctx.author.mention, amount=humanize_number(rarecost * amount)
                        ),
                    )
            elif box_rarity.lower() == "epic" and c.rebirths >= rebirth_epic:
                if c.treasure[2] >= (epiccost * amount):
                    c.treasure[2] -= epiccost * amount
                    c.treasure[3] += 1 * amount
                    await ctx.send(
                        box(
                            _(
                                "Successfully converted {converted} epic treasure "
                                "chests to {to} legendary treasure chest{plur}. \n{author} "
                                "now owns {normal} normal, {rare} rare, {epic} epic, "
                                "{leg} legendary treasure chests, {asc} ascended and {set} set treasure chests."
                            ).format(
                                converted=humanize_number(epiccost * amount),
                                to=humanize_number(1 * amount),
                                plur=plural,
                                author=self.escape(ctx.author.display_name),
                                normal=c.treasure[0],
                                rare=c.treasure[1],
                                epic=c.treasure[2],
                                leg=c.treasure[3],
                                asc=c.treasure[4],
                                set=c.treasure[5],
                            ),
                            lang="css",
                        )
                    )
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                else:
                    await smart_embed(
                        ctx,
                        _("**{author}**, you do not have {amount} epic treasure chests to convert.").format(
                            author=self.escape(ctx.author.display_name), amount=humanize_number(epiccost * amount),
                        ),
                    )
            else:
                await smart_embed(
                    ctx,
                    _("**{}**, please select between normal, rare, or epic treasure chests to convert.").format(
                        self.escape(ctx.author.display_name)
                    ),
                )

    @commands.command()
    async def equip(self, ctx: commands.Context, *, item: EquipableItemConverter):
        """This equips an item from your backpack."""
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to equip your item but the monster ahead nearly decapitated you."),
            )
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))

        await ctx.invoke(self.backpack_equip, equip_item=item)

    @commands.max_concurrency(1, per=commands.BucketType.user)
    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    async def forge(self, ctx):
        """[Tinkerer Class Only]

        This allows a Tinkerer to forge two items into a device. (1h cooldown)
        """
        if self.in_adventure(ctx):
            return await smart_embed(ctx, _("You tried to forge an item but there were no forges nearby."))
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if c.heroclass["name"] != "Tinkerer":
                return await smart_embed(
                    ctx,
                    _("**{}**, you need to be a Tinkerer to do this.").format(self.escape(ctx.author.display_name)),
                )
            else:
                cooldown_time = max(1800, (7200 - max((c.luck + c.total_int) * 2, 0)))
                if "cooldown" not in c.heroclass:
                    c.heroclass["cooldown"] = cooldown_time + 1
                if c.heroclass["cooldown"] > time.time():
                    cooldown_time = c.heroclass["cooldown"] - time.time()
                    return await smart_embed(
                        ctx,
                        _("This command is on cooldown. Try again in {}").format(
                            humanize_timedelta(seconds=int(cooldown_time)) if cooldown_time >= 1 else _("1 second")
                        ),
                    )
                ascended_forge_msg = ""
                ignored_rarities = ["forged", "set", "event"]
                if c.rebirths < 30:
                    ignored_rarities.append("ascended")
                    ascended_forge_msg += _("\n\nAscended items will be forgeable after 30 rebirths.")
                consumed = []
                forgeables_items = [str(i) for n, i in c.backpack.items() if i.rarity not in ignored_rarities]
                if len(forgeables_items) <= 1:
                    return await smart_embed(
                        ctx,
                        _("**{}**, you need at least two forgeable items in your backpack to forge.{}").format(
                            self.escape(ctx.author.display_name), ascended_forge_msg
                        ),
                    )
                pages = await c.get_backpack(forging=True, clean=True)
                if not pages:
                    return await smart_embed(
                        ctx,
                        _("**{}**, you need at least two forgeable items in your backpack to forge.").format(
                            self.escape(ctx.author.display_name)
                        ),
                    )
                await BaseMenu(
                    source=SimpleSource(pages), delete_message_after=True, clear_reactions_after=True, timeout=180,
                ).start(ctx=ctx)
                await smart_embed(
                    ctx,
                    _(
                        "Reply with the full or partial name of item 1 to select for forging. "
                        "Try to be specific. (Say `cancel` to exit){}".format(ascended_forge_msg)
                    ),
                )
                try:
                    item = None
                    while not item:
                        reply = await ctx.bot.wait_for(
                            "message", check=MessagePredicate.same_context(user=ctx.author), timeout=30,
                        )
                        new_ctx = await self.bot.get_context(reply)
                        if reply.content.lower() in ["cancel", "exit"]:
                            return await smart_embed(ctx, _("Forging process has been cancelled."))
                        with contextlib.suppress(BadArgument):
                            item = None
                            item = await ItemConverter().convert(new_ctx, reply.content)
                            if str(item) not in forgeables_items:
                                item = None

                        if not item:
                            wrong_item = _("**{c}**, I could not find that item - check your spelling.").format(
                                c=self.escape(ctx.author.display_name)
                            )
                            await smart_embed(ctx, wrong_item)
                        elif not can_equip(c, item):
                            wrong_item = _("**{c}**, this item is too high level for you to reforge it.").format(
                                c=self.escape(ctx.author.display_name)
                            )
                            await smart_embed(ctx, wrong_item)
                            item = None
                            continue
                        else:
                            break
                    consumed.append(item)
                except asyncio.TimeoutError:
                    timeout_msg = _("I don't have all day you know, **{}**.").format(
                        self.escape(ctx.author.display_name)
                    )
                    return await smart_embed(ctx, timeout_msg)
                if item.rarity in ["forged", "set"]:
                    return await smart_embed(
                        ctx,
                        _("**{c}**, {item.rarity} items cannot be reforged.").format(
                            c=self.escape(ctx.author.display_name), item=item
                        ),
                    )
                await smart_embed(
                    ctx,
                    _(
                        "Reply with the full or partial name of item 2 to select for forging. "
                        "Try to be specific. (Say `cancel` to exit)"
                    ),
                )
                try:
                    item = None
                    while not item:
                        reply = await ctx.bot.wait_for(
                            "message", check=MessagePredicate.same_context(user=ctx.author), timeout=30,
                        )
                        if reply.content.lower() in ["cancel", "exit"]:
                            return await smart_embed(ctx, _("Forging process has been cancelled."))
                        new_ctx = await self.bot.get_context(reply)
                        with contextlib.suppress(BadArgument):
                            item = None
                            item = await ItemConverter().convert(new_ctx, reply.content)
                            if str(item) not in forgeables_items:
                                item = None
                        if item and consumed[0].owned <= 1 and str(consumed[0]) == str(item):
                            wrong_item = _(
                                "**{c}**, you only own 1 copy of this item and you've already selected it."
                            ).format(c=self.escape(ctx.author.display_name))
                            await smart_embed(ctx, wrong_item)

                            continue
                        if not item:
                            wrong_item = _("**{c}**, I could not find that item - check your spelling.").format(
                                c=self.escape(ctx.author.display_name)
                            )
                            await smart_embed(ctx, wrong_item)
                        elif not can_equip(c, item):
                            wrong_item = _("**{c}**, this item is too high level for you to reforge it.").format(
                                c=self.escape(ctx.author.display_name)
                            )
                            await smart_embed(ctx, wrong_item)
                            item = None
                            continue
                        else:
                            break
                    consumed.append(item)
                except asyncio.TimeoutError:
                    timeout_msg = _("I don't have all day you know, **{}**.").format(
                        self.escape(ctx.author.display_name)
                    )
                    return await smart_embed(ctx, timeout_msg)
                if item.rarity in ["forged", "set"]:
                    return await smart_embed(
                        ctx,
                        _("**{c}**, {item.rarity} items cannot be reforged.").format(
                            c=self.escape(ctx.author.display_name), item=item
                        ),
                    )
                newitem = await self._to_forge(ctx, consumed, c)
                for x in consumed:
                    c.backpack[x.name].owned -= 1
                    if c.backpack[x.name].owned <= 0:
                        del c.backpack[x.name]
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                # save so the items are eaten up already
                for item in c.get_current_equipment():
                    if item.rarity == "forged":
                        c = await c.unequip_item(item)
                lookup = list(i for n, i in c.backpack.items() if i.rarity == "forged")
                if len(lookup) > 0:
                    forge_str = box(
                        _("{author}, you already have a device. Do you want to replace {replace}?").format(
                            author=self.escape(ctx.author.display_name), replace=", ".join([str(x) for x in lookup]),
                        ),
                        lang="css",
                    )
                    forge_msg = await ctx.send(forge_str)
                    start_adding_reactions(forge_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                    pred = ReactionPredicate.yes_or_no(forge_msg, ctx.author)
                    try:
                        await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                    except asyncio.TimeoutError:
                        await self._clear_react(forge_msg)
                        return
                    with contextlib.suppress(discord.HTTPException):
                        await forge_msg.delete()
                    if pred.result:  # user reacted with Yes.
                        c.heroclass["cooldown"] = time.time() + cooldown_time
                        created_item = box(
                            _("{author}, your new {newitem} consumed {lk} and is now lurking in your backpack.").format(
                                author=self.escape(ctx.author.display_name),
                                newitem=newitem,
                                lk=", ".join([str(x) for x in lookup]),
                            ),
                            lang="css",
                        )
                        for item in lookup:
                            del c.backpack[item.name]
                        await ctx.send(created_item)
                        c.backpack[newitem.name] = newitem
                        await self.config.user(ctx.author).set(await c.to_json(self.config))
                    else:
                        c.heroclass["cooldown"] = time.time() + cooldown_time
                        await self.config.user(ctx.author).set(await c.to_json(self.config))
                        mad_forge = box(
                            _("{author}, {newitem} got mad at your rejection and blew itself up.").format(
                                author=self.escape(ctx.author.display_name), newitem=newitem
                            ),
                            lang="css",
                        )
                        return await ctx.send(mad_forge)
                else:
                    c.heroclass["cooldown"] = time.time() + cooldown_time
                    c.backpack[newitem.name] = newitem
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                    forged_item = box(
                        _("{author}, your new {newitem} is lurking in your backpack.").format(
                            author=self.escape(ctx.author.display_name), newitem=newitem
                        ),
                        lang="css",
                    )
                    await ctx.send(forged_item)

    async def _to_forge(self, ctx: commands.Context, consumed, character):
        item1 = consumed[0]
        item2 = consumed[1]

        roll = random.randint(1, 20)
        modifier = (roll / 20) + 0.3
        base_cha = max(character._cha, 1)
        base_int = character._int
        base_luck = character._luck
        base_att = max(character._att, 1)
        modifier_bonus_luck = 0.01 * base_luck // 10
        modifier_bonus_int = 0.01 * base_int // 20
        modifier_penalty_str = -0.01 * base_att // 20
        modifier_penalty_cha = -0.01 * base_cha // 10
        modifier = sum([modifier_bonus_int, modifier_bonus_luck, modifier_penalty_cha, modifier_penalty_str, modifier])
        modifier = max(0.001, modifier)

        base_int = int(item1.int) + int(item2.int)
        base_cha = int(item1.cha) + int(item2.cha)
        base_att = int(item1.att) + int(item2.att)
        base_dex = int(item1.dex) + int(item2.dex)
        base_luck = int(item1.luck) + int(item2.luck)
        newatt = int((base_att * modifier) + base_att)
        newdip = int((base_cha * modifier) + base_cha)
        newint = int((base_int * modifier) + base_int)
        newdex = int((base_dex * modifier) + base_dex)
        newluck = int((base_luck * modifier) + base_luck)
        newslot = random.choice(ORDER)
        if newslot == "two handed":
            newslot = ["right", "left"]
        else:
            newslot = [newslot]
        if len(newslot) == 2:  # two handed weapons add their bonuses twice
            hand = "two handed"
        else:
            if newslot[0] == "right" or newslot[0] == "left":
                hand = newslot[0] + " handed"
            else:
                hand = newslot[0] + " slot"
        if len(newslot) == 2:
            two_handed_msg = box(
                _(
                    "{author}, your forging roll was {dice}({roll}).\n"
                    "The device you tinkered will have "
                    "(ATT {new_att} | "
                    "CHA {new_cha} | "
                    "INT {new_int} | "
                    "DEX {new_dex} | "
                    "LUCK {new_luck})"
                    " and be {hand}."
                ).format(
                    author=self.escape(ctx.author.display_name),
                    roll=roll,
                    dice=self.emojis.dice,
                    new_att=(newatt * 2),
                    new_cha=(newdip * 2),
                    new_int=(newint * 2),
                    new_dex=(newdex * 2),
                    new_luck=(newluck * 2),
                    hand=hand,
                ),
                lang="css",
            )
            await ctx.send(two_handed_msg)
        else:
            reg_item = box(
                _(
                    "{author}, your forging roll was {dice}({roll}).\n"
                    "The device you tinkered will have "
                    "(ATT {new_att} | "
                    "CHA {new_dip} | "
                    "INT {new_int} | "
                    "DEX {new_dex} | "
                    "LUCK {new_luck})"
                    " and be {hand}."
                ).format(
                    author=self.escape(ctx.author.display_name),
                    roll=roll,
                    dice=self.emojis.dice,
                    new_att=newatt,
                    new_dip=newdip,
                    new_int=newint,
                    new_dex=newdex,
                    new_luck=newluck,
                    hand=hand,
                ),
                lang="css",
            )
            await ctx.send(reg_item)
        get_name = _(
            "**{}**, please respond with "
            "a name for your creation within 30s.\n"
            "(You will not be able to change it afterwards. 40 characters maximum.)"
        ).format(self.escape(ctx.author.display_name))
        await smart_embed(ctx, get_name)
        reply = None
        name = _("Unnamed Artifact")
        try:
            reply = await ctx.bot.wait_for("message", check=MessagePredicate.same_context(user=ctx.author), timeout=30)
        except asyncio.TimeoutError:
            name = _("Unnamed Artifact")
        if reply is None:
            name = _("Unnamed Artifact")
        else:
            if hasattr(reply, "content"):
                if len(reply.content) > 40:
                    name = _("Long-winded Artifact")
                else:
                    name = reply.content.lower()
        item = {
            name: {
                "slot": newslot,
                "att": newatt,
                "cha": newdip,
                "int": newint,
                "dex": newdex,
                "luck": newluck,
                "rarity": "forged",
            }
        }
        item = Item.from_json(item)
        return item

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def give(self, ctx: commands.Context):
        """[Admin] Commands to add things to players' inventories."""

    @give.command(name="item")
    @commands.is_owner()
    async def _give_item(self, ctx: commands.Context, user: discord.User, item_name: str, *, stats: Stats):
        """[Owner] Adds a custom item to a specified member.

        Item names containing spaces must be enclosed in double quotes. `[p]give item @locastan
        "fine dagger" 1 att 1 charisma rare twohanded` will give a two handed .fine_dagger with 1
        attack and 1 charisma to locastan. if a stat is not specified it will default to 0, order
        does not matter. available stats are attack(att), charisma(diplo) or charisma(cha),
        intelligence(int), dexterity(dex), and luck.

        Item rarity is one of normal, rare, epic, legendary, set, forged, event.

        Event items can have their level requirement and degrade number set via:
        N degrade - (Set to -1 to never degrade on rebirths)
        N level

        `[p]give item @locastan "fine dagger" 1 att 1 charisma -1 degrade 100 level rare twohanded`
        """
        if item_name.isnumeric():
            return await smart_embed(ctx, _("Item names cannot be numbers."))
        item_name = re.sub(r"[^\w ]", "", item_name)
        if user is None:
            user = ctx.author
        new_item = {item_name: stats}
        item = Item.from_json(new_item)
        async with self.get_lock(user):
            try:
                c = await Character.from_json(self.config, user, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            await c.add_to_backpack(item)
            await self.config.user(user).set(await c.to_json(self.config))
        await ctx.send(
            box(
                _("An item named {item} has been created and placed in {author}'s backpack.").format(
                    item=item, author=self.escape(user.display_name)
                ),
                lang="css",
            )
        )

    @give.command(name="loot")
    @commands.is_owner()
    async def _give_loot(
        self, ctx: commands.Context, loot_type: str, users: commands.Greedy[discord.User] = None, number: int = 1
    ):
        """[Owner] Give treasure chest(s) to all specified users."""

        users = users or [ctx.author]
        loot_types = ["normal", "rare", "epic", "legendary", "ascended", "set"]
        if loot_type not in loot_types:
            return await smart_embed(
                ctx,
                (
                    "Valid loot types: `normal`, `rare`, `epic`, `legendary`, `ascended` or `set`: "
                    "ex. `{}give loot normal @locastan` "
                ).format(ctx.prefix),
            )
        if loot_type in ["legendary", "set", "ascended"] and not await ctx.bot.is_owner(ctx.author):
            return await smart_embed(ctx, _("You are not worthy to award legendary loot."))
        for user in users:
            async with self.get_lock(user):
                try:
                    c = await Character.from_json(self.config, user, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    continue
                if loot_type == "rare":
                    c.treasure[1] += number
                elif loot_type == "epic":
                    c.treasure[2] += number
                elif loot_type == "legendary":
                    c.treasure[3] += number
                elif loot_type == "ascended":
                    c.treasure[4] += number
                elif loot_type == "set":
                    c.treasure[5] += number
                else:
                    c.treasure[0] += number
                await self.config.user(user).set(await c.to_json(self.config))
                await ctx.send(
                    box(
                        _(
                            "{author} now owns {normal} normal, "
                            "{rare} rare, {epic} epic, "
                            "{leg} legendary, {asc} ascended and {set} set treasure chests."
                        ).format(
                            author=self.escape(user.display_name),
                            normal=str(c.treasure[0]),
                            rare=str(c.treasure[1]),
                            epic=str(c.treasure[2]),
                            leg=str(c.treasure[3]),
                            asc=str(c.treasure[4]),
                            set=str(c.treasure[5]),
                        ),
                        lang="css",
                    )
                )

    @commands.command(cooldown_after_parsing=True)
    @commands.bot_has_permissions(add_reactions=True)
    @commands.cooldown(rate=1, per=7200, type=commands.BucketType.user)
    async def heroclass(self, ctx: commands.Context, clz: str = None, action: str = None):
        """Allows you to select a class if you are level 10 or above.

        For information on class use: `[p]heroclass classname info`.
        """
        if self.in_adventure(ctx):
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(ctx, _("The monster ahead growls menacingly, and will not let you leave."))
        if not await self.allow_in_dm(ctx):
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))

        classes = {
            "Wizard": {
                "name": _("Wizard"),
                "ability": False,
                "desc": _(
                    "Wizards have the option to focus and add large bonuses to their magic, "
                    "but their focus can sometimes go astray...\n"
                    "Use the focus command when attacking in an adventure."
                ),
                "cooldown": time.time(),
            },
            "Tinkerer": {
                "name": _("Tinkerer"),
                "ability": False,
                "desc": _(
                    "Tinkerers can forge two different items into a device "
                    "bound to their very soul.\nUse the forge command."
                ),
                "cooldown": time.time(),
            },
            "Berserker": {
                "name": _("Berserker"),
                "ability": False,
                "desc": _(
                    "Berserkers have the option to rage and add big bonuses to attacks, "
                    "but fumbles hurt.\nUse the rage command when attacking in an adventure."
                ),
                "cooldown": time.time(),
            },
            "Cleric": {
                "name": _("Cleric"),
                "ability": False,
                "desc": _(
                    "Clerics can bless the entire group when praying.\n"
                    "Use the bless command when fighting in an adventure."
                ),
                "cooldown": time.time(),
            },
            "Ranger": {
                "name": _("Ranger"),
                "ability": False,
                "desc": _(
                    "Rangers can gain a special pet, which can find items and give "
                    "reward bonuses.\nUse the pet command to see pet options."
                ),
                "pet": {},
                "cooldown": time.time(),
                "catch_cooldown": time.time(),
            },
            "Bard": {
                "name": _("Bard"),
                "ability": False,
                "desc": _(
                    "Bards can perform to aid their comrades in diplomacy.\n"
                    "Use the music command when being diplomatic in an adventure."
                ),
                "cooldown": time.time(),
            },
            "Psychic": {
                "name": _("Psychic"),
                "ability": False,
                "desc": _(
                    "Psychics can show the enemy's weaknesses to their group "
                    "allowing them to target the monster's weak-points.\n"
                    "Use the insight command in an adventure."
                ),
                "cooldown": time.time(),
            },
        }

        if clz is None:
            ctx.command.reset_cooldown(ctx)
            await smart_embed(
                ctx,
                _(
                    "So you feel like taking on a class, **{author}**?\n"
                    "Available classes are: Tinkerer, Berserker, "
                    "Wizard, Cleric, Ranger, Psychic and Bard.\n"
                    "Use `{prefix}heroclass name-of-class` to choose one."
                ).format(author=self.escape(ctx.author.display_name), prefix=ctx.prefix),
            )

        else:
            clz = clz.title()
            if clz in classes and action == "info":
                ctx.command.reset_cooldown(ctx)
                return await smart_embed(ctx, f"{classes[clz]['desc']}")
            elif clz not in classes:
                ctx.command.reset_cooldown(ctx)
                return await smart_embed(ctx, _("{} may be a class somewhere, but not on my watch.").format(clz))
            elif clz in classes and action is None:
                async with self.get_lock(ctx.author):
                    bal = await bank.get_balance(ctx.author)
                    currency_name = await bank.get_currency_name(ctx.guild,)
                    if str(currency_name).startswith("<"):
                        currency_name = "credits"
                    spend = round(bal * 0.2)
                    try:
                        c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
                    except Exception as exc:
                        log.exception("Error with the new character sheet", exc_info=exc)
                        ctx.command.reset_cooldown(ctx)
                        return
                    if c.heroclass["name"] == clz:
                        ctx.command.reset_cooldown(ctx)
                        return await smart_embed(ctx, _("You already are a {}.").format(clz))
                    if clz == "Psychic" and c.rebirths < 25:
                        ctx.command.reset_cooldown(ctx)
                        return await smart_embed(ctx, _("You are too inexperienced to become a {}.").format(clz))
                    class_msg = await ctx.send(
                        box(
                            _("This will cost {spend} {currency_name}. Do you want to continue, {author}?").format(
                                spend=humanize_number(spend),
                                currency_name=currency_name,
                                author=self.escape(ctx.author.display_name),
                            ),
                            lang="css",
                        )
                    )
                    broke = box(
                        _("You don't have enough {currency_name} to train to be a {clz}.").format(
                            currency_name=currency_name, clz=clz.title()
                        ),
                        lang="css",
                    )
                    start_adding_reactions(class_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                    pred = ReactionPredicate.yes_or_no(class_msg, ctx.author)
                    try:
                        await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                    except asyncio.TimeoutError:
                        await self._clear_react(class_msg)
                        ctx.command.reset_cooldown(ctx)
                        return

                    if not pred.result:
                        await class_msg.edit(
                            content=box(
                                _("{author} decided to continue being a {h_class}.").format(
                                    author=self.escape(ctx.author.display_name), h_class=c.heroclass["name"],
                                ),
                                lang="css",
                            )
                        )
                        ctx.command.reset_cooldown(ctx)
                        return await self._clear_react(class_msg)
                    if bal < spend:
                        await class_msg.edit(content=broke)
                        ctx.command.reset_cooldown(ctx)
                        return await self._clear_react(class_msg)
                    if not await bank.can_spend(ctx.author, spend):
                        return await class_msg.edit(content=broke)
                    try:
                        c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
                    except Exception as exc:
                        log.exception("Error with the new character sheet", exc_info=exc)
                        return
                    now_class_msg = _("Congratulations, {author}.\nYou are now a {clz}.").format(
                        author=self.escape(ctx.author.display_name), clz=classes[clz]["name"]
                    )
                    if c.lvl >= 10:
                        if c.heroclass["name"] == "Tinkerer" or c.heroclass["name"] == "Ranger":
                            if c.heroclass["name"] == "Tinkerer":
                                await self._clear_react(class_msg)
                                await class_msg.edit(
                                    content=box(
                                        _(
                                            "{}, you will lose your forged "
                                            "device if you change your class.\nShall I proceed?"
                                        ).format(self.escape(ctx.author.display_name)),
                                        lang="css",
                                    )
                                )
                            else:
                                await self._clear_react(class_msg)
                                await class_msg.edit(
                                    content=box(
                                        _(
                                            "{}, you will lose your pet if you change your class.\nShall I proceed?"
                                        ).format(self.escape(ctx.author.display_name)),
                                        lang="css",
                                    )
                                )
                            start_adding_reactions(class_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                            pred = ReactionPredicate.yes_or_no(class_msg, ctx.author)
                            try:
                                await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                            except asyncio.TimeoutError:
                                await self._clear_react(class_msg)
                                ctx.command.reset_cooldown(ctx)
                                return
                            if pred.result:  # user reacted with Yes.
                                tinker_wep = []
                                for item in c.get_current_equipment():
                                    if item.rarity == "forged":
                                        c = await c.unequip_item(item)
                                for (name, item) in c.backpack.items():
                                    if item.rarity == "forged":
                                        tinker_wep.append(item)
                                for item in tinker_wep:
                                    del c.backpack[item.name]
                                if c.heroclass["name"] == "Tinkerer":
                                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                                    if tinker_wep:
                                        await class_msg.edit(
                                            content=box(
                                                _("{} has run off to find a new master.").format(
                                                    humanize_list(tinker_wep)
                                                ),
                                                lang="css",
                                            )
                                        )

                                else:
                                    c.heroclass["ability"] = False
                                    c.heroclass["pet"] = {}
                                    c.heroclass = classes[clz]

                                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                                    await self._clear_react(class_msg)
                                    await class_msg.edit(
                                        content=box(
                                            _("{} released their pet into the wild.\n").format(
                                                self.escape(ctx.author.display_name)
                                            ),
                                            lang="css",
                                        )
                                    )
                                await class_msg.edit(content=class_msg.content + box(now_class_msg, lang="css"))
                            else:
                                ctx.command.reset_cooldown(ctx)
                                return
                        if c.skill["pool"] < 0:
                            c.skill["pool"] = 0
                        c.heroclass = classes[clz]
                        if c.heroclass["name"] in ["Wizard", "Cleric"]:
                            c.heroclass["cooldown"] = (
                                max(300, (1200 - max((c.luck + c.total_int) * 2, 0))) + time.time()
                            )
                        elif c.heroclass["name"] == "Ranger":
                            c.heroclass["cooldown"] = (
                                max(1800, (7200 - max(c.luck * 2 + c.total_int * 2, 0))) + time.time()
                            )
                            c.heroclass["catch_cooldown"] = (
                                max(600, (3600 - max(c.luck * 2 + c.total_int * 2, 0))) + time.time()
                            )
                        elif c.heroclass["name"] == "Berserker":
                            c.heroclass["cooldown"] = (
                                max(300, (1200 - max((c.luck + c.total_att) * 2, 0))) + time.time()
                            )
                        elif c.heroclass["name"] == "Bard":
                            c.heroclass["cooldown"] = (
                                max(300, (1200 - max((c.luck + c.total_cha) * 2, 0))) + time.time()
                            )
                        elif c.heroclass["name"] == "Tinkerer":
                            c.heroclass["cooldown"] = (
                                max(900, (3600 - max((c.luck + c.total_int) * 2, 0))) + time.time()
                            )
                        elif c.heroclass["name"] == "Psychic":
                            c.heroclass["cooldown"] = max(300, (900 - max((c.luck - c.total_cha) * 2, 0))) + time.time()
                        await self.config.user(ctx.author).set(await c.to_json(self.config))
                        await self._clear_react(class_msg)
                        await class_msg.edit(content=box(now_class_msg, lang="css"))
                        try:
                            await bank.withdraw_credits(ctx.author, spend)
                        except ValueError:
                            return await class_msg.edit(content=broke)
                    else:
                        ctx.command.reset_cooldown(ctx)
                        await smart_embed(
                            ctx,
                            _("**{}**, you need to be at least level 10 to choose a class.").format(
                                self.escape(ctx.author.display_name)
                            ),
                        )

    @staticmethod
    def check_running_adventure(ctx):
        for (guild_id, session) in ctx.bot.get_cog("Adventure")._sessions.items():
            user_ids: list = []
            options = ["fight", "magic", "talk", "pray", "run"]
            for i in options:
                user_ids += [u.id for u in getattr(session, i)]
            if ctx.author.id in user_ids:
                return False
        return True

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    @commands.cooldown(rate=1, per=4, type=commands.BucketType.user)
    async def loot(self, ctx: commands.Context, box_type: str = None, number: int = 1):
        """This opens one of your precious treasure chests.

        Use the box rarity type with the command: normal, rare, epic, legendary, ascended or set.
        """
        if (not self.is_dev(ctx.author) and number > 100) or number < 1:
            return await smart_embed(ctx, _("Nice try :smirk:."))
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to open a loot chest but then realised you left them all back at the inn."),
            )
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        msgs = []
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if not box_type:
                return await ctx.send(
                    box(
                        _(
                            "{author} owns {normal} normal, "
                            "{rare} rare, {epic} epic, {leg} legendary, {asc} ascended and {set} set chests."
                        ).format(
                            author=self.escape(ctx.author.display_name),
                            normal=str(c.treasure[0]),
                            rare=str(c.treasure[1]),
                            epic=str(c.treasure[2]),
                            leg=str(c.treasure[3]),
                            asc=str(c.treasure[4]),
                            set=str(c.treasure[5]),
                        ),
                        lang="css",
                    )
                )
            if c.is_backpack_full(is_dev=self.is_dev(ctx.author)):
                await ctx.send(
                    _("**{author}**, your backpack is currently full.").format(
                        author=self.escape(ctx.author.display_name)
                    )
                )
                return
            if box_type == "normal":
                redux = 0
            elif box_type == "rare":
                redux = 1
            elif box_type == "epic":
                redux = 2
            elif box_type == "legendary":
                redux = 3
            elif box_type == "ascended":
                redux = 4
            elif box_type == "set":
                redux = 5
            else:
                return await smart_embed(
                    ctx, _("There is talk of a {} treasure chest but nobody ever saw one.").format(box_type),
                )
            treasure = c.treasure[redux]
            if treasure < 1 or treasure < number:
                await smart_embed(
                    ctx,
                    _("**{author}**, you do not have enough {box} treasure chests to open.").format(
                        author=self.escape(ctx.author.display_name), box=box_type
                    ),
                )
            else:
                if number > 1:
                    async with ctx.typing():
                        # atomically save reduced loot count then lock again when saving inside
                        # open chests
                        c.treasure[redux] -= number
                        await self.config.user(ctx.author).set(await c.to_json(self.config))
                        items = await self._open_chests(ctx, box_type, number, character=c)
                        msg = _("{}, you've opened the following items:\n\n").format(
                            self.escape(ctx.author.display_name)
                        )
                        msg_len = len(msg)
                        table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
                        table.set_style(BeautifulTable.STYLE_RST)
                        msgs = []
                        total = len(items.values())
                        table.columns.header = [
                            "Name",
                            "Slot",
                            "ATT",
                            "CHA",
                            "INT",
                            "DEX",
                            "LUC",
                            "LVL",
                            "QTY",
                            "DEG",
                            "SET",
                        ]
                        async for index, item in AsyncIter(items.values(), steps=100).enumerate(start=1):
                            if len(str(table)) > 1500:
                                table.rows.sort("LVL", reverse=True)
                                msgs.append(box(msg + str(table) + f"\nPage {len(msgs) + 1}", lang="css"))
                                table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
                                table.set_style(BeautifulTable.STYLE_RST)
                                table.columns.header = [
                                    "Name",
                                    "Slot",
                                    "ATT",
                                    "CHA",
                                    "INT",
                                    "DEX",
                                    "LUC",
                                    "LVL",
                                    "QTY",
                                    "DEG",
                                    "SET",
                                ]
                            table.rows.append(
                                (
                                    str(item),
                                    item.slot[0] if len(item.slot) == 1 else "two handed",
                                    item.att,
                                    item.cha,
                                    item.int,
                                    item.dex,
                                    item.luck,
                                    f"[{r}]" if (r := equip_level(c, item)) is not None and r > c.lvl else f"{r}",
                                    item.owned,
                                    f"[{item.degrade}]"
                                    if item.rarity in ["legendary", "event", "ascended"] and item.degrade >= 0
                                    else "N/A",
                                    item.set or "N/A",
                                )
                            )
                            if index == total:
                                table.rows.sort("LVL", reverse=True)
                                msgs.append(box(msg + str(table) + f"\nPage {len(msgs) + 1}", lang="css"))
                else:
                    # atomically save reduced loot count then lock again when saving inside
                    # open chests
                    c.treasure[redux] -= 1
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                    await self._open_chest(ctx, ctx.author, box_type, character=c)  # returns item and msg
        if msgs:
            await BaseMenu(
                source=SimpleSource(msgs), delete_message_after=True, clear_reactions_after=True, timeout=60,
            ).start(ctx=ctx)

    @commands.command(name="negaverse", aliases=["nv"], cooldown_after_parsing=True)
    @commands.cooldown(rate=1, per=3600, type=commands.BucketType.user)
    @commands.guild_only()
    async def _negaverse(
        self, ctx: commands.Context, offering: int = None, roll: Optional[int] = -1, nega: discord.User = None
    ):
        """This will send you to fight a nega-member!"""
        if self.in_adventure(ctx):
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(
                ctx, _("You tried to teleport to another dimension but the monster ahead did not give you a chance."),
            )

        bal = await bank.get_balance(ctx.author)
        currency_name = await bank.get_currency_name(ctx.guild,)
        if offering is None:
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(
                ctx,
                _(
                    "**{author}**, you need to specify how many "
                    "{currency_name} you are willing to offer to the gods for your success."
                ).format(author=self.escape(ctx.author.display_name), currency_name=currency_name),
            )
        if offering <= 500 or bal <= 500:
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(ctx, _("The gods refuse your pitiful offering."))
        if offering > bal:
            offering = int(bal)
        admin_roll = -1
        nega_set = False
        if (roll >= 0 or nega) and await self.bot.is_owner(ctx.author):
            if not self.is_dev(ctx.author):
                if not await no_dev_prompt(ctx):
                    ctx.command.reset_cooldown(ctx)
                    return
            nega_set = True
            admin_roll = roll
        offering_value = 0
        winning_state = False
        loss_state = False
        xp_won_final = 0
        lock = self.get_lock(ctx.author)
        await lock.acquire()
        try:
            nv_msg = await ctx.send(
                _(
                    "**{author}**, this will cost you at least {offer} {currency_name}.\n"
                    "You currently have {bal}. Do you want to proceed?"
                ).format(
                    author=self.escape(ctx.author.display_name),
                    offer=humanize_number(offering),
                    currency_name=currency_name,
                    bal=humanize_number(bal),
                )
            )
            start_adding_reactions(nv_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(nv_msg, ctx.author)
            try:
                await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
            except asyncio.TimeoutError:
                ctx.command.reset_cooldown(ctx)
                await self._clear_react(nv_msg)
                lock.release()
                return
            if not pred.result:
                with contextlib.suppress(discord.HTTPException):
                    ctx.command.reset_cooldown(ctx)
                    await nv_msg.edit(
                        content=_("**{}** decides against visiting the negaverse... for now.").format(
                            self.escape(ctx.author.display_name)
                        )
                    )
                    lock.release()
                    return await self._clear_react(nv_msg)

            percentage_offered = (offering / bal) * 100
            min_roll = int(percentage_offered / 10)
            entry_roll = max(random.randint(max(1, min_roll), 20), 0) if admin_roll == -1 else admin_roll
            if entry_roll == 1:
                tax_mod = random.randint(4, 8)
                tax = round(bal / tax_mod)
                if tax > offering:
                    loss = tax
                else:
                    loss = offering
                offering_value += loss
                loss_state = True
                await bank.withdraw_credits(ctx.author, loss)
                entry_msg = _(
                    "A swirling void slowly grows and you watch in horror as it rushes to "
                    "wash over you, leaving you cold... and your coin pouch significantly lighter. "
                    "The portal to the negaverse remains closed."
                )
                lock.release()
                return await nv_msg.edit(content=entry_msg)
            else:
                entry_msg = _(
                    "Shadowy hands reach out to take your offering from you and a swirling "
                    "black void slowly grows and engulfs you, transporting you to the negaverse."
                )
                await nv_msg.edit(content=entry_msg)
                await self._clear_react(nv_msg)
                await bank.withdraw_credits(ctx.author, offering)
            if nega_set:
                nega_member = nega
                negachar = _("The Almighty Nega-{c}").format(c=self.escape(nega_member.display_name))
            else:
                nega_member = random.choice(ctx.message.guild.members)
                negachar = _("Nega-{c}").format(c=self.escape(nega_member.display_name))

            nega_msg = await ctx.send(
                _("**{author}** enters the negaverse and meets **{negachar}**.").format(
                    author=self.escape(ctx.author.display_name), negachar=negachar
                )
            )

            try:
                character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                lock.release()
                ctx.command.reset_cooldown(ctx)
                return
            roll = random.randint(max(1, min_roll * 2), 50) if admin_roll == -1 else admin_roll
            if self.is_dev(nega_member):
                roll = -2
            versus = random.randint(10, 60)
            xp_mod = random.randint(1, 10)
            daymult = self._daily_bonus.get(str(datetime.today().isoweekday()), 0)
            xp_won = int((offering / xp_mod))
            xp_to_max = int((character.maxlevel + 1) ** 3.5)
            ten_percent = xp_to_max * 0.1
            xp_won = ten_percent if xp_won > ten_percent else xp_won
            xp_won = int(xp_won * (min(max(random.randint(0, character.rebirths), 1), 50) / 100 + 1))
            xp_won = int(xp_won * (character.gear_set_bonus.get("xpmult", 1) + daymult))
            if roll == -2:
                looted = ""
                curr_balance = character.bal
                await bank.set_balance(ctx.author, 0)
                offering_value += curr_balance
                loss_string = _("all of their")
                loss_state = True
                items = await character.looted(how_many=max(int(10 - roll) // 2, 1))
                if items:
                    item_string = "\n".join([f"{v} x{i}" for v, i in items])
                    looted = box(f"{item_string}", lang="css")
                    await self.config.user(ctx.author).set(await character.to_json(self.config))
                loss_msg = _(
                    ", losing {loss} {currency_name} as **{negachar}** rifled through their belongings."
                ).format(loss=loss_string, currency_name=currency_name, negachar=negachar)
                if looted:
                    loss_msg += _(" **{negachar}** also stole the following items:\n\n{items}").format(
                        items=looted, negachar=negachar
                    )
                await nega_msg.edit(
                    content=_("{content}\n**{author}** fumbled and died to **{negachar}'s** savagery{loss_msg}").format(
                        content=nega_msg.content,
                        author=self.escape(ctx.author.display_name),
                        negachar=negachar,
                        loss_msg=loss_msg,
                    )
                )
                ctx.command.reset_cooldown(ctx)
            elif roll < 10:
                loss = round(bal // 3)
                looted = ""
                curr_balance = character.bal
                try:
                    await bank.withdraw_credits(ctx.author, loss)
                    offering_value += loss
                    loss_string = humanize_number(loss)
                except ValueError:
                    await bank.set_balance(ctx.author, 0)
                    offering_value += curr_balance
                    loss_string = _("all of their")
                loss_state = True
                if character.bal < loss:
                    items = await character.looted(how_many=max(int(10 - roll) // 2, 1))
                    if items:
                        item_string = "\n".join([f"{v} {i}" for v, i in items])
                        looted = box(f"{item_string}", lang="css")
                        await self.config.user(ctx.author).set(await character.to_json(self.config))
                loss_msg = _(
                    ", losing {loss} {currency_name} as **{negachar}** rifled through their belongings."
                ).format(loss=loss_string, currency_name=currency_name, negachar=negachar)
                if looted:
                    loss_msg += _(" **{negachar}** also stole the following items:\n\n{items}").format(
                        items=looted, negachar=negachar
                    )
                await nega_msg.edit(
                    content=_("{content}\n**{author}** fumbled and died to **{negachar}'s** savagery{loss_msg}").format(
                        content=nega_msg.content,
                        author=self.escape(ctx.author.display_name),
                        negachar=negachar,
                        loss_msg=loss_msg,
                    )
                )
                ctx.command.reset_cooldown(ctx)
            elif roll == 50 and versus < 50:
                await nega_msg.edit(
                    content=_(
                        "{content}\n**{author}** decapitated **{negachar}**. "
                        "You gain {xp_gain} xp and take "
                        "{offering} {currency_name} back from the shadowy corpse."
                    ).format(
                        content=nega_msg.content,
                        author=self.escape(ctx.author.display_name),
                        negachar=negachar,
                        xp_gain=humanize_number(xp_won),
                        offering=humanize_number(offering),
                        currency_name=currency_name,
                    )
                )
                with contextlib.suppress(Exception):
                    lock.release()
                msg = await self._add_rewards(ctx, ctx.author, xp_won, offering, False)
                xp_won_final += xp_won
                offering_value += offering
                winning_state = True
                if msg:
                    await smart_embed(ctx, msg, success=True)
            elif roll > versus:
                await nega_msg.edit(
                    content=_(
                        "{content}\n**{author}** "
                        "{dice}({roll}) bravely defeated **{negachar}** {dice}({versus}). "
                        "You gain {xp_gain} xp."
                    ).format(
                        dice=self.emojis.dice,
                        content=nega_msg.content,
                        author=self.escape(ctx.author.display_name),
                        roll=roll,
                        negachar=negachar,
                        versus=versus,
                        xp_gain=humanize_number(xp_won),
                    )
                )
                with contextlib.suppress(Exception):
                    lock.release()
                msg = await self._add_rewards(ctx, ctx.author, xp_won, 0, False)
                xp_won_final += xp_won
                offering_value += offering
                winning_state = True
                if msg:
                    await smart_embed(ctx, msg, success=True)
            elif roll == versus:
                ctx.command.reset_cooldown(ctx)
                await nega_msg.edit(
                    content=_(
                        "{content}\n**{author}** {dice}({roll}) almost killed **{negachar}** {dice}({versus})."
                    ).format(
                        dice=self.emojis.dice,
                        content=nega_msg.content,
                        author=self.escape(ctx.author.display_name),
                        roll=roll,
                        negachar=negachar,
                        versus=versus,
                    )
                )
            else:
                loss = round(bal / (random.randint(10, 25)))
                curr_balance = character.bal
                looted = ""
                try:
                    await bank.withdraw_credits(ctx.author, loss)
                    offering_value += loss
                    loss_string = humanize_number(loss)
                except ValueError:
                    await bank.set_balance(ctx.author, 0)
                    loss_string = _("all of their")
                    offering_value += curr_balance
                loss_state = True
                if character.bal < loss:
                    items = await character.looted(how_many=max(int(10 - roll) // 2, 1))
                    if items:
                        item_string = "\n".join([f"{i}  - {v}" for v, i in items])
                        looted = box(f"{item_string}", lang="css")
                        await self.config.user(ctx.author).set(await character.to_json(self.config))
                loss_msg = _(", losing {loss} {currency_name} as **{negachar}** looted their backpack.").format(
                    loss=loss_string, currency_name=currency_name, negachar=negachar,
                )
                if looted:
                    loss_msg += _(" **{negachar}** also stole the following items\n\n{items}").format(
                        items=looted, negachar=negachar
                    )
                await nega_msg.edit(
                    content=_(
                        "**{author}** {dice}({roll}) was killed by **{negachar}** {dice}({versus}){loss_msg}"
                    ).format(
                        dice=self.emojis.dice,
                        author=self.escape(ctx.author.display_name),
                        roll=roll,
                        negachar=negachar,
                        versus=versus,
                        loss_msg=loss_msg,
                    )
                )
                ctx.command.reset_cooldown(ctx)
        finally:
            lock = self.get_lock(ctx.author)
            with contextlib.suppress(Exception):
                lock.release()
            try:
                character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
            else:
                changed = False
                if character.last_currency_check + 600 < time.time() or character.bal > character.last_known_currency:
                    character.last_known_currency = await bank.get_balance(ctx.author)
                    character.last_currency_check = time.time()
                    changed = True
                if offering_value > 0:
                    current_gold__losses_value = character.nega.get("gold__losses", 0)
                    character.nega.update({"gold__losses": int(current_gold__losses_value + offering_value)})
                    changed = True
                if xp_won_final > 0:
                    current_xp__earnings_value = character.nega.get("xp__earnings", 0)
                    character.nega.update({"xp__earnings": current_xp__earnings_value + xp_won_final})
                    changed = True
                if winning_state is not False:
                    current_wins_value = character.nega.get("wins", 0)
                    character.nega.update({"wins": current_wins_value + 1})
                    changed = True
                if loss_state is not False:
                    current_loses_value = character.nega.get("loses", 0)
                    character.nega.update({"loses": current_loses_value + 1})
                    changed = True

                if changed:
                    await self.config.user(ctx.author).set(await character.to_json(self.config))

    @commands.group(autohelp=False)
    @commands.cooldown(rate=1, per=5, type=commands.BucketType.user)
    async def pet(self, ctx: commands.Context):
        """[Ranger Class Only]

        This allows a Ranger to tame or set free a pet or send it foraging.
        """
        if ctx.invoked_subcommand is None:
            if self.in_adventure(ctx):
                return await smart_embed(ctx, _("You're too distracted with the monster you are facing."))

            if not await self.allow_in_dm(ctx):
                return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
            async with self.get_lock(ctx.author):
                try:
                    c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    return
                if c.heroclass["name"] != "Ranger":
                    return await smart_embed(
                        ctx,
                        _("**{}**, you need to be a Ranger to do this.").format(self.escape(ctx.author.display_name)),
                    )
                if c.heroclass["pet"]:
                    ctx.command.reset_cooldown(ctx)
                    return await ctx.send(
                        box(
                            _("{author}, you already have a pet. Try foraging ({prefix}pet forage).").format(
                                author=self.escape(ctx.author.display_name), prefix=ctx.prefix
                            ),
                            lang="css",
                        )
                    )
                else:
                    cooldown_time = max(600, (3600 - max((c.luck + c.total_int) * 2, 0)))
                    if "catch_cooldown" not in c.heroclass:
                        c.heroclass["catch_cooldown"] = cooldown_time + 1
                    if c.heroclass["catch_cooldown"] > time.time():
                        cooldown_time = c.heroclass["catch_cooldown"] - time.time()
                        return await smart_embed(
                            ctx,
                            _(
                                "You caught a pet recently, or you are a brand new Ranger. "
                                "You will be able to go hunting in {}."
                            ).format(
                                humanize_timedelta(seconds=int(cooldown_time))
                                if int(cooldown_time) >= 1
                                else _("1 second")
                            ),
                        )
                    theme = await self.config.theme()
                    extra_pets = await self.config.themes.all()
                    extra_pets = extra_pets.get(theme, {}).get("pets", {})
                    pet_list = {**self.PETS, **extra_pets}
                    pet_choices = list(pet_list.keys())
                    pet = random.choice(pet_choices)
                    roll = random.randint(1, 50)
                    dipl_value = c.total_cha + (c.total_int // 3) + (c.luck // 2)
                    pet_reqs = pet_list[pet].get("bonuses", {}).get("req", {})
                    pet_msg4 = ""
                    can_catch = True
                    force_catch = False
                    if any(x in c.sets for x in ["The Supreme One", "Ainz Ooal Gown"]):
                        can_catch = True
                        pet = random.choice(
                            ["Albedo", "Rubedo", "Guardians of Nazarick", *random.choices(pet_choices, k=10),]
                        )
                        if pet in ["Albedo", "Rubedo", "Guardians of Nazarick"]:
                            force_catch = True
                    elif pet_reqs.get("bonuses", {}).get("req"):
                        if pet_reqs.get("set", None) in c.sets:
                            can_catch = True
                        else:
                            can_catch = False
                            pet_msg4 = _("\nPerhaps you're missing some requirements to tame {pet}.").format(pet=pet)
                    pet_msg = box(
                        _("{c} is trying to tame a pet.").format(c=self.escape(ctx.author.display_name)), lang="css",
                    )
                    user_msg = await ctx.send(pet_msg)
                    await asyncio.sleep(2)
                    pet_msg2 = box(
                        _("{author} started tracking a wild {pet_name} with a roll of {dice}({roll}).").format(
                            dice=self.emojis.dice, author=self.escape(ctx.author.display_name), pet_name=pet, roll=roll,
                        ),
                        lang="css",
                    )
                    await user_msg.edit(content=f"{pet_msg}\n{pet_msg2}")
                    await asyncio.sleep(2)
                    bonus = ""
                    if roll == 1:
                        bonus = _("But they stepped on a twig and scared it away.")
                    elif roll in [50, 25]:
                        bonus = _("They happen to have its favorite food.")
                    if force_catch or (dipl_value > pet_list[pet]["cha"] and roll > 1 and can_catch):
                        if force_catch:
                            roll = 0
                        else:
                            roll = random.randint(0, (2 if roll in [50, 25] else 5))
                        if roll == 0:
                            if force_catch and any(x in c.sets for x in ["The Supreme One", "Ainz Ooal Gown"]):
                                msg = random.choice(
                                    [
                                        _("{author} commands {pet} into submission.").format(
                                            pet=pet, author=self.escape(ctx.author.display_name)
                                        ),
                                        _("{pet} swears allegiance to the Supreme One.").format(
                                            pet=pet, author=self.escape(ctx.author.display_name)
                                        ),
                                        _("{pet} takes an Oath of Allegiance to the Supreme One.").format(
                                            pet=pet, author=self.escape(ctx.author.display_name)
                                        ),
                                    ]
                                )
                                pet_msg3 = box(msg, lang="css",)
                            else:
                                pet_msg3 = box(
                                    _("{bonus}\nThey successfully tamed the {pet}.").format(bonus=bonus, pet=pet),
                                    lang="css",
                                )
                            await user_msg.edit(content=f"{pet_msg}\n{pet_msg2}\n{pet_msg3}")
                            c.heroclass["pet"] = pet_list[pet]
                            c.heroclass["catch_cooldown"] = time.time() + cooldown_time
                            await self.config.user(ctx.author).set(await c.to_json(self.config))
                        elif roll == 1:
                            bonus = _("But they stepped on a twig and scared it away.")
                            pet_msg3 = box(_("{bonus}\nThe {pet} escaped.").format(bonus=bonus, pet=pet), lang="css",)
                            await user_msg.edit(content=f"{pet_msg}\n{pet_msg2}\n{pet_msg3}{pet_msg4}")
                        else:
                            bonus = ""
                            pet_msg3 = box(_("{bonus}\nThe {pet} escaped.").format(bonus=bonus, pet=pet), lang="css",)
                            await user_msg.edit(content=f"{pet_msg}\n{pet_msg2}\n{pet_msg3}{pet_msg4}")
                    else:
                        pet_msg3 = box(_("{bonus}\nThe {pet} escaped.").format(bonus=bonus, pet=pet), lang="css",)
                        await user_msg.edit(content=f"{pet_msg}\n{pet_msg2}\n{pet_msg3}{pet_msg4}")

    @pet.command(name="forage")
    @commands.bot_has_permissions(add_reactions=True)
    async def _forage(self, ctx: commands.Context):
        """Use your pet to forage for items!"""
        if self.in_adventure(ctx):
            return await smart_embed(ctx, _("You're too distracted with the monster you are facing."))
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if c.heroclass["name"] != "Ranger":
                return
            if not c.heroclass["pet"]:
                return await smart_embed(
                    ctx, _("**{}**, you need to have a pet to do this.").format(self.escape(ctx.author.display_name)),
                )
            if c.is_backpack_full(is_dev=self.is_dev(ctx.author)):
                await ctx.send(
                    _("**{author}**, Your backpack is currently full.").format(
                        author=self.escape(ctx.author.display_name)
                    )
                )
                return
            cooldown_time = max(1800, (7200 - max((c.luck + c.total_int) * 2, 0)))
            if "cooldown" not in c.heroclass:
                c.heroclass["cooldown"] = cooldown_time + 1
            if c.heroclass["cooldown"] <= time.time():
                await self._open_chest(ctx, c.heroclass["pet"]["name"], "pet", character=c)
                c.heroclass["cooldown"] = time.time() + cooldown_time
                await self.config.user(ctx.author).set(await c.to_json(self.config))
            else:
                cooldown_time = c.heroclass["cooldown"] - time.time()
                return await smart_embed(
                    ctx,
                    _("This command is on cooldown. Try again in {}.").format(
                        humanize_timedelta(seconds=int(cooldown_time)) if int(cooldown_time) >= 1 else _("1 second")
                    ),
                )

    @pet.command(name="free")
    async def _free(self, ctx: commands.Context):
        """Free your pet :cry:"""
        if self.in_adventure(ctx):
            return await smart_embed(ctx, _("You're too distracted with the monster you are facing."))
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if c.heroclass["name"] != "Ranger":
                return await smart_embed(
                    ctx, _("**{}**, you need to be a Ranger to do this.").format(self.escape(ctx.author.display_name)),
                )
            if c.heroclass["pet"]:
                c.heroclass["pet"] = {}
                await self.config.user(ctx.author).set(await c.to_json(self.config))
                return await smart_embed(
                    ctx, _("**{}** released their pet into the wild..").format(self.escape(ctx.author.display_name)),
                )
            else:
                return await ctx.send(box(_("You don't have a pet."), lang="css"))

    @commands.command()
    async def bless(self, ctx: commands.Context):
        """[Cleric Class Only]

        This allows a praying Cleric to add substantial bonuses for heroes fighting the battle.
        """
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if c.heroclass["name"] != "Cleric":
                ctx.command.reset_cooldown(ctx)
                return await smart_embed(
                    ctx, _("**{}**, you need to be a Cleric to do this.").format(self.escape(ctx.author.display_name)),
                )
            else:
                if c.heroclass["ability"]:
                    return await smart_embed(
                        ctx, _("**{}**, ability already in use.").format(self.escape(ctx.author.display_name)),
                    )
                cooldown_time = max(300, (1200 - max((c.luck + c.total_int) * 2, 0)))
                if "cooldown" not in c.heroclass:
                    c.heroclass["cooldown"] = cooldown_time + 1
                if c.heroclass["cooldown"] <= time.time():
                    c.heroclass["ability"] = True
                    c.heroclass["cooldown"] = time.time() + cooldown_time
                    await self.config.user(ctx.author).set(await c.to_json(self.config))

                    await smart_embed(
                        ctx,
                        _("{bless} **{c}** is starting an inspiring sermon. {bless}").format(
                            c=self.escape(ctx.author.display_name), bless=self.emojis.skills.bless
                        ),
                    )
                else:
                    cooldown_time = c.heroclass["cooldown"] - time.time()
                    return await smart_embed(
                        ctx,
                        _(
                            "Your hero is currently recovering from the last time "
                            "they used this skill. Try again in {}."
                        ).format(
                            humanize_timedelta(seconds=int(cooldown_time)) if int(cooldown_time) >= 1 else _("1 second")
                        ),
                    )

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(rate=1, per=30, type=commands.BucketType.user)
    async def insight(self, ctx: commands.Context):
        """[Psychic Class Only]
        This allows a Psychic to expose the current enemy's weakeness to the party.
        """
        try:
            c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception:
            log.exception("Error with the new character sheet")
            ctx.command.reset_cooldown(ctx)
            return
        if c.heroclass["name"] != "Psychic":
            return await smart_embed(
                ctx, _("**{}**, you need to be a Psychic to do this.").format(self.escape(ctx.author.display_name)),
            )
        else:
            if ctx.guild.id not in self._sessions:
                return await smart_embed(ctx, _("There are no active adventures."),)
            if not self.in_adventure(ctx):
                return await smart_embed(
                    ctx, _("You tried to expose the enemy's weaknesses, but you aren't in an adventure."),
                )
            if c.heroclass["ability"]:
                return await smart_embed(
                    ctx, _("**{}**, ability already in use.").format(self.escape(ctx.author.display_name)),
                )
            cooldown_time = max(300, (900 - max((c.luck + c.total_cha) * 2, 0)))
            if "cooldown" not in c.heroclass:
                c.heroclass["cooldown"] = cooldown_time + 1
            if c.heroclass["cooldown"] + cooldown_time <= time.time():
                max_roll = 100 if c.rebirths >= 30 else 50 if c.rebirths >= 15 else 20
                roll = random.randint(min(c.rebirths - 25 // 2, (max_roll // 2)), max_roll) / max_roll
                if ctx.guild.id in self._sessions and self._sessions[ctx.guild.id].insight[0] < roll:
                    self._sessions[ctx.guild.id].insight = roll, c
                    good = True
                else:
                    good = False
                    await smart_embed(ctx, _("Another hero has already done a better job than you."))
                c.heroclass["ability"] = True
                c.heroclass["cooldown"] = time.time()
                async with self.get_lock(c.user):
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                    if good:
                        await smart_embed(
                            ctx,
                            _("{skill} **{c}** is focusing on the monster ahead...{skill}").format(
                                c=self.escape(ctx.author.display_name), skill=self.emojis.skills.psychic,
                            ),
                        )
                if good:
                    session = self._sessions[ctx.guild.id]
                    if roll <= 0.4:
                        return await smart_embed(ctx, _("You suck."))
                    msg = ""
                    if session.no_monster:
                        if roll >= 0.4:
                            msg += _("You are struggling to find anything in your current adventure.")
                    else:
                        pdef = session.monster_modified_stats["pdef"]
                        mdef = session.monster_modified_stats["mdef"]
                        cdef = session.monster_modified_stats.get("cdef", 1.0)
                        hp = session.monster_modified_stats["hp"]
                        diplo = session.monster_modified_stats["dipl"]
                        if roll == 1:
                            hp = int(hp * self.ATTRIBS[session.attribute][0] * session.monster_stats)
                            dipl = int(diplo * self.ATTRIBS[session.attribute][1] * session.monster_stats)
                            msg += _(
                                "This monster is **a{attr} {challenge}** ({hp_symbol} {hp}/{dipl_symbol} {dipl}){trans}.\n"
                            ).format(
                                challenge=session.challenge,
                                attr=session.attribute,
                                hp_symbol=self.emojis.hp,
                                hp=humanize_number(ceil(hp)),
                                dipl_symbol=self.emojis.dipl,
                                dipl=humanize_number(ceil(dipl)),
                                trans=f" (**Transcended**) {self.emojis.skills.psychic}"
                                if session.transcended
                                else f"{self.emojis.skills.psychic}",
                            )
                            self._sessions[ctx.guild.id].exposed = True
                        elif roll >= 0.95:
                            hp = hp * self.ATTRIBS[session.attribute][0] * session.monster_stats
                            dipl = diplo * self.ATTRIBS[session.attribute][1] * session.monster_stats
                            msg += _(
                                "This monster is **a{attr} {challenge}** ({hp_symbol} {hp}/{dipl_symbol} {dipl}).\n"
                            ).format(
                                challenge=session.challenge,
                                attr=session.attribute,
                                hp_symbol=self.emojis.hp,
                                hp=humanize_number(ceil(hp)),
                                dipl_symbol=self.emojis.dipl,
                                dipl=humanize_number(ceil(dipl)),
                            )
                            self._sessions[ctx.guild.id].exposed = True
                        elif roll >= 0.90:
                            hp = hp * self.ATTRIBS[session.attribute][0] * session.monster_stats
                            msg += _("This monster is **a{attr} {challenge}** ({hp_symbol} {hp}).\n").format(
                                challenge=session.challenge,
                                attr=session.attribute,
                                hp_symbol=self.emojis.hp,
                                hp=humanize_number(ceil(hp)),
                            )
                            self._sessions[ctx.guild.id].exposed = True
                        elif roll > 0.75:
                            msg += _("This monster is **a{attr} {challenge}**.\n").format(
                                challenge=session.challenge, attr=session.attribute,
                            )
                            self._sessions[ctx.guild.id].exposed = True
                        elif roll > 0.5:
                            msg += _("This monster is **a {challenge}**.\n").format(challenge=session.challenge,)
                            self._sessions[ctx.guild.id].exposed = True
                        if roll >= 0.4:
                            if pdef >= 1.5:
                                msg += _("Swords bounce off this monster as it's skin is **almost impenetrable!**\n")
                            elif pdef >= 1.25:
                                msg += _("This monster has **extremely tough** armour!\n")
                            elif pdef > 1:
                                msg += _("Swords don't cut this monster **quite as well!**\n")
                            elif pdef > 0.75:
                                msg += _("This monster is **soft and easy** to slice!\n")
                            else:
                                msg += _("Swords slice through this monster like a **hot knife through butter!**\n")
                        if roll >= 0.6:
                            if mdef >= 1.5:
                                msg += _("Magic? Pfft, magic is **no match** for this creature!\n")
                            elif mdef >= 1.25:
                                msg += _("This monster has **substantial magic resistance!**\n")
                            elif mdef > 1:
                                msg += _("This monster has increased **magic resistance!**\n")
                            elif mdef > 0.75:
                                msg += _("This monster's hide **melts to magic!**\n")
                            else:
                                msg += _("Magic spells are **hugely effective** against this monster!\n")
                        if roll >= 0.8:
                            if cdef >= 1.5:
                                msg += _(
                                    "You think you are charismatic? Pfft, this creature couldn't care less for what you want to say!\n"
                                )
                            elif cdef >= 1.25:
                                msg += _("Any attempts to communicate with this creature will be **very difficult!**\n")
                            elif cdef > 1:
                                msg += _("Any attempts to talk to this creature will be **difficult!**\n")
                            elif cdef > 0.75:
                                msg += _("This creature **can be reasoned** with!\n")
                            else:
                                msg += _("This monster can be **easily influenced!**\n")

                    if msg:
                        image = None
                        if roll >= 0.4:
                            image = session.monster["image"]
                        return await smart_embed(ctx, msg, image=image)
                    else:
                        return await smart_embed(ctx, _("You have failed to discover anything about this monster."))
            else:
                cooldown_time = (c.heroclass["cooldown"]) + cooldown_time - time.time()
                return await smart_embed(
                    ctx,
                    _(
                        "Your hero is currently recovering from the last time they used this skill. Try again in {}."
                    ).format(humanize_timedelta(seconds=int(cooldown_time))),
                )

    @commands.command()
    async def rage(self, ctx: commands.Context):
        """[Berserker Class Only]

        This allows a Berserker to add substantial attack bonuses for one battle.
        """
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if c.heroclass["name"] != "Berserker":
                ctx.command.reset_cooldown(ctx)
                return await smart_embed(
                    ctx,
                    _("**{}**, you need to be a Berserker to do this.").format(self.escape(ctx.author.display_name)),
                )
            else:
                if c.heroclass["ability"] is True:
                    return await smart_embed(
                        ctx, _("**{}**, ability already in use.").format(self.escape(ctx.author.display_name)),
                    )
                cooldown_time = max(300, (1200 - max((c.luck + c.total_att) * 2, 0)))
                if "cooldown" not in c.heroclass:
                    c.heroclass["cooldown"] = cooldown_time + 1
                if c.heroclass["cooldown"] <= time.time():
                    c.heroclass["ability"] = True
                    c.heroclass["cooldown"] = time.time() + cooldown_time
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                    await smart_embed(
                        ctx,
                        _("{skill} **{c}** is starting to froth at the mouth... {skill}").format(
                            c=self.escape(ctx.author.display_name), skill=self.emojis.skills.berserker,
                        ),
                    )
                else:
                    cooldown_time = c.heroclass["cooldown"] - time.time()
                    return await smart_embed(
                        ctx,
                        _(
                            "Your hero is currently recovering from the last time "
                            "they used this skill. Try again in {}."
                        ).format(
                            humanize_timedelta(seconds=int(cooldown_time)) if int(cooldown_time) >= 1 else _("1 second")
                        ),
                    )

    @commands.command()
    async def focus(self, ctx: commands.Context):
        """[Wizard Class Only]

        This allows a Wizard to add substantial magic bonuses for one battle.
        """
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if c.heroclass["name"] != "Wizard":
                ctx.command.reset_cooldown(ctx)
                return await smart_embed(
                    ctx, _("**{}**, you need to be a Wizard to do this.").format(self.escape(ctx.author.display_name)),
                )
            else:
                if c.heroclass["ability"] is True:
                    return await smart_embed(
                        ctx, _("**{}**, ability already in use.").format(self.escape(ctx.author.display_name)),
                    )
                cooldown_time = max(300, (1200 - max((c.luck + c.total_int) * 2, 0)))
                if "cooldown" not in c.heroclass:
                    c.heroclass["cooldown"] = cooldown_time + 1
                if c.heroclass["cooldown"] <= time.time():
                    c.heroclass["ability"] = True
                    c.heroclass["cooldown"] = time.time() + cooldown_time

                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                    await smart_embed(
                        ctx,
                        _("{skill} **{c}** is focusing all of their energy... {skill}").format(
                            c=self.escape(ctx.author.display_name), skill=self.emojis.skills.wizzard,
                        ),
                    )
                else:
                    cooldown_time = c.heroclass["cooldown"] - time.time()
                    return await smart_embed(
                        ctx,
                        _(
                            "Your hero is currently recovering from the "
                            "last time they used this skill. Try again in {}."
                        ).format(
                            humanize_timedelta(seconds=int(cooldown_time)) if int(cooldown_time) >= 1 else _("1 second")
                        ),
                    )

    @commands.command()
    async def music(self, ctx: commands.Context):
        """[Bard Class Only]

        This allows a Bard to add substantial diplomacy bonuses for one battle.
        """
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if c.heroclass["name"] != "Bard":
                ctx.command.reset_cooldown(ctx)
                return await smart_embed(
                    ctx, _("**{}**, you need to be a Bard to do this.").format(self.escape(ctx.author.display_name)),
                )
            else:
                if c.heroclass["ability"]:
                    return await smart_embed(
                        ctx, _("**{}**, ability already in use.").format(self.escape(ctx.author.display_name)),
                    )
                cooldown_time = max(300, (1200 - max((c.luck + c.total_cha) * 2, 0)))
                if "cooldown" not in c.heroclass:
                    c.heroclass["cooldown"] = cooldown_time + 1
                if c.heroclass["cooldown"] <= time.time():
                    c.heroclass["ability"] = True
                    c.heroclass["cooldown"] = time.time() + cooldown_time
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                    await smart_embed(
                        ctx,
                        _("{skill} **{c}** is whipping up a performance... {skill}").format(
                            c=self.escape(ctx.author.display_name), skill=self.emojis.skills.bard
                        ),
                    )
                else:
                    cooldown_time = c.heroclass["cooldown"] - time.time()
                    return await smart_embed(
                        ctx,
                        _(
                            "Your hero is currently recovering from the last time "
                            "they used this skill. Try again in {}."
                        ).format(humanize_timedelta(seconds=int(cooldown_time))),
                    )

    @commands.command()
    @commands.cooldown(rate=1, per=2, type=commands.BucketType.user)
    async def skill(self, ctx: commands.Context, spend: str = None, amount: int = 1):
        """This allows you to spend skillpoints.

        `[p]skill attack/charisma/intelligence`
        `[p]skill reset` Will allow you to reset your skill points for a cost.
        """
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("The skill cleric is back in town and the monster ahead of you is demanding your attention."),
            )
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        if amount < 1:
            return await smart_embed(ctx, _("Nice try :smirk:"))
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            if spend == "reset":
                last_reset = await self.config.user(ctx.author).last_skill_reset()
                if last_reset + 3600 > time.time():
                    return await smart_embed(ctx, _("You reset your skills within the last hour, try again later."))
                bal = c.bal
                currency_name = await bank.get_currency_name(ctx.guild,)
                offering = min(int(bal / 5 + (c.total_int // 3)), 1000000000)
                if not await bank.can_spend(ctx.author, offering):
                    return await smart_embed(
                        ctx,
                        _("{author.mention}, you don't have enough {name}.").format(
                            author=ctx.author, name=await bank.get_currency_name(ctx.guild)
                        ),
                    )
                nv_msg = await ctx.send(
                    _(
                        "{author}, this will cost you at least {offering} {currency_name}.\n"
                        "You currently have {bal}. Do you want to proceed?"
                    ).format(
                        author=self.escape(ctx.author.display_name),
                        offering=humanize_number(offering),
                        currency_name=currency_name,
                        bal=humanize_number(bal),
                    )
                )
                start_adding_reactions(nv_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(nv_msg, ctx.author)
                try:
                    await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
                except asyncio.TimeoutError:
                    await self._clear_react(nv_msg)
                    return

                if pred.result:
                    c.skill["pool"] += c.skill["att"] + c.skill["cha"] + c.skill["int"]
                    c.skill["att"] = 0
                    c.skill["cha"] = 0
                    c.skill["int"] = 0
                    await self.config.user(ctx.author).set(await c.to_json(self.config))
                    await self.config.user(ctx.author).last_skill_reset.set(int(time.time()))
                    await bank.withdraw_credits(ctx.author, offering)
                    await smart_embed(
                        ctx, _("{}, your skill points have been reset.").format(self.escape(ctx.author.display_name)),
                    )
                else:
                    await smart_embed(
                        ctx, _("Don't play games with me, {}.").format(self.escape(ctx.author.display_name)),
                    )
                return

            if c.skill["pool"] <= 0:
                return await smart_embed(
                    ctx, _("{}, you do not have unspent skillpoints.").format(self.escape(ctx.author.display_name)),
                )
            elif c.skill["pool"] < amount:
                return await smart_embed(
                    ctx,
                    _("{}, you do not have enough unspent skillpoints.").format(self.escape(ctx.author.display_name)),
                )
            if spend is None:
                await smart_embed(
                    ctx,
                    _(
                        "**{author}**, you currently have **{skillpoints}** unspent skillpoints.\n"
                        "If you want to put them towards a permanent attack, "
                        "charisma or intelligence bonus, use "
                        "`{prefix}skill attack`, `{prefix}skill charisma` or "
                        "`{prefix}skill intelligence`"
                    ).format(
                        author=self.escape(ctx.author.display_name),
                        skillpoints=str(c.skill["pool"]),
                        prefix=ctx.prefix,
                    ),
                )
            else:
                att = ["attack", "att", "atk"]
                cha = ["diplomacy", "charisma", "cha", "dipl"]
                intel = ["intelligence", "intellect", "int", "magic"]
                if spend not in att + cha + intel:
                    return await smart_embed(
                        ctx, _("Don't try to fool me! There is no such thing as {}.").format(spend)
                    )
                elif spend in att:
                    c.skill["pool"] -= amount
                    c.skill["att"] += amount
                    spend = "attack"
                elif spend in cha:
                    c.skill["pool"] -= amount
                    c.skill["cha"] += amount
                    spend = "charisma"
                elif spend in intel:
                    c.skill["pool"] -= amount
                    c.skill["int"] += amount
                    spend = "intelligence"
                await self.config.user(ctx.author).set(await c.to_json(self.config))
                await smart_embed(
                    ctx,
                    _("{author}, you permanently raised your {spend} value by {amount}.").format(
                        author=self.escape(ctx.author.display_name), spend=spend, amount=amount
                    ),
                )

    @commands.command(name="setinfo")
    @commands.bot_has_permissions(add_reactions=True, embed_links=True)
    async def set_show(self, ctx: commands.Context, *, set_name: str = None):
        """Show set bonuses for the specified set."""

        set_list = humanize_list(sorted([f"`{i}`" for i in self.SET_BONUSES.keys()], key=str.lower))
        if set_name is None:
            return await smart_embed(
                ctx, _("Use this command with one of the following set names: \n{sets}").format(sets=set_list),
            )

        title_cased_set_name = await self._title_case(set_name)
        sets = self.SET_BONUSES.get(title_cased_set_name)
        if sets is None:
            return await smart_embed(
                ctx,
                _("`{input}` is not a valid set.\n\nPlease use one of the following full set names: \n{sets}").format(
                    input=title_cased_set_name, sets=set_list
                ),
            )

        try:
            c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            return

        bonus_list = sorted(sets, key=itemgetter("parts"))
        msg_list = []
        for bonus in bonus_list:
            parts = bonus.get("parts", 0)
            attack = bonus.get("att", 0)
            charisma = bonus.get("cha", 0)
            intelligence = bonus.get("int", 0)
            dexterity = bonus.get("dex", 0)
            luck = bonus.get("luck", 0)

            attack = f"+{attack}" if attack > 0 else f"{attack}"
            charisma = f"+{charisma}" if charisma > 0 else f"{charisma}"
            intelligence = f"+{intelligence}" if intelligence > 0 else f"{intelligence}"
            dexterity = f"+{dexterity}" if dexterity > 0 else f"{dexterity}"
            luck = f"+{luck}" if luck > 0 else f"{luck}"

            statmult = round((bonus.get("statmult", 1) - 1) * 100)
            xpmult = round((bonus.get("xpmult", 1) - 1) * 100)
            cpmult = round((bonus.get("cpmult", 1) - 1) * 100)

            statmult = f"+{statmult}%" if statmult > 0 else f"{statmult}%"
            xpmult = f"+{xpmult}%" if xpmult > 0 else f"{xpmult}%"
            cpmult = f"+{cpmult}%" if cpmult > 0 else f"{cpmult}%"

            breakdown = _(
                "Attack:                [{attack}]\n"
                "Charisma:              [{charisma}]\n"
                "Intelligence:          [{intelligence}]\n"
                "Dexterity:             [{dexterity}]\n"
                "Luck:                  [{luck}]\n"
                "Stat Mulitplier:       [{statmult}]\n"
                "XP Multiplier:         [{xpmult}]\n"
                "Currency Multiplier:   [{cpmult}]\n\n"
            ).format(
                attack=attack,
                charisma=charisma,
                intelligence=intelligence,
                dexterity=dexterity,
                luck=luck,
                statmult=statmult,
                xpmult=xpmult,
                cpmult=cpmult,
            )
            stats_msg = _("{set_name} - {part_val} Part Bonus\n\n").format(
                set_name=title_cased_set_name, part_val=parts
            )
            stats_msg += breakdown
            stats_msg += "Multiple complete set bonuses stack."
            msg_list.append(box(stats_msg, lang="ini"))
        set_items = {key: value for key, value in self.TR_GEAR_SET.items() if value["set"] == title_cased_set_name}

        d = {}
        for k, v in set_items.items():
            if len(v["slot"]) > 1:
                d.update({v["slot"][0]: {k: v}})
                d.update({v["slot"][1]: {k: v}})
            else:
                d.update({v["slot"][0]: {k: v}})

        loadout_display = await self._build_loadout_display({"items": d}, loadout=False, rebirths=c.rebirths)
        set_msg = _("{set_name} Set Pieces\n\n").format(set_name=title_cased_set_name)
        set_msg += loadout_display
        msg_list.append(box(set_msg, lang="css"))
        backpack_contents = await c.get_backpack(set_name=title_cased_set_name, clean=True)
        if backpack_contents:
            msg_list.extend(backpack_contents)
        await BaseMenu(
            source=SimpleSource(msg_list), delete_message_after=True, clear_reactions_after=True, timeout=60,
        ).start(ctx=ctx)

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True)
    async def stats(self, ctx: commands.Context, *, user: discord.User = None):
        """This draws up a character sheet of you or an optionally specified member."""
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        if user is None:
            user = ctx.author
        if user.bot:
            return
        try:
            c = await Character.from_json(self.config, user, self._daily_bonus)
        except Exception:
            log.exception("Error with the new character sheet")
            return
        items = c.get_current_equipment(return_place_holder=True)
        msg = _("{}'s Character Sheet\n\n").format(self.escape(user.display_name))
        msg_len = len(msg)
        items_names = set()
        table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
        table.set_style(BeautifulTable.STYLE_RST)
        msgs = []
        total = len(items)
        table.columns.header = [
            "Name",
            "Slot",
            "ATT",
            "CHA",
            "INT",
            "DEX",
            "LUC",
            "LVL",
            "QTY",
            "DEG",
            "SET",
        ]
        async for index, item in AsyncIter(items, steps=100).enumerate(start=1):
            if len(str(table)) > 1500:
                msgs.append(box(msg + str(table) + f"\nPage {len(msgs) + 1}", lang="css"))
                table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
                table.set_style(BeautifulTable.STYLE_RST)
                table.columns.header = [
                    "Name",
                    "Slot",
                    "ATT",
                    "CHA",
                    "INT",
                    "DEX",
                    "LUC",
                    "LVL",
                    "QTY",
                    "DEG",
                    "SET",
                ]
            item_name = str(item)
            slots = len(item.slot)
            slot_name = item.slot[0] if slots == 1 else "two handed"
            if (item_name, slots, slot_name) in items_names:
                continue
            items_names.add((item_name, slots, slot_name))
            data = (
                item_name,
                slot_name,
                item.att * (1 if slots == 1 else 2),
                item.cha * (1 if slots == 1 else 2),
                item.int * (1 if slots == 1 else 2),
                item.dex * (1 if slots == 1 else 2),
                item.luck * (1 if slots == 1 else 2),
                f"[{r}]" if (r := equip_level(c, item)) is not None and r > c.lvl else f"{r}",
                item.owned,
                f"[{item.degrade}]"
                if item.rarity in ["legendary", "event", "ascended"] and item.degrade >= 0
                else "N/A",
                item.set or "N/A",
            )
            if data not in table.rows:
                table.rows.append(data)
            if index == total:
                table.set_style(BeautifulTable.STYLE_RST)
                msgs.append(box(msg + str(table) + f"\nPage {len(msgs) + 1}", lang="css"))
        await BaseMenu(
            source=SimpleSource([box(c, lang="css"), *msgs]),
            delete_message_after=True,
            clear_reactions_after=True,
            timeout=60,
        ).start(ctx=ctx)

    async def _build_loadout_display(self, userdata, loadout=True, rebirths: int = None, index: int = None):
        table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
        table.set_style(BeautifulTable.STYLE_RST)
        table.columns.header = [
            "Name",
            "Slot",
            "ATT",
            "CHA",
            "INT",
            "DEX",
            "LUC",
            "LVL",
            "SET",
        ]
        form_string = ""
        last_slot = ""
        att = 0
        cha = 0
        intel = 0
        dex = 0
        luck = 0

        def get_slot_index(slot):
            slot = slot[0]
            if slot not in ORDER:
                return float("inf")
            return ORDER.index(slot)

        data_sorted = sorted(userdata["items"].items(), key=get_slot_index)
        items_names = set()
        for (slot, data) in data_sorted:
            if slot == "backpack":
                continue
            if last_slot == "two handed":
                last_slot = slot
                continue
            if not data:
                continue
            item = Item.from_json(data)
            item_name = str(item)
            slots = len(item.slot)
            slot_name = item.slot[0] if slots == 1 else "two handed"
            if (item_name, slots, slot_name) in items_names:
                continue
            items_names.add((item_name, slots, slot_name))
            data = (
                item_name,
                slot_name,
                item.att * (1 if slots == 1 else 2),
                item.cha * (1 if slots == 1 else 2),
                item.int * (1 if slots == 1 else 2),
                item.dex * (1 if slots == 1 else 2),
                item.luck * (1 if slots == 1 else 2),
                equip_level(None, item, rebirths),
                item.set or "N/A",
            )
            if data not in table.rows:
                table.rows.append(data)
            att += item.att
            cha += item.cha
            intel += item.int
            dex += item.dex
            luck += item.luck

        table.set_style(BeautifulTable.STYLE_RST)
        form_string += str(table)

        form_string += _("\n\nTotal stats: ")
        form_string += f"({att} | {cha} | {intel} | {dex} | {luck})"
        if index is not None:
            form_string += f"\nPage {index}"
        return form_string

    @commands.command()
    async def unequip(self, ctx: commands.Context, *, item: EquipmentConverter):
        """This stashes a specified equipped item into your backpack.

        Use `[p]unequip name of item` or `[p]unequip slot`
        """
        if self.in_adventure(ctx):
            return await smart_embed(
                ctx, _("You tried to unequip your items, but the monster ahead of you looks mighty hungry..."),
            )
        if not await self.allow_in_dm(ctx):
            return await smart_embed(ctx, _("This command is not available in DM's on this bot."))
        async with self.get_lock(ctx.author):
            try:
                c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                return
            slots = [
                "head",
                "neck",
                "chest",
                "gloves",
                "belt",
                "legs",
                "boots",
                "left",
                "right",
                "ring",
                "charm",
            ]
            msg = ""
            if isinstance(item, list):
                for i in item:
                    await c.unequip_item(i)
                msg = _("{author} unequipped all their items and put them into their backpack.").format(
                    author=self.escape(ctx.author.display_name)
                )
            elif item in slots:
                current_item = getattr(c, item, None)
                if not current_item:
                    msg = _("{author}, you do not have an item equipped in the {item} slot.").format(
                        author=self.escape(ctx.author.display_name), item=item
                    )
                    return await ctx.send(box(msg, lang="css"))
                await c.unequip_item(current_item)
                msg = _("{author} removed the {current_item} and put it into their backpack.").format(
                    author=self.escape(ctx.author.display_name), current_item=current_item
                )
            else:
                for current_item in c.get_current_equipment():
                    if item.name.lower() in current_item.name.lower():
                        await c.unequip_item(current_item)
                        msg = _("{author} removed the {current_item} and put it into their backpack.").format(
                            author=self.escape(ctx.author.display_name), current_item=current_item
                        )
                        # We break if this works because unequip
                        # will autmatically remove multiple items
                        break
            if msg:
                await ctx.send(box(msg, lang="css"))
                await self.config.user(ctx.author).set(await c.to_json(self.config))
            else:
                await smart_embed(
                    ctx,
                    _("{author}, you do not have an item matching {item} equipped.").format(
                        author=self.escape(ctx.author.display_name), item=item
                    ),
                )

    @commands.command(name="adventurestats")
    @commands.bot_has_permissions(add_reactions=True, embed_links=True)
    @commands.is_owner()
    async def _adventurestats(self, ctx: commands.Context):
        """[Owner] Show all current adventures."""
        msg = "**Active Adventures**\n"
        embed_list = []

        if len(self._sessions) > 0:
            for server_id, adventure in self._sessions.items():
                msg += (
                    f"{self.bot.get_guild(server_id).name} - "
                    f"[{adventure.challenge}]({adventure.message.jump_url})\n"
                )
        else:
            msg += "None."
        for page in pagify(msg, delims=["\n"], page_length=1000):
            embed = discord.Embed(description=page)
            embed_list.append(embed)
        await BaseMenu(
            source=SimpleSource(embed_list), delete_message_after=True, clear_reactions_after=True, timeout=60,
        ).start(ctx=ctx)

    @commands.command(name="devcooldown")
    @commands.bot_has_permissions(add_reactions=True)
    @commands.is_owner()
    async def _devcooldown(self, ctx: commands.Context):
        """[Dev] Resets the after-adventure cooldown in this server."""
        if not await no_dev_prompt(ctx):
            return
        await self.config.guild(ctx.guild).cooldown.set(0)
        await ctx.tick()

    @commands.cooldown(rate=1, per=5, type=commands.BucketType.guild)
    @commands.command(name="adventure", aliases=["a"])
    @commands.bot_has_permissions(add_reactions=True)
    @commands.guild_only()
    async def _adventure(self, ctx: commands.Context, *, challenge=None):
        """This will send you on an adventure!

        You play by reacting with the offered emojis.
        """

        if ctx.guild.id in self._sessions and self._sessions[ctx.guild.id].finished is False:
            adventure_obj = self._sessions[ctx.guild.id]
            link = adventure_obj.message.jump_url

            challenge = adventure_obj.challenge if (adventure_obj.easy_mode or adventure_obj.exposed) else _("Unknown")
            return await smart_embed(
                ctx,
                _(
                    f"There's already another adventure going on in this server.\n"
                    f"Currently fighting: [{challenge}]({link})"
                ),
            )

        if not await has_funds(ctx.author, 250):
            currency_name = await bank.get_currency_name(ctx.guild,)
            extra = (
                _("\nRun `{ctx.clean_prefix}apayday` to get some gold.").format(ctx=ctx)
                if self._separate_economy
                else ""
            )
            return await smart_embed(
                ctx,
                _("You need {req} {name} to start an adventure.{extra}").format(
                    req=250, name=currency_name, extra=extra
                ),
            )
        guild_settings = await self.config.guild(ctx.guild).all()
        cooldown = guild_settings["cooldown"]

        cooldown_time = guild_settings["cooldown_timer_manual"]

        if cooldown + cooldown_time > time.time():
            cooldown_time = cooldown + cooldown_time - time.time()
            return await smart_embed(
                ctx,
                _("No heroes are ready to depart in an adventure, try again in {}.").format(
                    humanize_timedelta(seconds=int(cooldown_time)) if int(cooldown_time) >= 1 else _("1 second")
                ),
            )

        if challenge and not (self.is_dev(ctx.author) or await ctx.bot.is_owner(ctx.author)):
            # Only let the bot owner specify a specific challenge
            challenge = None

        adventure_msg = _("You feel adventurous, **{}**?").format(self.escape(ctx.author.display_name))
        try:
            reward, participants = await self._simple(ctx, adventure_msg, challenge)
            await self.config.guild(ctx.guild).cooldown.set(time.time())
            self._sessions[ctx.guild.id].finished = True
        except Exception as exc:
            self._sessions[ctx.guild.id].finished = True
            await self.config.guild(ctx.guild).cooldown.set(0)
            log.exception("Something went wrong controlling the game", exc_info=exc)
            while ctx.guild.id in self._sessions:
                del self._sessions[ctx.guild.id]
            return
        if not reward and not participants:
            await self.config.guild(ctx.guild).cooldown.set(0)
            while ctx.guild.id in self._sessions:
                del self._sessions[ctx.guild.id]
            return
        reward_copy = reward.copy()
        send_message = ""
        for (userid, rewards) in reward_copy.items():
            if rewards:
                user = ctx.guild.get_member(userid)  # bot.get_user breaks sometimes :ablobsweats:
                if user is None:
                    # sorry no rewards if you leave the server
                    continue
                msg = await self._add_rewards(ctx, user, rewards["xp"], rewards["cp"], rewards["special"])
                if msg:
                    send_message += f"{msg}\n"
                self._rewards[userid] = {}
        if send_message:
            for page in pagify(send_message):
                await smart_embed(ctx, page, success=True)
        if participants:
            for user in participants:  # reset activated abilities
                async with self.get_lock(user):
                    try:
                        c = await Character.from_json(self.config, user, self._daily_bonus)
                    except Exception as exc:
                        log.exception("Error with the new character sheet", exc_info=exc)
                        continue
                    if c.heroclass["name"] != "Ranger" and c.heroclass["ability"]:
                        c.heroclass["ability"] = False
                    if c.last_currency_check + 600 < time.time() or c.bal > c.last_known_currency:
                        c.last_known_currency = await bank.get_balance(user)
                        c.last_currency_check = time.time()
                    await self.config.user(user).set(await c.to_json(self.config))
        if ctx.message.id in self._reward_message:
            extramsg = self._reward_message.pop(ctx.message.id)
            if extramsg:
                for msg in pagify(extramsg, page_length=1900):
                    await smart_embed(ctx, msg, success=True)
        if ctx.message.id in self._loss_message:
            extramsg = self._loss_message.pop(ctx.message.id)
            if extramsg:
                for msg in pagify(extramsg, page_length=1900):
                    await smart_embed(ctx, msg, success=False)
        while ctx.guild.id in self._sessions:
            del self._sessions[ctx.guild.id]

    @_adventure.error
    async def _error_handler(self, ctx: commands.Context, error: Exception) -> None:
        error = getattr(error, "original", error)
        handled = False
        if not isinstance(
            error,
            (commands.CheckFailure, commands.UserInputError, commands.DisabledCommand, commands.CommandOnCooldown,),
        ):
            if ctx.guild.id in self._sessions:
                self._sessions[ctx.guild.id].finished = True
            while ctx.guild.id in self._sessions:
                del self._sessions[ctx.guild.id]
            handled = False
        elif isinstance(error, RuntimeError):
            handled = True

        await ctx.bot.on_command_error(ctx, error, unhandled_by_cog=not handled)

    async def cog_command_error(self, ctx: commands.Context, error: Exception) -> None:
        error = getattr(error, "original", error)
        handled = False
        if hasattr(ctx.command, "on_error"):
            return
        if isinstance(error, adventure.charsheet.ArgParserFailure):
            handled = True
            msg = _("`{command}` {message}").format(message=error.message, command=error.cmd,)
            await ctx.send(msg)

        await ctx.bot.on_command_error(ctx, error, unhandled_by_cog=not handled)

    async def get_challenge(self, ctx: commands.Context, monsters):
        try:
            c = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            choice = random.choice(list(monsters.keys()) * 3)
            return choice
        possible_monsters = []
        stat_range = self._adv_results.get_stat_range(ctx)
        async for (e, (m, stats)) in AsyncIter(monsters.items(), steps=100).enumerate(start=1):
            appropriate_range = max(stats["hp"], stats["dipl"]) <= (max(c.att, c.int, c.cha) * 5)
            if stat_range["max_stat"] > 0:
                main_stat = stats["hp"] if (stat_range["stat_type"] == "attack") else stats["dipl"]
                appropriate_range = (stat_range["min_stat"] * 0.75) <= main_stat <= (stat_range["max_stat"] * 1.2)
            if not appropriate_range:
                continue
            if not stats["boss"] and not stats["miniboss"]:
                count = 0
                break_at = random.randint(1, 15)
                while count < break_at:
                    count += 1
                    possible_monsters.append(m)
                    if count == break_at:
                        break
            else:
                possible_monsters.append(m)

        if len(possible_monsters) == 0:
            choice = random.choice(list(monsters.keys()) * 3)
        else:
            choice = random.choice(possible_monsters)
        return choice

    def _dynamic_monster_stats(self, ctx: commands.Context, choice: MutableMapping):
        stat_range = self._adv_results.get_stat_range(ctx)
        win_percentage = stat_range.get("win_percent", 0.5)
        choice["cdef"] = choice.get("cdef", 1.0)
        if win_percentage >= 0.90:
            monster_hp_min = int(choice["hp"] * 2)
            monster_hp_max = int(choice["hp"] * 3)
            monster_diplo_min = int(choice["dipl"] * 2)
            monster_diplo_max = int(choice["dipl"] * 3)
            percent_pdef = random.randrange(25, 30) / 100
            monster_pdef = choice["pdef"] * percent_pdef
            percent_mdef = random.randrange(25, 30) / 100
            monster_mdef = choice["mdef"] * percent_mdef
            percent_cdef = random.randrange(25, 30) / 100
            monster_cdef = choice["cdef"] * percent_cdef
        elif win_percentage >= 0.75:
            monster_hp_min = int(choice["hp"] * 1.5)
            monster_hp_max = int(choice["hp"] * 2)
            monster_diplo_min = int(choice["dipl"] * 1.5)
            monster_diplo_max = int(choice["dipl"] * 2)
            percent_pdef = random.randrange(15, 25) / 100
            monster_pdef = choice["pdef"] * percent_pdef
            percent_mdef = random.randrange(15, 25) / 100
            monster_mdef = choice["mdef"] * percent_mdef
            percent_cdef = random.randrange(15, 25) / 100
            monster_cdef = choice["cdef"] * percent_cdef
        elif win_percentage >= 0.50:
            monster_hp_min = int(choice["hp"])
            monster_hp_max = int(choice["hp"] * 1.5)
            monster_diplo_min = int(choice["dipl"])
            monster_diplo_max = int(choice["dipl"] * 1.5)
            percent_pdef = random.randrange(1, 15) / 100
            monster_pdef = choice["pdef"] * percent_pdef
            percent_mdef = random.randrange(1, 15) / 100
            monster_mdef = choice["mdef"] * percent_mdef
            percent_cdef = random.randrange(1, 15) / 100
            monster_cdef = choice["cdef"] * percent_cdef
        elif win_percentage >= 0.35:
            monster_hp_min = int(choice["hp"] * 0.9)
            monster_hp_max = int(choice["hp"])
            monster_diplo_min = int(choice["dipl"] * 0.9)
            monster_diplo_max = int(choice["dipl"])
            percent_pdef = random.randrange(1, 15) / 100
            monster_pdef = choice["pdef"] * percent_pdef * -1
            percent_mdef = random.randrange(1, 15) / 100
            monster_mdef = choice["mdef"] * percent_mdef * -1
            percent_cdef = random.randrange(1, 15) / 100
            monster_cdef = choice["cdef"] * percent_cdef * -1
        elif win_percentage >= 0.15:
            monster_hp_min = int(choice["hp"] * 0.8)
            monster_hp_max = int(choice["hp"] * 0.9)
            monster_diplo_min = int(choice["dipl"] * 0.8)
            monster_diplo_max = int(choice["dipl"] * 0.9)
            percent_pdef = random.randrange(15, 25) / 100
            monster_pdef = choice["pdef"] * percent_pdef * -1
            percent_mdef = random.randrange(15, 25) / 100
            monster_mdef = choice["mdef"] * percent_mdef * -1
            percent_cdef = random.randrange(15, 25) / 100
            monster_cdef = choice["cdef"] * percent_cdef * -1
        else:
            monster_hp_min = int(choice["hp"] * 0.6)
            monster_hp_max = int(choice["hp"] * 0.8)
            monster_diplo_min = int(choice["dipl"] * 0.6)
            monster_diplo_max = int(choice["dipl"] * 0.8)
            percent_pdef = random.randrange(25, 30) / 100
            monster_pdef = choice["pdef"] * percent_pdef * -1
            percent_mdef = random.randrange(25, 30) / 100
            monster_mdef = choice["mdef"] * percent_mdef * -1
            percent_cdef = random.randrange(25, 30) / 100
            monster_cdef = choice["cdef"] * percent_cdef * -1

        if monster_hp_min < monster_hp_max:
            new_hp = random.randrange(monster_hp_min, monster_hp_max)
        elif monster_hp_max < monster_hp_min:
            new_hp = random.randrange(monster_hp_max, monster_hp_min)
        else:
            new_hp = max(monster_hp_max, monster_hp_min)
        if monster_diplo_min < monster_diplo_max:
            new_diplo = random.randrange(monster_diplo_min, monster_diplo_max)
        elif monster_diplo_max < monster_diplo_min:
            new_diplo = random.randrange(monster_diplo_max, monster_diplo_min)
        else:
            new_diplo = max(monster_diplo_max, monster_diplo_min)

        new_pdef = choice["pdef"] + monster_pdef
        new_mdef = choice["mdef"] + monster_mdef
        new_cdef = choice["cdef"] + monster_cdef
        choice["hp"] = new_hp
        choice["dipl"] = new_diplo
        choice["pdef"] = new_pdef
        choice["mdef"] = new_mdef
        choice["cdef"] = new_cdef
        return choice

    async def update_monster_roster(self, user):

        try:
            c = await Character.from_json(self.config, user, self._daily_bonus)
            failed = False
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            failed = True

        transcended_chance = random.randint(0, 10)
        theme = await self.config.theme()
        extra_monsters = await self.config.themes.all()
        extra_monsters = extra_monsters.get(theme, {}).get("monsters", {})
        monster_stats = 1
        monsters = {**self.MONSTERS, **self.AS_MONSTERS, **extra_monsters}
        transcended = False
        if not failed:
            if transcended_chance == 5:
                monster_stats = 2 + max((c.rebirths // 10) - 1, 0)
                transcended = True
            elif c.rebirths >= 10:
                monster_stats = 1 + max((c.rebirths // 10) - 1, 0) / 2
        else:
            if transcended_chance == 5:
                monster_stats = 2
            else:
                monster_stats = 1
        return monsters, monster_stats, transcended

    async def _simple(self, ctx: commands.Context, adventure_msg, challenge: str = None, attribute: str = None):
        self.bot.dispatch("adventure", ctx)
        text = ""
        easy_mode = await self.config.easy_mode()
        monster_roster, monster_stats, transcended = await self.update_monster_roster(ctx.author)
        if not challenge or challenge not in monster_roster:
            challenge = await self.get_challenge(ctx, monster_roster)

        if attribute and attribute.lower() in self.ATTRIBS:
            attribute = attribute.lower()
        else:
            attribute = random.choice(list(self.ATTRIBS.keys()))
        if transcended:
            new_challenge = challenge.replace("Ascended", "Transcended")
        else:
            new_challenge = challenge

        if easy_mode:
            no_monster = False
            if monster_roster[challenge]["boss"]:
                timer = 60 * 5
                self.bot.dispatch("adventure_boss", ctx)
                text = box(_("\n [{} Alarm!]").format(new_challenge), lang="css")
            elif monster_roster[challenge]["miniboss"]:
                timer = 60 * 3
                self.bot.dispatch("adventure_miniboss", ctx)
            else:
                timer = 60 * 2
            if "Transcended" in new_challenge:
                self.bot.dispatch("adventure_transcended", ctx)
            elif "Ascended" in new_challenge:
                self.bot.dispatch("adventure_ascended", ctx)
            if attribute == "n immortal":
                self.bot.dispatch("adventure_immortal", ctx)
            elif attribute == " possessed":
                self.bot.dispatch("adventure_possessed", ctx)
        else:
            timer = 60 * 3
            no_monster = random.randint(0, 100) == 25
        self._sessions[ctx.guild.id] = GameSession(
            challenge=new_challenge if not no_monster else None,
            attribute=attribute if not no_monster else None,
            guild=ctx.guild,
            boss=monster_roster[challenge]["boss"] if not no_monster else None,
            miniboss=monster_roster[challenge]["miniboss"] if not no_monster else None,
            timer=timer,
            monster=monster_roster[challenge] if not no_monster else None,
            monsters=monster_roster if not no_monster else None,
            monster_stats=monster_stats if not no_monster else None,
            message=ctx.message,
            transcended=transcended if not no_monster else None,
            monster_modified_stats=self._dynamic_monster_stats(ctx, monster_roster[challenge]),
            easy_mode=easy_mode,
            no_monster=no_monster,
        )
        adventure_msg = (
            f"{adventure_msg}{text}\n{random.choice(self.LOCATIONS)}\n"
            f"**{self.escape(ctx.author.display_name)}**{random.choice(self.RAISINS)}"
        )
        await self._choice(ctx, adventure_msg)
        if ctx.guild.id not in self._sessions:
            return (None, None)
        rewards = self._rewards
        participants = self._sessions[ctx.guild.id].participants
        return (rewards, participants)

    async def _choice(self, ctx: commands.Context, adventure_msg):
        session = self._sessions[ctx.guild.id]
        easy_mode = session.easy_mode
        if easy_mode:
            dragon_text = _(
                "but **a{attr} {chall}** "
                "just landed in front of you glaring! \n\n"
                "What will you do and will other heroes be brave enough to help you?\n"
                "Heroes have 5 minutes to participate via reaction:"
                "\n\nReact with: {reactions}"
            ).format(
                attr=session.attribute,
                chall=session.challenge,
                reactions="**"
                + _("Fight")
                + "** - **"
                + _("Spell")
                + "** - **"
                + _("Talk")
                + "** - **"
                + _("Pray")
                + "** - **"
                + _("Run")
                + "**",
            )
            basilisk_text = _(
                "but **a{attr} {chall}** stepped out looking around. \n\n"
                "What will you do and will other heroes help your cause?\n"
                "Heroes have 3 minutes to participate via reaction:"
                "\n\nReact with: {reactions}"
            ).format(
                attr=session.attribute,
                chall=session.challenge,
                reactions="**"
                + _("Fight")
                + "** - **"
                + _("Spell")
                + "** - **"
                + _("Talk")
                + "** - **"
                + _("Pray")
                + "** - **"
                + _("Run")
                + "**",
            )
            normal_text = _(
                "but **a{attr} {chall}** "
                "is guarding it with{threat}. \n\n"
                "What will you do and will other heroes help your cause?\n"
                "Heroes have 2 minutes to participate via reaction:"
                "\n\nReact with: {reactions}"
            ).format(
                attr=session.attribute,
                chall=session.challenge,
                threat=random.choice(self.THREATEE),
                reactions="**"
                + _("Fight")
                + "** - **"
                + _("Spell")
                + "** - **"
                + _("Talk")
                + "** - **"
                + _("Pray")
                + "** - **"
                + _("Run")
                + "**",
            )

            embed = discord.Embed(colour=discord.Colour.blurple())
            use_embeds = await self.config.guild(ctx.guild).embed() and ctx.channel.permissions_for(ctx.me).embed_links
            if session.boss:
                if use_embeds:
                    embed.description = f"{adventure_msg}\n{dragon_text}"
                    embed.colour = discord.Colour.dark_red()
                    if session.monster["image"]:
                        embed.set_image(url=session.monster["image"])
                    adventure_msg = await ctx.send(embed=embed)
                else:
                    adventure_msg = await ctx.send(f"{adventure_msg}\n{dragon_text}")
                timeout = 60 * 5

            elif session.miniboss:
                if use_embeds:
                    embed.description = f"{adventure_msg}\n{basilisk_text}"
                    embed.colour = discord.Colour.dark_green()
                    if session.monster["image"]:
                        embed.set_image(url=session.monster["image"])
                    adventure_msg = await ctx.send(embed=embed)
                else:
                    adventure_msg = await ctx.send(f"{adventure_msg}\n{basilisk_text}")
                timeout = 60 * 3
            else:
                if use_embeds:
                    embed.description = f"{adventure_msg}\n{normal_text}"
                    if session.monster["image"]:
                        embed.set_thumbnail(url=session.monster["image"])
                    adventure_msg = await ctx.send(embed=embed)
                else:
                    adventure_msg = await ctx.send(f"{adventure_msg}\n{normal_text}")
                timeout = 60 * 2
        else:
            embed = discord.Embed(colour=discord.Colour.blurple())
            use_embeds = await self.config.guild(ctx.guild).embed() and ctx.channel.permissions_for(ctx.me).embed_links
            timeout = 60 * 3
            obscured_text = _(
                "What will you do and will other heroes help your cause?\n"
                "Heroes have {time} minutes to participate via reaction:"
                "\n\nReact with: {reactions}"
            ).format(
                reactions="**"
                + _("Fight")
                + "** - **"
                + _("Spell")
                + "** - **"
                + _("Talk")
                + "** - **"
                + _("Pray")
                + "** - **"
                + _("Run")
                + "**",
                time=timeout // 60,
            )
            if use_embeds:
                embed.description = f"{adventure_msg}\n{obscured_text}"
                adventure_msg = await ctx.send(embed=embed)
            else:
                adventure_msg = await ctx.send(f"{adventure_msg}\n{obscured_text}")

        session.message_id = adventure_msg.id
        session.message = adventure_msg
        start_adding_reactions(adventure_msg, self._adventure_actions)
        timer = await self._adv_countdown(ctx, session.timer, "Time remaining")
        self.tasks[adventure_msg.id] = timer
        try:
            await asyncio.wait_for(timer, timeout=timeout + 5)
        except Exception as exc:
            timer.cancel()
            log.exception("Error with the countdown timer", exc_info=exc)

        return await self._result(ctx, adventure_msg)

    async def local_perms(self, user):
        """Check the user is/isn't locally whitelisted/blacklisted.

        https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/release/3.0.0/redbot/core/global_checks.py
        """
        if await self.bot.is_owner(user):
            return True
        guild_settings = self.bot.db.guild(user.guild)
        local_blacklist = await guild_settings.blacklist()
        local_whitelist = await guild_settings.whitelist()

        _ids = [r.id for r in user.roles if not r.is_default()]
        _ids.append(user.id)
        if local_whitelist:
            return any(i in local_whitelist for i in _ids)

        return not any(i in local_blacklist for i in _ids)

    async def global_perms(self, user):
        """Check the user is/isn't globally whitelisted/blacklisted.

        https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/release/3.0.0/redbot/core/global_checks.py
        """
        if await self.bot.is_owner(user):
            return True
        whitelist = await self.bot.db.whitelist()
        if whitelist:
            return user.id in whitelist

        return user.id not in await self.bot.db.blacklist()

    async def has_perm(self, user):
        if hasattr(self.bot, "allowed_by_whitelist_blacklist"):
            return await self.bot.allowed_by_whitelist_blacklist(user)
        else:
            return await self.local_perms(user) or await self.global_perms(user)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """This will be a cog level reaction_add listener for game logic."""
        await self.bot.wait_until_ready()
        if user.bot:
            return
        emojis = ReactionPredicate.NUMBER_EMOJIS + self._adventure_actions
        if str(reaction.emoji) not in emojis:
            return
        if (guild := getattr(user, "guild", None)) is not None:
            if await self.bot.cog_disabled_in_guild(self, guild):
                return
        else:
            return
        if not await self.has_perm(user):
            return
        if guild.id in self._sessions:
            if reaction.message.id == self._sessions[guild.id].message_id:
                if guild.id in self._adventure_countdown:
                    (timer, done, sremain) = self._adventure_countdown[guild.id]
                    if sremain > 3:
                        await self._handle_adventure(reaction, user)
        if guild.id in self._current_traders:
            if reaction.message.id == self._current_traders[guild.id]["msg"] and not self.in_adventure(user=user):
                if user in self._current_traders[guild.id]["users"]:
                    return
                if guild.id in self._trader_countdown:
                    (timer, done, sremain) = self._trader_countdown[guild.id]
                    if sremain > 3:
                        await self._handle_cart(reaction, user)

    async def _handle_adventure(self, reaction, user):
        action = {v: k for k, v in self._adventure_controls.items()}[str(reaction.emoji)]
        session = self._sessions[user.guild.id]
        has_fund = await has_funds(user, 250)
        for x in ["fight", "magic", "talk", "pray", "run"]:
            if user in getattr(session, x, []):
                getattr(session, x).remove(user)

            if not has_fund or user in getattr(session, x, []):
                if reaction.message.channel.permissions_for(user.guild.me).manage_messages:
                    symbol = self._adventure_controls[x]
                    await reaction.message.remove_reaction(symbol, user)

        restricted = await self.config.restrict()
        if user not in getattr(session, action, []):
            if not has_fund:
                with contextlib.suppress(discord.HTTPException):
                    await user.send(
                        _(
                            "You contemplate going on an adventure with your friends, so "
                            "you go to your bank to get some money to prepare and they "
                            "tell you that your bank is empty!\n"
                            "You run home to look for some spare coins and you can't "
                            "even find a single one, so you tell your friends that you can't "
                            "join them as you already have plans... as you are too embarrassed "
                            "to tell them you are broke!"
                        )
                    )
                return
            if restricted:
                all_users = []
                for (guild_id, guild_session) in self._sessions.items():
                    guild_users_in_game = (
                        guild_session.fight
                        + guild_session.magic
                        + guild_session.talk
                        + guild_session.pray
                        + guild_session.run
                    )
                    all_users = all_users + guild_users_in_game

                if user in all_users:
                    user_id = f"{user.id}-{user.guild.id}"
                    # iterating through reactions here and removing them seems to be expensive
                    # so they can just keep their react on the adventures they can't join
                    if user_id not in self._react_messaged:
                        await reaction.message.channel.send(
                            _(
                                "**{c}**, you are already in an existing adventure. "
                                "Wait for it to finish before joining another one."
                            ).format(c=self.escape(user.display_name))
                        )
                        self._react_messaged.append(user_id)
                else:
                    getattr(session, action).append(user)
            else:
                getattr(session, action).append(user)

    async def _handle_cart(self, reaction, user):
        guild = user.guild
        emojis = ReactionPredicate.NUMBER_EMOJIS
        itemindex = emojis.index(str(reaction.emoji)) - 1
        items = self._current_traders[guild.id]["stock"][itemindex]
        self._current_traders[guild.id]["users"].append(user)
        spender = user
        channel = reaction.message.channel
        currency_name = await bank.get_currency_name(guild,)
        if currency_name.startswith("<"):
            currency_name = "credits"
        item_data = box(items["item"].formatted_name + " - " + humanize_number(items["price"]), lang="css")
        to_delete = await channel.send(
            _("{user}, how many {item} would you like to buy?").format(user=user.mention, item=item_data)
        )
        ctx = await self.bot.get_context(reaction.message)
        ctx.author = user
        pred = MessagePredicate.valid_int(ctx)
        try:
            msg = await self.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            self._current_traders[guild.id]["users"].remove(user)
            return
        if pred.result < 1:
            with contextlib.suppress(discord.HTTPException):
                await to_delete.delete()
                await msg.delete()
            await smart_embed(ctx, _("You're wasting my time."))
            self._current_traders[guild.id]["users"].remove(user)
            return
        if await bank.can_spend(spender, int(items["price"]) * pred.result):
            await bank.withdraw_credits(spender, int(items["price"]) * pred.result)
            async with self.get_lock(user):
                try:
                    c = await Character.from_json(self.config, user, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    return
                if c.is_backpack_full(is_dev=self.is_dev(user)):
                    with contextlib.suppress(discord.HTTPException):
                        await to_delete.delete()
                        await msg.delete()
                    await channel.send(
                        _("**{author}**, Your backpack is currently full.").format(
                            author=self.escape(user.display_name)
                        )
                    )
                    return
                item = items["item"]
                item.owned = pred.result
                await c.add_to_backpack(item, number=pred.result)
                await self.config.user(user).set(await c.to_json(self.config))
                with contextlib.suppress(discord.HTTPException):
                    await to_delete.delete()
                    await msg.delete()
                await channel.send(
                    box(
                        _(
                            "{author} bought {p_result} {item_name} for "
                            "{item_price} {currency_name} and put it into their backpack."
                        ).format(
                            author=self.escape(user.display_name),
                            p_result=pred.result,
                            item_name=item.formatted_name,
                            item_price=humanize_number(items["price"] * pred.result),
                            currency_name=currency_name,
                        ),
                        lang="css",
                    )
                )
                self._current_traders[guild.id]["users"].remove(user)
        else:
            with contextlib.suppress(discord.HTTPException):
                await to_delete.delete()
                await msg.delete()
            await channel.send(
                _("**{author}**, you do not have enough {currency_name}.").format(
                    author=self.escape(user.display_name), currency_name=currency_name
                )
            )
            self._current_traders[guild.id]["users"].remove(user)

    async def _result(self, ctx: commands.Context, message: discord.Message):
        if ctx.guild.id not in self._sessions:
            return
        calc_msg = await ctx.send(_("Calculating..."))
        attack = 0
        diplomacy = 0
        magic = 0
        fumblelist: list = []
        critlist: list = []
        failed = False
        lost = False
        with contextlib.suppress(discord.HTTPException):
            await message.clear_reactions()
        session = self._sessions[ctx.guild.id]
        challenge = session.challenge
        fight_list = list(set(session.fight))
        talk_list = list(set(session.talk))
        pray_list = list(set(session.pray))
        run_list = list(set(session.run))
        magic_list = list(set(session.magic))

        self._sessions[ctx.guild.id].fight = fight_list
        self._sessions[ctx.guild.id].talk = talk_list
        self._sessions[ctx.guild.id].pray = pray_list
        self._sessions[ctx.guild.id].run = run_list
        self._sessions[ctx.guild.id].magic = magic_list
        fight_name_list = []
        wizard_name_list = []
        talk_name_list = []
        pray_name_list = []
        repair_list = []
        for user in fight_list:
            fight_name_list.append(f"**{self.escape(user.display_name)}**")
        for user in magic_list:
            wizard_name_list.append(f"**{self.escape(user.display_name)}**")
        for user in talk_list:
            talk_name_list.append(f"**{self.escape(user.display_name)}**")
        for user in pray_list:
            pray_name_list.append(f"**{self.escape(user.display_name)}**")

        fighters_final_string = _(" and ").join(
            [", ".join(fight_name_list[:-1]), fight_name_list[-1]] if len(fight_name_list) > 2 else fight_name_list
        )
        wizards_final_string = _(" and ").join(
            [", ".join(wizard_name_list[:-1]), wizard_name_list[-1]] if len(wizard_name_list) > 2 else wizard_name_list
        )
        talkers_final_string = _(" and ").join(
            [", ".join(talk_name_list[:-1]), talk_name_list[-1]] if len(talk_name_list) > 2 else talk_name_list
        )
        preachermen_final_string = _(" and ").join(
            [", ".join(pray_name_list[:-1]), pray_name_list[-1]] if len(pray_name_list) > 2 else pray_name_list
        )
        if session.no_monster:
            avaliable_loot = [
                [0, 0, 1, 5, 2, 1],
                [0, 0, 0, 0, 1, 2],
                [0, 0, 1, 5, 1, 1],
                [0, 0, 1, 3, 0, 1],
                [0, 0, 1, 1, 1, 1],
                [0, 0, 0, 0, 0, 1],
                [0, 0, 3, 1, 0, 0],
                [0, 0, 1, 2, 1, 0],
                [0, 0, 0, 3, 2, 0],
            ]
            treasure = random.choice(avaliable_loot)

            session.participants = set(fight_list + magic_list + talk_list + pray_list + run_list + fumblelist)

            participants = {
                "fight": fight_list,
                "spell": magic_list,
                "talk": talk_list,
                "pray": pray_list,
                "run": run_list,
                "fumbles": fumblelist,
            }
            text = ""
            text += await self._reward(
                ctx,
                [u for u in fight_list + magic_list + pray_list + talk_list if u not in fumblelist],
                500 + int(500 * (0.25 * len(session.participants))),
                0,
                treasure,
            )
            parsed_users = []
            for (action_name, action) in participants.items():
                for user in action:
                    try:
                        c = await Character.from_json(self.config, user, self._daily_bonus)
                    except Exception as exc:
                        log.exception("Error with the new character sheet", exc_info=exc)
                        continue
                    current_val = c.adventures.get(action_name, 0)
                    c.adventures.update({action_name: current_val + 1})
                    if user not in parsed_users:
                        special_action = "loses" if lost or user in participants["run"] else "wins"
                        current_val = c.adventures.get(special_action, 0)
                        c.adventures.update({special_action: current_val + 1})
                        c.weekly_score.update({"adventures": c.weekly_score.get("adventures", 0) + 1})
                        parsed_users.append(user)
                    await self.config.user(user).set(await c.to_json(self.config))
            attack, diplomacy, magic, run_msg = await self.handle_run(
                ctx.guild.id, attack, diplomacy, magic, shame=True
            )
            if run_msg:
                run_msg = _("It's a shame for the following adventurers...\n{run_msg}\n").format(run_msg=run_msg)

            output = _(
                "All adventures prepared for an epic adventure, but they soon realise all this treasure was unprotected!\n{run_msg}{text}"
            ).format(text=text, run_msg=run_msg,)
            output = pagify(output, page_length=1900)
            await calc_msg.delete()
            for i in output:
                await smart_embed(ctx, i, success=True)
            return

        people = len(fight_list) + len(magic_list) + len(talk_list) + len(pray_list) + len(run_list)
        attack, diplomacy, magic, run_msg = await self.handle_run(ctx.guild.id, attack, diplomacy, magic)
        failed = await self.handle_basilisk(ctx)
        fumblelist, attack, diplomacy, magic, pray_msg = await self.handle_pray(
            ctx.guild.id, fumblelist, attack, diplomacy, magic
        )
        fumblelist, critlist, diplomacy, talk_msg = await self.handle_talk(
            ctx.guild.id, fumblelist, critlist, diplomacy
        )
        fumblelist, critlist, attack, magic, fight_msg = await self.handle_fight(
            ctx.guild.id, fumblelist, critlist, attack, magic
        )
        result_msg = run_msg + pray_msg + talk_msg + fight_msg
        challenge_attrib = session.attribute
        hp = int(session.monster_modified_stats["hp"] * self.ATTRIBS[challenge_attrib][0] * session.monster_stats)
        dipl = int(session.monster_modified_stats["dipl"] * self.ATTRIBS[challenge_attrib][1] * session.monster_stats)

        dmg_dealt = int(attack + magic)
        diplomacy = int(diplomacy)
        slain = dmg_dealt >= int(hp)
        persuaded = diplomacy >= int(dipl)
        damage_str = ""
        diplo_str = ""
        if dmg_dealt > 0:
            damage_str = _("The group {status} **{challenge}** **({result}/{int_hp})**.\n").format(
                status=_("hit the") if failed or not slain else _("killed the"),
                challenge=challenge,
                result=humanize_number(dmg_dealt),
                int_hp=humanize_number(hp),
            )
        if diplomacy > 0:
            diplo_str = _("The group {status} the **{challenge}** with {how} **({diplomacy}/{int_dipl})**.\n").format(
                status=_("tried to persuade") if not persuaded else _("distracted"),
                challenge=challenge,
                how=_("flattery") if failed or not persuaded else _("insults"),
                diplomacy=humanize_number(diplomacy),
                int_dipl=humanize_number(dipl),
            )
        if dmg_dealt >= diplomacy:
            self._adv_results.add_result(ctx, "attack", dmg_dealt, people, slain)
        else:
            self._adv_results.add_result(ctx, "talk", diplomacy, people, persuaded)
        result_msg = result_msg + "\n" + damage_str + diplo_str

        await calc_msg.delete()
        text = ""
        success = False
        treasure = [0, 0, 0, 0, 0, 0]
        if (slain or persuaded) and not failed:
            success = True
            roll = random.randint(1, 10)
            monster_amount = hp + dipl if slain and persuaded else hp if slain else dipl
            if session.transcended:
                if session.boss and "Trancended" in session.challenge:
                    avaliable_loot = [
                        [0, 0, 1, 5, 2, 1],
                        [0, 0, 0, 0, 1, 2],
                    ]
                else:
                    avaliable_loot = [
                        [0, 0, 1, 5, 1, 1],
                        [0, 0, 1, 3, 0, 1],
                        [0, 0, 1, 1, 1, 1],
                        [0, 0, 0, 0, 0, 1],
                    ]
                treasure = random.choice(avaliable_loot)
            elif session.boss:  # rewards 60:30:10 Epic Legendary Gear Set items
                avaliable_loot = [[0, 0, 3, 1, 0, 0], [0, 0, 1, 2, 1, 0], [0, 0, 0, 3, 2, 0]]
                treasure = random.choice(avaliable_loot)
            elif session.miniboss:  # rewards 50:50 rare:normal chest for killing something like the basilisk
                treasure = random.choice(
                    [[1, 1, 1, 0, 0, 0], [0, 0, 1, 1, 1, 0], [0, 0, 2, 2, 0, 0], [0, 1, 0, 2, 1, 0]]
                )
            elif monster_amount >= 700:  # super hard stuff
                if roll <= 7:
                    treasure = random.choice([[0, 0, 1, 0, 0, 0], [0, 1, 0, 0, 0, 0], [0, 0, 0, 1, 1, 0]])
            elif monster_amount >= 500:  # rewards 50:50 rare:epic chest for killing hard stuff.
                if roll <= 5:
                    treasure = random.choice([[0, 0, 1, 0, 0, 0], [0, 1, 0, 0, 0, 0], [0, 1, 1, 0, 0, 0]])
            elif monster_amount >= 300:  # rewards 50:50 rare:normal chest for killing hardish stuff
                if roll <= 2:
                    treasure = random.choice([[1, 0, 0, 0, 0, 0], [0, 1, 0, 0, 0, 0], [1, 1, 0, 0, 0, 0]])
            elif monster_amount >= 80:  # small chance of a normal chest on killing stuff that's not terribly weak
                if roll == 1:
                    treasure = [1, 0, 0, 0, 0, 0]

            if session.boss:  # always rewards at least an epic chest.
                # roll for legendary chest
                roll = random.randint(1, 100)
                if roll <= 10:
                    treasure[4] += 1
                elif roll <= 20:
                    treasure[3] += 1
                else:
                    treasure[2] += 1
            if len(critlist) != 0:
                treasure[0] += 1
            if treasure == [0, 0, 0, 0, 0, 0]:
                treasure = False
        if session.miniboss and failed:
            session.participants = set(fight_list + talk_list + pray_list + magic_list + fumblelist)
            currency_name = await bank.get_currency_name(ctx.guild,)
            for user in session.participants:
                try:
                    c = await Character.from_json(self.config, user, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    continue
                if c.bal > 0:
                    multiplier = 1 / 3 if c.rebirths >= 5 else 0.01
                    if c._dex < 0:
                        dex = min(1 / abs(c._dex), 1)
                    else:
                        dex = max(c._dex // 10, 1)
                    multiplier = multiplier / dex
                    loss = round(c.bal * multiplier)
                    if loss > c.bal:
                        loss = c.bal
                    if user not in [u for u, t in repair_list]:
                        repair_list.append([user, loss])
                        if c.bal > loss:
                            await bank.withdraw_credits(user, loss)
                        else:
                            await bank.set_balance(user, 0)
                c.adventures.update({"loses": c.adventures.get("loses", 0) + 1})
                c.weekly_score.update({"adventures": c.weekly_score.get("adventures", 0) + 1})
                await self.config.user(user).set(await c.to_json(self.config))
            loss_list = []
            result_msg += session.miniboss["defeat"]
            if len(repair_list) > 0:
                temp_repair = []
                for (user, loss) in repair_list:
                    if user not in temp_repair:
                        loss_list.append(
                            _("\n{user} used {loss} {currency_name}").format(
                                user=user.mention, loss=humanize_number(loss), currency_name=currency_name,
                            )
                        )
                        temp_repair.append(user)
                if loss_list:
                    self._loss_message[ctx.message.id] = humanize_list(loss_list).strip()
            return await smart_embed(ctx, result_msg)
        if session.miniboss and not slain and not persuaded:
            lost = True
            session.participants = set(fight_list + talk_list + pray_list + magic_list + fumblelist)
            currency_name = await bank.get_currency_name(ctx.guild,)
            for user in session.participants:
                try:
                    c = await Character.from_json(self.config, user, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    continue
                if c.bal > 0:
                    multiplier = 1 / 3 if c.rebirths >= 5 else 0.01
                    if c._dex < 0:
                        dex = min(1 / abs(c._dex), 1)
                    else:
                        dex = max(c._dex // 10, 1)
                    multiplier = multiplier / dex
                    loss = round(c.bal * multiplier)
                    if loss > c.bal:
                        loss = c.bal
                    if user not in [u for u, t in repair_list]:
                        repair_list.append([user, loss])
                        if c.bal > loss:
                            await bank.withdraw_credits(user, loss)
                        else:
                            await bank.set_balance(user, 0)
            loss_list = []
            if len(repair_list) > 0:
                temp_repair = []
                for (user, loss) in repair_list:
                    if user not in temp_repair:
                        loss_list.append(
                            _("\n{user} used {loss} {currency_name}").format(
                                user=user.mention, loss=humanize_number(loss), currency_name=currency_name,
                            )
                        )
                        temp_repair.append(user)
                if loss_list:
                    self._loss_message[ctx.message.id] = humanize_list(loss_list).strip()
            miniboss = session.challenge
            special = session.miniboss["special"]
            result_msg += _(
                "The **{miniboss}'s** " "{special} was countered, but he still managed to kill you."
            ).format(miniboss=miniboss, special=special)
        amount = 1 * session.monster_stats
        amount *= (hp + dipl) if slain and persuaded else hp if slain else dipl
        amount += int(amount * (0.25 * people))
        currency_name = await bank.get_currency_name(ctx.guild)
        if people == 1:
            if slain:
                group = fighters_final_string if len(fight_list) == 1 else wizards_final_string
                text = _("{b_group} has slain the {chall} in an epic battle!").format(
                    b_group=group, chall=session.challenge
                )
                text += await self._reward(
                    ctx,
                    [u for u in fight_list + magic_list + pray_list if u not in fumblelist],
                    amount,
                    round(((attack if group == fighters_final_string else magic) / hp) * 0.25),
                    treasure,
                )

            if persuaded:
                text = _("{b_talkers} almost died in battle, but confounded the {chall} in the last second.").format(
                    b_talkers=talkers_final_string, chall=session.challenge
                )
                text += await self._reward(
                    ctx,
                    [u for u in talk_list + pray_list if u not in fumblelist],
                    amount,
                    round((diplomacy / dipl) * 0.25),
                    treasure,
                )

            if not slain and not persuaded:
                lost = True
                currency_name = await bank.get_currency_name(ctx.guild,)
                users = set(fight_list + magic_list + talk_list + pray_list + fumblelist)
                for user in users:
                    try:
                        c = await Character.from_json(self.config, user, self._daily_bonus)
                    except Exception as exc:
                        log.exception("Error with the new character sheet", exc_info=exc)
                        continue
                    if c.bal > 0:
                        multiplier = 1 / 3 if c.rebirths >= 5 else 0.01
                        if c._dex < 0:
                            dex = min(1 / abs(c._dex), 1)
                        else:
                            dex = max(c._dex // 10, 1)
                        multiplier = multiplier / dex
                        loss = round(c.bal * multiplier)
                        if loss > c.bal:
                            loss = c.bal
                        if user not in [u for u, t in repair_list]:
                            repair_list.append([user, loss])
                            if c.bal > loss:
                                await bank.withdraw_credits(user, loss)
                            else:
                                await bank.set_balance(user, 0)
                loss_list = []
                if len(repair_list) > 0:
                    temp_repair = []
                    for (user, loss) in repair_list:
                        if user not in temp_repair:
                            loss_list.append(
                                _("\n{user} used {loss} {currency_name}").format(
                                    user=user.mention, loss=humanize_number(loss), currency_name=currency_name,
                                )
                            )
                            temp_repair.append(user)
                    if loss_list:
                        self._loss_message[ctx.message.id] = humanize_list(loss_list).strip()
                options = [
                    _("No amount of diplomacy or valiant fighting could save you."),
                    _("This challenge was too much for one hero."),
                    _("You tried your best, but the group couldn't succeed at their attempt."),
                ]
                text = random.choice(options)
        else:
            if run_list:
                users = run_list
                for user in users:
                    try:
                        c = await Character.from_json(self.config, user, self._daily_bonus)
                    except Exception as exc:
                        log.exception("Error with the new character sheet", exc_info=exc)
                        continue
                    if c.bal > 0:
                        multiplier = 1 / 3
                        if c._dex < 0:
                            dex = min(1 / abs(c._dex), 1)
                        else:
                            dex = max(c._dex // 10, 1)
                        multiplier = multiplier / dex
                        loss = round(c.bal * multiplier)
                        if loss > c.bal:
                            loss = c.bal
                        if user not in [u for u, t in repair_list]:
                            repair_list.append([user, loss])
                            if user not in [u for u, t in repair_list]:
                                if c.bal > loss:
                                    await bank.withdraw_credits(user, loss)
                                else:
                                    await bank.set_balance(user, 0)
            if slain and persuaded:
                if len(pray_list) > 0:
                    god = await self.config.god_name()
                    if await self.config.guild(ctx.guild).god_name():
                        god = await self.config.guild(ctx.guild).god_name()
                    if len(magic_list) > 0 and len(fight_list) > 0:
                        text = _(
                            "{b_fighters} slayed the {chall} "
                            "in battle, while {b_talkers} distracted with flattery, "
                            "{b_wizard} chanted magical incantations and "
                            "{b_preachers} aided in {god}'s name."
                        ).format(
                            b_fighters=fighters_final_string,
                            chall=session.challenge,
                            b_talkers=talkers_final_string,
                            b_wizard=wizards_final_string,
                            b_preachers=preachermen_final_string,
                            god=god,
                        )
                    else:
                        group = fighters_final_string if len(fight_list) > 0 else wizards_final_string
                        text = _(
                            "{b_group} slayed the {chall} "
                            "in battle, while {b_talkers} distracted with flattery and "
                            "{b_preachers} aided in {god}'s name."
                        ).format(
                            b_group=group,
                            chall=session.challenge,
                            b_talkers=talkers_final_string,
                            b_preachers=preachermen_final_string,
                            god=god,
                        )
                else:
                    if len(magic_list) > 0 and len(fight_list) > 0:
                        text = _(
                            "{b_fighters} slayed the {chall} "
                            "in battle, while {b_talkers} distracted with insults and "
                            "{b_wizard} chanted magical incantations."
                        ).format(
                            b_fighters=fighters_final_string,
                            chall=session.challenge,
                            b_talkers=talkers_final_string,
                            b_wizard=wizards_final_string,
                        )
                    else:
                        group = fighters_final_string if len(fight_list) > 0 else wizards_final_string
                        text = _(
                            "{b_group} slayed the {chall} in battle, while {b_talkers} distracted with insults."
                        ).format(b_group=group, chall=session.challenge, b_talkers=talkers_final_string)
                text += await self._reward(
                    ctx,
                    [u for u in fight_list + magic_list + pray_list + talk_list if u not in fumblelist],
                    amount,
                    round(((dmg_dealt / hp) + (diplomacy / dipl)) * 0.25),
                    treasure,
                )

            if not slain and persuaded:
                if len(pray_list) > 0:
                    text = _("{b_talkers} talked the {chall} down with {b_preachers}'s blessing.").format(
                        b_talkers=talkers_final_string, chall=session.challenge, b_preachers=preachermen_final_string,
                    )
                else:
                    text = _("{b_talkers} talked the {chall} down.").format(
                        b_talkers=talkers_final_string, chall=session.challenge
                    )
                text += await self._reward(
                    ctx,
                    [u for u in talk_list + pray_list if u not in fumblelist],
                    amount,
                    round((diplomacy / dipl) * 0.25),
                    treasure,
                )

            if slain and not persuaded:
                if len(pray_list) > 0:
                    if len(magic_list) > 0 and len(fight_list) > 0:
                        text = _(
                            "{b_fighters} killed the {chall} "
                            "in a most heroic battle with a little help from {b_preachers} and "
                            "{b_wizard} chanting magical incantations."
                        ).format(
                            b_fighters=fighters_final_string,
                            chall=session.challenge,
                            b_preachers=preachermen_final_string,
                            b_wizard=wizards_final_string,
                        )
                    else:
                        group = fighters_final_string if len(fight_list) > 0 else wizards_final_string
                        text = _(
                            "{b_group} killed the {chall} "
                            "in a most heroic battle with a little help from {b_preachers}."
                        ).format(b_group=group, chall=session.challenge, b_preachers=preachermen_final_string,)
                else:
                    if len(magic_list) > 0 and len(fight_list) > 0:
                        text = _(
                            "{b_fighters} killed the {chall} "
                            "in a most heroic battle with {b_wizard} chanting magical incantations."
                        ).format(
                            b_fighters=fighters_final_string, chall=session.challenge, b_wizard=wizards_final_string,
                        )
                    else:
                        group = fighters_final_string if len(fight_list) > 0 else wizards_final_string
                        text = _("{b_group} killed the {chall} in an epic fight.").format(
                            b_group=group, chall=session.challenge
                        )
                text += await self._reward(
                    ctx,
                    [u for u in fight_list + magic_list + pray_list if u not in fumblelist],
                    amount,
                    round((dmg_dealt / hp) * 0.25),
                    treasure,
                )

            if not slain and not persuaded:
                lost = True
                currency_name = await bank.get_currency_name(ctx.guild,)
                users = set(fight_list + magic_list + talk_list + pray_list + fumblelist)
                for user in users:
                    try:
                        c = await Character.from_json(self.config, user, self._daily_bonus)
                    except Exception as exc:
                        log.exception("Error with the new character sheet", exc_info=exc)
                        continue
                    if c.bal > 0:
                        multiplier = 1 / 3 if c.rebirths >= 5 else 0.01
                        if c._dex < 0:
                            dex = min(1 / abs(c._dex), 1)
                        else:
                            dex = max(c._dex // 10, 1)
                        multiplier = multiplier / dex
                        loss = round(c.bal * multiplier)
                        if loss > c.bal:
                            loss = c.bal
                        if user not in [u for u, t in repair_list]:
                            repair_list.append([user, loss])
                            if c.bal > loss:
                                await bank.withdraw_credits(user, loss)
                            else:
                                await bank.set_balance(user, 0)
                options = [
                    _("No amount of diplomacy or valiant fighting could save you."),
                    _("This challenge was too much for the group."),
                    _("You tried your best, but couldn't succeed."),
                ]
                text = random.choice(options)
        loss_list = []
        if len(repair_list) > 0:
            temp_repair = []
            for (user, loss) in repair_list:
                if user not in temp_repair:
                    loss_list.append(
                        _("\n{user} used {loss} {currency_name}").format(
                            user=user.mention, loss=humanize_number(loss), currency_name=currency_name,
                        )
                    )
                    temp_repair.append(user)
            if loss_list:
                self._loss_message[ctx.message.id] = humanize_list(loss_list).strip()
        output = f"{result_msg}\n{text}"
        output = pagify(output, page_length=1900)
        img_sent = session.monster["image"] if not session.easy_mode else None
        for i in output:
            await smart_embed(ctx, i, success=success, image=img_sent)
            if img_sent:
                img_sent = None
        await self._data_check(ctx)
        session.participants = set(fight_list + magic_list + talk_list + pray_list + run_list + fumblelist)

        participants = {
            "fight": fight_list,
            "spell": magic_list,
            "talk": talk_list,
            "pray": pray_list,
            "run": run_list,
            "fumbles": fumblelist,
        }

        parsed_users = []
        for (action_name, action) in participants.items():
            for user in action:
                try:
                    c = await Character.from_json(self.config, user, self._daily_bonus)
                except Exception as exc:
                    log.exception("Error with the new character sheet", exc_info=exc)
                    continue
                current_val = c.adventures.get(action_name, 0)
                c.adventures.update({action_name: current_val + 1})
                if user not in parsed_users:
                    special_action = "loses" if lost or user in participants["run"] else "wins"
                    current_val = c.adventures.get(special_action, 0)
                    c.adventures.update({special_action: current_val + 1})
                    c.weekly_score.update({"adventures": c.weekly_score.get("adventures", 0) + 1})
                    parsed_users.append(user)
                await self.config.user(user).set(await c.to_json(self.config))

    async def handle_run(self, guild_id, attack, diplomacy, magic, shame=False):
        runners = []
        msg = ""
        session = self._sessions[guild_id]
        if len(list(session.run)) != 0:
            for user in session.run:
                runners.append(f"**{self.escape(user.display_name)}**")
            msg += _("{} just ran away.\n").format(humanize_list(runners))
            if shame:
                msg += _(
                    "They are now regretting their pathetic display of courage as their friends enjoy all their new loot.\n"
                )
        return (attack, diplomacy, magic, msg)

    async def handle_fight(self, guild_id, fumblelist, critlist, attack, magic):
        session = self._sessions[guild_id]
        fight_list = list(set(session.fight))
        magic_list = list(set(session.magic))
        attack_list = list(set(fight_list + magic_list))
        pdef = max(session.monster_modified_stats["pdef"], 0.5)
        mdef = max(session.monster_modified_stats["mdef"], 0.5)

        fumble_count = 0
        # make sure we pass this check first
        failed_emoji = self.emojis.fumble
        if len(attack_list) >= 1:
            msg = ""
            report = _("Attack Party: \n\n")
        else:
            return (fumblelist, critlist, attack, magic, "")

        for user in fight_list:
            try:
                c = await Character.from_json(self.config, user, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                continue
            crit_mod = max(max(c.dex, c.luck // 2) + (c.total_att // 20), 0)  # Thanks GoaFan77
            mod = 0
            max_roll = 100 if c.rebirths >= 30 else 50 if c.rebirths >= 15 else 20
            if crit_mod != 0:
                mod = round(crit_mod / 10)
            if c.rebirths < 15 < mod:
                mod = 15
                max_roll = 20
            elif (mod + 1) > 45:
                mod = 45

            roll = max(random.randint((1 + mod), max_roll), 1)
            if c.heroclass.get("pet", {}).get("bonuses", {}).get("crit", False):
                pet_crit = c.heroclass.get("pet", {}).get("bonuses", {}).get("crit", 0)
                pet_crit = random.randint(pet_crit, 100)
                if pet_crit == 100:
                    roll = max_roll
                elif roll <= 25 and pet_crit >= 95:
                    roll = random.randint(max_roll - 5, max_roll)
                elif roll > 25 and pet_crit >= 95:
                    roll = random.randint(roll, max_roll)
            roll_perc = roll / max_roll
            att_value = c.total_att
            rebirths = c.rebirths * (3 if c.heroclass["name"] == "Berserker" else 1)
            if roll_perc < 0.10:
                if c.heroclass["name"] == "Berserker" and c.heroclass["ability"]:
                    bonus_roll = random.randint(5, 15)
                    bonus_multi = random.choice([0.2, 0.3, 0.4, 0.5])
                    bonus = max(bonus_roll, int((roll + att_value + rebirths) * bonus_multi))
                    attack += int((roll - bonus + att_value) / pdef)
                    report += (
                        f"**{self.escape(user.display_name)}**: "
                        f"{self.emojis.dice}({roll}) + "
                        f"{self.emojis.berserk}{humanize_number(bonus)} + "
                        f"{self.emojis.attack}{str(humanize_number(att_value))}\n"
                    )
                else:
                    msg += _("**{}** fumbled the attack.\n").format(self.escape(user.display_name))
                    fumblelist.append(user)
                    fumble_count += 1
            elif roll_perc > 0.95 or c.heroclass["name"] == "Berserker":
                crit_str = ""
                crit_bonus = 0
                base_bonus = random.randint(5, 10) + rebirths
                if roll_perc > 0.95:
                    msg += _("**{}** landed a critical hit.\n").format(self.escape(user.display_name))
                    critlist.append(user)
                    crit_bonus = (random.randint(5, 20)) + (rebirths * 2)
                    crit_str = f"{self.emojis.crit} {humanize_number(crit_bonus)}"
                if c.heroclass["name"] == "Berserker" and c.heroclass["ability"]:
                    base_bonus = (random.randint(1, 10) + 5) * (rebirths // 2)
                base_str = f"{self.emojis.crit}️ {humanize_number(base_bonus)}"
                attack += int((roll + base_bonus + crit_bonus + att_value) / pdef)
                bonus = base_str + crit_str
                report += (
                    f"**{self.escape(user.display_name)}**: "
                    f"{self.emojis.dice}({roll}) + "
                    f"{self.emojis.berserk}{bonus} + "
                    f"{self.emojis.attack}{str(humanize_number(att_value))}\n"
                )
            else:
                attack += int((roll + att_value) / pdef) + rebirths
                report += (
                    f"**{self.escape(user.display_name)}**: "
                    f"{self.emojis.dice}({roll}) + "
                    f"{self.emojis.attack}{str(humanize_number(att_value))}\n"
                )
            if session.insight[0] == 1 and user.id != session.insight[1].user.id:
                attack += int(session.insight[1].total_att * 0.2)
        for user in magic_list:
            try:
                c = await Character.from_json(self.config, user, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                continue
            crit_mod = max(max(c.dex, c.luck // 2) + (c.total_int // 20), 0)
            mod = 0
            max_roll = 100 if c.rebirths >= 30 else 50 if c.rebirths >= 15 else 20
            if crit_mod != 0:
                mod = round(crit_mod / 10)
            if c.rebirths < 15 < mod:
                mod = 15
                max_roll = 20
            elif (mod + 1) > 45:
                mod = 45
            roll = max(random.randint((1 + mod), max_roll), 1)
            if c.heroclass.get("pet", {}).get("bonuses", {}).get("crit", False):
                pet_crit = c.heroclass.get("pet", {}).get("bonuses", {}).get("crit", 0)
                pet_crit = random.randint(pet_crit, 100)
                if pet_crit == 100:
                    roll = max_roll
                elif roll <= 25 and pet_crit >= 95:
                    roll = random.randint(max_roll - 5, max_roll)
                elif roll > 25 and pet_crit >= 95:
                    roll = random.randint(roll, max_roll)
            roll_perc = roll / max_roll
            int_value = c.total_int
            rebirths = c.rebirths * (3 if c.heroclass["name"] == "Wizard" else 1)
            if roll_perc < 0.10:
                msg += _("{}**{}** almost set themselves on fire.\n").format(
                    failed_emoji, self.escape(user.display_name)
                )
                fumblelist.append(user)
                fumble_count += 1
                if c.heroclass["name"] == "Wizard" and c.heroclass["ability"]:
                    bonus_roll = random.randint(5, 15)
                    bonus_multi = random.choice([0.2, 0.3, 0.4, 0.5])
                    bonus = max(bonus_roll, int((roll + int_value + rebirths) * bonus_multi))
                    magic += int((roll - bonus + int_value) / mdef)
                    report += (
                        f"**{self.escape(user.display_name)}**: "
                        f"{self.emojis.dice}({roll}) + "
                        f"{self.emojis.magic_crit}{humanize_number(bonus)} + "
                        f"{self.emojis.magic}{str(humanize_number(int_value))}\n"
                    )
            elif roll_perc > 0.95 or (c.heroclass["name"] == "Wizard"):
                crit_str = ""
                crit_bonus = 0
                base_bonus = random.randint(5, 10) + rebirths
                base_str = f"{self.emojis.magic_crit}️ {humanize_number(base_bonus)}"
                if roll_perc > 0.95:
                    msg += _("**{}** had a surge of energy.\n").format(self.escape(user.display_name))
                    critlist.append(user)
                    crit_bonus = (random.randint(5, 20)) + (rebirths * 2)
                    crit_str = f"{self.emojis.crit} {humanize_number(crit_bonus)}"
                if c.heroclass["name"] == "Wizard" and c.heroclass["ability"]:
                    base_bonus = (random.randint(1, 10) + 5) * (rebirths // 2)
                    base_str = f"{self.emojis.magic_crit}️ {humanize_number(base_bonus)}"
                magic += int((roll + base_bonus + crit_bonus + int_value) / mdef)
                bonus = base_str + crit_str
                report += (
                    f"**{self.escape(user.display_name)}**: "
                    f"{self.emojis.dice}({roll}) + "
                    f"{bonus} + "
                    f"{self.emojis.magic}{humanize_number(int_value)}\n"
                )
            else:
                magic += int((roll + int_value) / mdef) + c.rebirths // 5
                report += (
                    f"**{self.escape(user.display_name)}**: "
                    f"{self.emojis.dice}({roll}) + "
                    f"{self.emojis.magic}{humanize_number(int_value)}\n"
                )
            if session.insight[0] == 1 and user.id != session.insight[1].user.id:
                attack += int(session.insight[1].total_int * 0.2)
        if fumble_count == len(attack_list):
            report += _("No one!")
        msg += report + "\n"
        for user in fumblelist:
            if user in session.fight:
                if session.insight[0] == 1 and user.id != session.insight[1].user.id:
                    attack -= int(session.insight[1].total_att * 0.2)
                session.fight.remove(user)
            elif user in session.magic:
                if session.insight[0] == 1 and user.id != session.insight[1].user.id:
                    attack -= int(session.insight[1].total_int * 0.2)
                session.magic.remove(user)
        return (fumblelist, critlist, attack, magic, msg)

    async def handle_pray(self, guild_id, fumblelist, attack, diplomacy, magic):
        session = self._sessions[guild_id]
        talk_list = list(set(session.talk))
        pray_list = list(set(session.pray))
        fight_list = list(set(session.fight))
        magic_list = list(set(session.magic))
        god = await self.config.god_name()
        guild_god_name = await self.config.guild(self.bot.get_guild(guild_id)).god_name()
        if guild_god_name:
            god = guild_god_name
        msg = ""
        failed_emoji = self.emojis.fumble
        for user in pray_list:
            try:
                c = await Character.from_json(self.config, user, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                continue
            rebirths = c.rebirths * (2 if c.heroclass["name"] == "Cleric" else 1)
            if c.heroclass["name"] == "Cleric":
                crit_mod = max(max(c.dex, c.luck // 2) + (c.total_int // 20), 0)
                mod = 0
                max_roll = 100 if c.rebirths >= 30 else 50 if c.rebirths >= 15 else 20
                if crit_mod != 0:
                    mod = round(crit_mod / 10)
                if c.rebirths < 15 < mod:
                    mod = 15
                    max_roll = 20
                elif (mod + 1) > 45:
                    mod = 45
                roll = max(random.randint((1 + mod), max_roll), 1)
                roll_perc = roll / max_roll
                if len(fight_list + talk_list + magic_list) == 0:
                    msg += _("**{}** blessed like a madman but nobody was there to receive it.\n").format(
                        self.escape(user.display_name)
                    )
                if roll_perc < 0.15:
                    pray_att_bonus = 0
                    pray_diplo_bonus = 0
                    pray_magic_bonus = 0
                    if fight_list:
                        pray_att_bonus = (5 * len(fight_list)) - ((5 * len(fight_list)) * max(rebirths * 0.01, 1.5))
                    if talk_list:
                        pray_diplo_bonus = (5 * len(talk_list)) - ((5 * len(talk_list)) * max(rebirths * 0.01, 1.5))
                    if magic_list:
                        pray_magic_bonus = (5 * len(magic_list)) - ((5 * len(magic_list)) * max(rebirths * 0.01, 1.5))
                    attack -= pray_att_bonus
                    diplomacy -= pray_diplo_bonus
                    magic -= pray_magic_bonus
                    fumblelist.append(user)
                    msg += _(
                        "**{user}'s** sermon offended the mighty {god}. {failed_emoji}"
                        "(-{len_f_list}{attack}/-{len_t_list}{talk}/-{len_m_list}{magic}) {roll_emoji}({roll})\n"
                    ).format(
                        user=self.escape(user.display_name),
                        god=god,
                        failed_emoji=failed_emoji,
                        attack=self.emojis.attack,
                        talk=self.emojis.talk,
                        magic=self.emojis.magic,
                        len_f_list=humanize_number(pray_att_bonus),
                        len_t_list=humanize_number(pray_diplo_bonus),
                        len_m_list=humanize_number(pray_magic_bonus),
                        roll_emoji=self.emojis.dice,
                        roll=roll,
                    )
                else:
                    mod = roll // 3 if not c.heroclass["ability"] else roll
                    pray_att_bonus = 0
                    pray_diplo_bonus = 0
                    pray_magic_bonus = 0

                    if fight_list:
                        pray_att_bonus = int(
                            (mod * len(fight_list)) + ((mod * len(fight_list)) * max(rebirths * 0.05, 1.5))
                        )
                    if talk_list:
                        pray_diplo_bonus = int(
                            (mod * len(talk_list)) + ((mod * len(talk_list)) * max(rebirths * 0.05, 1.5))
                        )
                    if magic_list:
                        pray_magic_bonus = int(
                            (mod * len(magic_list)) + ((mod * len(magic_list)) * max(rebirths * 0.05, 1.5))
                        )
                    attack += pray_att_bonus
                    magic += pray_magic_bonus
                    diplomacy += pray_diplo_bonus
                    if roll == 50:
                        roll_msg = _(
                            "**{user}** turned into an avatar of mighty {god}. "
                            "(+{len_f_list}{attack}/+{len_t_list}{talk}/+{len_m_list}{magic}) {roll_emoji}({roll})\n"
                        )
                    else:
                        roll_msg = _(
                            "**{user}** blessed you all in {god}'s name. "
                            "(+{len_f_list}{attack}/+{len_t_list}{talk}/+{len_m_list}{magic}) {roll_emoji}({roll})\n"
                        )
                    msg += roll_msg.format(
                        user=self.escape(user.display_name),
                        god=god,
                        attack=self.emojis.attack,
                        talk=self.emojis.talk,
                        magic=self.emojis.magic,
                        len_f_list=humanize_number(pray_att_bonus),
                        len_t_list=humanize_number(pray_diplo_bonus),
                        len_m_list=humanize_number(pray_magic_bonus),
                        roll_emoji=self.emojis.dice,
                        roll=roll,
                    )
            else:
                roll = random.randint(1, 10)
                if len(fight_list + talk_list + magic_list) == 0:
                    msg += _("**{}** prayed like a madman but nobody else helped them.\n").format(
                        self.escape(user.display_name)
                    )

                elif roll == 5:
                    attack_buff = 0
                    talk_buff = 0
                    magic_buff = 0
                    if fight_list:
                        attack_buff = 10 * (len(fight_list) + rebirths // 15)
                    if talk_list:
                        talk_buff = 10 * (len(talk_list) + rebirths // 15)
                    if magic_list:
                        magic_buff = 10 * (len(magic_list) + rebirths // 15)

                    attack += attack_buff
                    magic += magic_buff
                    diplomacy += talk_buff
                    msg += _(
                        "**{user}'s** prayer called upon the mighty {god} to help you. "
                        "(+{len_f_list}{attack}/+{len_t_list}{talk}/+{len_m_list}{magic}) {roll_emoji}({roll})\n"
                    ).format(
                        user=self.escape(user.display_name),
                        god=god,
                        attack=self.emojis.attack,
                        talk=self.emojis.talk,
                        magic=self.emojis.magic,
                        len_f_list=humanize_number(attack_buff),
                        len_t_list=humanize_number(talk_buff),
                        len_m_list=humanize_number(magic_buff),
                        roll_emoji=self.emojis.dice,
                        roll=roll,
                    )
                else:
                    fumblelist.append(user)
                    msg += _("{}**{}'s** prayers went unanswered.\n").format(
                        failed_emoji, self.escape(user.display_name)
                    )
        for user in fumblelist:
            if user in pray_list:
                pray_list.remove(user)
        return (fumblelist, attack, diplomacy, magic, msg)

    async def handle_talk(self, guild_id, fumblelist, critlist, diplomacy):
        session = self._sessions[guild_id]
        cdef = max(session.monster_modified_stats["cdef"], 0.5)
        talk_list = list(set(session.talk))
        if len(talk_list) >= 1:
            report = _("Talking Party: \n\n")
            msg = ""
            fumble_count = 0
        else:
            return (fumblelist, critlist, diplomacy, "")
        failed_emoji = self.emojis.fumble
        for user in talk_list:
            try:
                c = await Character.from_json(self.config, user, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                continue
            crit_mod = max(max(c.dex, c.luck // 2) + (c.total_int // 50) + (c.total_cha // 20), 0)
            mod = 0
            max_roll = 100 if c.rebirths >= 30 else 50 if c.rebirths >= 15 else 20
            if crit_mod != 0:
                mod = round(crit_mod / 10)
            if c.rebirths < 15 < mod:
                mod = 15
            elif (mod + 1) > 45:
                mod = 45
            roll = max(random.randint((1 + mod), max_roll), 1)
            dipl_value = c.total_cha
            rebirths = c.rebirths * (3 if c.heroclass["name"] == "Bard" else 1)
            roll_perc = roll / max_roll
            if roll_perc < 0.10:
                if c.heroclass["name"] == "Bard" and c.heroclass["ability"]:
                    bonus = random.randint(5, 15)
                    diplomacy += int((roll - bonus + dipl_value + rebirths) / cdef)
                    report += (
                        f"**{self.escape(user.display_name)}** "
                        f"🎲({roll}) +💥{bonus} +🗨{humanize_number(dipl_value)} | "
                    )
                else:
                    msg += _("{}**{}** accidentally offended the enemy.\n").format(
                        failed_emoji, self.escape(user.display_name)
                    )
                    fumblelist.append(user)
                    fumble_count += 1
            elif roll_perc > 0.95 or c.heroclass["name"] == "Bard":
                crit_str = ""
                crit_bonus = 0
                base_bonus = random.randint(5, 10) + rebirths
                if roll_perc > 0.95:
                    msg += _("**{}** made a compelling argument.\n").format(self.escape(user.display_name))
                    critlist.append(user)
                    crit_bonus = (random.randint(5, 20)) + (rebirths * 2)
                    crit_str = f"{self.emojis.crit} {crit_bonus}"

                if c.heroclass["name"] == "Bard" and c.heroclass["ability"]:
                    base_bonus = (random.randint(1, 10) + 5) * (rebirths // 2)
                base_str = f"🎵 {humanize_number(base_bonus)}"
                diplomacy += int((roll + base_bonus + crit_bonus + dipl_value) / cdef)
                bonus = base_str + crit_str
                report += (
                    f"**{self.escape(user.display_name)}** "
                    f"{self.emojis.dice}({roll}) + "
                    f"{bonus} + "
                    f"{self.emojis.talk}{humanize_number(dipl_value)}\n"
                )
            else:
                diplomacy += int((roll + dipl_value + c.rebirths // 5) / cdef)
                report += (
                    f"**{self.escape(user.display_name)}** "
                    f"{self.emojis.dice}({roll}) + "
                    f"{self.emojis.talk}{humanize_number(dipl_value)}\n"
                )
            if session.insight[0] == 1 and user.id != session.insight[1].user.id:
                diplomacy += int(session.insight[1].total_cha * 0.2)
        if fumble_count == len(talk_list):
            report += _("No one!")
        msg = msg + report + "\n"
        for user in fumblelist:
            if user in talk_list:
                if session.insight[0] == 1 and user.id != session.insight[1].user.id:
                    diplomacy -= int(session.insight[1].total_cha * 0.2)
                session.talk.remove(user)
        return (fumblelist, critlist, diplomacy, msg)

    async def handle_basilisk(self, ctx: commands.Context):
        session = self._sessions[ctx.guild.id]
        fight_list = list(set(session.fight))
        talk_list = list(set(session.talk))
        pray_list = list(set(session.pray))
        magic_list = list(set(session.magic))
        participants = list(set(fight_list + talk_list + pray_list + magic_list))
        if session.miniboss:
            failed = True
            req_item, slot = session.miniboss["requirements"]
            if req_item == "members":
                if len(participants) > int(slot):
                    failed = False
            elif req_item == "emoji" and session.reacted:
                failed = False
            else:
                for user in participants:  # check if any fighter has an equipped mirror shield to give them a chance.
                    try:
                        c = await Character.from_json(self.config, user, self._daily_bonus)
                    except Exception as exc:
                        log.exception("Error with the new character sheet", exc_info=exc)
                        continue
                    if any(x in c.sets for x in ["The Supreme One", "Ainz Ooal Gown"]):
                        failed = False
                        break
                    with contextlib.suppress(KeyError):
                        current_equipment = c.get_current_equipment()
                        for item in current_equipment:
                            item_name = str(item)
                            if item.rarity != "forged" and (req_item in item_name or "shiny" in item_name.lower()):
                                failed = False
                                break
        else:
            failed = False
        return failed

    async def _add_rewards(self, ctx: commands.Context, user, exp, cp, special):
        lock = self.get_lock(user)
        if not lock.locked():
            await lock.acquire()
        try:
            c = await Character.from_json(self.config, user, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            lock.release()
            return
        else:
            rebirth_text = ""
            c.exp += exp
            member = ctx.guild.get_member(user.id)
            cp = max(cp, 0)
            if cp > 0:
                try:
                    await bank.deposit_credits(member, cp)
                except BalanceTooHigh as e:
                    await bank.set_balance(member, e.max_balance)
            extra = ""
            rebirthextra = ""
            lvl_start = c.lvl
            lvl_end = int(max(c.exp, 0) ** (1 / 3.5))
            lvl_end = lvl_end if lvl_end < c.maxlevel else c.maxlevel
            levelup_emoji = self.emojis.level_up
            rebirth_emoji = self.emojis.rebirth
            if lvl_end >= c.maxlevel:
                rebirthextra = _("{} You can now rebirth {}").format(rebirth_emoji, user.mention)
            if lvl_start < lvl_end:
                # recalculate free skillpoint pool based on new level and already spent points.
                c.lvl = lvl_end
                assigned_stats = c.skill["att"] + c.skill["cha"] + c.skill["int"]
                starting_points = await calculate_sp(lvl_start, c) + assigned_stats
                ending_points = await calculate_sp(lvl_end, c) + assigned_stats

                if c.skill["pool"] < 0:
                    c.skill["pool"] = 0
                c.skill["pool"] += ending_points - starting_points
                if c.skill["pool"] > 0:
                    extra = _(" You have **{}** skill points available.").format(c.skill["pool"])
                rebirth_text = _("{} {} is now level **{}**!{}\n{}").format(
                    levelup_emoji, user.mention, lvl_end, extra, rebirthextra
                )
            if c.rebirths > 1:
                roll = random.randint(1, 100)
                if lvl_end == c.maxlevel:
                    roll += random.randint(50, 100)
                if special is False:
                    special = [0, 0, 0, 0, 0, 0]
                    if c.rebirths > 1 and roll < 50:
                        special[0] += 1
                    if c.rebirths > 5 and roll < 30:
                        special[1] += 1
                    if c.rebirths > 10 > roll:
                        special[2] += 1
                    if c.rebirths > 15 and roll < 5:
                        special[3] += 1
                    if special == [0, 0, 0, 0, 0, 0]:
                        special = False
                else:
                    if c.rebirths > 1 and roll < 50:
                        special[0] += 1
                    if c.rebirths > 5 and roll < 30:
                        special[1] += 1
                    if c.rebirths > 10 > roll:
                        special[2] += 1
                    if c.rebirths > 15 and roll < 5:
                        special[3] += 1
                    if special == [0, 0, 0, 0, 0, 0]:
                        special = False
            if special is not False:
                c.treasure = [sum(x) for x in zip(c.treasure, special)]
            await self.config.user(user).set(await c.to_json(self.config))
            return rebirth_text
        finally:
            lock = self.get_lock(user)
            with contextlib.suppress(Exception):
                lock.release()

    async def _adv_countdown(self, ctx: commands.Context, seconds, title) -> asyncio.Task:
        await self._data_check(ctx)

        async def adv_countdown():
            secondint = int(seconds)
            adv_end = await self._get_epoch(secondint)
            timer, done, sremain = await self._remaining(adv_end)
            message_adv = await ctx.send(f"⏳ [{title}] {timer}s")
            deleted = False
            while not done:
                timer, done, sremain = await self._remaining(adv_end)
                self._adventure_countdown[ctx.guild.id] = (timer, done, sremain)
                if done:
                    if not deleted:
                        await message_adv.delete()
                    break
                elif not deleted and int(sremain) % 5 == 0:
                    try:
                        await message_adv.edit(content=f"⏳ [{title}] {timer}s")
                    except discord.NotFound:
                        deleted = True
                await asyncio.sleep(1)
            log.debug("Timer countdown done.")

        return ctx.bot.loop.create_task(adv_countdown())

    async def _cart_countdown(self, ctx: commands.Context, seconds, title, room=None) -> asyncio.Task:
        room = room or ctx
        await self._data_check(ctx)

        async def cart_countdown():
            secondint = int(seconds)
            cart_end = await self._get_epoch(secondint)
            timer, done, sremain = await self._remaining(cart_end)
            message_cart = await room.send(f"⏳ [{title}] {timer}s")
            deleted = False
            while not done:
                timer, done, sremain = await self._remaining(cart_end)
                self._trader_countdown[ctx.guild.id] = (timer, done, sremain)
                if done:
                    if not deleted:
                        await message_cart.delete()
                    break
                if not deleted and int(sremain) % 5 == 0:
                    try:
                        await message_cart.edit(content=f"⏳ [{title}] {timer}s")
                    except discord.NotFound:
                        deleted = True
                await asyncio.sleep(1)

        return ctx.bot.loop.create_task(cart_countdown())

    @staticmethod
    async def _clear_react(msg):
        with contextlib.suppress(discord.HTTPException):
            await msg.clear_reactions()

    async def _data_check(self, ctx: commands.Context):
        try:
            self._adventure_countdown[ctx.guild.id]
        except KeyError:
            self._adventure_countdown[ctx.guild.id] = 0
        try:
            self._rewards[ctx.author.id]
        except KeyError:
            self._rewards[ctx.author.id] = {}
        try:
            self._trader_countdown[ctx.guild.id]
        except KeyError:
            self._trader_countdown[ctx.guild.id] = 0

    @staticmethod
    async def _get_epoch(seconds: int):
        epoch = time.time()
        epoch += seconds
        return epoch

    @staticmethod
    async def _title_case(phrase: str):
        exceptions = ["a", "and", "in", "of", "or", "the"]
        lowercase_words = re.split(" ", phrase.lower())
        final_words = [lowercase_words[0].capitalize()]
        final_words += [word if word in exceptions else word.capitalize() for word in lowercase_words[1:]]
        return " ".join(final_words)

    @commands.Cog.listener()
    async def on_message_without_command(self, message):
        await self._ready_event.wait()
        if message.guild is not None:
            if await self.bot.cog_disabled_in_guild(self, message.guild):
                return
        else:
            return
        channels = await self.config.guild(message.guild).cart_channels()
        if not channels:
            return
        if message.channel.id not in channels:
            return
        if not message.author.bot:
            roll = random.randint(1, 20)
            if roll == 20:
                try:
                    self._last_trade[message.guild.id]
                except KeyError:
                    self._last_trade[message.guild.id] = 0
                ctx = await self.bot.get_context(message)
                await asyncio.sleep(5)
                await self._trader(ctx)

    async def _roll_chest(self, chest_type: str, c: Character):
        # set rarity to chest by default
        rarity = chest_type
        if chest_type == "pet":
            rarity = "normal"
        INITIAL_MAX_ROLL = 400
        # max luck for best chest odds
        MAX_CHEST_LUCK = 200
        # lower gives you better chances for better items
        max_roll = INITIAL_MAX_ROLL - round(c.luck) - (c.rebirths // 2)
        top_range = max(max_roll, INITIAL_MAX_ROLL - MAX_CHEST_LUCK)
        roll = max(random.randint(1, top_range), 1)
        if chest_type == "normal":
            if roll <= INITIAL_MAX_ROLL * 0.05:  # 5% to roll rare
                rarity = "rare"
            else:
                pass  # 95% to roll common
        elif chest_type == "rare":
            if roll <= INITIAL_MAX_ROLL * 0.05:  # 5% to roll epic
                rarity = "epic"
            elif roll <= INITIAL_MAX_ROLL * 0.95:  # 90% to roll rare
                pass
            else:
                rarity = "normal"  # 5% to roll normal
        elif chest_type == "epic":
            if roll <= INITIAL_MAX_ROLL * 0.05:  # 5% to roll legendary
                rarity = "legendary"
            elif roll <= INITIAL_MAX_ROLL * 0.90:  # 85% to roll epic
                pass
            else:  # 10% to roll rare
                rarity = "rare"
        elif chest_type == "legendary":
            if roll <= INITIAL_MAX_ROLL * 0.75:  # 75% to roll legendary
                pass
            elif roll <= INITIAL_MAX_ROLL * 0.95:  # 20% to roll epic
                rarity = "epic"
            else:
                rarity = "rare"  # 5% to roll rare
        elif chest_type == "ascended":
            if roll <= INITIAL_MAX_ROLL * 0.55:  # 55% to roll set
                rarity = "ascended"
            else:
                rarity = "legendary"  # 45% to roll legendary
        elif chest_type == "pet":
            if roll <= INITIAL_MAX_ROLL * 0.05:  # 5% to roll legendary
                rarity = "legendary"
            elif roll <= INITIAL_MAX_ROLL * 0.15:  # 10% to roll epic
                rarity = "epic"
            elif roll <= INITIAL_MAX_ROLL * 0.57:  # 42% to roll rare
                rarity = "rare"
            else:
                rarity = "normal"  # 47% to roll common
        elif chest_type == "set":
            if roll <= INITIAL_MAX_ROLL * 0.55:  # 55% to roll set
                rarity = "set"
            elif roll <= INITIAL_MAX_ROLL * 0.87:
                rarity = "ascended"  # 45% to roll legendary
            else:
                rarity = "legendary"  # 45% to roll legendary

        return await self._genitem(rarity)

    async def _open_chests(
        self, ctx: commands.Context, chest_type: str, amount: int, character: Character,
    ):
        items = {}
        async for _loop_counter in AsyncIter(range(0, max(amount, 0)), steps=100):
            item = await self._roll_chest(chest_type, character)
            item_name = str(item)
            if item_name in items:
                items[item_name].owned += 1
            else:
                items[item_name] = item
            await character.add_to_backpack(item)
        await self.config.user(ctx.author).set(await character.to_json(self.config))
        return items

    async def _open_chest(self, ctx: commands.Context, user, chest_type, character):
        if hasattr(user, "display_name"):
            chest_msg = _("{} is opening a treasure chest. What riches lay inside?").format(
                self.escape(user.display_name)
            )
        else:
            chest_msg = _("{user}'s {f} is foraging for treasure. What will it find?").format(
                user=self.escape(ctx.author.display_name), f=(user[:1] + user[1:])
            )
        open_msg = await ctx.send(box(chest_msg, lang="css"))
        await asyncio.sleep(2)
        item = await self._roll_chest(chest_type, character)
        if chest_type == "pet" and not item:
            await open_msg.edit(
                content=box(
                    _("{c_msg}\nThe {user} found nothing of value.").format(
                        c_msg=chest_msg, user=(user[:1] + user[1:])
                    ),
                    lang="css",
                )
            )
            return None
        slot = item.slot[0]
        old_item = getattr(character, item.slot[0], None)
        old_stats = ""

        if old_item:
            old_slot = old_item.slot[0]
            if len(old_item.slot) > 1:
                old_slot = _("two handed")
                att = old_item.att * 2
                cha = old_item.cha * 2
                intel = old_item.int * 2
                luck = old_item.luck * 2
                dex = old_item.dex * 2
            else:
                att = old_item.att
                cha = old_item.cha
                intel = old_item.int
                luck = old_item.luck
                dex = old_item.dex

            old_stats = (
                _("You currently have {item} [{slot}] equipped | Lvl req {lv} equipped.").format(
                    item=old_item, slot=old_slot, lv=equip_level(character, old_item)
                )
                + f" (ATT: {str(att)}, "
                f"CHA: {str(cha)}, "
                f"INT: {str(intel)}, "
                f"DEX: {str(dex)}, "
                f"LUCK: {str(luck)}) "
            )
        if len(item.slot) > 1:
            slot = _("two handed")
            att = item.att * 2
            cha = item.cha * 2
            intel = item.int * 2
            luck = item.luck * 2
            dex = item.dex * 2
        else:
            att = item.att
            cha = item.cha
            intel = item.int
            luck = item.luck
            dex = item.dex
        if hasattr(user, "display_name"):
            chest_msg2 = (
                _("{user} found {item} [{slot}] | Lvl req {lv}.").format(
                    user=self.escape(user.display_name), item=str(item), slot=slot, lv=equip_level(character, item),
                )
                + f" (ATT: {str(att)}, "
                f"CHA: {str(cha)}, "
                f"INT: {str(intel)}, "
                f"DEX: {str(dex)}, "
                f"LUCK: {str(luck)}) "
            )

            await open_msg.edit(
                content=box(
                    _(
                        "{c_msg}\n\n{c_msg_2}\n\nDo you want to equip "
                        "this item, put in your backpack, or sell this item?\n\n"
                        "{old_stats}"
                    ).format(c_msg=chest_msg, c_msg_2=chest_msg2, old_stats=old_stats),
                    lang="css",
                )
            )
        else:
            chest_msg2 = (
                _("The {user} found {item} [{slot}] | Lvl req {lv}.").format(
                    user=user, item=str(item), slot=slot, lv=equip_level(character, item)
                )
                + f" (ATT: {str(att)}, "
                f"CHA: {str(cha)}, "
                f"INT: {str(intel)}, "
                f"DEX: {str(dex)}, "
                f"LUCK: {str(luck)}), "
            )
            await open_msg.edit(
                content=box(
                    _(
                        "{c_msg}\n{c_msg_2}\nDo you want to equip "
                        "this item, put in your backpack, or sell this item?\n\n{old_stats}"
                    ).format(c_msg=chest_msg, c_msg_2=chest_msg2, old_stats=old_stats),
                    lang="css",
                )
            )

        start_adding_reactions(open_msg, self._treasure_controls.keys())
        if hasattr(user, "id"):
            pred = ReactionPredicate.with_emojis(tuple(self._treasure_controls.keys()), open_msg, user)
        else:
            pred = ReactionPredicate.with_emojis(tuple(self._treasure_controls.keys()), open_msg, ctx.author)
        try:
            react, user = await self.bot.wait_for("reaction_add", check=pred, timeout=60)
        except asyncio.TimeoutError:
            await self._clear_react(open_msg)
            await character.add_to_backpack(item)
            await open_msg.edit(
                content=(
                    box(
                        _("{user} put the {item} into their backpack.").format(
                            user=self.escape(ctx.author.display_name), item=item
                        ),
                        lang="css",
                    )
                )
            )
            await self.config.user(ctx.author).set(await character.to_json(self.config))
            return
        await self._clear_react(open_msg)
        if self._treasure_controls[react.emoji] == "sell":
            price = self._sell(character, item)
            price = max(price, 0)
            if price > 0:
                try:
                    await bank.deposit_credits(ctx.author, price)
                except BalanceTooHigh as e:
                    await bank.set_balance(ctx.author, e.max_balance)
            currency_name = await bank.get_currency_name(ctx.guild,)
            if str(currency_name).startswith("<"):
                currency_name = "credits"
            await open_msg.edit(
                content=(
                    box(
                        _("{user} sold the {item} for {price} {currency_name}.").format(
                            user=self.escape(ctx.author.display_name),
                            item=item,
                            price=humanize_number(price),
                            currency_name=currency_name,
                        ),
                        lang="css",
                    )
                )
            )
            await self._clear_react(open_msg)
            character.last_known_currency = await bank.get_balance(ctx.author)
            character.last_currency_check = time.time()
            await self.config.user(ctx.author).set(await character.to_json(self.config))
        elif self._treasure_controls[react.emoji] == "equip":
            equiplevel = equip_level(character, item)
            if self.is_dev(ctx.author):
                equiplevel = 0
            if not can_equip(character, item):
                await character.add_to_backpack(item)
                await self.config.user(ctx.author).set(await character.to_json(self.config))
                return await smart_embed(
                    ctx,
                    f"**{self.escape(ctx.author.display_name)}**, you need to be level "
                    f"`{equiplevel}` to equip this item. I've put it in your backpack.",
                )
            if not getattr(character, item.slot[0]):
                equip_msg = box(
                    _("{user} equipped {item} ({slot} slot).").format(
                        user=self.escape(ctx.author.display_name), item=item, slot=slot
                    ),
                    lang="css",
                )
            else:
                equip_msg = box(
                    _("{user} equipped {item} ({slot} slot) and put {old_item} into their backpack.").format(
                        user=self.escape(ctx.author.display_name),
                        item=item,
                        slot=slot,
                        old_item=getattr(character, item.slot[0]),
                    ),
                    lang="css",
                )
            await open_msg.edit(content=equip_msg)
            character = await character.equip_item(item, False, self.is_dev(ctx.author))
            await self.config.user(ctx.author).set(await character.to_json(self.config))
        else:
            await character.add_to_backpack(item)
            await open_msg.edit(
                content=(
                    box(
                        _("{user} put the {item} into their backpack.").format(
                            user=self.escape(ctx.author.display_name), item=item
                        ),
                        lang="css",
                    )
                )
            )
            await self._clear_react(open_msg)
            await self.config.user(ctx.author).set(await character.to_json(self.config))

    @staticmethod
    async def _remaining(epoch):
        remaining = epoch - time.time()
        finish = remaining < 0
        m, s = divmod(remaining, 60)
        h, m = divmod(m, 60)
        s = int(s)
        m = int(m)
        h = int(h)
        if h == 0 and m == 0:
            out = "{:02d}".format(s)
        elif h == 0:
            out = "{:02d}:{:02d}".format(m, s)
        else:
            out = "{:01d}:{:02d}:{:02d}".format(h, m, s)
        return (out, finish, remaining)

    async def _reward(self, ctx: commands.Context, userlist, amount, modif, special):
        daymult = self._daily_bonus.get(str(datetime.today().isoweekday()), 0)
        xp = max(1, round(amount))
        cp = max(1, round(amount))
        newxp = 0
        newcp = 0
        rewards_list = []
        phrase = ""
        reward_message = ""
        currency_name = await bank.get_currency_name(ctx.guild,)
        can_embed = not ctx.guild or (await _config.guild(ctx.guild).embed() and await ctx.embed_requested())
        async for user in AsyncIter(userlist, steps=100):
            self._rewards[user.id] = {}
            try:
                c = await Character.from_json(self.config, user, self._daily_bonus)
            except Exception as exc:
                log.exception("Error with the new character sheet", exc_info=exc)
                continue
            userxp = int(xp + (xp * 0.5 * c.rebirths) + max((xp * 0.1 * min(250, c._int / 10)), 0))
            usercp = int(cp + max((cp * 0.1 * min(1000, (c._luck + c._att) / 10)), 0))
            userxp = int(userxp * (c.gear_set_bonus.get("xpmult", 1) + daymult))
            usercp = int(usercp * (c.gear_set_bonus.get("cpmult", 1) + daymult))
            newxp += userxp
            newcp += usercp
            roll = random.randint(1, 5)
            if c.heroclass.get("pet", {}).get("bonuses", {}).get("always", False):
                roll = 5
            if roll == 5 and c.heroclass["name"] == "Ranger" and c.heroclass["pet"]:
                petxp = int(userxp * c.heroclass["pet"]["bonus"])
                newxp += petxp
                userxp += petxp
                self._rewards[user.id]["xp"] = userxp
                petcp = int(usercp * c.heroclass["pet"]["bonus"])
                newcp += petcp
                usercp += petcp
                self._rewards[user.id]["cp"] = usercp
                reward_message += "{mention} gained {xp} XP and {coin} {currency}.\n".format(
                    mention=user.mention if can_embed else f"**{user.display_name}**",
                    xp=humanize_number(int(userxp)),
                    coin=humanize_number(int(usercp)),
                    currency=currency_name,
                )
                percent = round((c.heroclass["pet"]["bonus"] - 1.0) * 100)
                phrase += _("\n**{user}** received a **{percent}%** reward bonus from their {pet_name}.").format(
                    user=self.escape(user.display_name), percent=str(percent), pet_name=c.heroclass["pet"]["name"],
                )

            else:
                reward_message += "{mention} gained {xp} XP and {coin} {currency}.\n".format(
                    mention=user.mention,
                    xp=humanize_number(int(userxp)),
                    coin=humanize_number(int(usercp)),
                    currency=currency_name,
                )
                self._rewards[user.id]["xp"] = userxp
                self._rewards[user.id]["cp"] = usercp
            if special is not False:
                self._rewards[user.id]["special"] = special
            else:
                self._rewards[user.id]["special"] = False
            rewards_list.append(f"**{self.escape(user.display_name)}**")

        self._reward_message[ctx.message.id] = reward_message
        to_reward = " and ".join(
            [", ".join(rewards_list[:-1]), rewards_list[-1]] if len(rewards_list) > 2 else rewards_list
        )

        word = "has" if len(userlist) == 1 else "have"
        if special is not False and sum(special) == 1:
            types = [" normal", " rare", "n epic", " legendary", "n ascended", " set"]
            chest_type = types[special.index(1)]
            phrase += _(
                "\n{b_reward} {word} been awarded {xp} xp and found "
                "{cp} {currency_name} (split based on stats). "
                "You also secured **a{chest_type} treasure chest**!"
            ).format(
                b_reward=to_reward,
                word=word,
                xp=humanize_number(int(newxp)),
                cp=humanize_number(int(newcp)),
                currency_name=currency_name,
                chest_type=chest_type,
            )
        elif special is not False and sum(special) > 1:
            phrase += _(
                "\n{b_reward} {word} been awarded {xp} xp and found {cp} {currency_name} (split based on stats). "
                "You also secured **several treasure chests**!"
            ).format(
                b_reward=to_reward,
                word=word,
                xp=humanize_number(int(newxp)),
                cp=humanize_number(int(newcp)),
                currency_name=currency_name,
            )
        else:
            phrase += _(
                "\n{b_reward} {word} been awarded {xp} xp and found {cp} {currency_name} (split based on stats)."
            ).format(
                b_reward=to_reward,
                word=word,
                xp=humanize_number(int(newxp)),
                cp=humanize_number(int(newcp)),
                currency_name=currency_name,
            )
        return phrase

    @staticmethod
    def _sell(c: Character, item: Item, *, amount: int = 1):
        if item.rarity == "ascended":
            base = (5000, 10000)
        elif item.rarity == "legendary":
            base = (1000, 2000)
        elif item.rarity == "epic":
            base = (500, 750)
        elif item.rarity == "rare":
            base = (250, 500)
        else:
            base = (10, 100)
        price = random.randint(base[0], base[1]) * abs(item.max_main_stat)
        price += price * max(int((c.total_cha) / 1000), -1)

        if c.luck > 0:
            price = price + round(price * (c.luck / 1000))
        if c.luck < 0:
            price = price - round(price * (abs(c.luck) / 1000))
        if price < 0:
            price = 0
        price += round(price * min(0.1 * c.rebirths / 15, 0.4))

        return max(price, base[0])

    async def _trader(self, ctx: commands.Context, bypass=False):
        em_list = ReactionPredicate.NUMBER_EMOJIS

        cart = await self.config.cart_name()
        if await self.config.guild(ctx.guild).cart_name():
            cart = await self.config.guild(ctx.guild).cart_name()
        text = box(_("[{} is bringing the cart around!]").format(cart), lang="css")
        timeout = await self.config.guild(ctx.guild).cart_timeout()
        if ctx.guild.id not in self._last_trade:
            self._last_trade[ctx.guild.id] = 0

        if not bypass:
            if self._last_trade[ctx.guild.id] == 0:
                self._last_trade[ctx.guild.id] = time.time()
            elif self._last_trade[ctx.guild.id] >= time.time() - timeout:
                # trader can return after 3 hours have passed since last visit.
                return  # silent return.
        self._last_trade[ctx.guild.id] = time.time()

        room = await self.config.guild(ctx.guild).cartroom()
        if room:
            room = ctx.guild.get_channel(room)
        if room is None or bypass:
            room = ctx
        self.bot.dispatch("adventure_cart", ctx)  # dispatch after silent return
        stockcount = random.randint(3, 9)
        controls = {em_list[i + 1]: i for i in range(stockcount)}
        self._curent_trader_stock[ctx.guild.id] = (stockcount, controls)

        stock = await self._trader_get_items(stockcount)
        currency_name = await bank.get_currency_name(ctx.guild,)
        if str(currency_name).startswith("<"):
            currency_name = "credits"
        for (index, item) in enumerate(stock):
            item = stock[index]
            if len(item["item"].slot) == 2:  # two handed weapons add their bonuses twice
                hand = "two handed"
                att = item["item"].att * 2
                cha = item["item"].cha * 2
                intel = item["item"].int * 2
                luck = item["item"].luck * 2
                dex = item["item"].dex * 2
            else:
                if item["item"].slot[0] == "right" or item["item"].slot[0] == "left":
                    hand = item["item"].slot[0] + _(" handed")
                else:
                    hand = item["item"].slot[0] + _(" slot")
                att = item["item"].att
                cha = item["item"].cha
                intel = item["item"].int
                luck = item["item"].luck
                dex = item["item"].dex
            text += box(
                _(
                    "\n[{i}] Lvl req {lvl} | {item_name} ("
                    "Attack: {str_att}, "
                    "Charisma: {str_cha}, "
                    "Intelligence: {str_int}, "
                    "Dexterity: {str_dex}, "
                    "Luck: {str_luck} "
                    "[{hand}]) for {item_price} {currency_name}."
                ).format(
                    i=str(index + 1),
                    item_name=item["item"].formatted_name,
                    lvl=item["item"].lvl,
                    str_att=str(att),
                    str_int=str(intel),
                    str_cha=str(cha),
                    str_luck=str(luck),
                    str_dex=str(dex),
                    hand=hand,
                    item_price=humanize_number(item["price"]),
                    currency_name=currency_name,
                ),
                lang="css",
            )
        text += _("Do you want to buy any of these fine items? Tell me which one below:")
        msg = await room.send(text)
        start_adding_reactions(msg, controls.keys())
        self._current_traders[ctx.guild.id] = {"msg": msg.id, "stock": stock, "users": []}
        timeout = self._last_trade[ctx.guild.id] + 180 - time.time()
        if timeout <= 0:
            timeout = 0
        timer = await self._cart_countdown(ctx, timeout, _("The cart will leave in: "), room=room)
        self.tasks[msg.id] = timer
        try:
            await asyncio.wait_for(timer, timeout + 5)
        except asyncio.TimeoutError:
            await self._clear_react(msg)
            return
        with contextlib.suppress(discord.HTTPException):
            await msg.delete()

    async def _trader_get_items(self, howmany: int):
        items = {}
        output = {}

        while len(items) < howmany:
            rarity_roll = random.random()
            #  rarity_roll = .9
            # 1% legendary
            if rarity_roll >= 0.95:
                item = await self._genitem("legendary")
                # min. 10 stat for legendary, want to be about 50k
                price = random.randint(2500, 5000)
            # 20% epic
            elif rarity_roll >= 0.7:
                item = await self._genitem("epic")
                # min. 5 stat for epic, want to be about 25k
                price = random.randint(1000, 2000)
            # 35% rare
            elif rarity_roll >= 0.35:
                item = await self._genitem("rare")
                # around 3 stat for rare, want to be about 3k
                price = random.randint(500, 1000)
            else:
                item = await self._genitem("normal")
                # 1 stat for normal, want to be <1k
                price = random.randint(100, 500)
            # 35% normal
            price *= item.max_main_stat

            items.update({item.name: {"itemname": item.name, "item": item, "price": price, "lvl": item.lvl}})

        for (index, item) in enumerate(items):
            output.update({index: items[item]})
        return output

    def cog_unload(self):
        if self.cleanup_loop:
            self.cleanup_loop.cancel()
        if self._init_task:
            self._init_task.cancel()
        if self.gb_task:
            self.gb_task.cancel()

        for (msg_id, task) in self.tasks.items():
            task.cancel()

        for lock in self.locks.values():
            with contextlib.suppress(Exception):
                lock.release()

    async def get_leaderboard(self, positions: int = None, guild: discord.Guild = None) -> List[tuple]:
        """Gets the Adventure's leaderboard.

        Parameters
        ----------
        positions : `int`
            The number of positions to get
        guild : discord.Guild
            The guild to get the leaderboard of. If this
            is provided, get only guild members on the leaderboard

        Returns
        -------
        `list` of `tuple`
            The sorted leaderboard in the form of :code:`(user_id, raw_account)`
        """
        raw_accounts = await self.config.all_users()
        if guild is not None:
            tmp = raw_accounts.copy()
            for acc in tmp:
                if not guild.get_member(acc):
                    del raw_accounts[acc]
        raw_accounts_new = {}
        async for (k, v) in AsyncIter(raw_accounts.items(), steps=100):
            user_data = {}
            for item in ["lvl", "rebirths", "set_items"]:
                if item not in v:
                    v.update({item: 0})
            for (vk, vi) in v.items():
                if vk in ["lvl", "rebirths", "set_items"]:
                    user_data.update({vk: vi})

            if user_data:
                user_data = {k: user_data}
            raw_accounts_new.update(user_data)
        sorted_acc = sorted(
            raw_accounts_new.items(),
            key=lambda x: (x[1].get("rebirths", 0), x[1].get("lvl", 1), x[1].get("set_items", 0)),
            reverse=True,
        )
        if positions is None:
            return sorted_acc
        else:
            return sorted_acc[:positions]

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True, embed_links=True)
    @commands.guild_only()
    async def aleaderboard(self, ctx: commands.Context, show_global: bool = False):
        """Print the leaderboard."""
        guild = ctx.guild
        rebirth_sorted = await self.get_leaderboard(guild=guild if not show_global else None)
        if rebirth_sorted:
            await LeaderboardMenu(
                source=LeaderboardSource(entries=rebirth_sorted),
                delete_message_after=True,
                clear_reactions_after=True,
                timeout=60,
                cog=self,
                show_global=show_global,
            ).start(ctx=ctx)
        else:
            await smart_embed(ctx, _("There are no adventurers in the server."))

    async def get_global_scoreboard(
        self, positions: int = None, guild: discord.Guild = None, keyword: str = None
    ) -> List[tuple]:
        """Gets the bank's leaderboard.

        Parameters
        ----------
        positions : `int`
            The number of positions to get
        guild : discord.Guild
            The guild to get the leaderboard of. If this
            is provided, get only guild members on the leaderboard

        Returns
        -------
        `list` of `tuple`
            The sorted leaderboard in the form of :code:`(user_id, raw_account)`

        Raises
        ------
        TypeError
            If the bank is guild-specific and no guild was specified
        """
        if keyword is None:
            keyword = "wins"
        raw_accounts = await self.config.all_users()
        if guild is not None:
            tmp = raw_accounts.copy()
            for acc in tmp:
                if not guild.get_member(acc):
                    del raw_accounts[acc]
        raw_accounts_new = {}
        async for (k, v) in AsyncIter(raw_accounts.items(), steps=200):
            user_data = {}
            for item in ["adventures", "rebirths"]:
                if item not in v:
                    if item == "adventures":
                        v.update({item: {keyword: 0}})
                    else:
                        v.update({item: 0})

            for (vk, vi) in v.items():
                if vk in ["rebirths"]:
                    user_data.update({vk: vi})
                elif vk in ["adventures"]:
                    for (s, sv) in vi.items():
                        if s == keyword:
                            user_data.update(vi)

            if user_data:
                user_data = {k: user_data}
            raw_accounts_new.update(user_data)

        sorted_acc = sorted(
            raw_accounts_new.items(), key=lambda x: (x[1].get(keyword, 0), x[1].get("rebirths", 0)), reverse=True,
        )
        if positions is None:
            return sorted_acc
        else:
            return sorted_acc[:positions]

    async def get_global_negaverse_scoreboard(self, positions: int = None, guild: discord.Guild = None) -> List[tuple]:
        """Gets the bank's leaderboard.

        Parameters
        ----------
        positions : `int`
            The number of positions to get
        guild : discord.Guild
            The guild to get the leaderboard of. If this
            is provided, get only guild members on the leaderboard

        Returns
        -------
        `list` of `tuple`
            The sorted leaderboard in the form of :code:`(user_id, raw_account)`

        Raises
        ------
        TypeError
            If the bank is guild-specific and no guild was specified
        """
        raw_accounts = await self.config.all_users()
        if guild is not None:
            tmp = raw_accounts.copy()
            for acc in tmp:
                if not guild.get_member(acc):
                    del raw_accounts[acc]
        raw_accounts_new = {}
        async for (k, v) in AsyncIter(raw_accounts.items(), steps=200):
            user_data = {}
            for (vk, vi) in v.items():
                if vk in ["nega"]:
                    for (s, sv) in vi.items():
                        user_data.update(vi)

            if user_data:
                user_data = {k: user_data}
            raw_accounts_new.update(user_data)

        sorted_acc = sorted(
            raw_accounts_new.items(), key=lambda x: (x[1].get("wins", 0), x[1].get("loses", 0)), reverse=True,
        )
        if positions is None:
            return sorted_acc
        else:
            return sorted_acc[:positions]

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True, embed_links=True)
    @commands.guild_only()
    async def scoreboard(self, ctx: commands.Context, show_global: bool = False):
        """Print the scoreboard."""

        rebirth_sorted = await self.get_global_scoreboard(guild=ctx.guild if not show_global else None, keyword="wins")
        if rebirth_sorted:
            await ScoreBoardMenu(
                source=ScoreboardSource(entries=rebirth_sorted, stat="wins"),
                delete_message_after=True,
                clear_reactions_after=True,
                timeout=60,
                cog=self,
                show_global=show_global,
            ).start(ctx=ctx)
        else:
            await smart_embed(ctx, _("There are no adventurers in the server."))

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True, embed_links=True)
    @commands.guild_only()
    async def nvsb(self, ctx: commands.Context, show_global: bool = False):
        """Print the negaverse scoreboard."""
        guild = ctx.guild
        rebirth_sorted = await self.get_global_negaverse_scoreboard(guild=guild if not show_global else None)
        if rebirth_sorted:
            await BaseMenu(
                source=NVScoreboardSource(entries=rebirth_sorted),
                delete_message_after=True,
                clear_reactions_after=True,
                timeout=60,
            ).start(ctx=ctx)
        else:
            await smart_embed(ctx, _("There are no adventurers in the server."))

    @commands.command()
    @commands.bot_has_permissions(add_reactions=True, embed_links=True)
    @commands.guild_only()
    async def wscoreboard(self, ctx: commands.Context, show_global: bool = False):
        """Print the weekly scoreboard."""

        stats = "adventures"
        guild = ctx.guild
        adventures = await self.get_weekly_scoreboard(guild=guild if not show_global else None)
        if adventures:
            await BaseMenu(
                source=WeeklyScoreboardSource(entries=adventures, stat=stats.lower()),
                delete_message_after=True,
                clear_reactions_after=True,
                timeout=60,
            ).start(ctx=ctx)
        else:
            await smart_embed(ctx, _("No stats to show for this week."))

    async def get_weekly_scoreboard(self, positions: int = None, guild: discord.Guild = None) -> List[tuple]:
        """Gets the bank's leaderboard.

        Parameters
        ----------
        positions : `int`
            The number of positions to get
        guild : discord.Guild
            The guild to get the leaderboard of. If this
            is provided, get only guild members on the leaderboard

        Returns
        -------
        `list` of `tuple`
            The sorted leaderboard in the form of :code:`(user_id, raw_account)`

        Raises
        ------
        TypeError
            If the bank is guild-specific and no guild was specified
        """
        current_week = date.today().isocalendar()[1]
        keyword = "adventures"
        raw_accounts = await self.config.all_users()
        if guild is not None:
            tmp = raw_accounts.copy()
            for acc in tmp:
                if not guild.get_member(acc):
                    del raw_accounts[acc]
        raw_accounts_new = {}
        async for (k, v) in AsyncIter(raw_accounts.items(), steps=200):
            user_data = {}
            for item in ["weekly_score"]:
                if item not in v:
                    if item == "weekly_score":
                        v.update({item: {keyword: 0, "rebirths": 0}})

            for (vk, vi) in v.items():
                if vk in ["weekly_score"]:
                    if vi.get("week", -1) == current_week:
                        for (s, sv) in vi.items():
                            if s in [keyword]:
                                user_data.update(vi)

            if user_data:
                user_data = {k: user_data}
            raw_accounts_new.update(user_data)

        sorted_acc = sorted(
            raw_accounts_new.items(), key=lambda x: (x[1].get(keyword, 0), x[1].get("rebirths", 0)), reverse=True,
        )
        if positions is None:
            return sorted_acc
        else:
            return sorted_acc[:positions]

    @commands.command(name="apayday", cooldown_after_parsing=True)
    @has_separated_economy()
    @commands.cooldown(rate=1, per=600, type=commands.BucketType.user)
    async def commands_apayday(self, ctx: commands.Context):
        """Get some free gold."""
        author = ctx.author
        adventure_credits_name = await bank.get_currency_name(ctx.guild)
        amount = 333  # Make Customizable?
        try:
            await bank.deposit_credits(author, amount)
        except BalanceTooHigh as exc:
            await bank.set_balance(author, exc.max_balance)
            await smart_embed(
                ctx,
                _(
                    "You're struggling to move under the weight of all your {currency}!"
                    "Please spend some more \N{GRIMACING FACE}\n\n"
                    "You currently have {new_balance} {currency}."
                ).format(currency=adventure_credits_name, new_balance=humanize_number(exc.max_balance)),
            )
        else:
            await smart_embed(
                ctx,
                _(
                    "You receive a letter by post from the town's courier! "
                    "{author}, you've gained some interest on your {currency}. "
                    "You've been paid +{amount} {currency}!\n\n"
                    "You currently have {new_balance} {currency}."
                ).format(
                    author=author.mention,
                    currency=adventure_credits_name,
                    amount=humanize_number(amount),  # Make customizable?
                    new_balance=humanize_number(await bank.get_balance(author)),
                ),
            )
        try:
            character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
        else:
            if character.last_currency_check + 600 < time.time() or character.bal > character.last_known_currency:
                character.last_known_currency = await bank.get_balance(ctx.author)
                character.last_currency_check = time.time()
                await self.config.user(ctx.author).set(await character.to_json(self.config))

    @commands.group(name="atransfer")
    @has_separated_economy()
    async def commands_atransfer(self, ctx: commands.Context):
        """Transfer currency between players/economies."""

    @commands_atransfer.command(name="deposit")
    @commands.guild_only()
    async def commands_atransfer_deposit(self, ctx: commands.Context, *, amount: int):
        """Convert bank currency to gold."""
        from_conversion_rate = await self.config.to_conversion_rate()
        transferable_amount = amount * from_conversion_rate
        if amount <= 0:
            await smart_embed(
                ctx, _("{author.mention} You can't deposit 0 or negative values.").format(author=ctx.author),
            )
            return
        if not await bank.can_spend(ctx.author, amount=amount, _forced=True):
            await smart_embed(
                ctx,
                _("{author.mention} You don't have enough {name}.").format(
                    author=ctx.author, name=await bank.get_currency_name(ctx.guild, _forced=True)
                ),
            )
            return
        try:
            await bank.withdraw_credits(member=ctx.author, amount=amount, _forced=True)
        except ValueError:
            await smart_embed(
                ctx,
                _("{author.mention} You don't have enough {name}.").format(
                    author=ctx.author, name=await bank.get_currency_name(ctx.guild, _forced=True)
                ),
            )
            return
        try:
            await bank.deposit_credits(member=ctx.author, amount=transferable_amount)
        except BalanceTooHigh as exc:
            await bank.set_balance(member=ctx.author, amount=exc.max_balance)
        await smart_embed(
            ctx,
            _("{author.mention} you converted {amount} {currency} to {a_amount} {a_currency}.").format(
                author=ctx.author,
                amount=humanize_number(amount),
                a_amount=humanize_number(transferable_amount),
                a_currency=await bank.get_currency_name(ctx.guild),
                currency=await bank.get_currency_name(ctx.guild, _forced=True),
            ),
        )
        try:
            character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
        else:
            if character.last_currency_check + 600 < time.time() or character.bal > character.last_known_currency:
                character.last_known_currency = await bank.get_balance(ctx.author)
                character.last_currency_check = time.time()
                await self.config.user(ctx.author).set(await character.to_json(self.config))

    @commands_atransfer.command(name="withdraw", cooldown_after_parsing=True)
    @commands.guild_only()
    @commands.cooldown(rate=1, per=600, type=commands.BucketType.user)
    async def commands_atransfer_withdraw(self, ctx: commands.Context, *, amount: int):
        """Convert gold to bank currency."""
        if await bank.is_global(_forced=True):
            global_config = await self.config.all()
            can_withdraw = global_config["disallow_withdraw"]
            max_allowed_withdraw = global_config["max_allowed_withdraw"]
            is_global = True
        else:
            guild_config = await self.config.guild(ctx.guild).all()
            can_withdraw = guild_config["disallow_withdraw"]
            max_allowed_withdraw = guild_config["max_allowed_withdraw"]
            is_global = False
        if not can_withdraw or max_allowed_withdraw < 1:
            if is_global:
                string = _("{author.mention} my owner has disabled this option.")
            else:
                string = _("{author.mention} the admins of this server do not allow you to withdraw here.")
            await smart_embed(
                ctx, string.format(author=ctx.author),
            )
            return
        if amount <= 0:
            await smart_embed(
                ctx, _("{author.mention} You can't withdraw 0 or negative values.").format(author=ctx.author),
            )
            return
        configs = await self.config.all()
        from_conversion_rate = configs.get("from_conversion_rate")
        transferable_amount = amount // from_conversion_rate
        if not await bank.can_spend(member=ctx.author, amount=amount):
            return await smart_embed(
                ctx,
                _("{author.mention} you don't have enough {name}.").format(
                    author=ctx.author, name=await bank.get_currency_name(ctx.guild)
                ),
            )
        if transferable_amount > max_allowed_withdraw:
            return await smart_embed(
                ctx,
                _("{author.mention} I can't allow you to transfer {amount} to {bank}.").format(
                    author=ctx.author,
                    amount=humanize_number(transferable_amount),
                    bank=await bank.get_bank_name(ctx.guild),
                ),
            )
        try:
            await bank.deposit_credits(member=ctx.author, amount=transferable_amount, _forced=True)
        except BalanceTooHigh as exc:
            await bank.set_balance(ctx.author, exc.max_balance, _forced=True)
        await bank.withdraw_credits(member=ctx.author, amount=amount)
        await smart_embed(
            ctx,
            _("{author.mention} you converted {a_amount} {a_currency} to {amount} {currency}.").format(
                author=ctx.author,
                a_amount=humanize_number(amount),
                amount=humanize_number(transferable_amount),
                a_currency=await bank.get_currency_name(ctx.guild),
                currency=await bank.get_currency_name(ctx.guild, _forced=True),
            ),
        )

    @commands_atransfer.command(name="player", cooldown_after_parsing=True)
    @commands.guild_only()
    @commands.cooldown(rate=1, per=600, type=commands.BucketType.user)
    async def commands_atransfer_player(self, ctx: commands.Context, amount: int, *, player: discord.User):
        """Transfer gold to another player."""
        if amount <= 0:
            await smart_embed(
                ctx, _("{author.mention} You can't transfer 0 or negative values.").format(author=ctx.author),
            )
            ctx.command.reset_cooldown(ctx)
            return
        currency = await bank.get_currency_name(ctx.guild)
        if not await bank.can_spend(member=ctx.author, amount=amount):
            ctx.command.reset_cooldown(ctx)
            return await smart_embed(
                ctx,
                _("{author.mention} you don't have enough {name}.").format(
                    author=ctx.author, name=await bank.get_currency_name(ctx.guild)
                ),
            )
        tax = await self.config.tax_brackets.all()
        highest = 0
        for tax, percent in tax.items():
            tax = int(tax)
            if tax >= amount:
                break
            highest = percent

        try:
            transfered = await bank.transfer_credits(
                from_=ctx.author, to=player, amount=amount, tax=highest
            )  # Customizable Tax
        except (ValueError, BalanceTooHigh) as e:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(str(e))

        await ctx.send(
            _(
                "{user} transferred {num} {currency} to {other_user} (You have been taxed {tax:.2%}, total transfered: {transfered})"
            ).format(
                user=ctx.author.display_name,
                num=humanize_number(amount),
                currency=currency,
                other_user=player.display_name,
                tax=highest,
                transfered=humanize_number(transfered),
            )
        )

    @commands_atransfer.command(name="give")
    @commands.is_owner()
    async def commands_atransfer_give(self, ctx: commands.Context, amount: int, *players: discord.User):
        """[Owner] Give gold to adventurers."""
        if amount <= 0:
            await smart_embed(
                ctx, _("{author.mention} You can't give 0 or negative values.").format(author=ctx.author),
            )
            return
        players_string = ""
        for player in players:
            try:
                await bank.deposit_credits(member=player, amount=amount)
                players_string += f"{player.display_name}\n"
            except BalanceTooHigh as exc:
                await bank.set_balance(member=player, amount=exc.max_balance)
                players_string += f"{player.display_name}\n"

        await smart_embed(
            ctx,
            _("{author.mention} I've given {amount} {name} to the following adventurers:\n\n{players}").format(
                author=ctx.author,
                amount=humanize_number(amount),
                players=players_string,
                name=await bank.get_currency_name(ctx.guild),
            ),
        )

    @commands.command(name="mysets")
    async def commands_mysets(self, ctx: commands.Context):
        """Show your sets."""

        try:
            character = await Character.from_json(self.config, ctx.author, self._daily_bonus)
        except Exception as exc:
            log.exception("Error with the new character sheet", exc_info=exc)
            return

        sets = await character.get_set_count()
        table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
        table.set_style(BeautifulTable.STYLE_RST)
        table.columns.header = [
            "Name",
            "Unique Pieces",
            "Unique Owned",
        ]
        msgs = []
        for k, v in sets.items():
            if len(str(table)) > 1500:
                table.rows.sort("Name", reverse=False)
                msgs.append(box(str(table) + f"\nPage {len(msgs) + 1}", lang="css"))
                table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
                table.set_style(BeautifulTable.STYLE_RST)
                table.columns.header = [
                    "Name",
                    "Unique Pieces",
                    "Unique Owned",
                ]
            table.rows.append((k, f"{v[0]}", f" {v[1]}" if v[1] == v[0] else f"[{v[1]}]"))
        table.rows.sort("Name", reverse=False)
        msgs.append(box(str(table) + f"\nPage {len(msgs) + 1}", lang="css"))
        await BaseMenu(
            source=SimpleSource(msgs), delete_message_after=True, clear_reactions_after=True, timeout=60,
        ).start(ctx=ctx)
