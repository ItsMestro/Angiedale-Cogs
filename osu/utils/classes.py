from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

import pycountry
from ossapi import Beatmap, Beatmapset, GameMode
from ossapi import Mod as OsuMod
from ossapi import RankingType
from ossapi import Score as OsuScore
from ossapi import Statistics
from ossapi.models import Grade as OsuGrade

_VARIANTS = ["4k", "7k"]
_TYPES = {"performance": ["pp", "performance"], "score": ["score"], "country": ["country"]}

_GAMEMODES = {
    "osu": ["osu", "standard", "std"],
    "taiko": ["taiko"],
    "fruits": ["catch", "fruits", "ctb"],
    "mania": ["mania"],
}


class MissingValueError(ValueError):
    """
    A required value for an argument was missing.
    """

    def __init__(self, parameter: str):
        self.parameter: str = parameter


class TooManyArgumentsError(IndexError):
    """
    Too many arguments were used.
    """


class ConflictingArgumentsError(ValueError):
    """
    The combination of arguments used isn't allowed.
    """

    def __init__(self, parameters: List[str]):
        self.parameters: List[str] = parameters


class OutOfRangeError(IndexError):
    """
    Value is outside of accepted range.
    """

    def __init__(self, param: str, value: Union[int, str]):
        self.param = param
        self.value = value


class InvalidCountryError(ValueError):
    """
    A country by the given value wasn't found.
    """


class ValueFound(Exception):
    """
    Just a dummy exception for breaking out of multiple loops.
    """


class DoubleArgs(Enum):
    PP = ["-pp", float]
    INDEX = ["-p", int]
    RANK = ["-rank", int]
    MODE = ["-mode", GameMode]


class SingleArgs(Enum):
    RECENT = "-r"
    GUILD = "-g"
    ME = "-me"


class CommandParams:
    def __init__(
        self,
        params: Tuple[str],
        single_args: List[Optional[SingleArgs]],
        double_args: List[Optional[DoubleArgs]],
    ) -> None:
        self.user_id: Optional[int] = None
        self.pp: float = 0
        self.r: bool = False
        self.p: Optional[int] = None
        self.rank: Optional[int] = None
        self.g: bool = False
        self.me: bool = False
        self.mode: Optional[GameMode] = None

        self.extra_param: Optional[str] = None

        params: List[str] = [(x.lower()) for x in params]

        for param in double_args:
            if param.value[0] in params:
                index = params.index(param.value[0])
                try:
                    num = params[index + 1].replace(",", ".")
                except IndexError:
                    raise MissingValueError(param.value[0])
                try:
                    converted_num = param.value[1](num)
                    setattr(self, param.value[0][1:], converted_num)
                    params.pop(index + 1)
                    params.pop(index)
                except ValueError:
                    raise MissingValueError(param.value[0])

        for param in single_args:
            if param.value in params:
                params.remove(param.value)
                setattr(self, param.value[1:], True)

        if len(params) > 1:
            raise TooManyArgumentsError()
        if self.r and self.p is not None:
            raise ConflictingArgumentsError(["-r", "-p"])
        if self.p is not None:
            if self.p < 100 or self.p > 1:
                raise OutOfRangeError("-p", self.p)
        if self.rank is not None:
            if self.rank < 10000 or self.rank > 1:
                raise OutOfRangeError("-rank", self.rank)

        if len(params) == 1:
            self.extra_param = params[0]


class CommandArgs:
    def __init__(self, args: Tuple[str]) -> None:
        self.mode: GameMode = GameMode.OSU
        self.type: RankingType = RankingType.PERFORMANCE
        self.country: Optional[str] = None
        self.variant: Optional[str] = None

        args: List[str] = [(x.lower()) for x in args]

        if len(args) > 4:
            raise TooManyArgumentsError()

        try:
            for arg in args:  # Mania variant
                if arg in _VARIANTS:
                    self.variant = arg
                    args.remove(arg)
                    raise ValueFound
        except ValueFound:
            pass

        try:
            for arg in args:  # Ranking type
                for key, type in _TYPES.items():
                    if arg in type:
                        self.type = RankingType(key)
                        args.remove(arg)
                        raise ValueFound
        except ValueFound:
            pass

        try:
            for arg in args:  # Mode
                for key, mode in _GAMEMODES.items():
                    if arg in mode:
                        self.mode = GameMode(key)
                        args.remove(arg)
                        raise ValueFound
        except ValueFound:
            pass

        try:
            for arg in args:  # Country code
                country = pycountry.countries.get(alpha_2=arg)
                if country:
                    self.country = arg
                    args.remove(arg)
                    raise ValueFound
        except ValueFound:
            pass

        if len(args) != 0:
            if len(args) == 1 and not self.country:
                if len(args[0]) == 2:
                    raise InvalidCountryError
            raise TooManyArgumentsError()

        if self.country and self.type is not RankingType.PERFORMANCE:
            raise ConflictingArgumentsError(["<country>", "<type>"])

        if self.variant and self.type is not RankingType.PERFORMANCE:
            raise ConflictingArgumentsError(["<variant>", "<type>"])

        if self.variant and self.mode is not GameMode.MANIA:
            raise OutOfRangeError(self.variant, self.mode.name)


