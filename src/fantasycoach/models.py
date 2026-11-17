from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Player:
    player_id: str
    name: str
    position: str
    team: str | None = None
    injury_status: str | None = None
    opponent: str | None = None
    # Sleeper's projected points under league scoring for the week being analysed.
    # Injury designations are shown but never change this number (reports lag too much).
    projected: float = 0.0
    # Positions the player can fill (e.g. ["WR", "RB"] for dual-eligible players).
    eligible: list[str] = field(default_factory=list)
    # Expected / floor / ceiling (ranges.Range) when computed; display only.
    range: Any = None

    @property
    def label(self) -> str:
        team = self.team or "FA"
        return f"{self.name} ({self.position}, {team})"
