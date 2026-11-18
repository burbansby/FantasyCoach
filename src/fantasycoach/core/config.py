"""Local settings and cache locations.

Everything FantasyCoach stores lives under ``~/.fantasycoach`` (override with the
``FANTASYCOACH_HOME`` environment variable):

- ``config.json``: your Sleeper username, user id and default league (written by ``setup``)
- ``cache/``: cached API responses
- ``cache/history/``: condensed past weeks used to learn outcome ranges
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def home_dir() -> Path:
    """Root folder for FantasyCoach's local files."""
    override = os.environ.get("FANTASYCOACH_HOME")
    return Path(override) if override else Path.home() / ".fantasycoach"


def cache_dir() -> Path:
    """Folder for cached API responses."""
    return home_dir() / "cache"


@dataclass
class Config:
    """Saved command-line settings: who you are on Sleeper and your default league."""
    username: str | None = None
    user_id: str | None = None
    league_id: str | None = None

    @classmethod
    def load(cls) -> "Config":
        """Read ``config.json``, or return an empty config if setup hasn't run yet."""
        path = home_dir() / "config.json"
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: data.get(k) for k in ("username", "user_id", "league_id")})

    def save(self) -> Path:
        """Write ``config.json`` and return its path."""
        path = home_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path
