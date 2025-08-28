import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Dict, List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from fontTools import ttLib

from .cache import (
    HttpCacheEntry,
    FetchedResponse,
    http_cache,
    fetch_url,
)
from .config import settings
from .schemas import ClubSearchResult, Game, Table, TableEntry, Team, MatchEvent

logger = logging.getLogger(__name__)

FUSSBALL_DE_BASE_URL = "https://www.fussball.de"


def normalize_logo_url(url: str) -> str:
    """
    Normalizes a fussball.de logo URL so that the format is enforced to 'format/6'.
    This ensures optimal file size and consistent logo rendering.

    :param url: The original logo URL.
    :return: The normalized logo URL.
    """
    if not url:
        return url
    return re.sub(r"format/\d+", "format/9", url)


# Mapping from font names to digit values, used for score deobfuscation.
_FONT_DIGIT_MAPPING = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "hyphen": ":",  # Maps to the score separator
}


async def _get_font_mapping(font_name: str) -> Dict[str, str]:
    """
    Retrieves or creates a deobfuscation mapping for a given font.

    Downloads a .woff font file from fussball.de, parses it to extract
    the character-to-digit mapping, and caches the result.

    :param font_name: The name of the font (e.g., 'score-font-12345').
    :return: A dictionary mapping hex character codes to digit strings.
    """
    entry: Optional[HttpCacheEntry] = http_cache.get(f"font:{font_name}")
    if entry:
        expires = getattr(entry, "expires_at", None)
        if expires is None or expires > datetime.now(timezone.utc):
            logger.debug("Font mapping cache hit for: %s", font_name)
            return entry.content
        else:
            logger.debug("Font mapping cache expired for: %s", font_name)

    logger.info(f"Font mapping cache miss for: {font_name}. Fetching font.")
    font_url = f"{FUSSBALL_DE_BASE_URL}/export.fontface/-/format/woff/id/{font_name}/type/font"

    response = await asyncio.to_thread(fetch_url, font_url)
    if not response or response.status_code != 200:
        logger.error(f"Failed to download font file: {font_name}")
        return {}

    font_data = BytesIO(response.content)
    try:
        font = ttLib.TTFont(font_data)
        cmap = font.getBestCmap()
        if not cmap:
            logger.error(f"No cmap table found in font: {font_name}")
            return {}

        mapping = {}
        for code, name in cmap.items():
            hex_code = f"{code:x}"
            digit = _FONT_DIGIT_MAPPING.get(name)
            if digit:
                mapping[hex_code] = digit
            elif name.lower().startswith("uni"):
                # Map Private Use Area glyphs like "uniE675" directly by hex code
                uni_hex = name[3:]
                if uni_hex:
                    mapping[hex_code] = mapping.get(hex_code) or ""

        http_cache[f"font:{font_name}"] = HttpCacheEntry(
            url=font_url,
            final_url=font_url,
            status_code=200,
            headers={},
            content=mapping,
            text=None,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.CACHE_TTL_FONT),
        )
        logger.info(f"Successfully created and cached font mapping for: {font_name}")
        return mapping
    except ttLib.TTLibError as e:
        logger.error(f"Error parsing font file {font_name}: {e}")
        return {}


async def _deobfuscate_text(span_tag) -> Optional[str]:
    """
    Deobfuscates obfuscated text using the provided font mapping.
    Works for any <span data-obfuscation="..."> element.
    """
    if not span_tag or not span_tag.text:
        return None
    font_name = span_tag.get("data-obfuscation")
    if not font_name:
        return span_tag.get_text(strip=True)

    mapping = await _get_font_mapping(font_name)
    if not mapping:
        return span_tag.get_text(strip=True)

    decoded_chars = []
    for ch in span_tag.text.strip():
        hex_code = f"{ord(ch):x}"
        digit = mapping.get(hex_code) or mapping.get(hex_code.upper())
        if digit:
            decoded_chars.append(digit)

    return "".join(decoded_chars)


