"""Tests for the Matchup feature: lineup pairing, re-seating, optimal views, win probabilities and its API."""

import numpy as np
from fastapi.testclient import TestClient

from fantasycoach.interface import web
from fantasycoach.league import matchup as matchup_mod
from fantasycoach.league.lineup import LineupSlot
from fantasycoach.core.models import Player
from fantasycoach.projections import simulation

SLOTS = ["QB", "RB", "WR", "FLEX", "BN"]


def proj(pid, team, pos, opp, **stats):
    return {"player_id": pid, "team": team, "opponent": opp, "player": {"position": pos}, "stats": stats}


PROJ = [
    proj("q1", "BUF", "QB", "MIA", pass_yd=260, pass_td=2, pts_ppr=18.4),
    proj("r1", "DET", "RB", "TB", rush_yd=80, rush_td=0.7, rec=3, rec_yd=20, pts_ppr=18.2),
    proj("w1", "BUF", "WR", "MIA", rec=6, rec_yd=80, rec_td=0.5, pts_ppr=17.0),
    proj("q2", "KC", "QB", "ARI", pass_yd=240, pass_td=1.6, pts_ppr=16.0),
    proj("r2", "SF", "RB", "MIN", rush_yd=60, rush_td=0.5, rec=2, rec_yd=15, pts_ppr=12.5),
    proj("w2", "KC", "WR", "ARI", rec=4, rec_yd=50, rec_td=0.3, pts_ppr=10.8),
    proj("w3", "SF", "WR", "MIN", rec=3, rec_yd=40, rec_td=0.2, pts_ppr=8.2),
]
DB = {r["player_id"]: {"full_name": f"Player {r['player_id']}", "position": r["player"]["position"],
                       "team": r["team"], "fantasy_positions": [r["player"]["position"]]} for r in PROJ}


class FakeClient:
    def state(self):
        return {"week": 11, "league_season": "2026", "season": "2026"}

    def league(self, league_id):
        return {"name": "Test League", "season": "2026", "roster_positions": SLOTS,
                "scoring_settings": {"pass_yd": 0.04, "pass_td": 4, "rush_yd": 0.1, "rush_td": 6,
                                     "rec": 1, "rec_yd": 0.1, "rec_td": 6}}

    def rosters(self, league_id):
        return [{"roster_id": 1, "owner_id": "me", "players": ["q1", "r1", "w1", "w3"], "starters": ["q1", "r1", "w1", "w3"]},
                {"roster_id": 2, "owner_id": "you", "players": ["q2", "r2", "w2"], "starters": ["q2", "r2", "w2", "0"]}]

    def league_users(self, league_id):
        return [{"user_id": "me", "display_name": "me", "metadata": {"team_name": "Home Team"}},
                {"user_id": "you", "display_name": "rival", "avatar": "abc"}]

    def matchups(self, league_id, week):
        return [{"roster_id": 1, "matchup_id": 7, "starters": ["q1", "r1", "w1", "w3"]},
                {"roster_id": 2, "matchup_id": 7, "starters": ["q2", "r2", "w2", "0"]}]

    def players(self):
        return DB

    def projections(self, season, week, positions, persist=True, memo=True):
        return PROJ


def test_matchup_pairs_both_lineups_with_projections():
    m = matchup_mod.matchup(FakeClient(), "L", "me")
    assert m.week == 11 and m.league == "Test League"
    assert [s.slot for s in m.me.current.slots] == ["QB", "RB", "WR", "FLEX"]
    assert [s.player.player_id for s in m.me.current.slots] == ["q1", "r1", "w1", "w3"]
    assert m.opponent.team_name == "rival" and m.opponent.avatar.endswith("/abc")
    assert m.opponent.current.slots[3].player is None  # empty slot ("0")
    assert m.me.team_name == "Home Team"
    assert m.me.current.projected > m.opponent.current.projected
    r = m.me.current.range
    assert r.floor < r.expected < r.ceiling
    assert 0.5 < m.win_probabilities[("current", "current")] < 1.0
    assert all(s.player.range for s in m.me.current.slots)


def test_matchup_without_opponent():
    c = FakeClient()
    c.matchups = lambda league_id, week: [{"roster_id": 1, "matchup_id": None, "starters": ["q1", "r1", "w1", "w3"]}]
    m = matchup_mod.matchup(c, "L", "me")
    assert m.opponent is None and m.win_probabilities is None and m.me.current.range is not None


def test_matchup_endpoint(monkeypatch):
    monkeypatch.setattr(web, "client", FakeClient())
    body = TestClient(web.app).get("/api/matchup", params={"league_id": "L", "user_id": "me"}).json()
    assert body["me"]["team_name"] == "Home Team" and body["opponent"]["owner"] == "rival"
    slots = body["me"]["views"]["current"]["slots"]
    assert len(slots) == 4 and slots[0]["player"]["range"]
    assert 0 < body["win_probabilities"]["current|current"] < 1