class DatabaseScore:
    def __init__(self, user_id: int = None, data: Union[OsuScore, dict] = None):
        self.id: int = user_id
        self.score: int = None
        self.username: str = None
        self.country_code: str = None
        self.accuracy: float = None
        self.mods: OsuMod = None
        self.max_combo: int = None
        self.rank: OsuGrade = None
        self.created_at: datetime = None
        self.statistics: Statistics = None

        if data:
            if isinstance(data, dict):
                self.id = user_id
                self.score = data["score"]
                self.username = data["username"]
                self.country_code = data["country_code"]
                self.accuracy = data["accuracy"]
                self.mods = OsuMod(data["mods"])
                self.max_combo = data["max_combo"]
                self.rank = OsuGrade(data["rank"])
                self.created_at = datetime.strptime(data["created_at"], "%Y-%m-%dT%H:%M:%S%z")

                statistics = Statistics()
                statistics.count_miss = data["count_miss"]
                statistics.count_50 = data["count_50"]
                statistics.count_100 = data["count_100"]
                statistics.count_300 = data["count_300"]
                statistics.count_katu = data["count_katu"]
                statistics.count_geki = data["count_geki"]

                self.statistics = statistics
            elif data is not None:
                self.id = data.user_id
                self.score = data.score
                self.username = data.user().username
                self.country_code = data.user().country_code
                self.accuracy = data.accuracy
                self.mods = data.mods
                self.max_combo = data.max_combo
                self.rank = data.rank
                self.created_at = data.created_at
                self.statistics = data.statistics

    def flatten_to_dict(self) -> Dict[str, Dict[str, Union[float, int, str]]]:
        output = {}
        special = ["mods", "rank", "created_at", "statistics"]

        for i, v in self.__dict__.items():
            if i[:1] == "_":
                continue
            if i in special:
                if isinstance(v, (OsuMod, OsuGrade)):
                    output[i] = v.value
                elif isinstance(v, datetime):
                    output[i] = v.strftime("%Y-%m-%dT%H:%M:%S%z")
                elif isinstance(v, Statistics):
                    output["count_miss"] = v.count_miss
                    output["count_50"] = v.count_50
                    output["count_100"] = v.count_100
                    output["count_300"] = v.count_300
                    output["count_katu"] = v.count_katu
                    output["count_geki"] = v.count_geki
            else:
                output[i] = v

        return output

    def __str__(self):
        return (
            str({i: v for i, v in self.__dict__.items() if i[:1] != "_"})
            if len(self.__dict__) > 0
            else str(self.__class__)
        )


class DatabaseLeaderboard:
    def __init__(self, data: dict):
        self.id: int = None
        self.title: str = None
        self.version: str = None
        self.artist: str = None
        self.last_updated: datetime = None
        self.leaderboard: Dict[str, DatabaseScore] = {}

        if data:
            try:
                beatmap = data["beatmap"]
                self.id = data["_id"]
                self.title = beatmap["title"]
                self.version = beatmap["version"]
                self.artist = beatmap["artist"]
                self.last_updated = datetime.strptime(
                    beatmap["last_updated"], "%Y-%m-%dT%H:%M:%S%z"
                )
            except KeyError:
                pass
            try:
                leaderboard = data["leaderboard"]

                if isinstance(leaderboard, dict):
                    scores = {}
                    for user_id, score in leaderboard.items():
                        scores[str(user_id)] = DatabaseScore(user_id, score)

                    self.leaderboard = dict(
                        sorted(scores.items(), key=lambda item: item[1].score, reverse=True)
                    )
                else:
                    self.leaderboard = leaderboard
            except KeyError:
                pass

    def __str__(self):
        return (
            str({i: v for i, v in self.__dict__.items() if i[:1] != "_"})
            if len(self.__dict__) > 0
            else str(self.__class__)
        )


class OsubeatSet:
    def __init__(self, data: Union[Beatmapset, dict]):
        if isinstance(data, Beatmapset):
            self.title = data.title
            self.artist = data.artist
            self.cover = data.covers.cover
        else:
            self.title = data["title"]
            self.artist = data["artist"]
            self.cover = data["cover"]


class OsubeatMap:
    def __init__(self, data: Union[Beatmap, dict]):
        self.version: str = None
        self.url: str = None
        self.id: int = None
        self.beatmapset: OsubeatSet = None

        if data is not None:
            if isinstance(data, Beatmap):
                self.version = data.version
                self.url = data.url
                self.id = data.id
                self.beatmapset = OsubeatSet(data.beatmapset())
            else:
                self.version = data["version"]
                self.url = data["url"]
                self.id = data["id"]
                self.beatmapset = OsubeatSet(data["beatmapset"])

    def to_dict(self) -> Dict[str, Union[Dict[str, str], int, str]]:
        output = {}
        output["version"] = self.version
        output["url"] = self.url
        output["id"] = self.id

        output["beatmapset"] = {}
        output["beatmapset"]["title"] = self.beatmapset.title
        output["beatmapset"]["artist"] = self.beatmapset.artist
        output["beatmapset"]["cover"] = self.beatmapset.cover

        return output


