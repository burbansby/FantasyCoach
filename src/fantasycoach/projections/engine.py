"""Projection engine: turns "these players, this week" into outcome ranges.

For a week it gathers what the simulator needs (Sleeper projections, learned spreads from
``history``, DraftKings lines and Vegas team totals from ``sources.espn``, the weekly line
calibration and touchdown scaling) and runs ``simulation`` for:

- single players (``week_ranges``): floor / expected / ceiling
- whole lineups (``lineup_range``): team floor / ceiling
- head-to-head matchups (``matchup_scenarios``): both teams' ranges and the win
  probability for every current/optimal pairing

Betting lines exist only for the current week; other weeks use history and Sleeper alone.
``engine_for`` returns one shared engine per Sleeper client so learned spreads are reused.
"""

from __future__ import annotations

import logging
import threading
import zlib

import numpy as np

from fantasycoach.projections import simulation
from fantasycoach.core.config import cache_dir
from fantasycoach.projections.history import HistoryStore, Spread
from fantasycoach.sources.espn import Market, MarketFetcher
from fantasycoach.core.models import FANTASY_POSITIONS, fantasy_position
from fantasycoach.sources.sleeper import SleeperClient

log = logging.getLogger(__name__)


class RangeEngine:
    """Computes ranges for any week, caching learned spreads and sharing the client's HTTP cache."""
    def __init__(self, client: SleeperClient):
        self.client = client
        self.history = HistoryStore(client, cache_dir() / "history" if getattr(client, "cache_dir", None) else None)
        self.market = MarketFetcher(client)
        self._spreads: dict[tuple[int, int], Spread] = {}
        self._lock = threading.Lock()

    def spread(self, season: int, week: int) -> Spread:
        """Learned outcome spreads for ``week``; computed once, falls back to defaults offline."""
        key = (season, week)
        with self._lock:
            if key not in self._spreads:
                try:
                    self._spreads[key] = self.history.spread(season, week)
                except Exception as exc:  # offline etc.: fall back to built-in defaults
                    log.warning("History unavailable: %s", exc)
                    self._spreads[key] = Spread()
            return self._spreads[key]

    def current_market(self, season: str, week: int) -> Market:
        """Live betting lines and team totals for ``week`` (cached about 30 minutes)."""
        return self.market.week_market(season, week)

    def _inputs(
        self, season: str, week: int, current_week: int, player_ids: list[str], scoring: dict[str, float]
    ) -> list[tuple[str, str, str | None, float | None, dict]]:
        """(player_id, position, nfl_team, vegas_team_points, simulate kwargs) per player with a game.

        Betting lines only exist for the current week; later weeks use history + Sleeper only.
        """
        spread = self.spread(int(season), current_week)
        rows = {r.get("player_id"): r for r in self.client.projections(season, week, FANTASY_POSITIONS)}
        market = self.current_market(season, week) if week == current_week else Market(week=week)
        scales = simulation.team_td_scales(list(rows.values()), market.team_points) if market.team_points else {}
        db = self.client.players()
        calibration = simulation.line_calibration(
            market.lines,
            {pid: (rows.get(pid) or {}).get("stats") or {} for pid in market.lines},
            {pid: fantasy_position(db.get(pid) or {}) for pid in market.lines},
        ) if market.lines else {}

        out = []
        for pid in player_ids:
            row = rows.get(pid)
            if not row or not row.get("opponent"):
                continue  # bye week or no projection
            info = db.get(pid) or row.get("player") or {}
            position = fantasy_position(info)
            if position is None:
                continue
            team = row.get("team") or info.get("team")
            kwargs = dict(
                player_id=pid,
                position=position,
                projected_stats=row.get("stats") or {},
                scoring=scoring,
                spread=spread,
                lines=market.lines.get(pid),
                td_scale=scales.get(team, 1.0),
                line_calibration={
                    stat: calibration.get((position, stat), calibration.get((None, stat)))
                    for stat in (market.lines.get(pid) or {})
                    if (position, stat) in calibration or (None, stat) in calibration
                },
            )
            out.append((pid, position, team, market.team_points.get(team), kwargs))
        return out

    def week_ranges(
        self,
        season: str,
        week: int,
        current_week: int,
        player_ids: list[str],
        scoring: dict[str, float],
    ) -> dict[str, simulation.Range]:
        """Expected/floor/ceiling (10th/90th percentile) for each player who has a game in ``week``."""
        return {
            pid: simulation.simulate(team_points=team_points, **kwargs)
            for pid, _, _, team_points, kwargs in self._inputs(season, week, current_week, player_ids, scoring)
        }

    def lineup_range(
        self,
        season: str,
        week: int,
        current_week: int,
        player_ids: list[str],
        scoring: dict[str, float],
    ) -> simulation.TeamRange:
        """Whole-lineup expected score with a 5th-95th percentile floor and ceiling.

        Players are simulated game by game and summed within each simulated week, so the team
        range reflects how often good and bad games offset. Teammates on the same NFL team share
        part of their game-script factor.
        """
        samples = self._lineup_samples(season, week, current_week, player_ids, scoring, {})
        return simulation.team_range(samples)

    def matchup_scenarios(
        self,
        season: str,
        week: int,
        current_week: int,
        mine: dict[str, list[str]],
        theirs: dict[str, list[str]],
        scoring: dict[str, float],
    ) -> tuple[dict[str, simulation.TeamRange], dict[str, simulation.TeamRange], dict[tuple[str, str], float]]:
        """Team ranges for several versions of each lineup (e.g. "current"/"optimal") and the win
        probability for every pairing, keyed (my_version, their_version).

        Every player is simulated once over the same simulated weeks, so all pairings are
        consistent with each other.
        """
        union = list(dict.fromkeys(pid for ids in [*mine.values(), *theirs.values()] for pid in ids))
        per = self._player_samples(season, week, current_week, union, scoring, {})

        def pick(ids: list[str]) -> list[np.ndarray]:
            """Simulated samples for a lineup's players (players without a game are skipped)."""
            return [per[pid] for pid in ids if pid in per]

        my_ranges = {k: simulation.team_range(pick(v)) for k, v in mine.items()}
        their_ranges = {k: simulation.team_range(pick(v)) for k, v in theirs.items()}
        wins = {(a, b): simulation.win_probability(pick(mine[a]), pick(theirs[b])) for a in mine for b in theirs}
        return my_ranges, their_ranges, wins

    def _lineup_samples(
        self,
        season: str,
        week: int,
        current_week: int,
        player_ids: list[str],
        scoring: dict[str, float],
        shared: dict[str, np.ndarray],
    ) -> list[np.ndarray]:
        return list(self._player_samples(season, week, current_week, player_ids, scoring, shared).values())

    def _player_samples(
        self,
        season: str,
        week: int,
        current_week: int,
        player_ids: list[str],
        scoring: dict[str, float],
        shared: dict[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        out = {}
        for pid, position, team, _, kwargs in self._inputs(season, week, current_week, player_ids, scoring):
            z = None
            if team and position != "DEF":
                if team not in shared:
                    seed = zlib.crc32(f"{season}|{week}|{team}".encode())
                    shared[team] = np.random.default_rng(seed).standard_normal(simulation.SIMS)
                z = shared[team]
            out[pid], _ = simulation.simulate_points(shared_z=z, **kwargs)
        return out

    def warm(self, season: str, week: int) -> None:
        """Pre-load history and odds (used at web startup so the first page is fast)."""
        try:
            self.spread(int(season), week)
            self.current_market(season, week)
        except Exception as exc:
            log.warning("Warm-up failed: %s", exc)


_engines: dict[int, RangeEngine] = {}


def engine_for(client: SleeperClient) -> RangeEngine:
    """The shared engine for a Sleeper client, created on first use."""
    eng = _engines.get(id(client))
    if eng is None or eng.client is not client:
        eng = _engines[id(client)] = RangeEngine(client)
    return eng
