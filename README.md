# FantasyCoach

A command-line fantasy football assistant for **Sleeper** leagues. It reads your league's
roster settings and scoring rules, pulls weekly projections, and gives stats-driven advice.

## Features

- **Start/sit (`lineup`)** – builds the highest-projected legal lineup from your roster
  (handles FLEX, SUPER_FLEX, WR/RB and WR/TE flex, IDP), flags injuries and byes, and
  shows exactly which players to swap and the projected point gain.
- **Matchup (`matchup`)** – your starting lineup and your opponent's side by side for the
  week, slot by slot: each player's projection and floor–ceiling, both teams' projected
  totals and team floor/ceiling, and a simulated win probability. Each side has a
  Current / Optimal switch (optimal = best legal lineup from that team's roster, IR excluded)
  and the totals, ranges and win probability follow both switches. No coaching advice, just
  projections. CLI: `matchup --optimal` / `--opp-optimal`.
- **Player lookup & compare (`players`)** – no sign-in needed. Search any player or team
  defense and see week-by-week projections, key stat lines, totals and averages; add
  several to compare side by side with a "start this one" pick for the week. Scored with
  your league's settings when signed in, otherwise Sleeper's default (PPR), with Half PPR
  and Standard also available.
- **Outcome ranges (floor / expected / ceiling)** – shown next to Sleeper's projection on
  the lineup and in player lookup. Each player's game is simulated 4,000 times:
  - the centre of each stat comes from **live DraftKings prop lines** (via ESPN's public odds
    feed, no key or quota) when posted, otherwise Sleeper's projection; lines are calibrated
    each week against Sleeper so they shift players relative to each other without biasing
    the overall level;
  - touchdown rates scale with each team's **Vegas implied total** (from spread + over/under);
  - the spread comes from how far the player's past games landed from projections (Sleeper
    history, shrunk toward their position), with stats correlated within a game.
  Floor = 10th percentile, ceiling = 90th percentile. In a backtest over 2026 weeks 3–10
  (1,904 player-games, spreads learned only from earlier weeks) 10.8% of games fell below the
  floor and 8.4% above the ceiling. Sleeper's projection still drives start/sit and waiver
  decisions.
- **Team floor / ceiling** – the start/sit page shows the whole lineup's range (recommended
  vs current). Players are simulated together and summed week by week (teammates on the same
  NFL team are correlated), giving a floor the team beats in 95% of weeks and a ceiling it
  stays under in 95%. Backtest on 2,400 random lineups from 2026 weeks 3–10: 4.8% fell below
  the floor and 5.0% above the ceiling.
- **Waiver wire (`waivers`)** – ranks free agents by projected points over the next N weeks
  and pairs each with your weakest same-position player as the drop, sorted by net gain.

Projections are scored with **your league's** `scoring_settings`, so PPR, half-PPR, TE
premium, 6-pt passing TDs, etc. are all respected. Injury designations are shown as badges
but never change a projection (official reports lag), so you always see Sleeper's actual
projected points.

## Quick start (no virtual environment)

You only need Python 3.10+. From the project folder:

```bash
python3 run.py
```

That's it: the first run installs the needed packages into a local `.deps/` folder
(nothing system-wide), then starts the web app at http://localhost:8000 and opens your
browser. On Windows use `python run.py`.

If Ubuntu says pip is missing: `sudo apt install python3-pip`, then run it again.

`run.py` also runs any command-line command, e.g. `python3 run.py lineup`.
Use `python3 run.py web --host 0.0.0.0 --no-open` to reach it from other devices
(e.g. your phone) at `http://<this machine's IP>:8000`.

## Web interface

Enter your Sleeper username, pick a league, and switch between **Start / Sit**,
**Matchup**, **Waiver Wire** and **Players**. The **Players** tab works without signing in (use the
"look up players without signing in" link). Your username and league are remembered in the browser. JSON API docs
are at `/api/docs`.

## Command line

```bash
python3 run.py setup --username <your sleeper username>   # pick your default league
python3 run.py lineup                                      # this week's start/sit
python3 run.py lineup --week 12
python3 run.py matchup                                     # you vs. your opponent this week
python3 run.py waivers                                     # next 3 weeks
python3 run.py waivers --position RB --weeks 5
python3 run.py leagues                                     # list your leagues
python3 run.py players "Bijan Robinson" Gibbs Chiefs       # compare (no setup needed)
python3 run.py players Kelce --weeks 6 --scoring half
```

Any command accepts `--league <id>` to target a league other than your default.

Config and an HTTP cache live in `~/.fantasycoach` (override with `FANTASYCOACH_HOME`).

## Data sources

- Sleeper public API (`api.sleeper.app/v1`) for users, leagues, rosters, and players.
- Sleeper's undocumented projections and stats endpoints (`api.sleeper.app/projections/...`,
  `/stats/...`). They're what the Sleeper app uses but aren't officially supported.
- ESPN's public odds feed (`site.api.espn.com`, `sports.core.api.espn.com`) for DraftKings
  game lines and player prop lines. Unofficial; if it's unavailable, ranges fall back to the
  history-only model. Props are usually posted for every game by the weekend.

## Development

Run the tests with `python3 run.py test` (installs pytest into `.deps/` on first use).

Layout: `sleeper.py` (API client + cache) → `league.py` (league/roster context) →
`scoring.py`, `lineup.py`, `waivers.py`, `players.py`, `ranges.py` (pure logic, unit tested);
`market.py` (ESPN odds) + `history.py` (projection misses) → `range_engine.py` → `service.py`
(shared workflows) → `cli.py` and `web.py` + `static/index.html`.