async def _deobfuscate_all(parent_tag) -> str:
    """
    Deobfuscates all <span data-obfuscation="..."> tags inside a parent BeautifulSoup tag.
    Fetches each unique font mapping at most once and concatenates decoded and raw texts
    in document order, skipping Private Use Area glyphs.
    """
    if not parent_tag:
        return ""

    spans = parent_tag.find_all("span", attrs={"data-obfuscation": True})
    font_names = {s.get("data-obfuscation") for s in spans if s.get("data-obfuscation")}
    font_mappings: Dict[str, Dict[str, str]] = {}

    for font_name in font_names:
        try:
            font_mappings[font_name] = await _get_font_mapping(font_name)
        except Exception as exc:
            logger.warning("Failed to get font mapping for '%s': %s", font_name, exc)
            font_mappings[font_name] = {}

    parts: List[str] = []
    stack = [parent_tag]

    from bs4 import NavigableString, Tag  # local import to avoid global dependency in signatures

    while stack:
        node = stack.pop(0)

        if isinstance(node, Tag):
            if node.name == "span" and node.has_attr("data-obfuscation"):
                font_name = node["data-obfuscation"]
                mapping = font_mappings.get(font_name, {})
                text = node.get_text() or ""
                decoded = []
                for ch in text:
                    key = f"{ord(ch):x}"
                    decoded.append(mapping.get(key, ch))
                parts.append("".join(decoded))
                # do not enqueue children to avoid double-adding raw obfuscated text
                continue
            # enqueue children in order
            stack[0:0] = list(node.children)
        elif isinstance(node, NavigableString):
            txt = str(node)
            if txt:
                stripped = txt.strip()
                if stripped and not all("\uE000" <= c <= "\uF8FF" for c in stripped):
                    parts.append(stripped)

    return "".join(parts).strip()


