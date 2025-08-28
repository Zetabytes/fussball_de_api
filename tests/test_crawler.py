import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fussball_api.cache import http_cache, FetchedResponse
import httpx

from fussball_api.crawler import (
    _get_font_mapping,
    get_club_teams,
    get_team_prev_games,
    get_team_table,
    search_clubs,
    _deobfuscate_text,
)
from fussball_api.schemas import ClubSearchResult, Game, Table, Team


@pytest.fixture(autouse=True)
def clear_caches():
    """Fixture to clear all caches before each test."""
    http_cache.clear()


@pytest.fixture
def club_teams_html():
    """Sample HTML for testing get_club_teams."""
    return """
    <div class="item">
        <h4><a href="/mannschaft/team-a-herren/-/mannschaft/0A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P">Team A</a></h4>
    </div>
    <div class="item">
        <h4><a href="/mannschaft/team-b-jugend/-/mannschaft/1A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P">Team B</a></h4>
    </div>
    """


@pytest.fixture
def team_table_html():
    """Sample HTML for testing get_team_table."""
    return """
    <table>
        <tr class="thead">...</tr>
        <tr class="promotion">
            <td></td>
            <td>1.</td>
            <td><img src="//media.fussball.de/logo-1.png" /> <span class="club-name">Team One</span></td>
            <td>10</td>
            <td>8</td>
            <td>1</td>
            <td>1</td>
            <td>20:5</td>
            <td>15</td>
            <td>25</td>
        </tr>
        <tr class="relegation">
            <td></td>
            <td>2.</td>
            <td><img src="//media.fussball.de/logo-2.png" /> <span class="club-name">Team Two</span></td>
            <td>10</td>
            <td>2</td>
            <td>2</td>
            <td>6</td>
            <td>10:15</td>
            <td>-5</td>
            <td>8</td>
        </tr>
    </table>
    """


@pytest.fixture
def prev_games_html():
    """
    Sample HTML for testing _get_games.
    Note: &#x is replaced with '' in the crawler before parsing.
    """
    return """
    <table>
        <tr class="visible-small">
            <td colspan="3">Sa, 25.05.2024 - 15:30 Uhr | Herren | Kreisliga A</td>
        </tr>
        <tr>
            <td class="column-club-left">
                <span class="club-name">Home Team 1</span>
                <span data-responsive-image="//logo.home/img1.png"></span>
            </td>
            <td class="column-score">
                <a href="/spiel/123"></a>
                <span data-obfuscation="score-font-123" class="score-left">&#xE001;</span>
                <span class="score-seperator">:</span>
                <span data-obfuscation="score-font-123" class="score-right">&#xE002;</span>
            </td>
            <td class="column-club-right">
                <span class="club-name">Away Team 1</span>
                <span data-responsive-image="//logo.away/img1.png"></span>
            </td>
        </tr>
        <tr class="visible-small">
            <td colspan="3">So, 26.05.2024 - 11:00 Uhr | Frauen | Bezirksliga</td>
        </tr>
        <tr>
            <td class="column-club-left">
                <span class="club-name">Home Team 2</span>
                <span data-responsive-image="//logo.home/img2.png"></span>
            </td>
            <td class="column-score">
                <a href="/spiel/456"></a>
                <span class="info-text">Abgesagt</span>
            </td>
            <td class="column-club-right">
                <span class="club-name">Away Team 2</span>
                <span data-responsive-image="//logo.away/img2.png"></span>
            </td>
        </tr>
    </table>
    """


@pytest.fixture
def game_details_html():
    """Sample HTML for game details page to extract location."""
    return """
    <section id="stage">
        <a class="location" href="https://maps.google.com/q=Some+Stadium">Some Stadium</a>
    </section>
    <div id="rangescontainer" data-match-events="{'durationSections': 2, 'duration': 90, 'extraTimeDuration': 0,
        'first-half': {'start': 0,'end': 45,'events': [{'time':'29','type':'yellow-card','team':'away'}]},
        'second-half': {'start': 45,'end': 90,'events': [{'time':'72','type':'goal','team':'home'}]}}"></div>
    """
 