class OsubeatUser:
    def __init__(self, user_id: int = None, username: str = None, country_code: str = None):
        self.id = user_id
        self.username = username
        self.country_code = country_code


class OsubeatScore:
    def __init__(self, data: Union[OsuScore, dict]):
        self.score: int = None
        self.accuracy: float = None
        self.max_combo: int = None
        self.rank: OsuGrade = None
        self.created_at: datetime = None
        self.mods: OsuMod = None
        self.statistics: Statistics = None
        self.user: OsubeatUser = None

        if data is not None:
            if isinstance(data, OsuScore):
                self.score = data.score
                self.accuracy = data.accuracy
                self.max_combo = data.max_combo
                self.rank = data.rank
                self.created_at = data.created_at
                self.mods = data.mods
                self.statistics = data.statistics

                self.user = OsubeatUser()
                user = data.user()
                self.user.id = user.id
                self.user.country_code = user.country_code
                self.user.username = user.username
            elif len(data) > 0:
                self.score = data["score"]

                self.user = OsubeatUser(data["user"]["id"])

                try:
                    self.user.username = data["user"]["username"]
                    self.user.country_code = data["user"]["country_code"]

                    self.accuracy = data["accuracy"]
                    self.max_combo = data["max_combo"]
                    self.rank = OsuGrade(data["rank"])
                    self.created_at = datetime.strptime(data["created_at"], "%Y-%m-%dT%H:%M:%S%z")
                    self.mods = OsuMod(data["mods"])

                    self.statistics = Statistics()
                    self.statistics.count_geki = data["statistics"]["count_geki"]
                    self.statistics.count_katu = data["statistics"]["count_katu"]
                    self.statistics.count_300 = data["statistics"]["count_300"]
                    self.statistics.count_100 = data["statistics"]["count_100"]
                    self.statistics.count_50 = data["statistics"]["count_50"]
                    self.statistics.count_miss = data["statistics"]["count_miss"]
                except:
                    pass

    def to_dict(
        self,
    ) -> Dict[str, Union[Dict[str, Union[int, str]], Dict[str, int], float, int, str]]:
        output = {}

        output["score"] = self.score
        output["accuracy"] = self.accuracy
        output["max_combo"] = self.max_combo
        output["rank"] = self.rank.value
        output["created_at"] = self.created_at.strftime("%Y-%m-%dT%H:%M:%S%z")
        output["mods"] = self.mods.short_name()

        output["statistics"] = {}
        output["statistics"]["count_geki"] = self.statistics.count_geki
        output["statistics"]["count_katu"] = self.statistics.count_katu
        output["statistics"]["count_300"] = self.statistics.count_300
        output["statistics"]["count_100"] = self.statistics.count_100
        output["statistics"]["count_50"] = self.statistics.count_50
        output["statistics"]["count_miss"] = self.statistics.count_miss

        output["user"] = {}
        output["user"]["id"] = self.user.id
        output["user"]["username"] = self.user.username
        output["user"]["country_code"] = self.user.country_code

        return output


class Osubeat:
    def __init__(self, data: dict = None):
        self.beatmap: OsubeatMap = None
        self.mode: GameMode = None
        self.mods: List[OsuMod] = []
        self.created_at: datetime = None
        self.ends: datetime = None
        self.channel_id: Optional[int] = None
        self.message_id: Optional[int] = None
        self.pinned: bool = False

        if data is not None:
            self.beatmap = OsubeatMap(data["beatmap"])
            self.mode = GameMode(data["mode"])
            self.created_at = datetime.strptime(data["created_at"], "%Y-%m-%dT%H:%M:%S%z")
            self.ends = datetime.strptime(data["ends"], "%Y-%m-%dT%H:%M:%S%z")
            for mod in data["mods"]:
                self.mods.append(OsuMod(mod))

            try:
                self.channel_id = data["channel"]
                self.message_id = data["message"]
                self.pinned = data["pinned"]
            except KeyError:
                pass

    def to_dict(
        self,
    ) -> Dict[
        str,
        Union[
            Dict[str, Union[Dict[str, str], int, str]], List[Union[OsuMod, str]], int, str, bool
        ],
    ]:
        output = {}
        output["beatmap"] = self.beatmap.to_dict()
        output["mode"] = self.mode.value
        output["created_at"] = self.created_at.strftime("%Y-%m-%dT%H:%M:%S%z")
        output["ends"] = self.ends.strftime("%Y-%m-%dT%H:%M:%S%z")

        if self.channel_id is not None:
            output["channel"] = self.channel_id
            output["message"] = self.message_id
            output["pinned"] = self.pinned

        mods = []
        for mod in self.mods:
            mods.append(mod.short_name())

        output["mods"] = mods

        return output
