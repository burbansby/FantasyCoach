from fastapi.testclient import TestClient

from fantasycoach import web
from fantasycoach.models import Player
from fantasycoach.sleeper import SleeperError


def test_index_served():
    r = TestClient(web.app).get("/")
    assert r.status_code == 200
    assert "FantasyCoach" in r.text


def test_sleeper_errors_become_400(monkeypatch):
    def boom(_):
        raise SleeperError("Sleeper user 'nobody' not found")
    monkeypatch.setattr(web.client, "user", boom)
    r = TestClient(web.app).get("/api/user/nobody/leagues")
    assert r.status_code == 400
    assert "not found" in r.json()["detail"]


def test_player_json_uses_team_logo_for_defense():
    d = web.player_json(Player("KC", "Kansas City Chiefs", "DEF", "KC"))
    assert d["image"].endswith("/team_logos/nfl/kc.png")
    assert web.player_json(None) is None
