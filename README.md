# FantasyCoach

A command-line fantasy football assistant for **Sleeper** leagues. It reads your league's
roster settings and scoring rules, pulls weekly projections, and gives stats-driven advice.

## Features

- **Start/sit (`lineup`)** – builds the highest-projected legal lineup from your roster
  (handles FLEX, SUPER_FLEX, WR/RB and WR/TE flex, IDP), flags injuries and byes, and
  shows exactly which players to swap and the projected point gain.
- **Player lookup & compare (`players`)** – no sign-in needed. Search any player or team
  defense and see week-by-week projections, key stat lines, totals and averages; add
  several to compare side by side with a "start this one" pick for the week. Scored with
  your league's settings when signed in, otherwise Sleeper's default (PPR), with Half PPR
  and Standard also available.
- **Waiver wire (`waivers`)** – ranks free agents by projected points over the next N weeks
  and pairs each with your weakest same-position player as the drop, sorted by net gain.

Projections are scored with **your league's** `scoring_settings`, so PPR, half-PPR, TE
premium, 6-pt passing TDs, etc. are all respected. Players listed Out/IR/Suspended are
projected at 0; Doubtful players are discounted to 25%.

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
**Waiver Wire** and **Players**. The **Players** tab works without signing in (use the
"look up players without signing in" link). Your username and league are remembered in the browser. JSON API docs
are at `/api/docs`.

## Command line

```bash
python3 run.py setup --username <your sleeper username>   # pick your default league
python3 run.py lineup                                      # this week's start/sit
python3 run.py lineup --week 12
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
- Sleeper's undocumented projections endpoint (`api.sleeper.app/projections/nfl/...`).
  It's what the Sleeper app uses but isn't officially supported and could change.

## Development

Run the tests with `python3 run.py test` (installs pytest into `.deps/` on first use).

Layout: `sleeper.py` (API client + cache) → `league.py` (league/roster context) →
`scoring.py`, `lineup.py`, `waivers.py`, `players.py` (pure logic, unit tested) → `service.py`
(shared workflows) → `cli.py` and `web.py` + `static/index.html`.
