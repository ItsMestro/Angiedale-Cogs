import asyncio
import datetime
import json
import logging
import re
from random import choice

import aiohttp
import discord
from redbot.core.bot import Red
from redbot.core.utils.menus import DEFAULT_CONTROLS, commands, menu

log = logging.getLogger("red.angiedale.api")

SEARCH_ANIME_MANGA_QUERY = """
query ($id: Int, $page: Int, $search: String, $type: MediaType) {
    Page (page: $page, perPage: 10) {
        media (id: $id, search: $search, type: $type) {
            id
            idMal
            description(asHtml: false)
            title {
                english
                romaji
            }
            coverImage {
            		medium
            }
            bannerImage
            averageScore
            meanScore
            status
            episodes
            chapters
            externalLinks {
                url
                site
            }
            nextAiringEpisode {
                timeUntilAiring
            }
        }
    }
}
"""

SEARCH_CHARACTER_QUERY = """
query ($id: Int, $page: Int, $search: String) {
  Page(page: $page, perPage: 10) {
    characters(id: $id, search: $search) {
      id
      description (asHtml: true),
      name {
        first
        last
        native
      }
      image {
        large
      }
      media {
        nodes {
          id
          type
          title {
            romaji
            english
            native
            userPreferred
          }
        }
      }
    }
  }
}
"""

SEARCH_USER_QUERY = """
query ($id: Int, $page: Int, $search: String) {
    Page (page: $page, perPage: 10) {
        users (id: $id, search: $search) {
            id
            name
            siteUrl
            avatar {
                    large
            }
            about (asHtml: true),
            stats {
                watchedTime
                chaptersRead
            }
            favourites {
            manga {
              nodes {
                id
                title {
                  romaji
                  english
                  native
                  userPreferred
                }
              }
            }
            characters {
              nodes {
                id
                name {
                  first
                  last
                  native
                }
              }
            }
            anime {
              nodes {
                id
                title {
                  romaji
                  english
                  native
                  userPreferred
                }
              }
            }
            }
        }
    }
}
"""


