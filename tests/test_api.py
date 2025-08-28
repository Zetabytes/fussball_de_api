import asyncio
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

from fussball_api.schemas import (
    ClubInfoResponse,
    ClubSearchResult,
    FullClubInfoResponse,
    Game,
    Table,
    TableEntry,
    Team,
    TeamInfoResponse,
    TeamWithDetails,
)


@pytest.fixture
def client(monkeypatch):
    # Ensure that the StaticFiles mount path ../examples resolves correctly
    monkeypatch.chdir("fussball_api")
    from fussball_api.main import app
    from fussball_api.security import get_api_key
    app.dependency_overrides[get_api_key] = lambda: None
    return TestClient(app)


def _sample_team(idx: int = 1) -> Team:
    return Team(id=f"T{idx}", name=f"Team {idx}", fussball_de_url=f"/mannschaft/{idx}")


def _sample_game(idx: int = 1) -> Game:
    # Minimal required fields for serialization by FastAPI
    return Game(
        id=f"G{idx}",
        datetime_utc="2024-05-25T13:30:00+00:00",
        competition="Liga",
        age_group="Herren",
        home_team=f"Home {idx}",
        away_team=f"Away {idx}",
        home_logo="https://logo/home.png",
        away_logo="https://logo/away.png",
        location=None,
        location_url=None,
        home_score=None,
        away_score=None,
        status=None,
        match_events=[],
    )


def _sample_table() -> Table:
    return Table(
        entries=[
            TableEntry(
                place=1, team="A", img="https://img/a.png", games=1, won=1, draw=0, lost=0,
                goal="1:0", goal_difference=1, points=3, is_promotion=True, is_relegation=False
            )
        ]
    )


def test_root(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "Welcome to the Fussball.de API" in r.json().get("message", "")


def test_search_clubs_endpoint(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_search_clubs(query: str) -> List[ClubSearchResult]:
        return [
            ClubSearchResult(
                id="C1",
                name="Club One",
                logo_url="https://logo/club.png",
                city="12345 City",
            )
        ]
    monkeypatch.setattr(main, "search_clubs", fake_search_clubs)
    r = client.get("/api/search/clubs?query=abc")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["id"] == "C1"


def test_read_club_teams_endpoint(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_get_club_teams(club_id: str) -> List[Team]:
        return [_sample_team(1), _sample_team(2)]
    monkeypatch.setattr(main, "get_club_teams", fake_get_club_teams)
    r = client.get("/api/club/CLUB123/teams")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["id"] == "T1"


def test_read_club_info_endpoint(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_get_club_teams(club_id: str) -> List[Team]:
        return [_sample_team(1)]
    async def fake_get_club_next_games(club_id: str) -> List[Game]:
        return [_sample_game(1)]
    async def fake_get_club_prev_games(club_id: str) -> List[Game]:
        return [_sample_game(2)]
    monkeypatch.setattr(main, "get_club_teams", fake_get_club_teams)
    monkeypatch.setattr(main, "get_club_next_games", fake_get_club_next_games)
    monkeypatch.setattr(main, "get_club_prev_games", fake_get_club_prev_games)
    r = client.get("/api/club/CLUB123/info")
    assert r.status_code == 200
    data = r.json()
    assert "teams" in data and len(data["teams"]) == 1
    assert len(data["next_games"]) == 1


def test_read_full_club_info_endpoint(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_get_club_teams(club_id: str) -> List[Team]:
        return [_sample_team(1), _sample_team(2)]
    async def fake_get_club_next_games(club_id: str) -> List[Game]:
        return [_sample_game(1)]
    async def fake_get_club_prev_games(club_id: str) -> List[Game]:
        return [_sample_game(2)]
    async def fake_get_team_table(team_id: str) -> Optional[Table]:
        return _sample_table()
    async def fake_get_team_next_games(team_id: str) -> List[Game]:
        return [_sample_game(3)]
    async def fake_get_team_prev_games(team_id: str) -> List[Game]:
        return [_sample_game(4)]
    monkeypatch.setattr(main, "get_club_teams", fake_get_club_teams)
    monkeypatch.setattr(main, "get_club_next_games", fake_get_club_next_games)
    monkeypatch.setattr(main, "get_club_prev_games", fake_get_club_prev_games)
    monkeypatch.setattr(main, "get_team_table", fake_get_team_table)
    monkeypatch.setattr(main, "get_team_next_games", fake_get_team_next_games)
    monkeypatch.setattr(main, "get_team_prev_games", fake_get_team_prev_games)
    r = client.get("/api/club/CLUB123")
    assert r.status_code == 200
    data = r.json()
    assert "club_next_games" in data and len(data["club_next_games"]) == 1
    assert "club_prev_games" in data and len(data["club_prev_games"]) == 1
    assert "teams" in data and len(data["teams"]) == 2
    assert data["teams"][0]["table"] is not None


def test_read_team_info_endpoint(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_get_team_table(team_id: str) -> Optional[Table]:
        return _sample_table()
    async def fake_get_team_next_games(team_id: str) -> List[Game]:
        return [_sample_game(1)]
    async def fake_get_team_prev_games(team_id: str) -> List[Game]:
        return [_sample_game(2)]
    monkeypatch.setattr(main, "get_team_table", fake_get_team_table)
    monkeypatch.setattr(main, "get_team_next_games", fake_get_team_next_games)
    monkeypatch.setattr(main, "get_team_prev_games", fake_get_team_prev_games)
    r = client.get("/api/team/T1")
    assert r.status_code == 200
    data = r.json()
    assert data["table"] is not None
    assert len(data["next_games"]) == 1
    assert len(data["prev_games"]) == 1


def test_read_team_table_endpoint(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_get_team_table(team_id: str) -> Optional[Table]:
        return _sample_table()
    monkeypatch.setattr(main, "get_team_table", fake_get_team_table)
    r = client.get("/api/team/T1/table")
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data and len(data["entries"]) == 1


def test_club_next_prev_games_endpoints(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_next(club_id: str) -> List[Game]:
        return [_sample_game(1)]
    async def fake_prev(club_id: str) -> List[Game]:
        return [_sample_game(2)]
    monkeypatch.setattr(main, "get_club_next_games", fake_next)
    monkeypatch.setattr(main, "get_club_prev_games", fake_prev)
    r1 = client.get("/api/club/C123/next_games")
    r2 = client.get("/api/club/C123/prev_games")
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(r1.json()) == 1 and len(r2.json()) == 1


def test_team_next_prev_games_endpoints(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_next(team_id: str) -> List[Game]:
        return [_sample_game(1)]
    async def fake_prev(team_id: str) -> List[Game]:
        return [_sample_game(2)]
    monkeypatch.setattr(main, "get_team_next_games", fake_next)
    monkeypatch.setattr(main, "get_team_prev_games", fake_prev)
    r1 = client.get("/api/team/TX/next_games")
    r2 = client.get("/api/team/TX/prev_games")
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(r1.json()) == 1 and len(r2.json()) == 1


def test_read_game_by_id_endpoint_ok_and_404(client: TestClient, monkeypatch):
    from fussball_api import main
    async def fake_ok(game_id: str) -> Game:
        return _sample_game(9)
    async def fake_none(game_id: str):
        return None
    monkeypatch.setattr(main, "get_game_by_id", fake_ok)
    ok = client.get("/api/game/G9")
    assert ok.status_code == 200
    monkeypatch.setattr(main, "get_game_by_id", fake_none)
    nf = client.get("/api/game/GNF")
    assert nf.status_code == 404
