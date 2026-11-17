"""Expected score, floor and ceiling by simulating a player's stat line thousands of times.

For each simulated game:

* **Volume stats** (pass/rush/rec yards, receptions) are log-normal around a centre:
  the DraftKings prop line when one exists (a median), otherwise Sleeper's projection
  (a mean). Their spread comes from how far this player's real games have landed from
  projections (``history.Spread``).
* **Touchdowns, interceptions, fumbles** are Poisson. TD rates come from Sleeper, scaled by
  how many points Vegas expects the team to score; a passing-TD prop line overrides.
* A shared "game script" factor correlates a player's stats (a big yardage day usually
  comes with more catches and a better TD chance), which widens the range realistically.

Points use the league's scoring. Expected = mean, floor = 10th percentile (90% of games
score at least this), ceiling = 90th percentile (90% of games score at most this).
Kickers and defenses are simulated directly on points from their projection.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

import numpy as np

from .history import DEFAULT_PTS_SD, VOLUME_STATS, Spread
from .scoring import score

SIMS = 4000
POISSON_STATS = ("pass_td", "pass_int", "rush_td", "rec_td", "fum_lost")
TD_STATS = {"pass_td", "rush_td", "rec_td"}
CORRELATED = set(VOLUME_STATS) | TD_STATS
MODELED = set(VOLUME_STATS) | set(POISSON_STATS)
# Share of variance explained by the shared game-script factor (tuned by backtest).
RHO = 0.4
# Scale applied to learned sigmas (tuned by backtest so ~10% of games fall outside each side).
SIGMA_SCALE = 1.0
POSITION_SCALE = {"QB": 0.8, "RB": 0.9, "WR": 1.05, "TE": 1.45, "K": 1.15, "DEF": 1.05}
# Kicker/defense points are right-skewed: shifted log-normal with this offset.
POINTS_OFFSET = {"K": 1.0, "DEF": 6.0}
FLOOR_Q, CEILING_Q = 10, 90
# Whole-lineup range: the team beats its floor in 95% of simulated weeks and stays under its
# ceiling in 95%.
TEAM_FLOOR_Q, TEAM_CEILING_Q = 5, 95
# Share of each player's game-script factor that is shared with NFL teammates in the same
# lineup (a QB and his receiver boom or bust together). Defenses don't share it.
TEAM_RHO = 0.25
# Summing nine players compounds their far tails, which run a little heavy. These factors shrink
# the team's simulated deviation from its mean so ~5% of real lineups fall outside each side
# (fit on 2,400 random lineups from 2026 weeks 3-10; upper factor stable across halves).
TEAM_LOW_SCALE, TEAM_HIGH_SCALE = 0.90, 0.73


@dataclass
class Range:
    expected: float
    floor: float
    ceiling: float
    market_stats: list[str] = field(default_factory=list)  # stats centred on a betting line
    team_points: float | None = None  # Vegas implied team total used for TD scaling

    @property
    def uses_market(self) -> bool:
        return bool(self.market_stats) or self.team_points is not None


def _phi(z: np.ndarray) -> np.ndarray:
    """Standard normal CDF (Abramowitz & Stegun 7.1.26, |error| < 1.5e-7)."""
    x = np.abs(z) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-x * x)
    return 0.5 * (1.0 + np.sign(z) * erf)


def _poisson_ppf(u: np.ndarray, lam: float) -> np.ndarray:
    kmax = max(12, int(lam * 4 + 10))
    k = np.arange(kmax + 1)
    log_pmf = k * math.log(lam) - lam - np.array([math.lgamma(i + 1) for i in k])
    cdf = np.cumsum(np.exp(log_pmf))
    return (u[:, None] > cdf[None, :]).sum(axis=1)


def poisson_rate_for_line(line: float) -> float:
    """Poisson rate whose median-split matches an x.5 prop line (P(X <= floor(line)) = 0.5)."""
    target = math.floor(line)
    lo, hi = 1e-4, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        cdf = sum(math.exp(-mid) * mid**i / math.factorial(i) for i in range(target + 1))
        lo, hi = (mid, hi) if cdf > 0.5 else (lo, mid)
    return (lo + hi) / 2


@dataclass
class TeamRange:
    expected: float
    floor: float  # 5th percentile: beaten in 95% of simulated weeks
    ceiling: float  # 95th percentile: not exceeded in 95% of simulated weeks


def simulate(
    player_id: str,
    position: str,
    projected_stats: dict[str, float],
    scoring: dict[str, float],
    spread: Spread,
    lines: dict[str, float] | None = None,
    td_scale: float = 1.0,
    team_points: float | None = None,
    line_calibration: dict[str, float] | None = None,
    rng: np.random.Generator | None = None,
    sims: int = SIMS,
) -> Range:
    pts, used_lines = simulate_points(
        player_id, position, projected_stats, scoring, spread, lines, td_scale, line_calibration, rng, sims
    )
    return _summarise(pts, used_lines, None if position in ("K", "DEF") else team_points)


def simulate_points(
    player_id: str,
    position: str,
    projected_stats: dict[str, float],
    scoring: dict[str, float],
    spread: Spread,
    lines: dict[str, float] | None = None,
    td_scale: float = 1.0,
    line_calibration: dict[str, float] | None = None,
    rng: np.random.Generator | None = None,
    sims: int = SIMS,
    shared_z: np.ndarray | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Simulated fantasy points (one value per simulated game) and the stats centred on lines.

    ``shared_z`` is an NFL-team game factor; when given, ``TEAM_RHO`` of this player's own
    game-script factor comes from it so teammates' outcomes are correlated.
    """
    # Stable seed (Python's hash() is randomised per process) so the same inputs give the same range.
    rng = rng or np.random.default_rng(zlib.crc32(f"{player_id}|{sorted(projected_stats.items())}|{lines}".encode()))
    lines = lines or {}
    calibration = line_calibration or {}
    scoring = scoring or {}

    scale = SIGMA_SCALE * POSITION_SCALE.get(position, 1.0)
    if position in ("K", "DEF"):
        mean = score(projected_stats, scoring)
        c = POINTS_OFFSET[position]
        sd = spread.points_sd.get(position, DEFAULT_PTS_SD[position]) * scale
        sigma = math.sqrt(math.log(1 + (sd / max(mean + c, 1.0)) ** 2))
        z = rng.standard_normal(sims)
        if shared_z is not None and position == "K":
            z = math.sqrt(TEAM_RHO) * shared_z + math.sqrt(1 - TEAM_RHO) * z
        return (mean + c) * np.exp(sigma * z - sigma**2 / 2) - c, []

    z0 = rng.standard_normal(sims)
    if shared_z is not None:
        z0 = math.sqrt(TEAM_RHO) * shared_z + math.sqrt(1 - TEAM_RHO) * z0
    # Stats we don't simulate (bonuses, first downs, 2-pt...) contribute their projected value.
    base = sum(w * (projected_stats.get(k) or 0.0) for k, w in scoring.items() if k not in MODELED)
    pts = np.full(sims, float(base))
    used_lines: list[str] = []

    for stat, (c, _) in VOLUME_STATS.items():
        w = scoring.get(stat, 0.0)
        line = lines.get(stat)
        proj = projected_stats.get(stat) or 0.0
        if not w or (line is None and proj <= 0):
            continue
        base_sigma = spread.sigma(player_id, position, stat)
        sigma = base_sigma * scale
        if line is not None:
            # Convert the market's line to a projected mean, then simulate exactly as the backtested
            # model does with a projection. Preferred: this week's league-wide line-to-projection
            # ratio (keeps the overall level anchored to Sleeper, removes book shading). Fallback:
            # how median games historically compare with projections.
            k = calibration.get(stat)
            if k is None:
                k = math.exp(-spread.median_log_ratio(position, stat, base_sigma))
            centre = (line + c) * k - c
            used_lines.append(stat)
        else:
            centre = proj
        z = math.sqrt(RHO) * z0 + math.sqrt(1 - RHO) * rng.standard_normal(sims)
        x = (centre + c) * np.exp(sigma * z - sigma**2 / 2) - c  # mean = centre
        x = np.maximum(x, 0.0)
        if stat == "rec":
            x = np.round(x)
        pts += w * x

    for stat in POISSON_STATS:
        w = scoring.get(stat, 0.0)
        if not w:
            continue
        if stat in lines:
            lam = poisson_rate_for_line(lines[stat]) * calibration.get(stat, 1.0)
            used_lines.append(stat)
        else:
            lam = (projected_stats.get(stat) or 0.0) * (td_scale if stat in TD_STATS else 1.0)
        if lam <= 0:
            continue
        rho = RHO if stat in CORRELATED else 0.0
        z = math.sqrt(rho) * z0 + math.sqrt(1 - rho) * rng.standard_normal(sims)
        pts += w * _poisson_ppf(_phi(z), lam)

    return pts, used_lines


