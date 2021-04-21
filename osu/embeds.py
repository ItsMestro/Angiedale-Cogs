import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from math import ceil

import discord
from redbot.core.utils.chat_formatting import humanize_number, humanize_timedelta, inline

from .database import Database

log = logging.getLogger("red.angiedale.osu.embeds")

EMOJI = {
    "XH": "<:SSH_Rank:794823890873483305>",
    "X": "<:SS_Rank:794823687807172608>",
    "SH": "<:SH_Rank:794823687311720450>",
    "S": "<:S_Rank:794823687492337714>",
    "A": "<:A_Rank:794823687470710815>",
    "B": "<:B_Rank:794823687446593557>",
    "C": "<:C_Rank:794823687488012308>",
    "D": "<:F_Rank:794823687781613609>",
    "F": "<:F_Rank:794823687781613609>",
    "BPM": "<:BPM:833130972668493824>",
}


class Data:
    """Simplifies api data handling."""

    def __init__(self):
        self.db = Database()

    def topdata(self, d):
        """/users/{user}/scores"""

        data = []
        index = 0

        for s in d:
            score = self.scoredata(s)

            score["index"] = index
            index += 1

            data.append(score)

        return data

    async def recentdata(self, d):
        """/users/{user}/scores"""

        data = self.scoredata(d)
        data["extra_data"] = await self.db.extra_beatmap_info(data)
        return data

    def scoredata(self, s):
        """/users/{user}/scores"""

        score = {}

        score["mods"] = s["mods"]
        score["scorepp"] = s["pp"]
        score["accuracy"] = s["accuracy"]
        score["score"] = s["score"]
        score["combo"] = s["max_combo"]
        score["scorerank"] = s["rank"]
        score["played"] = s["created_at"]
        score["scoregeki"] = s["statistics"]["count_geki"]
        score["score300"] = s["statistics"]["count_300"]
        score["scorekatu"] = s["statistics"]["count_katu"]
        score["score100"] = s["statistics"]["count_100"]
        score["score50"] = s["statistics"]["count_50"]
        score["scoremiss"] = s["statistics"]["count_miss"]
        try:
            score["weightpercentage"] = s["weight"]["percentage"]
            score["weightpp"] = s["weight"]["pp"]
        except:
            pass
        score["mode"] = s["mode"]

        score = {**score, **Data.userbasicdata(s["user"])}

        score["mapmode"] = s["beatmap"]["mode_int"]
        score["version"] = s["beatmap"]["version"]
        score["mapurl"] = s["beatmap"]["url"]
        score["mapid"] = s["beatmap"]["id"]
        score["sr"] = s["beatmap"]["difficulty_rating"]
        score["ar"] = s["beatmap"]["ar"]
        score["cs"] = s["beatmap"]["cs"]
        score["hp"] = s["beatmap"]["drain"]
        score["od"] = s["beatmap"]["accuracy"]
        score["bpm"] = s["beatmap"]["bpm"]
        score["circles"] = s["beatmap"]["count_circles"]
        score["sliders"] = s["beatmap"]["count_sliders"]
        score["spinners"] = s["beatmap"]["count_spinners"]
        score["updated"] = s["beatmap"]["last_updated"]

        try:
            score["title"] = s["beatmapset"]["title"]
            score["artist"] = s["beatmapset"]["artist"]
            score["creator"] = s["beatmapset"]["creator"]
            score["creatorid"] = s["beatmapset"]["user_id"]
            score["status"] = s["beatmapset"]["status"]
            score["setid"] = s["beatmapset"]["id"]
        except:
            pass

        return score

    def mapdata(self, d):
        """/beatmaps/{map}"""

        data = {}

        data["version"] = d["version"]
        data["mapurl"] = d["url"]
        data["mapmode"] = d["mode_int"]
        data["sr"] = d["difficulty_rating"]
        data["maxcombo"] = d["max_combo"]
        data["circles"] = d["count_circles"]
        data["sliders"] = d["count_sliders"]
        data["spinners"] = d["count_spinners"]
        data["ar"] = d["ar"]
        data["cs"] = d["cs"]
        data["hp"] = d["drain"]
        data["od"] = d["accuracy"]
        data["bpm"] = d["bpm"]
        data["length"] = d["total_length"]
        data["draintime"] = d["hit_length"]
        data["mode"] = d["mode"]

        data["artist"] = d["beatmapset"]["artist"]
        data["title"] = d["beatmapset"]["title"]
        data["creator"] = d["beatmapset"]["creator"]
        data["creatorid"] = d["beatmapset"]["user_id"]
        data["status"] = d["beatmapset"]["status"]
        data["setid"] = d["beatmapset"]["id"]
        data["favouritecount"] = d["beatmapset"]["favourite_count"]
        data["source"] = d["beatmapset"]["source"]
        data["tags"] = d["beatmapset"]["tags"]
        data["ratings"] = list(d["beatmapset"]["ratings"])
        data["submitted"] = d["beatmapset"]["submitted_date"]
        data["updated"] = d["beatmapset"]["last_updated"]
        data["playcount"] = d["beatmapset"]["play_count"]
        data["hasvideo"] = d["beatmapset"]["video"]

        return data

    def changelogdata(self, d):
        """/changelog"""

        data = []

        for c in d:
            cl = {}
            en = []

            cl["streamname"] = c["update_stream"]["name"]
            cl["users"] = c["users"]
            cl["builddisplayname"] = c["update_stream"]["display_name"]
            cl["displayversion"] = c["display_version"]
            cl["posted"] = c["created_at"]
            cl["version"] = c["version"]

            for e in c["changelog_entries"]:
                et = {}

                et["prid"] = e["github_pull_request_id"]
                et["repo"] = e["repository"]
                et["giturl"] = e["github_url"]
                et["userurl"] = e["github_user"]["user_url"]
                et["gitusername"] = e["github_user"]["display_name"]
                et["gituserurl"] = e["github_user"]["github_url"]
                et["major"] = e["major"]
                et["title"] = e["title"]
                et["category"] = e["category"]

                en.append(et)

            cl["entry"] = en

            data.append(cl)

        return data

    def rankingsdata(self, d):
        """/users"""

        data = {}

        data = {**data, **Data.userbasicdata(d["user"])}

        data["pp"] = d["pp"]
        data["accuracy"] = d["hit_accuracy"]
        data["playcount"] = d["play_count"]
        data["rankedscore"] = d["ranked_score"]

        return data

    def userbasicdata(d):
        data = {}

        data["username"] = d["username"]
        data["userflag"] = d["country_code"]
        data["userid"] = d["id"]
        try:
            data["countryname"] = d["country"]["name"]
        except:
            pass

        return data

    def userdata(self, d):

        data = {}

        data = {**data, **Data.userbasicdata(d)}

        data["lastonline"] = d["last_visit"]
        data["mappingfollowers"] = d["mapping_follower_count"]
        data["firstscores"] = d["scores_first_count"]
        data["kudosutotal"] = d["kudosu"]["total"]
        data["achievements"] = d["user_achievements"]
        data["followers"] = d["follower_count"]
        data["joined"] = d["join_date"]
        try:
            data["rankhistory"] = d["rank_history"]["data"]
        except:
            pass
        data["online"] = d["is_online"]

        data["graveyardedmaps"] = d["graveyard_beatmapset_count"]
        data["rankedapprovedmaps"] = d["ranked_and_approved_beatmapset_count"]
        data["lovedmaps"] = d["loved_beatmapset_count"]
        data["unrankedmaps"] = d["unranked_beatmapset_count"]
        data["favouritemaps"] = d["favourite_beatmapset_count"]

        data["globalrank"] = d["statistics"]["global_rank"]
        try:
            data["countryrank"] = d["statistics"]["country_rank"]
        except:
            data["countryrank"] = 0
        try:
            data["globalrank4k"] = d["statistics"]["variants"][0]["global_rank"]
            data["globalrank7k"] = d["statistics"]["variants"][1]["global_rank"]
            data["countryrank4k"] = d["statistics"]["variants"][0]["country_rank"]
            data["countryrank7k"] = d["statistics"]["variants"][1]["country_rank"]
            data["pp4k"] = d["statistics"]["variants"][0]["pp"]
            data["pp7k"] = d["statistics"]["variants"][1]["pp"]
        except:
            pass

        data["accuracy"] = d["statistics"]["hit_accuracy"]
        data["playcount"] = d["statistics"]["play_count"]
        data["maxcombo"] = d["statistics"]["maximum_combo"]
        data["levelcurrent"] = d["statistics"]["level"]["current"]
        data["levelprogress"] = d["statistics"]["level"]["progress"]
        data["pp"] = d["statistics"]["pp"]
        data["rankedscore"] = d["statistics"]["ranked_score"]
        data["totalscore"] = d["statistics"]["total_score"]
        data["replayswatched"] = d["statistics"]["replays_watched_by_others"]
        data["playtime"] = d["statistics"]["play_time"]
        data["totalhits"] = d["statistics"]["total_hits"]

        data["gradess"] = d["statistics"]["grade_counts"]["ss"]
        data["gradessh"] = d["statistics"]["grade_counts"]["ssh"]
        data["grades"] = d["statistics"]["grade_counts"]["s"]
        data["gradesh"] = d["statistics"]["grade_counts"]["sh"]
        data["gradea"] = d["statistics"]["grade_counts"]["a"]

        return data