async def _get_games(url: str, cache_key: str) -> List[Game]:
    """
    Generic function to crawl and parse a list of games.
    Uses a cache to avoid redundant requests.

    :param url: The URL to fetch the games from.
    :param cache_key: The key to use for caching.
    :return: A list of Game objects.
    """
    logger.debug(f"Attempting to get games for cache_key: {cache_key}")

    logger.info(f"Fetching games from URL: {url}")
    response = await asyncio.to_thread(fetch_url, url)

    if response is None:
        logger.warning(f"Request failed for {url}. Cannot fetch games for {cache_key}.")
        return []

    html_content = response.text or ""
    if not html_content.strip():
        logger.info(f"No game content available for URL: {url}")
        return []

    logger.debug(f"Parsing new HTML content for games: {cache_key}")
    soup = BeautifulSoup(html_content, "lxml")
    game_rows = soup.find_all("tr")

    games = []
    current_date_info = {}

    for row in game_rows:
        if "visible-small" in row.get("class", []):
            info_cell = row.find("td")
            if not info_cell:
                continue

            info_text = info_cell.get_text(strip=True)
            try:
                date_time_info = info_text.split(" - ")
                date_str = date_time_info[0]
                date = date_str.split(", ")[1]

                other_info_part = " - ".join(date_time_info[1:])
                other_info = other_info_part.split(" | ")
                time_str = other_info[0].replace(" Uhr", "").strip()

                game_datetime_utc = None
                try:
                    # fussball.de provides naive datetimes, we assume they are in local German time
                    local_tz = ZoneInfo("Europe/Berlin")
                    naive_dt = datetime.strptime(f"{date} {time_str}", "%d.%m.%Y %H:%M")
                    local_dt = naive_dt.replace(tzinfo=local_tz)
                    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
                    game_datetime_utc = utc_dt
                except ValueError:
                    logger.warning(f"Could not parse datetime: '{date} {time_str}'.")
                    current_date_info = {}
                    continue

                age_group = None
                competition = None
                if len(other_info) == 3:
                    age_group = other_info[1].strip()
                    competition = other_info[2].strip()
                elif len(other_info) == 2:
                    competition = other_info[1].strip()

                current_date_info = {
                    "datetime_utc": game_datetime_utc,
                    "competition": competition,
                    "age_group": age_group,
                }
            except (ValueError, IndexError) as e:
                logger.warning(f"Could not parse date/time/comp row: '{info_text}'. Error: {e}")
                current_date_info = {}
            continue

        score_cell = row.find("td", class_="column-score")
        if not score_cell or not current_date_info:
            continue

        try:
            home_team_cell = row.find("td", class_="column-club-left")
            away_team_cell = row.find("td", class_="column-club-right")

            # Fallback for different HTML structure (e.g., in past games)
            if not home_team_cell or not away_team_cell:
                club_cells = row.find_all("td", class_="column-club")
                if len(club_cells) == 2:
                    home_team_cell = club_cells[0]
                    away_team_cell = club_cells[1]
                else:
                    logger.warning(f"Could not find home/away team cells. Skipping. Row: {row}")
                    continue

            home_team_name = home_team_cell.find(class_="club-name").get_text(strip=True)
            home_logo_span = home_team_cell.find("span", attrs={"data-responsive-image": True})
            home_team_logo = home_logo_span["data-responsive-image"] if home_logo_span else ""
            if home_team_logo.startswith("//"):
                home_team_logo = f"https:{home_team_logo}"
            home_team_logo = normalize_logo_url(home_team_logo)

            away_team_name = away_team_cell.find(class_="club-name").get_text(strip=True)
            away_logo_span = away_team_cell.find("span", attrs={"data-responsive-image": True})
            away_team_logo = away_logo_span["data-responsive-image"] if away_logo_span else ""
            if away_team_logo.startswith("//"):
                away_team_logo = f"https:{away_team_logo}"
            away_team_logo = normalize_logo_url(away_team_logo)

            location = None
            location_url = None
            game_details_url = None
            game_id: Optional[str] = None
            game_details_link_tag = score_cell.find("a")
            if game_details_link_tag and game_details_link_tag.has_attr("href"):
                game_details_url = f"{game_details_link_tag['href']}"
                try:
                    game_id = game_details_url.strip("/").split("/")[-1]
                except Exception:
                    game_id = None
                logger.debug(f"Fetching game details from: {game_details_url}")

                details_response = await asyncio.to_thread(fetch_url, game_details_url)
                if details_response and details_response.status_code == 200:
                    details_soup = BeautifulSoup(details_response.text, "lxml")
                    stage_section = details_soup.find("section", id="stage")
                    if stage_section:
                        location_link = stage_section.find("a", class_="location")
                        if location_link:
                            location = location_link.get_text(strip=True)
                            if location_link.has_attr("href"):
                                location_url = location_link["href"]
                            location = location.replace("Rasenplatz, ", "")
                            logger.debug(f"Found location: {location}")

                        # Extract match events JSON if available
                        events_container = details_soup.find("div", id="rangescontainer")
                        match_events = None
                        if events_container and events_container.has_attr("data-match-events"):
                            raw_events = events_container["data-match-events"]
                            try:
                                import json
                                events_json = json.loads(raw_events.replace("'", '"'))
                                match_events = events_json
                            except Exception as e:
                                logger.warning(
                                    f"Failed to parse match events JSON for {game_details_url}: {e}"
                                )
                elif details_response:
                    logger.warning(
                        f"Failed to fetch game details from {game_details_url}, "
                        f"status: {details_response.status_code}"
                    )
                else:
                    logger.warning(f"Request for game details failed for URL: {game_details_url}")

            status_tag = score_cell.find("span", class_="info-text")
            status = status_tag.get_text(strip=True) if status_tag else None

            # Deobfuscate scores for past games
            home_score = None
            away_score = None

            decoded_score = await _deobfuscate_all(score_cell)
            if ":" in decoded_score:
                parts = decoded_score.split(":", 1)
                home_score, away_score = parts[0].strip() or None, parts[1].strip() or None

            match_events: List[MatchEvent] = []
            if game_id:
                try:
                    match_events = await _get_match_course(game_id)
                except Exception as e:
                    logger.warning(f"Could not fetch match course for {game_details_url}: {e}")

            # Ensure we always have a game ID; fall back to a deterministic composite ID
            fallback_id = game_id or f"{current_date_info.get('datetime_utc')}_{home_team_name}_vs_{away_team_name}"
            game = Game(
                id=fallback_id,
                **current_date_info,
                home_team=home_team_name,
                home_logo=home_team_logo,
                away_team=away_team_name,
                away_logo=away_team_logo,
                status=status,
                home_score=home_score,
                away_score=away_score,
                location=location,
                location_url=location_url,
                match_events=match_events,
            )
            games.append(game)
        except (AttributeError, TypeError) as e:
            logger.error(f"Error parsing game row for {cache_key}: {e}. Row content: {row}")
            continue

    logger.info(f"Parsed {len(games)} games for {cache_key}")
    return games


