import codecs
import logging
from datetime import datetime, timezone
from typing import Dict, List, Union

log = logging.getLogger("red.angiedale.osu")


class ObjectType:
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        """Compares the ``value`` of each object"""
        if not isinstance(other, ObjectType):
            return False

        temp_value = self.value

        for i, v in enumerate(reversed(range(8))):
            value = bool(temp_value >> v)
            temp_value -= 1 << v if value else 0
            if i == other.value:
                break

        return value

    def __hash__(self):
        return hash(self.value)

    def __str__(self):
        return str(self.value)

    def __contains__(self, other):
        return bool(self.value & other.value)


class HitObjectType(ObjectType):  # TODO: Actually figure all this stuff out
    CIRCLE = ObjectType(0)
    SLIDER = ObjectType(1)
    NEW_COMBO = ObjectType(2)
    SPINNER = ObjectType(3)
    COMBO_SKIP_ONE = ObjectType(4)
    COMBO_SKIP_TWO = ObjectType(5)
    COMBO_SKIP_THREE = ObjectType(6)
    MANIALN = ObjectType(7)

    def __init__(self, type: int):
        self.value = type
        super().__init__(type)


class HitObject:
    def __init__(
        self,
        x: int,
        y: int,
        time: int,
        type: Union[HitObjectType, int],
        hitsounds: str,
    ):
        self.x = x
        self.y = y
        self.time = time
        self.hitsounds = hitsounds

        if isinstance(type, HitObjectType):
            self.type = type
        else:
            self.type = HitObjectType(type)


class DatabaseBeatmap:
    def __init__(self, data: dict = None):
        self.cachedate: datetime
        self.title: str
        self.artist: str
        self.creator: str
        self.version: str
        self.hp: float
        self.cs: float
        self.od: float
        self.ar: float
        self.sv: float
        self.tr: float
        self.hitobjects: List[HitObject] = []
        if data:
            self._init_parse(data)

    def _init_parse(self, data):
        try:
            self.cachedate = datetime.strptime(data["Cached"], "%Y-%m-%dT%H:%M:%S%z")
            self.title = data["Title"]
            self.artist = data["Artist"]
            self.creator = data["Mapper"]
            self.version = data["Version"]
            self.hp = float(data["HP"])
            self.cs = float(data["CS"])
            self.od = float(data["OD"])
            self.ar = float(data["AR"])
            self.sv = float(data["SV"])
            self.tr = float(data["TR"])

            for hb in data["Hitobjects"]:
                self.hitobjects.append(
                    HitObject(
                        int(hb["x"]),
                        int(hb["y"]),
                        int(hb["time"]),
                        int(hb["type"]),
                        hb["hitsounds"],
                    )
                )
        except Exception as e:
            log.info("Failed to parse database beatmap.", exc_info=e)
            pass

    def flatten_to_dict(self) -> Dict[str, Union[str, float, List[Dict[str, Union[int, str]]]]]:
        output = {}

        output["Cached"] = self.cachedate.strftime("%Y-%m-%dT%H:%M:%S%z")
        output["Title"] = self.title
        output["Artist"] = self.artist
        output["Mapper"] = self.creator
        output["Version"] = self.version
        output["HP"] = self.hp
        output["CS"] = self.cs
        output["OD"] = self.od
        output["AR"] = self.ar
        output["SV"] = self.sv
        output["TR"] = self.tr

        new_hitobjects: List[Dict[str, Union[int, str]]] = []
        for hb in self.hitobjects:
            new_hitobjects.append(
                {
                    "x": hb.x,
                    "y": hb.y,
                    "time": hb.time,
                    "type": hb.type.value,
                    "hitsounds": hb.hitsounds,
                }
            )

        output["Hitobjects"] = new_hitobjects

        return output


def parse_beatmap(beatmap_path: str) -> DatabaseBeatmap:
    """Custom parser for beatmap info"""

    if beatmap_path[-4:] == ".osu":
        # filename
        with codecs.open(beatmap_path, "r", "utf-8") as beatmap:
            map_lines = beatmap.readlines()
    else:
        raise ValueError("Path given was not a .osu file.")
    index = -1
    for i, line in enumerate(map_lines):
        if line.startswith("[HitObjects]"):
            index = i
            break
    if index == -1:
        raise Exception('Missing "[HitObjects]"')

    metadata = map_lines[:index]
    hitcircles = map_lines[index + 1 :]

    beatmap_info = DatabaseBeatmap()

    beatmap_info.cachedate = datetime.now(timezone.utc)

    def findline(search):
        for x in metadata:
            if x.startswith(search):
                x = x.replace(search, "", 1)

                # if x.startswith(" "):
                #     x = x[1:]

                return x[:-2]

    beatmap_info.title = findline("Title:")
    beatmap_info.artist = findline("Artist:")
    beatmap_info.creator = findline("Creator:")
    beatmap_info.version = findline("Version:")
    beatmap_info.hp = findline("HPDrainRate:")
    beatmap_info.cs = findline("CircleSize:")
    beatmap_info.od = findline("OverallDifficulty:")
    beatmap_info.ar = findline("ApproachRate:")
    beatmap_info.sv = findline("SliderMultiplier:")
    beatmap_info.tr = findline("SliderTickRate:")

    objects: List[HitObject] = []
    for x in hitcircles:
        if x.endswith("\r\n"):
            x = x[:-2]
        if not x:
            continue
        data = x.split(",", 4)
        objects.append(HitObject(int(data[0]), int(data[1]), int(data[2]), int(data[3]), data[4]))

    beatmap_info.hitobjects = objects

    return beatmap_info