def win_probability(samples_a: list[np.ndarray], samples_b: list[np.ndarray], sims: int = SIMS) -> float:
    """Share of simulated weeks in which lineup A outscores lineup B (ties count half).

    Team totals' deviations are shrunk by the average of the team calibration factors, the same
    correction that makes team floors and ceilings match real outcomes.
    """
    k = (TEAM_LOW_SCALE + TEAM_HIGH_SCALE) / 2
    totals = []
    for samples in (samples_a, samples_b):
        t = np.sum(samples, axis=0) if samples else np.zeros(sims)
        totals.append(t.mean() + k * (t - t.mean()))
    a, b = totals
    return round(float(np.mean(a > b) + 0.5 * np.mean(a == b)), 4)


def team_range(player_samples: list[np.ndarray], sims: int = SIMS) -> TeamRange:
    """Sum players' simulated games week by week and read off the team's 5th/95th percentiles."""
    total = np.zeros(sims)
    for pts in player_samples:
        total += pts
    mean = float(total.mean())
    q_low, q_high = np.percentile(total, [TEAM_FLOOR_Q, TEAM_CEILING_Q])
    floor = mean + TEAM_LOW_SCALE * (q_low - mean)
    ceiling = mean + TEAM_HIGH_SCALE * (q_high - mean)
    return TeamRange(round(mean, 2), round(float(floor), 2), round(float(ceiling), 2))