async def get_club_next_games(club_id: str) -> List[Game]:
    """
    Crawls and parses the next games for a given club ID.

    :param club_id: The ID of the club.
    :return: A list of Game objects.
    """
    url = f"{FUSSBALL_DE_BASE_URL}/ajax.club.next.games/-/id/{club_id}/mode/PAGE"
    cache_key = f"club_next_games:{club_id}"
    return await _get_games(url, cache_key)


async def get_club_prev_games(club_id: str) -> List[Game]:
    """
    Crawls and parses the previous games for a given club ID.

    :param club_id: The ID of the club.
    :return: A list of Game objects.
    """
    url = f"{FUSSBALL_DE_BASE_URL}/ajax.club.prev.games/-/id/{club_id}/mode/PAGE"
    cache_key = f"club_prev_games:{club_id}"
    return await _get_games(url, cache_key)


async def get_team_next_games(team_id: str) -> List[Game]:
    """
    Crawls and parses the next games for a given team ID.

    :param team_id: The ID of the team.
    :return: A list of Game objects.
    """
    url = f"{FUSSBALL_DE_BASE_URL}/ajax.team.next.games/-/mode/PAGE/team-id/{team_id}"
    cache_key = f"team_next_games:{team_id}"
    return await _get_games(url, cache_key)


async def get_team_prev_games(team_id: str) -> List[Game]:
    """
    Crawls and parses the previous games for a given team ID.

    :param team_id: The ID of the team.
    :return: A list of Game objects.
    """
    url = f"{FUSSBALL_DE_BASE_URL}/ajax.team.prev.games/-/mode/PAGE/team-id/{team_id}"
    cache_key = f"team_prev_games:{team_id}"
    return await _get_games(url, cache_key)


async def get_club_teams(club_id: str) -> List[Team]:
    """
    Crawls and parses the list of teams for a given club ID.
    Uses a cache to avoid redundant requests.

    :param club_id: The ID of the club.
    :return: A list of Team objects.
    """
    logger.debug(f"Attempting to get teams for club_id: {club_id}")
    url = f"{FUSSBALL_DE_BASE_URL}/ajax.club.teams/-/action/search/id/{club_id}"
    response = await asyncio.to_thread(fetch_url, url)

    if response is None:  # Error during request
        logger.warning(f"Request failed for {url}. Cannot fetch teams for {club_id}.")
        return []

    # New data received, parse it
    logger.debug(f"Parsing new HTML content for club_id: {club_id}")
    html_content = response.text or ""
    soup = BeautifulSoup(html_content, "lxml")
    teams = []
    team_items = soup.find_all("div", class_="item")

    for item in team_items:
        link = item.find("h4").find("a")
        if not link:
            continue

        team_name = link.get_text(strip=True)
        team_url = link.get("href")
        if not team_url:
            continue

        team_id = team_url.strip("/").split("/")[-1]

        teams.append(
            Team(
                id=team_id,
                name=team_name,
                fussball_de_url=f"{team_url}",
            )
        )

    logger.info(f"Parsed {len(teams)} teams for club {club_id}")
    return teams


