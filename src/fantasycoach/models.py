from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Player:
    player_id: str
    name: str
    position: str
    team: str | None = None
    injury_status: str | None = None
    opponent: str | None = None
    # Raw projected points under league scoring for the week being analysed.
    projected: float = 0.0
    # Projection after injury adjustment; what decisions are based on.
    adjusted: float = 0.0
    # Positions the player can fill (e.g. ["WR", "RB"] for dual-eligible players).
    eligible: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        team = self.team or "FA"
        return f"{self.name} ({self.position}, {team})"
