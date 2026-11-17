from fantasycoach.lineup import advise, optimize
from fantasycoach.models import Player
from fantasycoach.scoring import availability_factor, score
from fantasycoach.waivers import recommend


def P(pid, pos, pts, status=None, eligible=None):
    adj = round(pts * availability_factor(status), 2)
    return Player(pid, f"Player {pid}", pos, "XX", status, "YY", pts, adj, eligible or [pos])


def test_score_uses_league_settings():
    stats = {"pass_yd": 250, "pass_td": 2, "rec": 5, "rec_yd": 50, "pts_ppr": 99}
    assert score(stats, {"pass_yd": 0.04, "pass_td": 4, "rec": 0.5, "rec_yd": 0.1}) == 25.5


def test_score_falls_back_to_ppr():
    assert score({"pts_ppr": 12.3}, {}) == 12.3


def test_out_players_are_zeroed_and_doubtful_discounted():
    assert availability_factor("Out") == 0
    assert availability_factor("IR") == 0
    assert availability_factor("Doubtful") == 0.25
    assert availability_factor("Questionable") == 1


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


def test_injured_starter_gets_benched():
    slots = ["RB", "RB"]
    players = [P("a", "RB", 18, "Out"), P("b", "RB", 10), P("c", "RB", 8)]
    advice = advise(players, slots, current_starter_ids=["a", "b"])
    assert [p.player_id for p in advice.to_start] == ["c"]
    assert [p.player_id for p in advice.to_bench] == ["a"]
    assert advice.gain == 8


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


def test_out_status_only_affects_current_week():
    assert availability_factor("Out", weeks_ahead=1) == 1
    assert availability_factor("Doubtful", weeks_ahead=2) == 1
    assert availability_factor("IR", weeks_ahead=1) == 0


def test_score_tolerates_null_stats():
    assert score({"rec": None, "rec_yd": 40}, {"rec": 1, "rec_yd": 0.1}) == 4.0
    assert score({"pts_ppr": None}, {}) == 0.0


def test_two_way_player_maps_to_league_position():
    from fantasycoach.league import LeagueContext
    ctx = LeagueContext({"roster_positions": ["WR", "RB"]}, "2026", 11, {}, set(), ["RB", "WR"],
                        {"1": {"full_name": "Two Way", "position": "DB", "fantasy_positions": ["DB", "WR"]},
                         "2": {"full_name": "Pure DB", "position": "DB", "fantasy_positions": ["DB"]}})
    assert ctx.build_player("1", None).position == "WR"
    fas = ctx.free_agents({"1": {"stats": {}}, "2": {"stats": {}}})
    assert [p.player_id for p in fas] == ["1"]