async def get_team_table(team_id: str) -> Optional[Table]:
    """
    Crawls and parses the league table for a given team ID.
    Uses a cache to avoid redundant requests.

    :param team_id: The ID of the team.
    :return: A Table object, or None if no table is available.
    """
    logger.debug(f"Attempting to get table for team_id: {team_id}")
    url = f"{FUSSBALL_DE_BASE_URL}/ajax.team.table/-/team-id/{team_id}"
    response = await asyncio.to_thread(fetch_url, url)

    if response is None:
        logger.warning(f"Request failed for {url}. Cannot fetch table for {team_id}.")
        return None

    html_content = response.text or ""
    if not html_content.strip():
        logger.info(f"No table content available for team_id: {team_id}")
        return None

    logger.debug(f"Parsing new HTML content for team table: {team_id}")
    soup = BeautifulSoup(html_content, "lxml")
    table_rows = soup.find_all("tr")

    table_entries = []
    for row in table_rows:
        if "thead" in row.get("class", []):
            continue

        cols = row.find_all("td")
        if len(cols) < 10:
            continue

        row_classes = row.get("class", [])
        is_promotion = "promotion" in row_classes
        is_relegation = "relegation" in row_classes

        logo_tag = cols[2].find("img")
        logo_url = logo_tag["src"] if logo_tag else ""
        if logo_url.startswith("//"):
            logo_url = f"https:{logo_url}"
        logo_url = normalize_logo_url(logo_url)

        try:
            entry = TableEntry(
                place=int(cols[1].get_text(strip=True).replace(".", "")),
                team=cols[2].find(class_="club-name").get_text(strip=True),
                img=logo_url,
                games=int(cols[3].get_text(strip=True)),
                won=int(cols[4].get_text(strip=True)),
                draw=int(cols[5].get_text(strip=True)),
                lost=int(cols[6].get_text(strip=True)),
                goal=cols[7].get_text(strip=True),
                goal_difference=int(cols[8].get_text(strip=True)),
                points=int(cols[9].get_text(strip=True)),
                is_promotion=is_promotion,
                is_relegation=is_relegation,
            )
            table_entries.append(entry)
        except (ValueError, IndexError, AttributeError) as e:
            logger.error(f"Error parsing table row for team {team_id}: {e}. Row content: {row}")
            continue

    if not table_entries:
        logger.warning(f"Could not parse any table entries for team_id: {team_id}")
        return None

    table = Table(entries=table_entries)

    logger.info(f"Parsed {len(table.entries)} table entries for team {team_id}")
    return table


async def search_clubs(query: str) -> List[ClubSearchResult]:
    """
    Searches for clubs on fussball.de and parses the results.

    :param query: The search term for the club name.
    :return: A list of ClubSearchResult objects.
    """
    logger.debug(f"Searching for clubs with query: '{query}'")
    # URL-encode the query to handle special characters
    encoded_query = quote(query)
    url = f"{FUSSBALL_DE_BASE_URL}/suche/-/text/{encoded_query}/restriction/CLUB_AND_TEAM"

    response = await asyncio.to_thread(fetch_url, url)
    if response is None:
        logger.warning(f"Request failed for club search with query: '{query}'")
        return []

    html_content = response.text or ""
    soup = BeautifulSoup(html_content, "lxml")

    club_list_div = soup.find("div", id="clublist")
    if not club_list_div:
        logger.info(f"No club list found for query: '{query}'")
        return []

    clubs = []
    list_items = club_list_div.find_all("li")

    for item in list_items:
        link_tag = item.find("a")
        if not link_tag or not link_tag.has_attr("href"):
            continue

        href = link_tag["href"]
        club_id = href.strip("/").split("/")[-1]

        img_tag = link_tag.find("img")
        logo_url = img_tag["src"] if img_tag and img_tag.has_attr("src") else ""
        if logo_url.startswith("//"):
            logo_url = f"https:{logo_url}"
        logo_url = normalize_logo_url(logo_url)

        name_p = link_tag.find("p", class_="name")
        name = name_p.get_text(strip=True) if name_p else "Unknown Club"

        sub_p = link_tag.find("p", class_="sub")
        city = sub_p.get_text(strip=True).replace("\xa0", " ") if sub_p else ""

        clubs.append(
            ClubSearchResult(
                id=club_id,
                name=name,
                logo_url=logo_url,
                city=city,
            )
        )

    logger.info(f"Found {len(clubs)} clubs for query: '{query}'")
    return clubs