class Embed(Data):
    """Puts data into embeds. Because why not."""

    async def ppembed(self, ctx, data, pp_num):
        d = self.topdata(data)

        pp_list = []
        for s in d:
            pp_list.append(s["scorepp"])
        pp_average = sum(pp_list) / len(pp_list)
        pp_median = pp_list[round((len(pp_list) - 1) / 2)]

        mode = d[0]["mode"]
        if mode == "osu":
            mode = "standard"
        elif mode == "fruits":
            mode = "catch"
        mode = mode.capitalize()

        embed_list = []
        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        if pp_num:
            count = 0
            for s in d:
                if s["scorepp"] > pp_num:
                    count += 1
            embed.title = f"You have {count} plays above {round(pp_num, 2)}pp"

        embed.set_author(
            name=f'{d[0]["username"]} | osu!{mode}',
            url=f'https://osu.ppy.sh/users/{d[0]["userid"]}',
            icon_url=f'https://osu.ppy.sh/images/flags/{d[0]["userflag"]}.png',
        )

        embed.set_thumbnail(url=f'https://a.ppy.sh/{d[0]["userid"]}')

        embed.add_field(
            name="Highest pp Play", value=humanize_number(round(d[0]["scorepp"], 2)), inline=True
        )
        embed.add_field(
            name="Lowest pp Play", value=humanize_number(round(d[-1]["scorepp"], 2)), inline=True
        )
        embed.add_field(
            name="Average / Median",
            value=f"{humanize_number(round(pp_average,2))} / {humanize_number(round(pp_median,2))}",
            inline=True,
        )

        embed_list.append(embed)

        return embed_list

    async def topembed(self, ctx, data, recent, pos):
        d = self.topdata(data)

        recent_text = "Top"
        if recent == True:
            d = sorted(d, key=lambda item: item["played"], reverse=True)
            recent_text = "Most recent top"

        author_text = "plays"
        if pos:
            author_text = "#" + str(pos)

        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        base_embed.set_author(
            name=f'{recent_text} {author_text} for {d[0]["username"]} | osu!{d[0]["mode"].capitalize()}',
            url=f'https://osu.ppy.sh/users/{d[0]["userid"]}',
            icon_url=f'https://osu.ppy.sh/images/flags/{d[0]["userflag"]}.png',
        )

        base_embed.set_thumbnail(url=f'https://a.ppy.sh/{d[0]["userid"]}')

        if pos:
            map = d[pos - 1]

            mods = ""
            if map["mods"]:
                mods = f' +{mods.join(map["mods"])}'

            date = datetime.now() - datetime.strptime(
                map["played"], "%Y-%m-%dT%H:%M:%S%z"
            ).replace(tzinfo=None)
            time = re.split(r",\s", humanize_timedelta(timedelta=date))
            try:
                time = f"{time[0]} {time[1]}"
            except ValueError:
                pass
            except IndexError:
                time = time[0]

            if map["mapmode"] == 3:
                version = re.sub(r"^\S*\s", "", map["version"])
                hits = f'{humanize_number(map["scoregeki"])}/{humanize_number(map["score300"])}/{humanize_number(map["scorekatu"])}/{humanize_number(map["score100"])}/{humanize_number(map["score50"])}/{humanize_number(map["scoremiss"])}'
            else:
                version = map["version"]
                hits = f'{humanize_number(map["score300"])}/{humanize_number(map["score100"])}/{humanize_number(map["score50"])}/{humanize_number(map["scoremiss"])}'

            embed = base_embed.copy()

            embed.description = (
                f'**{map["index"] + 1}. [{map["title"]} - [{version}]]({map["mapurl"]}){mods}** [{map["sr"]}★]\n'
                f'{EMOJI[map["scorerank"]]} **{humanize_number(round(map["scorepp"],2))}pp** ◈ ({"{:.2%}".format(map["accuracy"])}) ◈ {humanize_number(map["score"])}\n'
                f'**{humanize_number(map["combo"])}x** ◈ [{hits}] ◈ {time} ago'
            )

            embed.set_footer(
                text=f'Weighted pp | {round(map["weightpp"],1)}pp ({round(map["weightpercentage"],1)}%)'
            )

            embed_list.append(embed)
        else:
            page_num = 1
            while page_num <= ceil(len(d) / 5):
                start_index = (page_num - 1) * 5
                end_index = (page_num - 1) * 5 + 5
                maps = ""
                for map in d[start_index:end_index]:
                    mods = ""
                    if map["mods"]:
                        mods = f' +{mods.join(map["mods"])}'

                    date = datetime.now() - datetime.strptime(
                        map["played"], "%Y-%m-%dT%H:%M:%S%z"
                    ).replace(tzinfo=None)
                    time = re.split(r",\s", humanize_timedelta(timedelta=date))
                    try:
                        time = f"{time[0]} {time[1]}"
                    except ValueError:
                        pass
                    except IndexError:
                        time = time[0]

                    if map["mapmode"] == 3:
                        version = re.sub(r"^\S*\s", "", map["version"])
                        hits = f'{humanize_number(map["scoregeki"])}/{humanize_number(map["score300"])}/{humanize_number(map["scorekatu"])}/{humanize_number(map["score100"])}/{humanize_number(map["score50"])}/{humanize_number(map["scoremiss"])}'
                    else:
                        version = map["version"]
                        hits = f'{humanize_number(map["score300"])}/{humanize_number(map["score100"])}/{humanize_number(map["score50"])}/{humanize_number(map["scoremiss"])}'

                    maps += (
                        f'**{map["index"] + 1}. [{map["title"]} - [{version}]]({map["mapurl"]}){mods}** [{map["sr"]}★]\n'
                        f'{EMOJI[map["scorerank"]]} **{humanize_number(round(map["scorepp"],2))}pp** ◈ ({"{:.2%}".format(map["accuracy"])}) ◈ {humanize_number(map["score"])}\n'
                        f'**{humanize_number(map["combo"])}x** ◈ [{hits}] ◈ {time} ago\n\n'
                    )

                embed = base_embed.copy()

                embed.description = maps

                embed.set_footer(text=f"Page {page_num}/{ceil(len(d) / 5)}")

                embed_list.append(embed)
                page_num += 1

        return embed_list

    async def topcompareembed(self, ctx, adata, udata):
        ad = self.topdata(adata)
        ud = self.topdata(udata)

        for ascore in enumerate(ad):
            for uscore in enumerate(ud):
                if ud[uscore[0]]["mapid"] == ad[ascore[0]]["mapid"]:
                    ud.pop(uscore[0])
                    break
        d = ud

        if len(d) > 0:
            embed_list = []
            base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

            base_embed.set_author(
                name=f'Comparing unique top plays for {d[0]["username"]} | osu!{d[0]["mode"].capitalize()}',
                url=f'https://osu.ppy.sh/users/{d[0]["userid"]}',
                icon_url=f'https://osu.ppy.sh/images/flags/{d[0]["userflag"]}.png',
            )

            base_embed.set_thumbnail(url=f'https://a.ppy.sh/{d[0]["userid"]}')

            page_num = 1
            while page_num <= ceil(len(d) / 5):
                start_index = (page_num - 1) * 5
                end_index = (page_num - 1) * 5 + 5
                maps = ""
                for map in d[start_index:end_index]:
                    mods = ""
                    if map["mods"]:
                        mods = f' +{mods.join(map["mods"])}'

                    date = datetime.now() - datetime.strptime(
                        map["played"], "%Y-%m-%dT%H:%M:%S%z"
                    ).replace(tzinfo=None)
                    time = re.split(r",\s", humanize_timedelta(timedelta=date))
                    try:
                        time = f"{time[0]} {time[1]}"
                    except ValueError:
                        pass
                    except IndexError:
                        time = time[0]

                    if map["mapmode"] == 3:
                        version = re.sub(r"^\S*\s", "", map["version"])
                        hits = f'{humanize_number(map["scoregeki"])}/{humanize_number(map["score300"])}/{humanize_number(map["scorekatu"])}/{humanize_number(map["score100"])}/{humanize_number(map["score50"])}/{humanize_number(map["scoremiss"])}'
                    else:
                        version = map["version"]
                        hits = f'{humanize_number(map["score300"])}/{humanize_number(map["score100"])}/{humanize_number(map["score50"])}/{humanize_number(map["scoremiss"])}'

                    maps += (
                        f'**{map["index"] + 1}. [{map["title"]} - [{version}]]({map["mapurl"]}){mods}** [{map["sr"]}★]\n'
                        f'{EMOJI[map["scorerank"]]} **{humanize_number(round(map["scorepp"],2))}pp** ◈ ({"{:.2%}".format(map["accuracy"])}) ◈ {humanize_number(map["score"])}\n'
                        f'**{humanize_number(map["combo"])}x** ◈ [{hits}] ◈ {time} ago\n\n'
                    )

                embed = base_embed.copy()

                embed.description = maps

                embed.set_footer(
                    text=f'Page {page_num}/{ceil(len(d) / 5)} ◈ Found {len(d)} unique plays not in top 100 for {ad[0]["username"]}'
                )

                embed_list.append(embed)
                page_num += 1

            return embed_list
        else:
            return

    async def mapembed(self, ctx, data):
        d = self.mapdata(data)

        submitted = datetime.strptime(d["submitted"], "%Y-%m-%dT%H:%M:%S%z").strftime("%B %-d, %Y")
        updated = datetime.strptime(d["updated"], "%Y-%m-%dT%H:%M:%S%z").strftime("%B %-d, %Y")

        if d["mapmode"] == 3:
            max_combo = "{:.2%}".format(d["sliders"] / (d["sliders"] + d["circles"]))
            max_combo_text = "LN Ratio"
            stats = f'Notes: `{humanize_number(d["circles"])}` | Long Notes: `{humanize_number(d["sliders"])}`'
            stats2 = f'OD: `{d["od"]}` | HP: `{d["hp"]}`'
            version = re.sub(r"^\S*\s", "", d["version"])
        else:
            max_combo = humanize_number(d["maxcombo"])
            max_combo_text = "Max Combo"
            stats = f'Circles: `{humanize_number(d["circles"])}` | Sliders: `{humanize_number(d["sliders"])}` | Spinners: `{humanize_number(d["spinners"])}`'
            stats2 = f'CS: `{d["cs"]}` | AR: `{d["ar"]}` | OD: `{d["od"]}` | HP: `{d["hp"]}`'
            version = d["version"]

        draintime = time.gmtime(d["draintime"])
        if draintime[3] > 0:
            draintime = time.strftime("%-H:%M:%S", draintime)
        else:
            draintime = time.strftime("%-M:%S", draintime)

        length = time.gmtime(d["length"])
        if length[3] > 0:
            length = time.strftime("%-H:%M:%S", length)
        else:
            length = time.strftime("%-M:%S", length)

        if d["hasvideo"]:
            download = f'[Link](https://osu.ppy.sh/d/{d["setid"]}) ([No Video](https://osu.ppy.sh/d/{d["setid"]}n))'
        else:
            download = f'[Link](https://osu.ppy.sh/d/{d["setid"]})'

        embed_list = []
        embed = discord.Embed(
            color=await self.bot.get_embed_color(ctx),
            title=f'{d["artist"]} - {d["title"]} [{version}]',
            url=d["mapurl"],
        )

        embed.set_author(
            name=f'Mapped by {d["creator"]} | osu!{d["mode"].capitalize()}',
            url=f'https://osu.ppy.sh/users/{d["creatorid"]}',
            icon_url=f'https://a.ppy.sh/{d["creatorid"]}',
        )

        embed.set_footer(text=f'Status: {d["status"]}')

        embed.set_image(url=f'https://assets.ppy.sh/beatmaps/{d["setid"]}/covers/cover.jpg')

        embed.add_field(
            name="Stats",
            value=f'SR: `{d["sr"]}★` | {stats2}\n'
            f'{stats} | Total: `{d["circles"] + d["sliders"] + d["spinners"]}`',
            inline=False,
        )
        embed.add_field(name="Length / Drain", value=f"{length} / {draintime}", inline=True)
        embed.add_field(name=EMOJI["BPM"], value=d["bpm"], inline=True)
        embed.add_field(name=max_combo_text, value=max_combo, inline=True)
        embed.add_field(name="Playcount", value=humanize_number(d["playcount"]), inline=True)
        embed.add_field(name="Favorites", value=humanize_number(d["favouritecount"]), inline=True)
        embed.add_field(name="Download", value=download, inline=True)
        if not sum(d["ratings"]) == 0:
            rating = 0
            p = 0
            s = 0
            star_emojis = ""

            for i in d["ratings"]:
                rating = rating + p * i
                p += 1
            final_rating = int(rating / sum(d["ratings"]))

            while s < final_rating:
                star_emojis = star_emojis + ":star:"
                s += 1
            embed.add_field(
                name="Rating",
                value=f'{star_emojis} {round(rating / sum(d["ratings"]), 1)} / 10',
                inline=False,
            )
        embed.add_field(name="Submitted", value=submitted, inline=True)
        embed.add_field(name="Last Update", value=updated, inline=True)
        if d["source"]:
            embed.add_field(name="Source", value=d["source"], inline=True)
        else:
            embed.add_field(name="Source", value="None", inline=True)
        if d["tags"]:
            embed.add_field(name="Tags", value=f'`{d["tags"].replace(" ", "` `")}`', inline=False)

        if d["status"] == "ranked":
            status = "Ranked on"
            embed.timestamp = datetime.strptime(
                data["beatmapset"]["ranked_date"], "%Y-%m-%dT%H:%M:%S%z"
            )
        elif d["status"] == "loved":
            status = "Loved on"
            embed.timestamp = datetime.strptime(
                data["beatmapset"]["ranked_date"], "%Y-%m-%dT%H:%M:%S%z"
            )
        elif d["status"] == "wip":
            status = d["status"].upper()
        else:
            status = d["status"].capitalize()

        embed.set_footer(text=f"Status: {status}")

        embed_list.append(embed)

        return embed_list

    async def newsembed(self, ctx, data):
        posts = data["news_posts"]
        count = len(posts)

        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        for i in range(count):
            postimage = posts[i]["first_image"]
            if postimage.startswith("/"):
                postimage = f"https://osu.ppy.sh/{postimage}"

            embed = base_embed.copy()
            embed.set_image(url=postimage)
            embed.set_author(
                name=posts[i]["author"], icon_url=f"https://osu.ppy.sh/favicon-32x32.png"
            )
            embed.url = f'https://osu.ppy.sh/home/news/{posts[i]["slug"]}'
            embed.timestamp = datetime.strptime(posts[i]["published_at"], "%Y-%m-%dT%H:%M:%S%z")
            embed.title = posts[i]["title"]
            embed.description = posts[i]["preview"]
            embed.set_footer(text=f"Post {i + 1}/{len(posts)}")

            embed_list.append(embed)

        return embed_list

    async def changelogembed(self, ctx, data):
        d = self.changelogdata(data["builds"])

        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        activeusers = ""
        if not d[0]["streamname"] == "lazer" and not d[0]["streamname"] == "web":
            activeusers = f' ◈ Active users on branch: {humanize_number(d[0]["users"])}'

        base_embed.set_author(
            name=f'Changelog | {d[0]["builddisplayname"]}{activeusers}',
            icon_url="https://osu.ppy.sh/favicon-32x32.png",
        )

        page_num = 1
        for b in d:
            embed = base_embed.copy()

            embed.title = b["displayversion"]

            catfull = {}
            catshort = {}
            catmini = {}

            for e in b["entry"]:
                prlink = ""
                dev = ""

                if e["prid"]:
                    prlink = f' ([{e["repo"].replace("ppy/","")}#{e["prid"]}]({e["giturl"]}))'

                if e["userurl"]:
                    dev = f' [{e["gitusername"]}]({e["userurl"]})'
                elif e["gituserurl"]:
                    dev = f' [{e["gitusername"]}]({e["gituserurl"]})'

                titlefull = f'{e["title"]}{prlink}{dev}'
                titleshort = f'{e["title"]}{dev}'
                titlemini = f'{e["title"]}'
                if e["major"] == True:
                    titlefull = f"**{titlefull}**"
                    titleshort = f"**{titleshort}**"
                    titlemini = f"**{titlemini}**"

                if e["category"] in catfull:
                    catfull[e["category"]].append(titlefull)
                    catshort[e["category"]].append(titleshort)
                    catmini[e["category"]].append(titlemini)
                else:
                    catfull[e["category"]] = [titlefull]
                    catshort[e["category"]] = [titleshort]
                    catmini[e["category"]] = [titlemini]

            for category in catfull.items():
                entries = ""
                for item in category[1]:
                    entries = entries + f"◈ {item}\n"
                if len(entries) >= 1024:
                    entries = ""
                    for item in catshort[category[0]]:
                        entries = entries + f"◈ {item}\n"
                    if len(entries) >= 1024:
                        entries = ""
                        for item in catmini[category[0]]:
                            entries = entries + f"◈ {item}\n"
                        if len(entries) >= 1024:
                            entries = f'◈ Too big for embed. {len(category[1])} changes to {category[0]}. [Read on the site](https://osu.ppy.sh/home/changelog/{b["streamname"]}/{b["version"]})'

                embed.add_field(name=category[0], value=entries, inline=False)

            fields = [embed.title, embed.description, embed.footer.text, embed.author.name]

            fields.extend([field.name for field in embed.fields])
            fields.extend([field.value for field in embed.fields])

            total = ""
            for item in fields:
                total += str(item) if str(item) != "Embed.Empty" else ""

            if len(total) >= 6000:
                embed.clear_fields()
                for category in catshort.items():
                    entries = ""
                    for item in category[1]:
                        entries = entries + f"◈ {item}\n"
                    if len(entries) >= 1024:
                        entries = ""
                        for item in catmini[category[0]]:
                            entries = entries + f"◈ {item}\n"
                        if len(entries) >= 1024:
                            entries = f'◈ Too big for embed. {len(category[1])} changes to {category[0]}. [Read on the site](https://osu.ppy.sh/home/changelog/{b["streamname"]}/{b["version"]})'

                    embed.add_field(name=category[0], value=entries, inline=False)

                fields = [embed.title, embed.description, embed.footer.text, embed.author.name]

                fields.extend([field.name for field in embed.fields])
                fields.extend([field.value for field in embed.fields])

                total = ""
                for item in fields:
                    total += str(item) if str(item) != "Embed.Empty" else ""

                if len(total) >= 6000:
                    embed.clear_fields()

                    embed.description = f'Too big to display in discord. [Read on the site](https://osu.ppy.sh/home/changelog/{b["streamname"]}/{b["version"]})'

            embed.timestamp = datetime.strptime(b["posted"], "%Y-%m-%dT%H:%M:%S%z")

            embed.set_footer(text=f"Page {page_num}/{len(d)}")

            embed_list.append(embed)
            page_num += 1

        return embed_list

    async def rankingsembed(self, ctx, data, mode, type, country, variant):
        d = []
        for u in data["ranking"]:
            d.append(self.rankingsdata(u))

        if mode == "osu":
            mode = "standard"
        elif mode == "fruits":
            mode = "catch"
        mode = mode.capitalize()

        if variant:
            variant = f"{variant} "
        else:
            variant = ""

        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        if country:
            type = d[0]["countryname"]

            base_embed.set_thumbnail(url=f"https://osu.ppy.sh/images/flags/{country}.png")

        base_embed.set_author(
            name=f"{type.capitalize()} {variant}ranking | osu!{mode}",
            icon_url="https://osu.ppy.sh/favicon-32x32.png",
        )

        page_num = 1

        while page_num <= len(d) / 10:
            i = (page_num - 1) * 10
            user = ""
            while i < (page_num * 10):
                if country:
                    user = f'{user}\n**{i+1}.** | **{d[i]["username"]}** ◈ {humanize_number(d[i]["pp"])}pp ◈ {round(d[i]["accuracy"],2)}% ◈ {humanize_number(d[i]["playcount"])}\n'
                elif type == "score":
                    user = f'{user}\n**{i+1}.** | :flag_{d[i]["userflag"].lower()}: **{d[i]["username"]}** ◈ {humanize_number(d[i]["rankedscore"])} ◈ {round(d[i]["accuracy"],2)}% ◈ {humanize_number(d[i]["pp"])}pp\n'
                else:
                    user = f'{user}\n**{i+1}.** | :flag_{d[i]["userflag"].lower()}: **{d[i]["username"]}** ◈ {humanize_number(d[i]["pp"])}pp ◈ {round(d[i]["accuracy"],2)}% ◈ {humanize_number(d[i]["playcount"])}\n'
                i += 1

            embed = base_embed.copy()

            embed.description = user

            if type == "score":
                embed.set_footer(
                    text=f"Page {page_num}/{int(len(d) / 10)} | Username ◈ Score ◈ Accuracy ◈ pp"
                )
            else:
                embed.set_footer(
                    text=f"Page {page_num}/{int(len(d) / 10)} | Username ◈ pp ◈ Accuracy ◈ Play Count"
                )

            embed_list.append(embed)
            page_num += 1

        return embed_list

    async def profileembed(self, ctx, data, mode="osu"):
        d = self.userdata(data)

        if mode == "osu":
            mode = "standard"
        elif mode == "fruits":
            mode = "catch"

        globalrank = 0
        countryrank = 0
        if d["globalrank"]:
            globalrank = humanize_number(d["globalrank"])
            countryrank = humanize_number(d["countryrank"])

        if mode == "mania":
            if d["pp4k"] == 0 and d["pp7k"] == 0:
                performancevalue = f'{humanize_number(d["pp"])}pp'
            elif d["pp4k"] == 0:
                performancevalue = (
                    f'{humanize_number(d["pp"])}pp\n{humanize_number(d["pp7k"])}pp | **7k**'
                )
            elif d["pp7k"] == 0:
                performancevalue = (
                    f'{humanize_number(d["pp"])}pp\n{humanize_number(d["pp4k"])}pp | **4k**'
                )
            else:
                performancevalue = f'{humanize_number(d["pp"])}pp\n{humanize_number(d["pp4k"])}pp | **4k**\n{humanize_number(d["pp7k"])}pp | **7k**'

            if d["globalrank4k"] == None and d["globalrank7k"] == None:
                rankingvalue = f'#{globalrank} ({d["userflag"].upper()} #{countryrank})'
            elif d["globalrank4k"] == None:
                rankingvalue = f'#{globalrank} ({d["userflag"].upper()} #{countryrank})\n#{humanize_number(d["globalrank7k"])} ({d["userflag"].upper()} #{humanize_number(d["countryrank7k"])}) | **7k**'
            elif d["globalrank7k"] == None:
                rankingvalue = f'#{globalrank} ({d["userflag"].upper()} #{countryrank})\n#{humanize_number(d["globalrank4k"])} ({d["userflag"].upper()} #{humanize_number(d["countryrank4k"])}) | **4k**'
            else:
                rankingvalue = f'#{globalrank} ({d["userflag"].upper()} #{countryrank})\n#{humanize_number(d["globalrank4k"])} ({d["userflag"].upper()} #{humanize_number(d["countryrank4k"])}) | **4k**\n#{humanize_number(d["globalrank7k"])} ({d["userflag"].upper()} #{humanize_number(d["countryrank7k"])}) | **7k**'
        else:
            performancevalue = f'{humanize_number(d["pp"])}pp'
            rankingvalue = f'#{globalrank} ({d["userflag"].upper()} #{countryrank})'

        if d["playtime"]:
            playtime = re.split(
                r",\s", humanize_timedelta(timedelta=timedelta(seconds=d["playtime"]))
            )
            try:
                playtime = f"{playtime[0]}, {playtime[1]}, {playtime[2]}"
            except IndexError:
                try:
                    playtime = f"{playtime[0]}, {playtime[1]}"
                except IndexError:
                    try:
                        playtime = f"{playtime[0]}"
                    except IndexError:
                        playtime = "0"
        else:
            playtime = "None"

        joindate = datetime.strptime(d["joined"], "%Y-%m-%dT%H:%M:%S%z")
        joindate = joindate.strftime("%B %-d, %Y")

        try:
            rankhistory = list(map(int, d["rankhistory"]))
            rankhistory = (
                f"``` Delta |   Rank   | Date\n"
                f"-----------------------\n"
                f'   -   |{"{0:^10}".format(humanize_number(rankhistory[0]))}| -90d\n'
                f'{"{0:^7}".format(humanize_number(rankhistory[0] - rankhistory[14]))}|{"{0:^10}".format(humanize_number(rankhistory[14]))}| -75d\n'
                f'{"{0:^7}".format(humanize_number(rankhistory[14] - rankhistory[29]))}|{"{0:^10}".format(humanize_number(rankhistory[29]))}| -60d\n'
                f'{"{0:^7}".format(humanize_number(rankhistory[29] - rankhistory[44]))}|{"{0:^10}".format(humanize_number(rankhistory[44]))}| -45d\n'
                f'{"{0:^7}".format(humanize_number(rankhistory[44] - rankhistory[59]))}|{"{0:^10}".format(humanize_number(rankhistory[59]))}| -30d\n'
                f'{"{0:^7}".format(humanize_number(rankhistory[59] - rankhistory[74]))}|{"{0:^10}".format(humanize_number(rankhistory[74]))}| -15d\n'
                f'{"{0:^7}".format(humanize_number(rankhistory[74] - rankhistory[89]))}|{"{0:^10}".format(humanize_number(rankhistory[89]))}|  Now```'
            )
        except (TypeError, KeyError):
            rankhistory = "This user doesn't have any rank history."

        embed_list = []
        base_embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        base_embed.set_author(
            name=f'{d["username"]} | osu!{mode.capitalize()}',
            url=f'https://osu.ppy.sh/users/{d["userid"]}',
            icon_url=f'https://osu.ppy.sh/images/flags/{d["userflag"]}.png',
        )

        base_embed.set_thumbnail(url=f'https://a.ppy.sh/{d["userid"]}')

        page = 1

        while page <= 2:
            embed = base_embed.copy()

            embed.clear_fields()

            embed.add_field(name="Ranking", value=rankingvalue, inline=True)
            embed.add_field(name="Performance", value=performancevalue, inline=True)
            embed.add_field(name="Accuracy", value=f'{round(d["accuracy"], 2)}%', inline=True)
            embed.add_field(
                name="Level", value=f'{d["levelcurrent"]} ({d["levelprogress"]}%)', inline=True
            )
            embed.add_field(name="Max Combo", value=humanize_number(d["maxcombo"]), inline=True)
            embed.add_field(name="Playcount", value=humanize_number(d["playcount"]), inline=True)
            embed.add_field(
                name="Grades",
                value=f'{EMOJI["XH"]} {d["gradessh"]} {EMOJI["X"]} {d["gradess"]} {EMOJI["SH"]} {d["gradesh"]} {EMOJI["S"]} {d["grades"]} {EMOJI["A"]} {d["gradea"]}',
                inline=False,
            )

            if page >= 2:
                embed.add_field(
                    name="Ranked Score", value=humanize_number(d["rankedscore"]), inline=True
                )
                embed.add_field(
                    name="#1 Scores", value=humanize_number(d["firstscores"]), inline=True
                )
                embed.add_field(name="Play Time", value=playtime, inline=True)
                embed.add_field(
                    name="Total Score", value=humanize_number(d["totalscore"]), inline=True
                )
                embed.add_field(
                    name="Replays Watched", value=humanize_number(d["replayswatched"]), inline=True
                )
                embed.add_field(name="Joined osu!", value=joindate, inline=True)
                embed.add_field(name="Rank Change", value=rankhistory, inline=False)
                embed.add_field(
                    name="Total Hits", value=humanize_number(d["totalhits"]), inline=True
                )
                embed.add_field(name="Medals", value=len(d["achievements"]), inline=True)
                embed.add_field(
                    name="Favorite Beatmaps",
                    value=humanize_number(d["favouritemaps"]),
                    inline=True,
                )
                embed.add_field(
                    name="Followers", value=humanize_number(d["followers"]), inline=True
                )
                embed.add_field(
                    name="Mapping Followers",
                    value=humanize_number(d["mappingfollowers"]),
                    inline=True,
                )
                embed.add_field(
                    name="Kudoso Total", value=humanize_number(d["kudosutotal"]), inline=True
                )
                embed.add_field(
                    name="Uploaded Beatmaps",
                    value=f'Ranked: **{d["rankedapprovedmaps"]}** ◈ Loved: **{d["lovedmaps"]}** ◈ Unranked: **{d["unrankedmaps"]}** ◈ Graveyarded: **{d["graveyardedmaps"]}**',
                    inline=False,
                )

            if data["is_online"] == True:
                embed.set_footer(text="Currently Online")
            elif not d["lastonline"]:
                embed.set_footer(text="Last Online | Unknown")
            else:
                embed.set_footer(text="Last Online")
                embed.timestamp = datetime.strptime(d["lastonline"], "%Y-%m-%dT%H:%M:%S%z")

            embed_list.append(embed)
            page += 1

        return embed_list

    async def recentembed(self, ctx, data, page, mapdata=None):
        if mapdata:
            m = self.mapdata(mapdata)
            da = self.topdata(data)[0]
            embeddata = {**m, **da}
        else:
            embeddata = await self.recentdata(data[page])

        mode = embeddata["mode"]
        if embeddata["mode"] == "osu":
            mode = "standard"
        elif embeddata["mode"] == "fruits":
            mode = "catch"

        if embeddata["mode"] == "mania":
            comboratio = "Combo / Ratio"
            version = re.sub(r"^\S*\s", "", embeddata["version"])
            try:
                ratio = round(embeddata["scoregeki"] / embeddata["score300"], 2)
            except:
                ratio = "Perfect"
            combo = f'**{embeddata["combo"]:,}x** / {ratio}'
            hits = f'{humanize_number(embeddata["scoregeki"])}/{humanize_number(embeddata["score300"])}/{humanize_number(embeddata["scorekatu"])}/{humanize_number(embeddata["score100"])}/{humanize_number(embeddata["score50"])}/{humanize_number(embeddata["scoremiss"])}'
            stats = f'OD: `{embeddata["od"]}` | HP: `{embeddata["hp"]}`'
        else:
            version = embeddata["version"]
            comboratio = "Combo"
            combo = f'**{embeddata["combo"]:,}x**'
            hits = f'{humanize_number(embeddata["score300"])}/{humanize_number(embeddata["score100"])}/{humanize_number(embeddata["score50"])}/{humanize_number(embeddata["scoremiss"])}'
            stats = f'CS: `{embeddata["cs"]}` | AR: `{embeddata["ar"]}` | OD: `{embeddata["od"]}` | HP: `{embeddata["hp"]}`'

        if embeddata["scorerank"] == "F":
            if embeddata["mode"] == "osu":
                failpoint = (
                    embeddata["score300"]
                    + embeddata["score100"]
                    + embeddata["score50"]
                    + embeddata["scoremiss"]
                    - 1
                )
            elif embeddata["mode"] == "fruits":
                failpoint = (
                    embeddata["scoregeki"]
                    + embeddata["score300"]
                    + embeddata["scorekatu"]
                    + embeddata["score100"]
                    + embeddata["score50"]
                    + embeddata["scoremiss"]
                    - 1
                )
            elif embeddata["mode"] == "taiko":
                taikohitobjects = embeddata["extra_data"]["Hitobjects"]
                for object, item in enumerate(embeddata["extra_data"]["Hitobjects"]):
                    if (
                        item["type"] == "8"
                        or item["type"] == "12"
                        or item["type"] == "2"
                        or item["type"] == "6"
                    ):
                        taikohitobjects.pop(object)
                    embeddata["extra_data"]["Hitobjects"] = taikohitobjects
                failpoint = (
                    embeddata["scoregeki"]
                    + embeddata["score300"]
                    + embeddata["scorekatu"]
                    + embeddata["score100"]
                    + embeddata["score50"]
                    + embeddata["scoremiss"]
                    - 1
                )
            elif embeddata["mode"] == "mania":
                failpoint = (
                    embeddata["scoregeki"]
                    + embeddata["score300"]
                    + embeddata["scorekatu"]
                    + embeddata["score100"]
                    + embeddata["score50"]
                    + embeddata["scoremiss"]
                    - 1
                )

            mapstart = int(embeddata["extra_data"]["Hitobjects"][0]["time"])
            mapend = int(embeddata["extra_data"]["Hitobjects"][-1]["time"])
            mapfail = int(embeddata["extra_data"]["Hitobjects"][failpoint]["time"])
            failedat = "{:.2%}".format((mapfail - mapstart) / (mapend - mapstart))

        mods = ""
        if embeddata["mods"]:
            mods = mods.join(embeddata["mods"])
            mods = f" +{mods}"

        try:
            performance = humanize_number(round(embeddata["scorepp"], 2))
        except TypeError:
            performance = 0

        embed_out = []

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx))

        embed.set_author(
            name=f'{embeddata["artist"]} - {embeddata["title"]} [{version}] [{str(embeddata["sr"])}★]',
            url=embeddata["mapurl"],
            icon_url=f'https://a.ppy.sh/{embeddata["userid"]}',
        )

        if embeddata["scorerank"] == "F":
            embed.title = f"Failed at {failedat}"
        else:
            embed.title = "Passed"

        embed.set_image(
            url=f'https://assets.ppy.sh/beatmaps/{embeddata["setid"]}/covers/cover.jpg'
        )

        embed.add_field(name="Grade", value=f'{EMOJI[embeddata["scorerank"]]}{mods}', inline=True)
        embed.add_field(name="Score", value=humanize_number(embeddata["score"]), inline=True)
        embed.add_field(name="Accuracy", value="{:.2%}".format(embeddata["accuracy"]), inline=True)
        embed.add_field(name="PP", value=f"**{performance}pp**", inline=True)
        embed.add_field(name=comboratio, value=combo, inline=True)
        embed.add_field(name="Hits", value=hits, inline=True)
        embed.add_field(
            name="Map Info",
            value=f'Mapper: [{embeddata["creator"]}](https://osu.ppy.sh/users/{embeddata["creatorid"]}) | {EMOJI["BPM"]} `{embeddata["bpm"]}` | Objects: `{humanize_number(embeddata["circles"] + embeddata["sliders"] + embeddata["spinners"])}` \n'
            f'Status: {inline(embeddata["status"].capitalize())} | {stats}',
            inline=False,
        )

        embed.set_footer(
            text=f'Play {page + 1}/{len(data)} | {embeddata["username"]} | osu!{mode.capitalize()} | Played'
        )

        embed.timestamp = datetime.strptime(embeddata["played"], "%Y-%m-%dT%H:%M:%S%z")

        embed_out.append(embed)

        return embed_out

    async def trackingembed(self, channels, udata, ndata):
        for p in enumerate(udata):
            for o in enumerate(ndata):
                if ndata[o[0]]["mapid"] == udata[p[0]]["mapid"]:
                    ndata[o[0]]["oldpp"] = udata[p[0]]["scorepp"]
                    ndata[o[0]]["oldindex"] = udata[p[0]]["index"]
                    ndata[o[0]]["oldacc"] = udata[p[0]]["accuracy"]
                    if ndata[o[0]]["played"] == udata[p[0]]["played"]:
                        ndata.pop(o[0])
                        break

        badchannels = []
        for d in ndata:
            if d["mode"] == "osu":
                mode = "standard"
            elif d["mode"] == "fruits":
                mode = "catch"
            else:
                mode = d["mode"]

            if d["mode"] == "mania":
                comboratio = "Combo / Ratio"
                version = re.sub(r"^\S*\s", "", d["version"])
                try:
                    ratio = round(d["scoregeki"] / d["score300"], 2)
                except:
                    ratio = "Perfect"
                combo = f'**{d["combo"]:,}x** / {ratio}'
                hits = f'{humanize_number(d["scoregeki"])}/{humanize_number(d["score300"])}/{humanize_number(d["scorekatu"])}/{humanize_number(d["score100"])}/{humanize_number(d["score50"])}/{humanize_number(d["scoremiss"])}'
                stats = f'OD: `{d["od"]}` | HP: `{d["hp"]}`'
            else:
                version = d["version"]
                comboratio = "Combo"
                combo = f'**{d["combo"]:,}x**'
                hits = f'{humanize_number(d["score300"])}/{humanize_number(d["score100"])}/{humanize_number(d["score50"])}/{humanize_number(d["scoremiss"])}'
                stats = f'CS: `{d["cs"]}` | AR: `{d["ar"]}` | OD: `{d["od"]}` | HP: `{d["hp"]}`'

            mods = ""
            if d["mods"]:
                mods = mods.join(d["mods"])
                mods = f" +{mods}"

            ppaddon = ""
            accaddon = ""
            embedtitle = f'New #{d["index"] + 1} for {d["username"]}'
            try:
                performance = f'{humanize_number(round(d["scorepp"], 2))}'
            except TypeError:
                performance = 0
            try:
                if d["oldpp"]:
                    checkedpp = round(d["scorepp"] - d["oldpp"], 2)
                    checkedacc = d["accuracy"] - d["oldacc"]
                    ppaddon = f" ({humanize_number(checkedpp)})"
                    accaddon = f' ({"{:.2%}".format(checkedacc)})'
                    if checkedpp > 0:
                        ppaddon = f" (+{humanize_number(checkedpp)})"
                    if checkedacc > 0:
                        accaddon = f' (+{"{:.2%}".format(checkedacc)})'
                    if d["index"] > d["oldindex"]:
                        embedtitle = f'Improved #{d["index"] + 1} from #{d["oldindex"] + 1} for {d["username"]}'
                    else:
                        embedtitle = f'Changed #{d["index"] + 1} from #{d["oldindex"] + 1} for {d["username"]}'
            except KeyError:
                pass

            embed = discord.Embed(color=0)

            embed.set_author(
                name=f'{d["artist"]} - {d["title"]} [{version}] [{str(d["sr"])}★]',
                url=d["mapurl"],
                icon_url=f'https://a.ppy.sh/{d["userid"]}',
            )

            embed.title = embedtitle

            embed.set_image(url=f'https://assets.ppy.sh/beatmaps/{d["setid"]}/covers/cover.jpg')

            embed.add_field(name="Grade", value=f'{EMOJI[d["scorerank"]]}{mods}', inline=True)
            embed.add_field(name="Score", value=humanize_number(d["score"]), inline=True)
            embed.add_field(
                name="Accuracy", value="{:.2%}{}".format(d["accuracy"], accaddon), inline=True
            )
            embed.add_field(name="PP", value=f"**{performance}pp{ppaddon}**", inline=True)
            embed.add_field(name=comboratio, value=combo, inline=True)
            embed.add_field(name="Hits", value=hits, inline=True)
            embed.add_field(
                name="Map Info",
                value=f'Mapper: [{d["creator"]}](https://osu.ppy.sh/users/{d["creatorid"]}) | {EMOJI["BPM"]} `{d["bpm"]}` | Objects: `{humanize_number(d["circles"] + d["sliders"] + d["spinners"])}` \n'
                f'Status: {inline(d["status"].capitalize())} | {stats}',
                inline=False,
            )

            embed.set_footer(text=f'{d["username"]} | osu!{mode.capitalize()} | Played')

            embed.timestamp = datetime.strptime(d["played"], "%Y-%m-%dT%H:%M:%S%z")

            for channelid in channels:
                channel = self.bot.get_channel(channelid)
                if channel:
                    embed.color = await self.bot.get_embed_color(channel)
                    try:
                        await channel.send(embed=embed)
                    except discord.errors.Forbidden:
                        pass
                    await asyncio.sleep(1)
                else:
                    badchannels.append(channelid)
            await asyncio.sleep(1)

        return badchannels