class API(commands.Cog):
    """Search the web for information"""

    def __init__(self, bot: Red):
        self.bot = bot
        super().__init__()

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    def format_name(
        self, first_name, last_name
    ):  # Combines first_name and last_name and/or shows either of the two
        if first_name and last_name:
            return first_name + " " + last_name
        elif first_name:
            return first_name
        elif last_name:
            return last_name
        else:
            return "No name"

    def clean_html(self, description):  # Removes html tags
        if not description:
            return ""
        cleanr = re.compile("<.*?>")
        cleantext = re.sub(cleanr, "", description)
        return cleantext

    def clean_spoilers(self, description):  # Removes spoilers using the html tag given by AniList
        if not description:
            return ""
        cleanr = re.compile("/<span[^>]*>.*</span>/g")
        cleantext = re.sub(cleanr, "", description)
        return cleantext

    def description_parser(
        self, description
    ):  # Limits text to 400characters and 5 lines and adds "..." at the end
        description = self.clean_spoilers(description)
        description = self.clean_html(description)
        description = "\n".join(description.split("\n")[:5])
        if len(description) > 400:
            return description[:400] + "..."
        else:
            return description

    def list_maximum(self, items):  # Limits to 5 strings than adds "+X more"
        if len(items) > 5:
            return items[:5] + ["+ " + str(len(items) - 5) + " more"]
        else:
            return items

    async def _request(self, query, variables=None):

        if variables is None:
            variables = {}

        request_json = {"query": query, "variables": variables}

        headers = {"content-type": "application/json"}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://graphql.anilist.co", data=json.dumps(request_json), headers=headers
            ) as response:
                return await response.json()

    async def _search_anime_manga(self, ctx, cmd, entered_title):

        # Outputs MediaStatuses to strings
        MediaStatusToString = {
            # Has completed and is no longer being released
            "FINISHED": "Finished",
            # Currently releasing
            "RELEASING": "Releasing",
            # To be released at a later date
            "NOT_YET_RELEASED": "Not yet released",
            # Ended before the work could be finished
            "CANCELLED": "Cancelled",
        }

        variables = {"search": entered_title, "page": 1, "type": cmd}

        data = (await self._request(SEARCH_ANIME_MANGA_QUERY, variables))["data"]["Page"]["media"]

        if data is not None and len(data) > 0:

            # a list of embeds
            embeds = []

            for anime_manga in data:
                # Sets up various variables for Embed
                link = f"https://anilist.co/{cmd.lower()}/{anime_manga['id']}"
                description = anime_manga["description"]
                title = anime_manga["title"]["english"] or anime_manga["title"]["romaji"]
                if anime_manga.get("nextAiringEpisode"):
                    seconds = anime_manga["nextAiringEpisode"]["timeUntilAiring"]
                    time_left = str(datetime.timedelta(seconds=seconds))
                else:
                    time_left = "Never"

                external_links = ""
                for i in range(0, len(anime_manga["externalLinks"])):
                    ext_link = anime_manga["externalLinks"][i]
                    external_links += f"[{ext_link['site']}]({ext_link['url']}), "
                    if i + 1 == len(anime_manga["externalLinks"]):
                        external_links = external_links[:-2]

                embed = discord.Embed(title=title)
                embed.url = link
                embed.color = 3447003
                embed.description = self.description_parser(description)
                embed.set_thumbnail(url=anime_manga["coverImage"]["medium"])
                embed.add_field(name="Score", value=anime_manga.get("averageScore", "N/A"))
                if cmd == "ANIME":
                    embed.add_field(name="Episodes", value=anime_manga.get("episodes", "N/A"))
                    embed.set_footer(
                        text="Status : "
                        + MediaStatusToString[anime_manga["status"]]
                        + ", Next episode : "
                        + time_left
                        + ", Powered by Anilist"
                    )
                else:
                    embed.add_field(name="Chapters", value=anime_manga.get("chapters", "N/A"))
                    embed.set_footer(
                        text="Status : "
                        + MediaStatusToString.get(anime_manga.get("status"), "N/A")
                        + ", Powered by Anilist"
                    )
                if external_links:
                    embed.add_field(name="Streaming and/or Info sites", value=external_links)
                if anime_manga["bannerImage"]:
                    embed.set_image(url=anime_manga["bannerImage"])
                embed.add_field(
                    name="You can find out more",
                    value=f"[Anilist]({link}), [MAL](https://myanimelist.net/{cmd.lower()}/{anime_manga['idMal']}), Kitsu (Soon™)",
                )
                embeds.append(embed)

            return embeds, data

        else:
            return None

    async def _search_character(self, ctx, entered_title):

        variables = {"search": entered_title, "page": 1}

        data = (await self._request(SEARCH_CHARACTER_QUERY, variables))["data"]["Page"][
            "characters"
        ]

        if data is not None and len(data) > 0:

            # a list of embeds
            embeds = []

            for character in data:
                # Sets up various variables for Embed
                link = f"https://anilist.co/character/{character['id']}"
                character_anime = [
                    f'[{anime["title"]["userPreferred"]}]({"https://anilist.co/anime/" + str(anime["id"])})'
                    for anime in character["media"]["nodes"]
                    if anime["type"] == "ANIME"
                ]
                character_manga = [
                    f'[{manga["title"]["userPreferred"]}]({"https://anilist.co/manga/" + str(manga["id"])})'
                    for manga in character["media"]["nodes"]
                    if manga["type"] == "MANGA"
                ]
                embed = discord.Embed(
                    title=self.format_name(character["name"]["first"], character["name"]["last"])
                )
                embed.url = link
                embed.color = 3447003
                embed.description = self.description_parser(character["description"])
                embed.set_thumbnail(url=character["image"]["large"])
                if len(character_anime) > 0:
                    embed.add_field(
                        name="Anime", value="\n".join(self.list_maximum(character_anime))
                    )
                if len(character_manga) > 0:
                    embed.add_field(
                        name="Manga", value="\n".join(self.list_maximum(character_manga))
                    )
                embed.set_footer(text="Powered by Anilist")
                embeds.append(embed)

            return embeds, data

        else:
            return None

    async def _search_user(self, ctx, entered_title):

        variables = {"search": entered_title, "page": 1}

        data = (await self._request(SEARCH_USER_QUERY, variables))["data"]["Page"]["users"]

        if data is not None and len(data) > 0:

            # a list of embeds
            embeds = []

            for user in data:
                # Sets up various variables for Embed
                link = f"https://anilist.co/user/{user['id']}"
                title = f"[{user['name']}]({link})"
                title = user["name"]

                embed = discord.Embed(title=title)
                embed.url = link
                embed.color = 3447003
                embed.description = self.description_parser(user["about"])
                embed.set_thumbnail(url=user["avatar"]["large"])
                embed.add_field(
                    name="Watched time",
                    value=datetime.timedelta(minutes=int(user["stats"]["watchedTime"])),
                )
                embed.add_field(
                    name="Chapters read", value=user["stats"].get("chaptersRead", "N/A")
                )
                for category in "anime", "manga", "characters":
                    fav = []
                    for node in user["favourites"][category]["nodes"]:
                        url_path = category
                        if category == "characters":
                            name = node["name"]
                            title = self.format_name(name["first"], name["last"])
                            url_path = "character"  # without the s
                        else:
                            title = node["title"]["userPreferred"]

                        fav.append(f'[{title}](https://anilist.co/{url_path}/{node["id"]})')

                    if fav:
                        embed.add_field(
                            name=f"Favorite {category}", value="\n".join(self.list_maximum(fav))
                        )
                embed.set_footer(text="Powered by Anilist")
                embeds.append(embed)

            return embeds, data

        else:
            return None

    @commands.group()
    async def anilist(self, ctx: commands.Context):
        """Search Anilist for things."""

    @anilist.command(name="anime")
    @commands.bot_has_permissions(embed_links=True, add_reactions=True)
    async def anilist_anime(self, ctx, *, entered_title):
        """Searches for anime using Anilist"""

        try:
            cmd = "ANIME"
            embeds, data = await self._search_anime_manga(ctx, cmd, entered_title)

            if embeds is not None:
                await menu(
                    ctx, pages=embeds, controls=DEFAULT_CONTROLS, message=None, page=0, timeout=30
                )
            else:
                await ctx.send("No anime was found or there was an error in the process")

        except TypeError:
            await ctx.send("No anime was found or there was an error in the process")

    @anilist.command(name="manga")
    @commands.bot_has_permissions(embed_links=True, add_reactions=True)
    async def anilist_manga(self, ctx, *, entered_title):
        """Searches for manga using Anilist"""

        try:
            cmd = "MANGA"
            embeds, data = await self._search_anime_manga(ctx, cmd, entered_title)

            if embeds is not None:
                await menu(
                    ctx, pages=embeds, controls=DEFAULT_CONTROLS, message=None, page=0, timeout=30
                )
            else:
                await ctx.send("No mangas were found or there was an error in the process")

        except TypeError:
            await ctx.send("No mangas were found or there was an error in the process")

    @anilist.command(name="character")
    async def anilist_character(self, ctx, *, entered_title):
        """Searches for characters using Anilist"""

        try:
            embeds, data = await self._search_character(ctx, entered_title)

            if embeds is not None:
                await menu(
                    ctx, pages=embeds, controls=DEFAULT_CONTROLS, message=None, page=0, timeout=30
                )
            else:
                await ctx.send("No characters were found or there was an error in the process")

        except TypeError:
            await ctx.send("No characters were found or there was an error in the process")

    @anilist.command(name="user")
    @commands.bot_has_permissions(embed_links=True, add_reactions=True)
    async def anilist_user(self, ctx, *, entered_title):
        """Searches users using Anilist"""

        try:
            embeds, data = await self._search_user(ctx, entered_title)

            if embeds is not None:
                await menu(
                    ctx, pages=embeds, controls=DEFAULT_CONTROLS, message=None, page=0, timeout=30
                )
            else:
                await ctx.send("No users were found or there was an error in the process")

        except TypeError:
            await ctx.send("No users were found or there was an error in the process")

    @commands.command()
    async def urban(self, ctx, *, word):
        """Search the Urban Dictionary.

        This uses the unofficial Urban Dictionary API.
        """

        try:
            url = "https://api.urbandictionary.com/v0/define"

            params = {"term": str(word).lower()}

            headers = {"content-type": "application/json"}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as response:
                    data = await response.json()

        except aiohttp.ClientError:
            await ctx.send(
                ("No Urban Dictionary entries were found, or there was an error in the process.")
            )
            return

        if data.get("error") != 404:
            if not data.get("list"):
                return await ctx.send(("No Urban Dictionary entries were found."))
            if await ctx.embed_requested():
                # a list of embeds
                embeds = []
                for ud in data["list"]:
                    embed = discord.Embed()
                    title = ("{word} by {author}").format(
                        word=ud["word"].capitalize(), author=ud["author"]
                    )
                    if len(title) > 256:
                        title = "{}...".format(title[:253])
                    embed.title = title
                    embed.url = ud["permalink"]

                    description = ("{definition}\n\n**Example:** {example}").format(**ud)
                    if len(description) > 2048:
                        description = "{}...".format(description[:2045])
                    embed.description = description

                    embed.set_footer(
                        text=(
                            "{thumbs_down} Down / {thumbs_up} Up, Powered by Urban Dictionary."
                        ).format(**ud)
                    )
                    embeds.append(embed)

                if embeds is not None and len(embeds) > 0:
                    await menu(
                        ctx,
                        pages=embeds,
                        controls=DEFAULT_CONTROLS,
                        message=None,
                        page=0,
                        timeout=30,
                    )
            else:
                messages = []
                for ud in data["list"]:
                    ud.setdefault("example", "N/A")
                    message = (
                        "<{permalink}>\n {word} by {author}\n\n{description}\n\n"
                        "{thumbs_down} Down / {thumbs_up} Up, Powered by Urban Dictionary."
                    ).format(word=ud.pop("word").capitalize(), description="{description}", **ud)
                    max_desc_len = 2000 - len(message)

                    description = ("{definition}\n\n**Example:** {example}").format(**ud)
                    if len(description) > max_desc_len:
                        description = "{}...".format(description[: max_desc_len - 3])

                    message = message.format(description=description)
                    messages.append(message)

                if messages is not None and len(messages) > 0:
                    await menu(
                        ctx,
                        pages=messages,
                        controls=DEFAULT_CONTROLS,
                        message=None,
                        page=0,
                        timeout=30,
                    )
        else:
            await ctx.send(
                ("No Urban Dictionary entries were found, or there was an error in the process.")
            )

    @commands.command()
    async def gif(self, ctx, *, search=None):
        """Gets gifs from tenor."""
        token = (await self.bot.get_shared_api_tokens("tenor")).get("token")

        if token:
            params = {"key": token, "contentfilter": "low", "limit": 20, "media_filter": "minimal"}
            if ctx.channel.is_nsfw() == True:
                params["contentfilter"] = "off"

            if search:
                url = "https://api.tenor.com/v1/random"
                params["q"] = str(search).lower()
            else:
                url = "https://api.tenor.com/v1/trending"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as r:
                    if r.status == 404:
                        message = await ctx.maybe_send_embed("Unknown error")
                        await asyncio.sleep(10)
                        try:
                            await message.delete()
                        except (discord.errors.NotFound, discord.errors.Forbidden):
                            pass
                    else:
                        data = await r.json(encoding="utf-8")
                        try:
                            result = choice(data["results"])
                            embed = discord.Embed(
                                color=await self.bot.get_embed_color(ctx), title=result["title"]
                            )
                            if search:
                                embed.set_author(name=f"Result for {search}")
                            else:
                                embed.set_author(name=f"Trending image on tenor")
                            embed.set_image(url=result["media"][0]["gif"]["url"])
                            embed.set_footer(text="Powered by tenor")
                            await ctx.send(embed=embed)
                        except:
                            message = await ctx.maybe_send_embed(
                                "Could not find a gif for that search term."
                            )
                            await asyncio.sleep(10)
                            try:
                                await message.delete()
                            except (discord.errors.NotFound, discord.errors.Forbidden):
                                pass

        else:
            message = await ctx.maybe_send_embed("No api token")
            await asyncio.sleep(10)
            try:
                await message.delete()
            except (discord.errors.NotFound, discord.errors.Forbidden):
                pass

    async def _youtube_results(self, query: str):
        try:
            headers = {"user-agent": "Red-cog/3.0"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.youtube.com/results",
                    params={"search_query": query},
                    headers=headers,
                ) as r:
                    result = await r.text()
            yt_find = re.findall(r"{\"videoId\":\"(.{11})", result)
            url_list = []
            for track in yt_find:
                url = f"https://www.youtube.com/watch?v={track}"
                if url not in url_list:
                    url_list.append(url)

        except Exception as e:
            url_list = [f"Something went terribly wrong! [{e}]"]

        return url_list

    @commands.command()
    async def youtube(self, ctx, *, query: str):
        """Search on Youtube."""
        result = await self._youtube_results(query)
        if result:
            await menu(ctx, result, DEFAULT_CONTROLS)
        else:
            await ctx.send("Nothing found. Try again later.")
