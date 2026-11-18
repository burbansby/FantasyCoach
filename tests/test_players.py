"""Tests for player lookup and comparison: name search, scoring choice, outlooks and the player API."""

from fastapi.testclient import TestClient

from fantasycoach.core.models import normalize
from fantasycoach.interface import web
from fantasycoach.players import compare
from fantasycoach.players import lookup as players

DB = {
    "1": {"full_name": "Jonathan Taylor", "first_name": "Jonathan", "last_name": "Taylor",
          "position": "RB", "team": "IND", "search_rank": 10},
    "2": {"full_name": "Taylor Heinicke", "first_name": "Taylor", "last_name": "Heinicke",
          "position": "QB", "team": None, "search_rank": 900, "injury_status": "Out"},
    "3": {"full_name": "Amon-Ra St. Brown", "first_name": "Amon-Ra", "last_name": "St. Brown",
          "position": "WR", "team": "DET", "search_rank": 5},
    "KC": {"first_name": "Kansas City", "last_name": "Chiefs", "position": "DEF", "team": "KC"},
    "9": {"full_name": "Some Lineman", "position": "OL", "team": "DET", "search_rank": 1},
}


class FakeClient:
    def __init__(self):
        self.league_calls = 0

    def players(self):
        return DB

    def state(self):
        return {"week": 11, "league_season": "2026"}

    def league(self, league_id):
        self.league_calls += 1
        return {"name": "My League", "scoring_settings": {"rec": 0.5, "rec_yd": 0.1}}

    def projections(self, season, week, positions):
        if week == 12:  # player 1 on bye in week 12
            return [{"player_id": "1", "opponent": None, "stats": {}},
                    {"player_id": "3", "opponent": "CHI", "stats": {"rec": 6, "rec_yd": 80}}]
        return [{"player_id": "1", "opponent": "HOU", "stats": {"rush_yd": 90, "rush_td": 1, "rec": 2, "rec_yd": 10}},
                {"player_id": "3", "opponent": "TB", "stats": {"rec": 7, "rec_yd": 90, "rec_td": 1}}]


def ids(results):
    return [pid for pid, _ in results]


def test_search_prefers_relevant_active_players():
    assert ids(players.search(DB, "taylor"))[0] == "1"
    assert ids(players.search(DB, "st brown")) == ["3"]
    assert ids(players.search(DB, "Amon-Ra St. Brown")) == ["3"]
    assert ids(players.search(DB, "chiefs")) == ["KC"]
    assert ids(players.search(DB, "lineman")) == []  # non-fantasy position
    assert players.search(DB, " ") == []


def test_default_scoring_without_league_and_league_scoring_with_one():
    c = FakeClient()
    label, rules = compare.resolve_scoring(c, None, None)
    assert label.startswith("Sleeper default") and rules["rec"] == 1
    assert compare.resolve_scoring(c, "half", None)[1]["rec"] == 0.5
    assert compare.resolve_scoring(c, None, "L1")[0] == "My League"
    assert compare.resolve_scoring(c, "std", "L1")[1]["rec"] == 0  # explicit preset wins over league
    assert c.league_calls == 1


def test_outlooks_week_by_week_with_bye():
    label, start, outs = compare.compare_players(FakeClient(), ["1", "3"], weeks=2)
    jt, ars = outs
    assert start == 11
    assert jt.weeks[0].points == 9 + 6 + 2 + 1  # PPR default
    assert jt.weeks[1].bye and jt.weeks[1].points == 0
    assert jt.average == jt.weeks[0].points  # bye excluded from average
    assert ars.total == round(7 + 9 + 6 + 6 + 8, 2)


def test_player_without_projection_is_not_a_bye():
    _, _, outs = compare.compare_players(FakeClient(), ["2"], weeks=1)
    assert not outs[0].weeks[0].projected and not outs[0].weeks[0].bye


def test_compare_endpoint_works_without_sign_in(monkeypatch):
    monkeypatch.setattr(web, "client", FakeClient())
    api = TestClient(web.app)
    r = api.get("/api/players/search", params={"q": "taylor"})
    assert r.json()["results"][0]["name"] == "Jonathan Taylor"
    r = api.get("/api/players/compare", params={"ids": "1,3,1", "weeks": 2})
    body = r.json()
    assert r.status_code == 200 and body["scoring"].startswith("Sleeper default")
    assert [p["id"] for p in body["players"]] == ["1", "3"]
    assert body["players"][0]["weeks"][1]["bye"] is True
    assert api.get("/api/players/compare", params={"ids": "1", "scoring": "bogus"}).status_code == 422


def test_outlook_uses_projection_even_when_listed_out():
    db = dict(DB)
    db["1"] = {**DB["1"], "injury_status": "Out"}
    c = FakeClient()
    c.players = lambda: db
    _, _, outs = compare.compare_players(c, ["1"], weeks=1)
    assert outs[0].injury_status == "Out"
    assert outs[0].weeks[0].points == 18


def test_normalize_strips_punctuation_and_suffixes():
    assert normalize("A.J. Brown Jr.") == "ajbrown"
    assert normalize("Marvin Harrison II") == "marvinharrison"
    assert normalize("Amon-Ra St. Brown") == "amonrastbrown"
    assert normalize("Ivan Smith") == "ivansmith"  # "iv" inside a word is not a suffix


def test_search_finds_two_way_players_by_fantasy_position():
    db = {"th": {"full_name": "Travis Hunter", "first_name": "Travis", "last_name": "Hunter",
                 "position": "DB", "fantasy_positions": ["DB", "WR"], "team": "JAX", "search_rank": 50}}
    assert [pid for pid, _ in players.search(db, "hunter")] == ["th"]
