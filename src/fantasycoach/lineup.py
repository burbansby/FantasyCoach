"""Start/sit: choose the highest-projected legal lineup from a roster."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Player

SLOT_ELIGIBILITY: dict[str, set[str]] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "DL": {"DL"},
    "LB": {"LB"},
    "DB": {"DB"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "IDP_FLEX": {"DL", "LB", "DB"},
}

NON_STARTER_SLOTS = {"BN", "IR", "TAXI"}


@dataclass
class LineupSlot:
    slot: str
    player: Player | None


@dataclass
class LineupAdvice:
    recommended: list[LineupSlot]
    bench: list[Player]
    current_total: float
    recommended_total: float
    to_start: list[Player]  # recommended starters not currently starting
    to_bench: list[Player]  # current starters not in the recommended lineup
    # Whole-lineup expected / 5th-95th percentile range (ranges.TeamRange), when computed.
    recommended_range: Any = None
    current_range: Any = None

    @property
    def gain(self) -> float:
        return round(self.recommended_total - self.current_total, 2)


def can_fill(player: Player, slot: str) -> bool:
    allowed = SLOT_ELIGIBILITY.get(slot, {slot})
    return any(pos in allowed for pos in (player.eligible or [player.position]))


def optimize(players: list[Player], slots: list[str]) -> tuple[list[LineupSlot], list[Player]]:
    """Fill the most restrictive slots first with the best available player.

    Restrictive-first greedy is optimal for the nested slot types Sleeper uses
    (position ⊂ FLEX ⊂ SUPER_FLEX).
    """
    order = sorted(range(len(slots)), key=lambda i: len(SLOT_ELIGIBILITY.get(slots[i], {slots[i]})))
    pool = sorted(players, key=lambda p: p.projected, reverse=True)
    filled: dict[int, Player | None] = {}
    for i in order:
        choice = next((p for p in pool if can_fill(p, slots[i])), None)
        if choice is not None:
            pool.remove(choice)
        filled[i] = choice
    lineup = [LineupSlot(slots[i], filled[i]) for i in range(len(slots))]
    return lineup, pool


def advise(players: list[Player], slots: list[str], current_starter_ids: list[str]) -> LineupAdvice:
    lineup, bench = optimize(players, slots)
    by_id = {p.player_id: p for p in players}
    current = [by_id[pid] for pid in current_starter_ids if pid in by_id]
    current_ids = {p.player_id for p in current}
    rec_players = [s.player for s in lineup if s.player]
    rec_ids = {p.player_id for p in rec_players}
    return LineupAdvice(
        recommended=lineup,
        bench=bench,
        current_total=round(sum(p.projected for p in current), 2),
        recommended_total=round(sum(p.projected for p in rec_players), 2),
        to_start=[p for p in rec_players if p.player_id not in current_ids],
        to_bench=[p for p in current if p.player_id not in rec_ids],
    )
