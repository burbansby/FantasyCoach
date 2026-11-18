"""Command-line interface (Typer).

Commands: ``setup`` (link your Sleeper account), ``leagues``, ``lineup`` (start/sit),
``matchup``, ``waivers``, ``players`` (lookup and compare, no setup needed) and ``web``
(start the web app). Run them through ``python3 run.py <command>``; with no command,
``run.py`` starts the web app. This module only formats output; the work happens in
``league``, ``players`` and ``projections``.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from fantasycoach.league.advice import lineup_advice, waiver_picks
from fantasycoach.league.matchup import matchup as build_matchup
from fantasycoach.players.compare import compare_players
from fantasycoach.core.config import Config, cache_dir
from fantasycoach.core.models import Player
from fantasycoach.sources.sleeper import SleeperClient, SleeperError, current_season_week

app = typer.Typer(help="Fantasy football advice for your Sleeper league.", no_args_is_help=True)
console = Console()
SETUP_HINT = "python3 run.py setup --username <your sleeper username>"


def _client() -> SleeperClient:
    return SleeperClient(cache_dir=cache_dir())


def _require_config(league: Optional[str]) -> tuple[Config, str]:
    cfg = Config.load()
    league_id = league or cfg.league_id
    if not cfg.user_id or not league_id:
        console.print(f"[red]Not set up yet.[/] Run [bold]{SETUP_HINT}[/] first.")
        raise typer.Exit(1)
    return cfg, league_id


def _status(p: Player) -> str:
    if not p.injury_status:
        return ""
    color = "yellow" if p.injury_status == "Questionable" else "red"
    return f"[{color}]{p.injury_status}[/]"


def _num(x: float) -> str:
    return f"{0.0 if abs(x) < 0.05 else x:.1f}"  # avoid "-0.0"


def _range(r) -> str:
    return f"{_num(r.floor)}-{_num(r.ceiling)}" if r else ""


def _opp(p: Player) -> str:
    return f"vs {p.opponent}" if p.opponent else "[dim]BYE/—[/]"


@app.command()
def setup(
    username: str = typer.Option(..., "--username", "-u", help="Your Sleeper username"),
    league: Optional[str] = typer.Option(None, "--league", "-l", help="League ID (skip the picker)"),
):
    """Link your Sleeper account and choose a default league."""
    client = _client()
    try:
        user = client.user(username)
        season, _ = current_season_week(client)
        leagues = client.user_leagues(user["user_id"], season)
    except SleeperError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)

    if league is None:
        if not leagues:
            console.print(f"[red]No {season} leagues found for {username}.[/]")
            raise typer.Exit(1)
        for i, lg in enumerate(leagues, 1):
            console.print(f"  [bold]{i}[/]. {lg['name']}  [dim]({lg['league_id']})[/]")
        choice = 1 if len(leagues) == 1 else typer.prompt("Pick a league", type=int, default=1)
        league = leagues[choice - 1]["league_id"]

    path = Config(username=username, user_id=user["user_id"], league_id=league).save()
    console.print(f"[green]Saved[/] to {path}")


@app.command()
def leagues():
    """List your leagues for the current season."""
    cfg = Config.load()
    if not cfg.user_id:
        console.print(f"[red]Not set up yet.[/] Run [bold]{SETUP_HINT}[/] first.")
        raise typer.Exit(1)
    client = _client()
    season, _ = current_season_week(client)
    for lg in client.user_leagues(cfg.user_id, season):
        marker = "*" if lg["league_id"] == cfg.league_id else " "
        console.print(f"{marker} {lg['name']}  [dim]({lg['league_id']})[/]")


@app.command()
def lineup(
    week: Optional[int] = typer.Option(None, "--week", "-w", help="NFL week (default: current)"),
    league: Optional[str] = typer.Option(None, "--league", "-l"),
):
    """Start/sit: recommend the best lineup for the week."""
    cfg, league_id = _require_config(league)
    ctx, advice = lineup_advice(_client(), league_id, cfg.user_id, week)

    table = Table(title=f"{ctx.league['name']} — Week {ctx.week} recommended lineup")
    for col in ("Slot", "Player", "Matchup", "Status", "Proj", "Floor-Ceiling"):
        table.add_column(col, justify="right" if col in ("Proj", "Floor-Ceiling") else "left")
    for s in advice.recommended:
        p = s.player
        if p is None:
            table.add_row(s.slot, "[red]EMPTY[/]", "", "", "", "")
            continue
        new = " [green]▲ start[/]" if p in advice.to_start else ""
        table.add_row(s.slot, p.label + new, _opp(p), _status(p), f"{p.projected:.1f}", _range(p.range))
    table.add_section()
    for p in sorted(advice.bench, key=lambda p: p.projected, reverse=True):
        if p in advice.to_bench:
            table.add_row("BN", f"{p.label} [red]▼ bench[/]", _opp(p), _status(p), f"{p.projected:.1f}", _range(p.range))
        else:
            table.add_row("BN", f"[dim]{p.label}[/]", _opp(p), _status(p), f"[dim]{p.projected:.1f}[/]",
                          f"[dim]{_range(p.range)}[/]")
    console.print(table)

    if advice.to_start:
        console.print("[bold]Changes:[/]")
        for p in advice.to_start:
            console.print(f"  [green]Start[/] {p.label}  {p.projected:.1f}")
        for p in advice.to_bench:
            console.print(f"  [red]Bench[/] {p.label}  {p.projected:.1f}")
        console.print(
            f"Projected: {advice.current_total:.1f} → [bold]{advice.recommended_total:.1f}[/] "
            f"([green]{advice.gain:+.1f}[/])"
        )
    else:
        console.print(f"[green]Your current lineup is optimal.[/] Projected {advice.recommended_total:.1f}")
    r = advice.recommended_range
    if r:
        console.print(
            f"Team range: floor [bold]{r.floor:.1f}[/] - ceiling [bold]{r.ceiling:.1f}[/] "
            f"(expected {r.expected:.1f}; 95% of weeks score above the floor, 95% below the ceiling)"
        )
        c = advice.current_range
        if advice.to_start and c:
            console.print(f"[dim]Current lineup range: {c.floor:.1f} - {c.ceiling:.1f} (expected {c.expected:.1f})[/]")


@app.command()
def waivers(
    week: Optional[int] = typer.Option(None, "--week", "-w", help="First week to evaluate (default: current)"),
    weeks: int = typer.Option(3, "--weeks", "-n", min=1, max=8, help="How many weeks ahead to value players"),
    position: Optional[str] = typer.Option(None, "--position", "-p", help="Only this position (QB, RB, WR, ...)"),
    limit: int = typer.Option(15, "--limit"),
    league: Optional[str] = typer.Option(None, "--league", "-l"),
):
    """Waiver wire: free agents ranked by upgrade over your weakest player."""
    cfg, league_id = _require_config(league)
    ctx, picks = waiver_picks(_client(), league_id, cfg.user_id, week, weeks, position, limit)

    span = f"weeks {ctx.week}-{ctx.week + weeks - 1}" if weeks > 1 else f"week {ctx.week}"
    table = Table(title=f"{ctx.league['name']} — waiver targets ({span})")
    for col in ("#", "Add", "Status", f"Proj ({weeks}w)", "This wk", "Drop", "Drop proj", "Gain"):
        table.add_column(col, justify="right" if col not in ("Add", "Drop", "Status") else "left")
    for i, pk in enumerate(picks, 1):
        drop = f"{pk.drop.label} {_status(pk.drop)}" if pk.drop else "[dim](open spot)[/]"
        table.add_row(
            str(i), pk.add.label, _status(pk.add), f"{pk.add_value:.1f}", f"{pk.this_week:.1f}",
            drop, f"{pk.drop_value:.1f}", f"[green]+{pk.gain:.1f}[/]",
        )
    if picks:
        console.print(table)
    else:
        console.print("[green]No free agent projects better than your current players.[/]")


@app.command()
def matchup(
    week: Optional[int] = typer.Option(None, "--week", "-w", help="NFL week (default: current)"),
    league: Optional[str] = typer.Option(None, "--league", "-l"),
    optimal: bool = typer.Option(False, "--optimal", help="Show your optimal lineup instead of the one you set"),
    opp_optimal: bool = typer.Option(False, "--opp-optimal", help="Show your opponent's optimal lineup"),
):
    """Your matchup: both starting lineups with projections and floor-ceiling ranges."""
    cfg, league_id = _require_config(league)
    m = build_matchup(_client(), league_id, cfg.user_id, week)
    my_view, their_view = ("optimal" if optimal else "current"), ("optimal" if opp_optimal else "current")
    me = m.me.view(my_view)
    them = m.opponent.view(their_view) if m.opponent else None
    me_name = m.me.team_name + (" (optimal)" if optimal else "")
    them_name = (m.opponent.team_name + (" (optimal)" if opp_optimal else "")) if m.opponent else ""

    def cell(p) -> tuple[str, str]:
        """(player text, projection + range text) for one side of a table row."""
        if p is None:
            return "[red]EMPTY[/]", ""
        return f"{p.label} {_status(p)}".strip(), f"{p.projected:.1f}\n[dim]{_range(p.range)}[/]"

    title = f"{m.league} - Week {m.week}: {me_name}" + (f" vs {them_name}" if them else " (no opponent)")
    table = Table(title=title)
    table.add_column(me_name)
    table.add_column("Proj", justify="right")
    table.add_column("Slot", justify="center", style="dim")
    if them:
        table.add_column("Proj", justify="right")
        table.add_column(them_name)
    for i, s in enumerate(me.slots):
        left, left_pts = cell(s.player)
        if them:
            opp = them.slots[i].player if i < len(them.slots) else None
            right, right_pts = cell(opp)
            mine_v = s.player.projected if s.player else None
            opp_v = opp.projected if opp else None
            # Subtle edge marker: colour the higher projection in each slot.
            if mine_v is not None and (opp_v is None or mine_v > opp_v):
                left_pts = "[green]" + left_pts.replace("\n", "[/]\n", 1)
            elif opp_v is not None and (mine_v is None or opp_v > mine_v):
                right_pts = "[yellow]" + right_pts.replace("\n", "[/]\n", 1)
            table.add_row(left, left_pts, s.slot, right_pts, right)
        else:
            table.add_row(left, left_pts, s.slot)
    table.add_section()
    if them:
        table.add_row("[bold]Total[/]", f"[bold]{me.projected:.1f}[/]", "", f"[bold]{them.projected:.1f}[/]", "[bold]Total[/]")
    else:
        table.add_row("[bold]Total[/]", f"[bold]{me.projected:.1f}[/]", "")
    console.print(table)
    for name, sd in [(me_name, me)] + ([(them_name, them)] if them else []):
        if sd.range:
            console.print(f"{name}: floor {sd.range.floor:.1f} - ceiling {sd.range.ceiling:.1f} "
                          f"(expected {sd.range.expected:.1f})")
    win = (m.win_probabilities or {}).get((my_view, their_view))
    if win is not None:
        console.print(f"Win probability: [bold]{win:.0%}[/] for {me_name}")


@app.command()
def players(
    names: list[str] = typer.Argument(..., help='Player names, e.g. "Bijan Robinson" Gibbs'),
    week: Optional[int] = typer.Option(None, "--week", "-w", help="First week (default: current)"),
    weeks: int = typer.Option(4, "--weeks", "-n", min=1, max=8, help="How many weeks to show"),
    scoring: Optional[str] = typer.Option(
        None, "--scoring", "-s",
        help="league | default | half | std  (default: your league if set up, else Sleeper default)",
    ),
    league: Optional[str] = typer.Option(None, "--league", "-l", help="Use this league's scoring"),
):
    """Look up one or more players and compare their projections. No setup required."""
    from fantasycoach.core.models import display_name, normalize
    from fantasycoach.core.scoring import SCORING_PRESETS
    from fantasycoach.players import lookup
    from fantasycoach.sources.espn import STAT_LABELS

    if scoring not in (None, "league", *SCORING_PRESETS):
        console.print("[red]--scoring must be one of: league, default, half, std[/]")
        raise typer.Exit(1)
    client = _client()
    db = client.players()
    ids: list[str] = []
    for name in names:
        matches = lookup.search(db, name, limit=4)
        if not matches:
            console.print(f"[red]No player found for '{name}'[/]")
            continue
        pid, info = matches[0]
        ids.append(pid)
        if len(matches) > 1 and normalize(name) != normalize(display_name(info)):
            others = ", ".join(f"{display_name(i)} ({i.get('position')})" for _, i in matches[1:])
            console.print(f"[dim]'{name}' -> {display_name(info)}  (also matched: {others})[/]")
    if not ids:
        raise typer.Exit(1)

    league_id = league or (Config.load().league_id if scoring in (None, "league") else None)
    label, start, outs = compare_players(client, list(dict.fromkeys(ids)), scoring, league_id, week, weeks)

    table = Table(title=f"Projections - {label}")
    table.add_column("Player")
    for w in outs[0].weeks:
        table.add_column(f"Wk {w.week}", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Avg", justify="right")
    best = {i: max(o.weeks[i].points for o in outs) for i in range(len(outs[0].weeks))}
    for o in sorted(outs, key=lambda o: o.total, reverse=True):
        status = f" [red]{o.injury_status}[/]" if o.injury_status else ""
        cells = []
        for i, w in enumerate(o.weeks):
            txt = "[dim]BYE[/]" if w.bye else ("[dim]-[/]" if not w.projected else f"{w.points:.1f}")
            if len(outs) > 1 and w.opponent and w.points == best[i] and w.points > 0:
                txt = f"[bold green]{txt}[/]"
            cells.append(f"{txt}\n[dim]{w.opponent or ''}[/]")
        table.add_row(
            f"{o.name}{status}\n[dim]{o.position} - {o.team or 'FA'}[/]", *cells, f"{o.total:.1f}", f"{o.average:.1f}"
        )
    console.print(table)

    ranged = [(o, o.weeks[0]) for o in outs if o.weeks and o.weeks[0].range]
    if ranged:
        out_table = Table(title=f"Week {start} outcome range (90% of simulated games fall between floor and ceiling)")
        for col in ("Player", "Sleeper", "Expected", "Floor", "Ceiling", "Based on"):
            out_table.add_column(col, justify="left" if col in ("Player", "Based on") else "right")
        for o, w in sorted(ranged, key=lambda x: x[1].range.expected, reverse=True):
            r = w.range
            source = ("DraftKings " + ", ".join(STAT_LABELS.get(s, s) for s in r.market_stats)) if r.market_stats else (
                "team total + history" if r.team_points else "history")
            out_table.add_row(o.name, f"{w.points:.1f}", f"[bold]{r.expected:.1f}[/]", f"{r.floor:.1f}",
                              f"{r.ceiling:.1f}", f"[dim]{source}[/]")
        console.print(out_table)
    if len(outs) > 1:
        top = max(outs, key=lambda o: o.this_week)
        console.print(f"Week {start}: start [bold]{top.name}[/] ({top.this_week:.1f} pts)")
        if len(ranged) > 1:
            safest = max(ranged, key=lambda x: x[1].range.floor)[0]
            upside = max(ranged, key=lambda x: x[1].range.ceiling)[0]
            console.print(f"Safest floor: [bold]{safest.name}[/] - Highest ceiling: [bold]{upside.name}[/]")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="Interface to bind (use 0.0.0.0 to allow other devices)"),
    port: int = typer.Option(8000, help="Port to serve on"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open a browser tab"),
):
    """Start the web interface."""
    import threading
    import webbrowser

    import uvicorn

    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    console.print(f"FantasyCoach running at [bold]{url}[/]  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run("fantasycoach.interface.web:app", host=host, port=port, log_level="warning")


def main() -> None:
    """Entry point: run the CLI and turn Sleeper errors into a short message instead of a traceback."""
    try:
        app()
    except SleeperError as e:
        console.print(f"[red]{e}[/]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
