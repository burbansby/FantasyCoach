"""Player lookup and side-by-side projection comparison (no sign-in required)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .scoring import score
from .sleeper import SleeperClient

FANTASY_POSITIONS = ["DEF", "K", "QB", "RB", "TE", "WR"]

# Sleeper's standard scoring for a new league (full PPR receptions).
SLEEPER_DEFAULT_SCORING: dict[str, float] = {
    # passing
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2,
    # rushing / receiving
    "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
    "rec": 1, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
    # misc offense
    "fum_lost": -2, "fum_rec_td": 6,
    # kicking
    "fgm_0_19": 3, "fgm_20_29": 3, "fgm_30_39": 3, "fgm_40_49": 4, "fgm_50p": 5,
    "fgmiss": -1, "xpm": 1, "xpmiss": -1,
    # team defense
    "def_td": 6, "sack": 1, "int": 2, "fum_rec": 2, "safe": 2, "ff": 1, "blk_kick": 2,
    "def_st_td": 6, "def_st_ff": 1, "def_st_fum_rec": 1,
    "pts_allow_0": 10, "pts_allow_1_6": 7, "pts_allow_7_13": 4, "pts_allow_14_20": 1,
    "pts_allow_21_27": 0, "pts_allow_28_34": -1, "pts_allow_35p": -4,
}

SCORING_PRESETS: dict[str, tuple[str, dict[str, float]]] = {
    "default": ("Sleeper default (PPR)", SLEEPER_DEFAULT_SCORING),
    "half": ("Half PPR", {**SLEEPER_DEFAULT_SCORING, "rec": 0.5}),
    "std": ("Standard (no PPR)", {**SLEEPER_DEFAULT_SCORING, "rec": 0}),
}

# Stats worth showing in a quick per-week line, by position.
KEY_STATS: dict[str, list[tuple[str, str]]] = {
    "QB": [("pass_yd", "Pass Yds"), ("pass_td", "Pass TD"), ("pass_int", "INT"), ("rush_yd", "Rush Yds"), ("rush_td", "Rush TD")],
    "RB": [("rush_att", "Att"), ("rush_yd", "Rush Yds"), ("rush_td", "Rush TD"), ("rec", "Rec"), ("rec_yd", "Rec Yds")],
    "WR": [("rec_tgt", "Tgt"), ("rec", "Rec"), ("rec_yd", "Rec Yds"), ("rec_td", "Rec TD"), ("rush_yd", "Rush Yds")],
    "TE": [("rec_tgt", "Tgt"), ("rec", "Rec"), ("rec_yd", "Rec Yds"), ("rec_td", "Rec TD")],
    "K": [("fgm", "FG"), ("fga", "FGA"), ("xpm", "XP")],
    "DEF": [("sack", "Sacks"), ("int", "INT"), ("fum_rec", "Fum Rec"), ("pts_allow", "Pts Allowed")],
}

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def normalize(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]", "", text.lower())
    return re.sub(r"\s+", "", _SUFFIX.sub("", text))


def display_name(info: dict) -> str:
    return info.get("full_name") or " ".join(p for p in (info.get("first_name"), info.get("last_name")) if p)


def search(players_db: dict[str, dict], query: str, limit: int = 8) -> list[tuple[str, dict]]:
    """Fantasy-relevant players matching ``query``, best matches first.

    Exact name > full/first/last name starts with query > name contains query;
    ties broken by Sleeper's ``search_rank`` (lower = more relevant), then active team.
    """
    q = normalize(query)
    if not q:
        return []
    hits: list[tuple[tuple, str, dict]] = []
    for pid, info in players_db.items():
        if info.get("position") not in FANTASY_POSITIONS:
            continue
        name = normalize(display_name(info))
        parts = [normalize(info.get("first_name") or ""), normalize(info.get("last_name") or "")]
        if q == name or q == pid.lower():
            tier = 0
        elif name.startswith(q) or any(part.startswith(q) for part in parts if part):
            tier = 1
        elif q in name:
            tier = 2
        else:
            continue
        rank = info.get("search_rank") or 10**7
        hits.append(((tier, not info.get("team"), rank), pid, info))
    hits.sort(key=lambda h: h[0])
    return [(pid, info) for _, pid, info in hits[:limit]]


@dataclass
class WeekLine:
    week: int
    opponent: str | None
    points: float
    stats: dict[str, float]
    projected: bool = True  # False when Sleeper has no projection row (e.g. free agent)
    range: Any = None  # ranges.Range: expected / floor / ceiling, when available

    @property
    def bye(self) -> bool:
        return self.projected and not self.opponent


@dataclass
class PlayerOutlook:
    player_id: str
    name: str
    position: str
    team: str | None
    injury_status: str | None
    weeks: list[WeekLine] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(w.points for w in self.weeks), 2)

    @property
    def average(self) -> float:
        played = [w for w in self.weeks if w.opponent and w.projected]
        return round(sum(w.points for w in played) / len(played), 2) if played else 0.0

    @property
    def this_week(self) -> float:
        return self.weeks[0].points if self.weeks else 0.0


def outlooks(
    client: SleeperClient,
    player_ids: list[str],
    scoring: dict[str, float],
    season: str,
    start_week: int,
    weeks: int = 4,
) -> list[PlayerOutlook]:
    """Week-by-week projections for each player over ``weeks`` weeks from ``start_week``."""
    db = client.players()
    results = []
    for pid in player_ids:
        info = db.get(pid) or {}
        results.append(PlayerOutlook(pid, display_name(info) or pid, info.get("position") or "?",
                                     info.get("team"), info.get("injury_status")))
    for offset in range(weeks):
        week = start_week + offset
        if week > 18:
            break
        rows = {r.get("player_id"): r for r in client.projections(season, week, FANTASY_POSITIONS)}
        for out in results:
            row = rows.get(out.player_id)
            stats = (row or {}).get("stats") or {}
            keys = [k for k, _ in KEY_STATS.get(out.position, [])]
            out.weeks.append(WeekLine(
                week=week,
                opponent=(row or {}).get("opponent"),
                points=score(stats, scoring) if row else 0.0,
                stats={k: round(stats.get(k) or 0.0, 1) for k in keys},
                projected=row is not None,
            ))
    return results
