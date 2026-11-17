"""Local config and cache locations (~/.fantasycoach by default)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def home_dir() -> Path:
    override = os.environ.get("FANTASYCOACH_HOME")
    return Path(override) if override else Path.home() / ".fantasycoach"


def cache_dir() -> Path:
    return home_dir() / "cache"


@dataclass
class Config:
    username: str | None = None
    user_id: str | None = None
    league_id: str | None = None

    @classmethod
    def load(cls) -> "Config":
        path = home_dir() / "config.json"
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: data.get(k) for k in ("username", "user_id", "league_id")})

    def save(self) -> Path:
        path = home_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path
