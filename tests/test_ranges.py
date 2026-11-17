import math

import numpy as np

from fantasycoach import ranges
from fantasycoach.history import HistoryStore, Spread, _week_sequence
from fantasycoach.market import implied_points
from fantasycoach.players import SLEEPER_DEFAULT_SCORING as PPR

WR = {"rec": 5.0, "rec_yd": 60.0, "rec_td": 0.4, "rush_yd": 2.0}


def sim(stats=WR, pos="WR", **kw):
    return ranges.simulate("p1", pos, stats, PPR, Spread(), rng=np.random.default_rng(7), **kw)


def test_expected_matches_projection_and_range_brackets_it():
    r = sim()
    sleeper = 5 + 6 + 0.4 * 6 + 0.2
    assert abs(r.expected - sleeper) < 1.0
    assert r.floor < r.expected < r.ceiling
    assert r.floor >= 0


def test_range_widens_with_more_uncertainty():
    tight = ranges.simulate("p", "WR", WR, PPR, Spread(position_sigma={"WR": {"rec": 0.2, "rec_yd": 0.2}}),
                            rng=np.random.default_rng(1))
    wide = ranges.simulate("p", "WR", WR, PPR, Spread(position_sigma={"WR": {"rec": 0.8, "rec_yd": 0.9}}),
                           rng=np.random.default_rng(1))
    assert wide.ceiling - wide.floor > tight.ceiling - tight.floor


def test_betting_line_moves_the_centre():
    low = sim(lines={"rec_yd": 30.5}, line_calibration={"rec_yd": 1.0})
    high = sim(lines={"rec_yd": 95.5}, line_calibration={"rec_yd": 1.0})
    assert high.expected > low.expected + 5
    assert high.market_stats == ["rec_yd"] and high.uses_market


def test_team_total_scales_touchdowns():
    base = sim()
    boosted = sim(td_scale=1.4, team_points=31.0)
    assert boosted.expected > base.expected
    assert boosted.uses_market


def test_kicker_and_defense_simulated_on_points():
    r = ranges.simulate("KC", "DEF", {"pts_ppr": 8.0}, {"pts_ppr": 1}, Spread(), rng=np.random.default_rng(3))
    assert abs(r.expected - 8.0) < 0.5 and r.floor < 8 < r.ceiling
    assert r.ceiling - 8 > 8 - r.floor  # right-skewed: big games are possible, disasters are bounded


def test_poisson_rate_for_line():
    assert abs(ranges.poisson_rate_for_line(0.5) - math.log(2)) < 1e-3
    lam = ranges.poisson_rate_for_line(1.5)
    assert abs(math.exp(-lam) * (1 + lam) - 0.5) < 1e-3


def test_line_calibration_anchors_level_to_projections():
    # Books shade every line ~10% above the projection; calibration should recover that ratio.
    lines = {str(i): {"rec_yd": 50.0 * 1.1 + i} for i in range(8)}
    proj = {str(i): {"rec_yd": 50.0 + i / 1.1} for i in range(8)}
    cal = ranges.line_calibration(lines, proj, {str(i): "WR" for i in range(8)})
    assert 0.88 < cal[("WR", "rec_yd")] < 0.94
    assert ("WR", "rec_yd") in cal and (None, "rec_yd") in cal


def test_team_td_scales_compare_vegas_to_sleeper():
    rows = [{"team": "DAL", "player": {"position": "RB"}, "stats": {"rush_td": 1.5, "rec_td": 0.5}},
            {"team": "DAL", "player": {"position": "K"}, "stats": {"fgm": 2.0}}]
    scales = ranges.team_td_scales(rows, {"DAL": 26.0})
    assert abs(scales["DAL"] - 26.0 / (6.95 * 2 + 6)) < 0.01


def test_implied_points_from_spread_and_total():
    assert implied_points("HOU -12.5", 44.5, "HOU", "IND") == {"HOU": 28.5, "IND": 16.0}
    assert implied_points("EVEN", 40, "A", "B") == {"A": 20, "B": 20}
    assert implied_points(None, None, "A", "B") == {}


def test_week_sequence_crosses_seasons():
    assert _week_sequence(2026, 3, 4) == [(2026, 2), (2026, 1), (2025, 18), (2025, 17)]


class HistClient:
    def projections(self, season, week, positions, persist=True, memo=True):
        return [{"player_id": f"w{i}", "player": {"position": "WR"}, "stats": {"pts_ppr": 12, "rec": 5, "rec_yd": 60}}
                for i in range(40)]

    def stats(self, season, week, positions, persist=True, memo=True):
        # Half the receivers boom, half bust, relative to a 60-yard projection.
        return [{"player_id": f"w{i}", "stats": {"gp": 1, "rec": 5, "rec_yd": 100 if i % 2 else 25, "pts_ppr": 12}}
                for i in range(40)]


def test_history_learns_spread_from_misses(tmp_path):
    spread = HistoryStore(HistClient(), tmp_path).spread(2026, 5, lookback=4)
    assert spread.weeks_used == 4
    assert spread.position_sigma["WR"]["rec_yd"] > 0.3   # big misses -> wide sigma
    assert spread.position_sigma["WR"]["rec"] < 0.05     # receptions always on projection
    assert any(tmp_path.iterdir())                         # condensed weeks cached to disk


def _lineup_samples(shared=None):
    out = []
    for i in range(9):
        pts, _ = ranges.simulate_points(f"p{i}", "WR", WR, PPR, Spread(), rng=np.random.default_rng(i),
                                        shared_z=shared)
        out.append(pts)
    return out


def test_team_range_is_tighter_than_summing_individual_floors():
    samples = _lineup_samples()
    team = ranges.team_range(samples)
    floors = sum(np.percentile(s, ranges.TEAM_FLOOR_Q) for s in samples)
    ceilings = sum(np.percentile(s, ranges.TEAM_CEILING_Q) for s in samples)
    assert abs(team.expected - sum(s.mean() for s in samples)) < 0.01
    assert floors < team.floor < team.expected < team.ceiling < ceilings


def test_team_range_applies_calibration_scales():
    total = np.sum(_lineup_samples(), axis=0)
    mean = total.mean()
    q5, q95 = np.percentile(total, [5, 95])
    team = ranges.team_range(_lineup_samples())
    assert abs(team.floor - (mean + ranges.TEAM_LOW_SCALE * (q5 - mean))) < 0.01
    assert abs(team.ceiling - (mean + ranges.TEAM_HIGH_SCALE * (q95 - mean))) < 0.01


def test_teammates_sharing_a_game_widen_the_team_range():
    independent = ranges.team_range(_lineup_samples())
    stacked = ranges.team_range(_lineup_samples(shared=np.random.default_rng(99).standard_normal(ranges.SIMS)))
    assert stacked.ceiling - stacked.floor > independent.ceiling - independent.floor