@pytest.fixture
def club_search_html():
    """Sample HTML for testing search_clubs."""
    return """
    <div id="clublist">
        <ul>
            <li>
                <a href="/verein/test-club-e-v/001VTR8D8C000000VARTQG41VT4929AS">
                    <img src="//media.fussball.de/club-logo.png">
                    <p class="name">Test Club e.V.</p>
                    <p class="sub">12345 Teststadt</p>
                </a>
            </li>
            <li>
                <a><!-- Incomplete link, should be skipped --></a>
            </li>
        </ul>
    </div>
    """


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_get_club_teams(mock_fetch, club_teams_html):
    """Tests the parsing of club teams."""
    # Arrange
    mock_fetch.return_value = FetchedResponse(
        url="u", status_code=200, headers={}, content=club_teams_html.encode("utf-8"),
        text=club_teams_html
    )

    # Act
    teams = await get_club_teams("test_club_id")

    # Assert
    assert len(teams) == 2
    assert isinstance(teams[0], Team)
    assert teams[0].id == "0A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P"
    assert teams[0].name == "Team A"
    assert (
        teams[0].fussball_de_url
        == "/mannschaft/team-a-herren/-/mannschaft/0A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P"
    )

    assert isinstance(teams[1], Team)
    assert teams[1].id == "1A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P"
    assert teams[1].name == "Team B"

    mock_fetch.assert_called_once()


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_get_team_table(mock_fetch, team_table_html):
    """Tests the parsing of a team's league table."""
    # Arrange
    mock_fetch.return_value = FetchedResponse(
        url="u", status_code=200, headers={}, content=team_table_html.encode("utf-8"),
        text=team_table_html
    )

    # Act
    table = await get_team_table("test_team_id")

    # Assert
    assert table is not None
    assert isinstance(table, Table)
    assert len(table.entries) == 2

    entry1 = table.entries[0]
    assert entry1.place == 1
    assert entry1.team == "Team One"
    assert entry1.img == "https://media.fussball.de/logo-1.png"
    assert entry1.games == 10
    assert entry1.won == 8
    assert entry1.draw == 1
    assert entry1.lost == 1
    assert entry1.goal == "20:5"
    assert entry1.goal_difference == 15
    assert entry1.points == 25
    assert entry1.is_promotion is True
    assert entry1.is_relegation is False

    entry2 = table.entries[1]
    assert entry2.place == 2
    assert entry2.team == "Team Two"
    assert entry2.is_promotion is False
    assert entry2.is_relegation is True


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_get_team_table_no_content(mock_fetch):
    """Tests handling of an empty response for a team table."""
    # Arrange
    mock_fetch.return_value = FetchedResponse(
        url="u", status_code=200, headers={}, content=b"", text=""
    )

    # Act
    table = await get_team_table("test_team_id")

    # Assert
    assert table is None


@pytest.mark.asyncio
@patch("fussball_api.crawler.ttLib.TTFont")
@patch("fussball_api.crawler.fetch_url")
async def test_get_font_mapping(mock_fetch, mock_ttfont):
    """Tests the score deobfuscation font mapping logic."""
    # Arrange
    # Mock the HTTP response for the font file
    mock_fetch.return_value = FetchedResponse(
        url="u", status_code=200, headers={}, content=b"woff-content", text=None
    )

    # Mock the font parsing library
    mock_font_instance = MagicMock()
    mock_cmap = {0x61: "one", 0x62: "two", 0x3A: "hyphen", 0x99: "unknown"}
    mock_font_instance.getBestCmap.return_value = mock_cmap
    mock_ttfont.return_value = mock_font_instance

    # Act
    mapping = await _get_font_mapping("test-font")

    # Assert
    assert mapping == {"61": "1", "62": "2", "3a": ":"}
    mock_fetch.assert_called_once()
    mock_ttfont.assert_called_once()


