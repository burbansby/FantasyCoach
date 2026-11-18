"""FantasyCoach: a fantasy football assistant for Sleeper leagues.

Package layout, one folder per area of responsibility:

- ``core``: the Player model, name/position helpers, scoring rules, local config
- ``sources``: outside data (the Sleeper API and ESPN's DraftKings odds feed)
- ``projections``: outcome ranges and win probability (history, simulation, engine)
- ``league``: your league's features (roster context, start/sit, waivers, matchup)
- ``players``: player search, outlooks and comparison
- ``interface``: the command line and the web app

Data flows from ``sources`` through the ``league`` / ``players`` logic, picks up ranges
from ``projections``, and is shown by ``interface``.
"""

__version__ = "0.1.0"