def _summarise(pts: np.ndarray, used_lines: list[str], team_points: float | None) -> Range:
    floor, ceiling = np.percentile(pts, [FLOOR_Q, CEILING_Q])
    return Range(
        expected=round(float(pts.mean()), 2),
        floor=round(float(floor), 2),
        ceiling=round(float(ceiling), 2),
        market_stats=used_lines,
        team_points=team_points,
    )


def line_calibration(
    lines: dict[str, dict[str, float]], projections: dict[str, dict[str, float]], positions: dict[str, str]
) -> dict[tuple[str | None, str], float]:
    """This week's typical ratio of Sleeper's projection to the betting line, per (position, stat).

    Volume stats compare (projection + c) / (line + c); Poisson stats compare the projected rate
    with the rate implied by the line. Keys with fewer than 6 players fall back to a pooled
    (None, stat) ratio. Applying the ratio keeps lines' relative information (who the market
    likes more or less than Sleeper) while the overall level stays on Sleeper's unbiased scale.
    """
    groups: dict[tuple[str | None, str], list[float]] = {}
    for pid, player_lines in lines.items():
        proj = projections.get(pid) or {}
        pos = positions.get(pid)
        for stat, line in player_lines.items():
            value = proj.get(stat) or 0.0
            if value <= 0:
                continue
            if stat in VOLUME_STATS:
                c = VOLUME_STATS[stat][0]
                ratio = (value + c) / (line + c)
            elif stat in POISSON_STATS:
                ratio = value / poisson_rate_for_line(line)
            else:
                continue
            groups.setdefault((pos, stat), []).append(ratio)
            groups.setdefault((None, stat), []).append(ratio)
    return {key: round(float(np.median(v)), 4) for key, v in groups.items() if len(v) >= 6}


def team_td_scales(week_projections: list[dict], team_points: dict[str, float]) -> dict[str, float]:
    """How much Vegas' implied team total exceeds what Sleeper's projections add up to.

    Sleeper-implied points ~ 7 x projected offensive TDs + 3 x projected field goals.
    The ratio (clamped to 0.7-1.4) scales each player's touchdown rates.
    """
    tds: dict[str, float] = {}
    fgs: dict[str, float] = {}
    for row in week_projections:
        team = row.get("team")
        s = row.get("stats") or {}
        pos = (row.get("player") or {}).get("position")
        if not team or pos == "DEF":
            continue
        tds[team] = tds.get(team, 0.0) + (s.get("rush_td") or 0.0) + (s.get("rec_td") or 0.0)
        if pos == "K":
            fgs[team] = fgs.get(team, 0.0) + (s.get("fgm") or 0.0)
    scales = {}
    for team, vegas in team_points.items():
        implied = 6.95 * tds.get(team, 0.0) + 3.0 * fgs.get(team, 0.0)
        if implied > 5:
            scales[team] = round(min(1.4, max(0.7, vegas / implied)), 3)
    return scales