@pytest.mark.asyncio
@patch("fussball_api.crawler._get_match_course", new_callable=AsyncMock)
@patch("fussball_api.crawler._get_font_mapping", new_callable=AsyncMock)
@patch("fussball_api.crawler.fetch_url")
async def test_get_team_prev_games(
    mock_fetch, mock_get_font_mapping, mock_get_match_course, prev_games_html, game_details_html
):
    """Tests the complex parsing of previous games, including score deobfuscation."""
    # Arrange
    # Mock the sequence of HTTP responses: games list, details for game 1, details for game 2
    mock_fetch.side_effect = [
        FetchedResponse(url="list", status_code=200, headers={}, content=prev_games_html.encode("utf-8"), text=prev_games_html),
        FetchedResponse(url="/spiel/123", status_code=200, headers={}, content=game_details_html.encode("utf-8"), text=game_details_html),
        FetchedResponse(url="/spiel/456", status_code=404, headers={}, content=b"", text="Not Found"),
    ]

    # Mock the font mapping result
    mock_get_font_mapping.return_value = {
        f"{ord('\ue001'):x}": "1",
        f"{ord('\ue002'):x}": "2",
    }
    mock_get_match_course.return_value = []

    # Act
    games = await get_team_prev_games("test_team_id")

    # Assert
    assert len(games) == 2

    game1 = games[0]
    assert isinstance(game1, Game)
    assert game1.datetime_utc.isoformat() == "2024-05-25T13:30:00+00:00"
    assert game1.competition == "Kreisliga A"
    assert game1.age_group == "Herren"
    assert game1.home_team == "Home Team 1"
    assert game1.home_logo == "https://logo.home/img1.png"
    assert game1.away_team == "Away Team 1"
    assert game1.away_logo == "https://logo.away/img1.png"
    assert game1.home_score == "1"
    assert game1.away_score == "2"
    assert game1.status is None
    assert game1.location == "Some Stadium"
    assert game1.location_url == "https://maps.google.com/q=Some+Stadium"
    assert isinstance(game1.match_events, list)

    game2 = games[1]
    assert isinstance(game2, Game)
    assert game2.datetime_utc.isoformat() == "2024-05-26T09:00:00+00:00"
    assert game2.competition == "Bezirksliga"
    assert game2.age_group == "Frauen"
    assert game2.home_team == "Home Team 2"
    assert game2.away_team == "Away Team 2"
    assert game2.home_score is None
    assert game2.away_score is None
    assert game2.status == "Abgesagt"
    assert game2.location is None
    assert game2.location_url is None

    mock_get_font_mapping.assert_called_once_with("score-font-123")
    assert mock_fetch.call_count == 3
    assert mock_fetch.call_args_list[0].args[0].endswith(
        "/ajax.team.prev.games/-/mode/PAGE/team-id/test_team_id"
    )
    assert mock_fetch.call_args_list[1].args[0] == "/spiel/123"
    assert mock_fetch.call_args_list[2].args[0] == "/spiel/456"


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_search_clubs(mock_fetch, club_search_html):
    """Tests the parsing of club search results."""
    # Arrange
    mock_fetch.return_value = FetchedResponse(
        url="u", status_code=200, headers={}, content=club_search_html.encode("utf-8"),
        text=club_search_html
    )

    # Act
    clubs = await search_clubs("test")

    # Assert
    assert len(clubs) == 1
    club1 = clubs[0]
    assert isinstance(club1, ClubSearchResult)
    assert club1.id == "001VTR8D8C000000VARTQG41VT4929AS"
    assert club1.name == "Test Club e.V."
    assert club1.logo_url == "https://media.fussball.de/club-logo.png"
    assert club1.city == "12345 Teststadt"


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_get_match_course_parses_events(mock_fetch):
    """Tests that match events are parsed correctly into MatchEvent objects."""
    # Arrange: simulate HTML with one goal event
    html = """
    <div id="match_course_body">
        <div class="row-event event-left">
            <div class="column-time"><div class="valign-inner">16’</div></div>
            <div class="column-event"><span class="even">1:0</span></div>
            <div class="column-player">Spieler A</div>
        </div>
    </div>
    """
    mock_fetch.return_value = FetchedResponse(
        url="u", status_code=200, headers={}, content=html.encode("utf-8"), text=html
    )

    from fussball_api.crawler import _get_match_course

    # Act
    events = await _get_match_course("testgame")

    # Assert
    assert len(events) == 1
    ev = events[0]
    assert ev.time == "16’"
    assert ev.type == "goal"
    assert ev.team == "home"       # correctly mapped
    assert ev.score == "1:0"
    assert "Spieler A" in ev.description


@pytest.mark.asyncio
@patch("fussball_api.crawler._get_font_mapping", new_callable=AsyncMock)
async def test_deobfuscate_player_name(mock_get_font_mapping):
    """Tests that obfuscated player names are decoded via font mapping."""
    from bs4 import BeautifulSoup

    mock_get_font_mapping.return_value = {
        f"{ord(''):x}": "N",
        f"{ord(''):x}": "i",
        f"{ord(''):x}": "c",
        f"{ord(''):x}": "o",
    }

    html = '<span data-obfuscation="font123"></span>'
    span = BeautifulSoup(html, "lxml").find("span")

    decoded = await _deobfuscate_text(span)

    assert decoded == "Nico"
    mock_get_font_mapping.assert_called_once_with("font123")


@pytest.mark.asyncio
@patch("fussball_api.crawler._get_font_mapping", new_callable=AsyncMock)
@patch("fussball_api.crawler.fetch_url")
async def test_get_match_course_with_obfuscated_player(
    mock_fetch, mock_get_font_mapping
):
    """Tests that _get_match_course decodes obfuscated player names via font mapping."""
    mock_get_font_mapping.return_value = {f"{ord(''):x}": "A"}

    html = """
    <div id="match_course_body">
        <div class="row-event event-left">
            <div class="column-time"><div class="valign-inner">10’</div></div>
            <div class="column-event"><span class="even">1:0</span></div>
            <div class="column-player"><span data-obfuscation="font123"></span></div>
        </div>
    </div>
    """
    mock_fetch.return_value = FetchedResponse(
        url="u", status_code=200, headers={}, content=html.encode("utf-8"), text=html
    )

    from fussball_api.crawler import _get_match_course
    events = await _get_match_course("game123")

    assert len(events) == 1
    ev = events[0]
    assert ev.description == "A"
    mock_get_font_mapping.assert_called_once_with("font123")