def test_win_probability_symmetry_and_ordering():
    rng = np.random.default_rng(0)
    a = [rng.normal(100, 15, simulation.SIMS)]
    b = [rng.normal(100, 15, simulation.SIMS)]
    c = [rng.normal(130, 15, simulation.SIMS)]
    p_ab, p_ba = simulation.win_probability(a, b), simulation.win_probability(b, a)
    assert abs(p_ab + p_ba - 1) < 1e-9
    assert 0.4 < p_ab < 0.6
    assert simulation.win_probability(c, a) > 0.9


def _p(pid, pos, pts):
    return Player(pid, pid, pos, "XX", projected=pts, eligible=[pos])


def test_order_by_projection_reseats_interchangeable_players():
    slots = [LineupSlot("QB", _p("q", "QB", 20)), LineupSlot("RB", _p("r_low", "RB", 9)),
             LineupSlot("RB", _p("r_mid", "RB", 12)), LineupSlot("FLEX", _p("r_top", "RB", 16)),
             LineupSlot("FLEX", _p("w_low", "WR", 7)), LineupSlot("FLEX", _p("w_hi", "WR", 11))]
    out = matchup_mod.order_by_projection(slots)
    assert [s.slot for s in out] == ["QB", "RB", "RB", "FLEX", "FLEX", "FLEX"]
    assert [s.player.player_id for s in out] == ["q", "r_top", "r_mid", "w_hi", "r_low", "w_low"]


def test_order_by_projection_keeps_empty_slots_and_original_when_infeasible():
    slots = [LineupSlot("RB", _p("r1", "RB", 5)), LineupSlot("RB", None), LineupSlot("K", _p("k", "K", 8))]
    out = matchup_mod.order_by_projection(slots)
    assert [s.player.player_id if s.player else None for s in out] == ["r1", None, "k"]
    odd = [LineupSlot("WR", _p("d", "DB", 5))]  # starter not eligible for any slot by position
    assert matchup_mod.order_by_projection(odd) is odd


BENCH_STAR = proj("wb", "DET", "WR", "TB", rec=8, rec_yd=110, rec_td=0.9, pts_ppr=24.4)


class BenchStarClient(FakeClient):
    """Your best WR is on the bench; the opponent has an empty FLEX and a free agent-quality WR on IR."""

    def projections(self, season, week, positions, persist=True, memo=True):
        return PROJ + [BENCH_STAR, proj("ir", "SF", "WR", "MIN", rec=9, rec_yd=120, rec_td=1, pts_ppr=27.0)]

    def players(self):
        return {**DB, "wb": {"full_name": "Bench Star", "position": "WR", "team": "DET", "fantasy_positions": ["WR"]},
                "ir": {"full_name": "Hurt Guy", "position": "WR", "team": "SF", "fantasy_positions": ["WR"]}}

    def rosters(self, league_id):
        mine, theirs = super().rosters(league_id)
        return [{**mine, "players": mine["players"] + ["wb"]},
                {**theirs, "players": theirs["players"] + ["w3x", "ir"], "reserve": ["ir"]}]


def test_optimal_views_use_bench_and_skip_injured_reserve():
    m = matchup_mod.matchup(BenchStarClient(), "L", "me")
    cur_ids = {s.player.player_id for s in m.me.current.slots if s.player}
    opt_ids = {s.player.player_id for s in m.me.optimal.slots if s.player}
    assert "wb" not in cur_ids and "wb" in opt_ids
    assert m.me.optimal.projected > m.me.current.projected
    assert m.me.optimal.range.expected > m.me.current.range.expected
    their_opt = {s.player.player_id for s in m.opponent.optimal.slots if s.player}
    assert "ir" not in their_opt  # on IR: never in the optimal lineup


def test_win_probabilities_for_every_pairing():
    m = matchup_mod.matchup(BenchStarClient(), "L", "me")
    w = m.win_probabilities
    assert set(w) == {(a, b) for a in ("current", "optimal") for b in ("current", "optimal")}
    assert w[("optimal", "current")] > w[("current", "current")]  # starting the bench star helps you


def test_matchup_endpoint_exposes_both_views(monkeypatch):
    monkeypatch.setattr(web, "client", BenchStarClient())
    body = TestClient(web.app).get("/api/matchup", params={"league_id": "L", "user_id": "me"}).json()
    me = body["me"]
    assert set(me["views"]) == {"current", "optimal"}
    assert me["views"]["optimal"]["projected"] > me["views"]["current"]["projected"]
    assert set(body["win_probabilities"]) == {"current|current", "current|optimal", "optimal|current", "optimal|optimal"}
