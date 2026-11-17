"""Turn Sleeper stat lines into fantasy points using a league's scoring settings."""

from __future__ import annotations


def score(stats: dict[str, float], scoring_settings: dict[str, float]) -> float:
    """Fantasy points for a stat line.

    Sleeper projections and league ``scoring_settings`` share stat keys
    (``pass_yd``, ``rec``, ``rush_td``...), so points are a dot product.
    Falls back to Sleeper's precomputed PPR total when no settings are given.
    """
    if not scoring_settings:
        return float(stats.get("pts_ppr") or 0.0)
    return round(sum((stats.get(k) or 0.0) * v for k, v in scoring_settings.items()), 2)