from bs4 import BeautifulSoup

@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_get_font_mapping_fails_and_empty(mock_fetch):
    mock_fetch.return_value = FetchedResponse(
        url="u", status_code=404, headers={}, content=b"", text="Not Found"
    )
    mapping = await _get_font_mapping("bad-font")
    assert mapping == {}


@pytest.mark.asyncio
async def test_get_font_mapping_cache_hit(monkeypatch):
    from fussball_api import crawler
    crawler.http_cache["font:cached-font"] = crawler.HttpCacheEntry(
        url="dummy",
        final_url="dummy",
        status_code=200,
        headers={},
        content={"61": "1"},
        text=None,
    )
    result = await crawler._get_font_mapping("cached-font")
    assert result == {"61": "1"}


@pytest.mark.asyncio
async def test_deobfuscate_text_no_obfuscation():
    html = "<span>Hello</span>"
    span = BeautifulSoup(html, "lxml").find("span")
    decoded = await _deobfuscate_text(span)
    assert decoded == "Hello"


@pytest.mark.asyncio
@patch("fussball_api.crawler._get_font_mapping", new_callable=AsyncMock)
async def test_deobfuscate_text_unknown_char(mock_get_font_mapping):
    mock_get_font_mapping.return_value = {}
    html = '<span data-obfuscation="fontX">X</span>'
    span = BeautifulSoup(html, "lxml").find("span")
    decoded = await _deobfuscate_text(span)
    assert decoded == "X"


@pytest.mark.asyncio
async def test_fetch_response_handles_requesterror(monkeypatch):
    from fussball_api.cache import fetch_url

    class FakeClient:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass
        def request(self, *a, **kw):
            raise httpx.RequestError("boom", request=httpx.Request("GET","x"))

    monkeypatch.setattr(httpx, "Client", lambda **kw: FakeClient())
    resp = fetch_url("url")
    assert resp is None


@pytest.mark.asyncio
async def test_fetch_response_handles_httpstatuserror(monkeypatch):
    from fussball_api.cache import fetch_url

    class FakeClient:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass
        def request(self, *a, **kw):
            return httpx.Response(500, request=httpx.Request("GET","x"))

    monkeypatch.setattr(httpx, "Client", lambda **kw: FakeClient())
    resp = fetch_url("url")
    assert resp is not None
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_get_game_by_id_no_stage(monkeypatch):
    from fussball_api import crawler
    def fake_fetch_sync(*a, **k):
        return FetchedResponse(url="u", status_code=200, headers={}, content=b"<html></html>", text="<html></html>")
    monkeypatch.setattr(crawler, "fetch_url", fake_fetch_sync)
    game = await crawler.get_game_by_id("gid")
    assert game is None


@pytest.mark.asyncio
async def test_get_game_by_id_team_logos_from_img(monkeypatch):
    from fussball_api import crawler
    html = """
    <section id="stage">
      <div class="team-home"><div class="team-name">A</div><img src="//a.png"></div>
      <div class="team-away"><div class="team-name">B</div><img src="//b.png"></div>
    </section>
    """
    def fake_fetch_sync(*a, **k):
        return FetchedResponse(url="u", status_code=200, headers={}, content=html.encode("utf-8"), text=html)
    monkeypatch.setattr(crawler, "fetch_url", fake_fetch_sync)
    game = await crawler.get_game_by_id("gid")
    assert game.home_logo.startswith("https://")
    assert game.away_logo.startswith("https://")


@pytest.mark.asyncio
async def test_get_team_table_skips_bad_rows(monkeypatch):
    from fussball_api import crawler
    html = "<table><tr><td>onlyfew</td></tr></table>"
    def fake_fetch_sync(*a, **k):
        return FetchedResponse(url="u", status_code=200, headers={}, content=html.encode("utf-8"), text=html)
    monkeypatch.setattr(crawler, "fetch_url", fake_fetch_sync)
    result = await crawler.get_team_table("tid")
    assert result is None


@pytest.mark.asyncio
async def test_search_clubs_no_results(monkeypatch):
    from fussball_api import crawler
    def fake_fetch_sync(*a, **k):
        return FetchedResponse(url="u", status_code=200, headers={}, content=b"", text="<html></html>")
    monkeypatch.setattr(crawler, "fetch_url", fake_fetch_sync)
    result = await crawler.search_clubs("abc")
    assert result == []
