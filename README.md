# FantasyCoach

A fantasy football assistant for **Sleeper** leagues, with a web app and a command line.
It reads your league's roster settings and scoring rules, pulls weekly projections and live
betting lines, and shows lineup advice, matchups, waiver targets and player comparisons.

## Quick start

You only need Python 3.10+ (no virtual environment). From the project folder:

```bash
python3 run.py
```

The first run installs the required packages into a local `.deps/` folder (nothing
system-wide), then starts the web app at http://localhost:8000 and opens your browser.
On Windows use `python run.py`. If Ubuntu says pip is missing, run
`sudo apt install python3-pip` and try again.

To use it from another device (e.g. your phone) on the same network:

```bash
python3 run.py web --host 0.0.0.0 --no-open
```

then browse to `http://<this machine's IP>:8000`.

## What it does

Enter your Sleeper username and pick a league. The **Players** tab also works without
signing in.

- **Start / Sit** – the highest-projected legal lineup from your roster (FLEX, SUPER_FLEX,
  WR/RB and WR/TE flex, IDP), with green START / red BENCH tags for each change, the
  projected point gain, and your team's floor and ceiling (recommended vs current lineup).
- **Matchup** – your lineup and your opponent's side by side, slot by slot, ordered best to
  worst within each position. Shows each player's projection and range, both teams' totals
  and floor/ceiling, and a win probability. A Current / Optimal switch on each side swaps in
  that team's best possible lineup and updates everything at the top. Projections only, no
  advice.
- **Waiver Wire** – free agents ranked by projected points over the next 1–8 weeks, each
  paired with your weakest same-position player as the drop.
- **Players** – search any player or team defense: week-by-week projections, key stat
  lines, floor/expected/ceiling, and side-by-side comparison with a pick for the week plus
  the safest floor and highest ceiling. Uses your league's scoring when signed in,
  otherwise Sleeper's default (PPR), with Half PPR and Standard available.

Projections are Sleeper's, scored with **your league's** `scoring_settings` (PPR, TE premium,
6-pt passing TDs, etc.), and they drive every recommendation. Injury designations are shown
as badges but never change a projection, because official reports lag.

## Floors, ceilings and win probability

Next to each projection the app shows an outcome range from 4,000 simulated games:

- **Centre of each stat:** live **DraftKings prop lines** (via ESPN's public odds feed, no key
  or quota) when posted, otherwise Sleeper's projection. Each week the lines are calibrated
  against Sleeper, so they shift players relative to each other without biasing the overall
  level. Props for every game are usually posted by the weekend.
- **Touchdowns:** scaled by each team's Vegas implied total (from the spread and over/under).
- **Spread:** how far the player's past games landed from their projections (about 20 weeks of
  Sleeper history, shrunk toward their position), with a player's stats correlated within a
  game.

| Range | Meaning | Backtest (2026 weeks 3–10, out of sample) |
|---|---|---|
| Player floor / ceiling | 10th / 90th percentile | 10.8% below, 8.4% above (1,904 player-games) |
| Team floor / ceiling | 5th / 95th percentile of the summed lineup | 4.8% below, 5.0% above (2,400 lineups) |

The backtests run without betting lines (past props aren't archived). Team totals are
simulated player by player and summed within each simulated week, with teammates from the
same NFL team correlated. Win probability compares both lineups over the same simulated
weeks; it hasn't been backtested against real head-to-head results.

## Command line

Every web feature is also a command (prefix with `python3 run.py`):

```bash
python3 run.py setup --username <your sleeper username>   # pick your default league
python3 run.py lineup                                      # this week's start/sit
python3 run.py lineup --week 12
python3 run.py matchup                                     # you vs. your opponent
python3 run.py matchup --optimal --opp-optimal             # both teams' best lineups
python3 run.py waivers --position RB --weeks 5
python3 run.py players "Bijan Robinson" Gibbs Chiefs       # compare (no setup needed)
python3 run.py players Kelce --weeks 6 --scoring half
python3 run.py leagues                                     # list your leagues
```

League commands accept `--league <id>` to use a league other than your default. Config and
caches live in `~/.fantasycoach` (override with `FANTASYCOACH_HOME`). The first run that
needs history downloads about 20 weeks of Sleeper data (roughly 3 MB per week) and keeps a
small condensed copy; the web app loads it in the background at startup.

## Data sources

- Sleeper public API (`api.sleeper.app/v1`): users, leagues, rosters, matchups, players.
- Sleeper's undocumented projections and stats endpoints (`/projections/...`, `/stats/...`),
  which the Sleeper app itself uses but aren't officially supported.
- ESPN's public odds feed (`site.api.espn.com`, `sports.core.api.espn.com`): DraftKings
  game lines and player prop lines. Unofficial; if it's unavailable, ranges fall back to the
  history-only model.

## Development

Run the tests with `python3 run.py test` (installs pytest into `.deps/` on first use).
JSON API docs are served at `/api/docs`.

Code layout (`src/fantasycoach/`, one folder per area):

```text
core/          Shared basics
  models.py      Player object; name matching and fantasy-position helpers
  scoring.py     Stat lines -> points; Sleeper default / Half PPR / Standard presets
  config.py      Local settings and cache folders (~/.fantasycoach)
sources/       Outside data (fetch + cache, no decisions)
  sleeper.py     Sleeper API: leagues, rosters, matchups, players, projections, stats
  espn.py        ESPN odds feed: DraftKings game lines and player prop lines
projections/   Floor / expected / ceiling and win probability
  history.py     Learns how far results land from projections (past weeks)
  simulation.py  Pure math: simulate games, summarise player and team ranges
  engine.py      Gathers a week's data and runs the simulation
league/        Your league's features
  context.py     League, roster and player context for one team
  lineup.py      Start/sit optimiser (pure)
  waivers.py     Waiver ranking (pure)
  advice.py      Start/sit and waiver workflows
  matchup.py     You vs your opponent, current and optimal lineups
players/       Player lookup (no sign-in)
  lookup.py      Name search and week-by-week outlooks
  compare.py     Scoring choice and ranges for comparisons
interface/     How you use it
  cli.py         Command-line commands
  web.py         FastAPI JSON API; serves static/index.html (the web app)
```

Each file starts with a comment explaining what it does and how it fits in.