async def _get_player_name_from_profile(profile_url: str) -> Optional[str]:
    """
    Load a player's profile from fussball.de and extract the plain name.

    :param profile_url: Absolute URL to the player's profile.
    :return: The player's real name or None.
    """
    response = await asyncio.to_thread(fetch_url, profile_url)
    if response is None or response.status_code != 200:
        logger.warning(f"Failed to fetch player profile: {profile_url}")
        return None

    try:
        soup = BeautifulSoup(response.text, "lxml")
        tag = soup.find("p", class_="profile-name")
        if tag:
            return tag.get_text(strip=True)
    except Exception as e:
        logger.error(f"Error parsing player profile {profile_url}: {e}")
    return None


async def _get_match_course(game_id: str) -> List[MatchEvent]:
    """
    Fetches and parses the detailed match course for a given game ID.

    :param game_id: The fussball.de game ID.
    :return: A list of MatchEvent objects.
    """
    url = f"{FUSSBALL_DE_BASE_URL}/ajax.match.course/-/mode/PAGE/spiel/{game_id}"
    response = await asyncio.to_thread(fetch_url, url)
    if response is None or response.status_code != 200:
        logger.warning(f"Failed to fetch match course for game {game_id}")
        return []

    html_content = response.text or ""
    soup = BeautifulSoup(html_content, "lxml")

    events: List[MatchEvent] = []

    for row in soup.select("#match_course_body .row-event"):
        side = "home" if "event-left" in row.get("class", []) else "away"

        time_tag = row.select_one(".column-time .valign-inner")
        time_text = time_tag.get_text(strip=True) if time_tag else None

        ev_type = "unknown"
        score = None
        desc = None

        score_left = row.select_one(".column-event .score-left")
        score_right = row.select_one(".column-event .score-right")

        score_tag = row.select_one(".column-event")
        if score_tag:
            score = await _deobfuscate_all(score_tag)
            ev_type = "goal"

        if row.select_one(".icon-card.yellow-card"):
            ev_type = "yellow-card"
            desc = "Gelbe Karte"
        if row.select_one(".icon-card.red-card"):
            ev_type = "red-card"
            desc = "Rote Karte"

        if row.select_one(".icon-substitute"):
            ev_type = "substitution"
            desc = "Auswechslung"
            desc_tag = row.select_one(".column-player .substitute")
            if desc_tag:
                links = desc_tag.find_all("a", href=True)
                names = []
                for link in links:
                    if "spielerprofil" in link["href"]:
                        real_name = await _get_player_name_from_profile(link["href"])
                        if real_name:
                            names.append(real_name)
                if len(names) == 2:
                    desc = f"{names[0]} für {names[1]}"
                elif names:
                    desc = " / ".join(names)

        if not desc:
            txt_tag = row.select_one(".column-player")
            if txt_tag:
                profile_link = txt_tag.find("a", href=True)
                if profile_link and "spielerprofil" in profile_link["href"]:
                    desc = await _get_player_name_from_profile(profile_link["href"])
                else:
                    desc = await _deobfuscate_all(txt_tag)

        events.append(
            MatchEvent(
                time=time_text or "",
                type=ev_type,
                team=side,
                description=desc,
                score=score,
            )
        )

    logger.debug(f"Extracted {len(events)} match events for game {game_id}")
    return events


