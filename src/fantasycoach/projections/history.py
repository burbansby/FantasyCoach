"""How far real outcomes land from projections, learned from past weeks of Sleeper data.

For each completed week we pair every player's projected stat line with what actually
happened, then measure the spread of the log-ratio ``ln((actual + c) / (projected + c))``
per stat. That spread (sigma) is what turns a single projection into a range:

* per player when they have enough games, shrunk toward their position's typical value;
* per position otherwise (rookies, newly relevant players).

Completed weeks never change, so each one is condensed to a small JSON file and kept
forever; only the raw download is large (~3 MB per week, once).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from fantasycoach.core.models import FANTASY_POSITIONS as POSITIONS
from fantasycoach.sources.sleeper import SleeperClient

# Volume stats modelled as log-normal around their centre: stat -> (offset c, min projection to count)
VOLUME_STATS: dict[str, tuple[float, float]] = {
    "pass_yd": (20.0, 80.0),
    "rush_yd": (8.0, 8.0),
    "rec": (1.0, 0.8),
    "rec_yd": (8.0, 8.0),
}
# Stats kept per player-week in the condensed history.
KEPT = list(VOLUME_STATS) + ["pass_td", "pass_int", "rush_td", "rec_td", "fum_lost", "pts_ppr"]

# Prior strength (in games) pulling a player's sigma toward the position value.
SHRINK_GAMES = 10.0
DEFAULT_SIGMA = {"pass_yd": 0.33, "rush_yd": 0.55, "rec": 0.5, "rec_yd": 0.65}
DEFAULT_PTS_SD = {"K": 4.0, "DEF": 5.5}


@dataclass
class Spread:
    """Learned outcome spreads for the players and positions in the lookback window."""

    position_sigma: dict[str, dict[str, float]] = field(default_factory=dict)  # pos -> stat -> sigma
    # Median log-ratio of actual to projected: how a typical (median) game compares with the
    # projected mean. Used to turn a betting line (a median) into an equivalent mean.
    position_median: dict[str, dict[str, float]] = field(default_factory=dict)
    player_sigma: dict[str, dict[str, float]] = field(default_factory=dict)  # pid -> stat -> sigma
    points_sd: dict[str, float] = field(default_factory=dict)  # K / DEF: sd of (actual - projected) points
    weeks_used: int = 0

    def median_log_ratio(self, position: str, stat: str, sigma: float) -> float:
        """How a median game compares with the projected mean for a stat, on a log scale."""
        value = self.position_median.get(position, {}).get(stat)
        return value if value is not None else -(sigma**2) / 2  # log-normal identity

    def sigma(self, player_id: str, position: str, stat: str) -> float:
        """Outcome spread for a player's stat: their own (shrunk) value, else their position's."""
        own = self.player_sigma.get(player_id, {}).get(stat)
        if own is not None:
            return own
        return self.position_sigma.get(position, {}).get(stat, DEFAULT_SIGMA.get(stat, 0.5))


def _week_sequence(season: int, week: int, count: int) -> list[tuple[int, int]]:
    """The ``count`` completed regular-season weeks before (season, week), newest first."""
    out = []
    s, w = season, week - 1
    while len(out) < count and s >= season - 3:
        if w < 1:
            s, w = s - 1, 18
            continue
        out.append((s, w))
        w -= 1
    return out


class HistoryStore:
    """Downloads, condenses and caches completed weeks, and learns a ``Spread`` from them."""
    def __init__(self, client: SleeperClient, folder: Path | None):
        self.client = client
        self.folder = folder
        self._weeks: dict[tuple[int, int], dict] = {}

    def week(self, season: int, week: int) -> dict[str, dict]:
        """Condensed {player_id: {"pos", "p": {...}, "a": {...}}} for one completed week."""
        key = (season, week)
        if key in self._weeks:
            return self._weeks[key]
        path = self.folder / f"{season}_{week:02d}.json" if self.folder else None
        if path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = self._download(season, week)
            if path is not None and data:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        self._weeks[key] = data
        return data

    def _download(self, season: int, week: int) -> dict[str, dict]:
        proj = self.client.projections(str(season), week, POSITIONS, persist=False, memo=False)
        stats = self.client.stats(str(season), week, POSITIONS, persist=False, memo=False)
        actual = {r["player_id"]: r.get("stats") or {} for r in stats if r.get("player_id")}
        out = {}
        for row in proj:
            pid = row.get("player_id")
            a = actual.get(pid)
            if not pid or not a or not a.get("gp"):
                continue  # didn't play: no information about spread
            p = row.get("stats") or {}
            pos = (row.get("player") or {}).get("position")
            if pos not in POSITIONS or not p.get("pts_ppr"):
                continue
            out[pid] = {
                "pos": pos,
                "p": {k: round(p[k], 2) for k in KEPT if p.get(k)},
                "a": {k: round(a[k], 2) for k in KEPT if a.get(k)},
            }
        return out

    def spread(self, season: int, week: int, lookback: int = 20) -> Spread:
        """Learn spreads from the ``lookback`` completed weeks before ``week``."""
        per_player: dict[str, dict[str, list[float]]] = {}
        per_pos: dict[str, dict[str, list[float]]] = {}
        pts_resid: dict[str, list[float]] = {"K": [], "DEF": []}
        positions: dict[str, str] = {}
        used = 0
        for s, w in _week_sequence(season, week, lookback):
            try:
                rows = self.week(s, w)
            except Exception:
                continue
            if not rows:
                continue
            used += 1
            for pid, row in rows.items():
                pos, p, a = row["pos"], row["p"], row["a"]
                positions[pid] = pos
                if pos in pts_resid:
                    pts_resid[pos].append(a.get("pts_ppr", 0.0) - p.get("pts_ppr", 0.0))
                    continue
                for stat, (c, min_proj) in VOLUME_STATS.items():
                    proj_v = p.get(stat, 0.0)
                    if proj_v < min_proj:
                        continue
                    r = math.log((max(a.get(stat, 0.0), 0.0) + c) / (proj_v + c))
                    per_player.setdefault(pid, {}).setdefault(stat, []).append(r)
                    per_pos.setdefault(pos, {}).setdefault(stat, []).append(r)

        result = Spread(weeks_used=used)
        for pos, stats in per_pos.items():
            result.position_sigma[pos] = {st: _robust_sd(v) for st, v in stats.items() if len(v) >= 30}
            result.position_median[pos] = {st: round(_median(v), 4) for st, v in stats.items() if len(v) >= 30}
        for pos, resid in pts_resid.items():
            result.points_sd[pos] = _robust_sd(resid) if len(resid) >= 30 else DEFAULT_PTS_SD[pos]
        for pid, stats in per_player.items():
            pos = positions[pid]
            for stat, v in stats.items():
                prior = result.position_sigma.get(pos, {}).get(stat, DEFAULT_SIGMA[stat])
                n = len(v)
                own = _sd(v) if n >= 2 else prior
                # Shrink variance (not sd) toward the position prior.
                var = (n * own**2 + SHRINK_GAMES * prior**2) / (n + SHRINK_GAMES)
                result.player_sigma.setdefault(pid, {})[stat] = round(math.sqrt(var), 4)
        return result


def _median(values: list[float]) -> float:
    v = sorted(values)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _sd(values: list[float]) -> float:
    m = sum(values) / len(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / max(len(values) - 1, 1))


def _robust_sd(values: list[float]) -> float:
    """Standard deviation with the most extreme 1% on each side trimmed (bad data, ejections)."""
    v = sorted(values)
    k = len(v) // 100
    return round(_sd(v[k: len(v) - k] if k else v), 4)
