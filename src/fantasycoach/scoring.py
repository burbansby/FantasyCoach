"""Turn Sleeper stat lines into fantasy points using a league's scoring settings."""

from __future__ import annotations

# Injury statuses that mean the player will not (or almost certainly will not) play.
OUT_STATUSES = {"Out", "IR", "PUP", "Sus", "NA", "DNR", "COV"}
# Statuses that span multiple weeks; "Out"/"Doubtful" are assumed to apply to one week only.
LONG_TERM_STATUSES = {"IR", "PUP", "Sus", "NA"}
DOUBTFUL_FACTOR = 0.25


def score(stats: dict[str, float], scoring_settings: dict[str, float]) -> float:
    """Fantasy points for a stat line.

    Sleeper projections and league ``scoring_settings`` share stat keys
    (``pass_yd``, ``rec``, ``rush_td``...), so points are a dot product.
    Falls back to Sleeper's precomputed PPR total when no settings are given.
    """
    if not scoring_settings:
        return float(stats.get("pts_ppr") or 0.0)
    return round(sum((stats.get(k) or 0.0) * v for k, v in scoring_settings.items()), 2)


def availability_factor(injury_status: str | None, weeks_ahead: int = 0) -> float:
    """Multiplier applied to projections to account for injury designations.

    For future weeks (``weeks_ahead > 0``) only long-term designations apply.
    """
    if weeks_ahead > 0:
        return 0.0 if injury_status in LONG_TERM_STATUSES else 1.0
    if injury_status in OUT_STATUSES:
        return 0.0
    if injury_status == "Doubtful":
        return DOUBTFUL_FACTOR
    return 1.0