async def get_game_by_id(game_id: str) -> Optional[Game]:
    """
    Fetches and parses details of a single game by its game ID.

    :param game_id: The fussball.de game ID.
    :return: A Game object with details and match_events, or None if not found.
    """
    details_url = f"{FUSSBALL_DE_BASE_URL}/spiel/-/spiel/{game_id}"
    details_response = await asyncio.to_thread(fetch_url, details_url)
    if details_response is None or details_response.status_code != 200:
        logger.error(f"Failed to fetch game details for game_id={game_id}")
        return None

    details_soup = BeautifulSoup(details_response.text or "", "lxml")

    stage_section = details_soup.find("section", id="stage")
    if not stage_section:
        logger.warning(f"No stage section found for game {game_id}")
        return None

    # Extract location
    location = None
    location_url = None
    location_link = stage_section.find("a", class_="location")
    if location_link:
        location = location_link.get_text(strip=True).replace("Rasenplatz, ", "")
        location_url = location_link.get("href")

    # Extract team names and logos (support both old and new class names)
    home_team_div = stage_section.find("div", class_="team-left") or stage_section.find(
        "div", class_="team-home"
    )
    away_team_div = stage_section.find("div", class_="team-right") or stage_section.find(
        "div", class_="team-away"
    )
    if not (home_team_div and away_team_div):
        logger.warning(f"Could not parse team information for game {game_id}")
        return None

    # Extract team names more robustly
    home_team_name_tag = home_team_div.find("div", class_="team-name") or home_team_div
    away_team_name_tag = away_team_div.find("div", class_="team-name") or away_team_div
    home_team_name = home_team_name_tag.get_text(strip=True)
    away_team_name = away_team_name_tag.get_text(strip=True)

    # Extract logos (support <span data-responsive-image> and <img>)
    home_logo_span = home_team_div.find("span", attrs={"data-responsive-image": True})
    away_logo_span = away_team_div.find("span", attrs={"data-responsive-image": True})
    home_logo_img = home_team_div.find("img")
    away_logo_img = away_team_div.find("img")

    home_team_logo = ""
    if home_logo_span and home_logo_span.has_attr("data-responsive-image"):
        home_team_logo = normalize_logo_url(f"https:{home_logo_span['data-responsive-image']}")
    elif home_logo_img and home_logo_img.has_attr("src"):
        src = home_logo_img["src"]
        home_team_logo = normalize_logo_url(f"https:{src}" if src.startswith("//") else src)

    away_team_logo = ""
    if away_logo_span and away_logo_span.has_attr("data-responsive-image"):
        away_team_logo = normalize_logo_url(f"https:{away_logo_span['data-responsive-image']}")
    elif away_logo_img and away_logo_img.has_attr("src"):
        src = away_logo_img["src"]
        away_team_logo = normalize_logo_url(f"https:{src}" if src.startswith("//") else src)

    status_tag = stage_section.find("span", class_="info-text")
    status = status_tag.get_text(strip=True) if status_tag else None

    # Extract final result (home/away score) if available
    result_div = stage_section.find("div", class_="result")
    home_score = None
    away_score = None
    if result_div:
        score_text = await _deobfuscate_all(result_div)
        if ":" in score_text:
            parts = score_text.split(":")
            if len(parts) == 2:
                home_score, away_score = parts[0].strip(), parts[1].strip()

    # Get events list
    match_events = await _get_match_course(game_id)

    return Game(
        id=game_id,
        datetime_utc=datetime.now(timezone.utc),
        competition="Unknown",
        home_team=home_team_name,
        home_logo=home_team_logo,
        away_team=away_team_name,
        away_logo=away_team_logo,
        status=status,
        home_score=home_score,
        away_score=away_score,
        location=location,
        location_url=location_url,
        match_events=match_events,
    )
