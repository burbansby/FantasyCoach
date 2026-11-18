"""Tests for league logic: scoring, lineup optimisation, waiver picks and league context."""

from fantasycoach.core.models import Player
from fantasycoach.core.scoring import score
from fantasycoach.league.context import LeagueContext
from fantasycoach.league.lineup import advise, optimize
from fantasycoach.league.waivers import recommend


def P(pid, pos, pts, status=None, eligible=None):
    return Player(pid, f"Player {pid}", pos, "XX", status, "YY", pts, eligible or [pos])


def test_score_uses_league_settings():
    stats = {"pass_yd": 250, "pass_td": 2, "rec": 5, "rec_yd": 50, "pts_ppr": 99}
    assert score(stats, {"pass_yd": 0.04, "pass_td": 4, "rec": 0.5, "rec_yd": 0.1}) == 25.5


def test_score_falls_back_to_ppr():
    assert score({"pts_ppr": 12.3}, {}) == 12.3


def test_optimize_fills_flex_with_best_leftover():
    slots = ["QB", "RB", "WR", "FLEX"]
    players = [P("q", "QB", 20), P("r1", "RB", 15), P("r2", "RB", 9), P("w1", "WR", 12), P("w2", "WR", 10)]
    lineup, bench = optimize(players, slots)
    assert [s.player.player_id for s in lineup] == ["q", "r1", "w1", "w2"]
    assert {p.player_id for p in bench} == {"r2"}


def test_superflex_takes_second_qb():
    slots = ["QB", "RB", "SUPER_FLEX"]
    players = [P("q1", "QB", 22), P("q2", "QB", 18), P("r1", "RB", 14), P("r2", "RB", 11)]
    lineup, _ = optimize(players, slots)
    assert [s.player.player_id for s in lineup] == ["q1", "r1", "q2"]


def test_injury_status_does_not_change_decisions():
    # Injury reports lag, so a listed-Out player is judged on his actual projection.
    slots = ["RB", "RB"]
    players = [P("a", "RB", 18, "Out"), P("b", "RB", 10), P("c", "RB", 8)]
    advice = advise(players, slots, current_starter_ids=["b", "c"])
    assert [p.player_id for p in advice.to_start] == ["a"]
    assert [p.player_id for p in advice.to_bench] == ["c"]
    assert advice.gain == 10


def test_empty_slot_when_no_eligible_player():
    lineup, _ = optimize([P("r", "RB", 10)], ["K"])
    assert lineup[0].player is None


def test_waivers_pairs_with_weakest_same_position_and_skips_protected():
    mine = [P("w1", "WR", 15), P("w2", "WR", 4), P("ir", "WR", 0)]
    fas = [P("fa1", "WR", 9), P("fa2", "RB", 7), P("fa3", "WR", 3)]
    horizon = {"w1": 45, "w2": 12, "ir": 0, "fa1": 27, "fa2": 21, "fa3": 9}
    picks = recommend(fas, mine, horizon, protected_ids={"ir"})
    ids = [(pk.add.player_id, pk.drop.player_id if pk.drop else None) for pk in picks]
    # fa2 has no RB to replace -> full value; fa1 replaces w2; fa3 is worse than w2.
    assert ids == [("fa2", None), ("fa1", "w2")]
    assert picks[1].gain == 15


def test_league_projection_ignores_injury_status():
    ctx = LeagueContext({}, "2026", 11, {}, set(), ["RB"],
                        {"1": {"full_name": "Hurt Guy", "position": "RB", "injury_status": "IR"}})
    p = ctx.build_player("1", {"stats": {"pts_ppr": 12.5}, "opponent": "DAL"})
    assert p.projected == 12.5 and p.injury_status == "IR"


def test_score_tolerates_null_stats():
    assert score({"rec": None, "rec_yd": 40}, {"rec": 1, "rec_yd": 0.1}) == 4.0
    assert score({"pts_ppr": None}, {}) == 0.0


def test_two_way_player_maps_to_league_position():
    ctx = LeagueContext({"roster_positions": ["WR", "RB"]}, "2026", 11, {}, set(), ["RB", "WR"],
                        {"1": {"full_name": "Two Way", "position": "DB", "fantasy_positions": ["DB", "WR"]},
                         "2": {"full_name": "Pure DB", "position": "DB", "fantasy_positions": ["DB"]}})
    assert ctx.build_player("1", None).position == "WR"
    fas = ctx.free_agents({"1": {"stats": {}}, "2": {"stats": {}}})
    assert [p.player_id for p in fas] == ["1"]
