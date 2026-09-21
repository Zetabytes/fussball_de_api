from datetime import datetime, timezone

from fussball_api import cache
from fussball_api.schemas import FullClubInfoResponse, Game


def test_save_and_load_club_info_cache_roundtrip(tmp_path, monkeypatch):
    """Tests that the prewarmed club cache (incl. datetimes) survives a save/load cycle."""
    monkeypatch.setattr(cache, "CACHE_DUMP_FILE", tmp_path / "fussball_cache.json")
    monkeypatch.setattr(cache.settings, "PREWARM_CLUB_ID", "club1")
    monkeypatch.setattr(cache, "http_cache", {})

    game = Game(
        id="g1",
        datetime_utc=datetime(2026, 9, 19, 8, 30, tzinfo=timezone.utc),
        competition="Kreisliga",
        home_team="Heim",
        home_logo="",
        away_team="Gast",
        away_logo="",
        duration=60,
    )
    club_info = FullClubInfoResponse(club_prev_games=[game], club_next_games=[], teams=[])
    monkeypatch.setattr(cache, "club_info_cache", {"club1": club_info})

    cache.save_caches_to_file()
    cache.club_info_cache.clear()
    cache.load_caches_from_file()

    assert cache.club_info_cache["club1"] == club_info
