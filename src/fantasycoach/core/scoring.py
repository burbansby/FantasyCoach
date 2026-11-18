"""Fantasy scoring: turn stat lines into points, plus standard scoring presets.

Sleeper's projections, its actual stats and every league's ``scoring_settings`` use the
same stat keys (``pass_yd``, ``rec``, ``rush_td``...), so a player's points are simply each
stat times its point value, summed. The presets (Sleeper default PPR, Half PPR, Standard)
are used by player lookup when no league is selected.
"""

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


# Sleeper's standard scoring for a new league (full PPR receptions).
SLEEPER_DEFAULT_SCORING: dict[str, float] = {
    # passing
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2,
    # rushing / receiving
    "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
    "rec": 1, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
    # misc offense
    "fum_lost": -2, "fum_rec_td": 6,
    # kicking
    "fgm_0_19": 3, "fgm_20_29": 3, "fgm_30_39": 3, "fgm_40_49": 4, "fgm_50p": 5,
    "fgmiss": -1, "xpm": 1, "xpmiss": -1,
    # team defense
    "def_td": 6, "sack": 1, "int": 2, "fum_rec": 2, "safe": 2, "ff": 1, "blk_kick": 2,
    "def_st_td": 6, "def_st_ff": 1, "def_st_fum_rec": 1,
    "pts_allow_0": 10, "pts_allow_1_6": 7, "pts_allow_7_13": 4, "pts_allow_14_20": 1,
    "pts_allow_21_27": 0, "pts_allow_28_34": -1, "pts_allow_35p": -4,
}

SCORING_PRESETS: dict[str, tuple[str, dict[str, float]]] = {
    "default": ("Sleeper default (PPR)", SLEEPER_DEFAULT_SCORING),
    "half": ("Half PPR", {**SLEEPER_DEFAULT_SCORING, "rec": 0.5}),
    "std": ("Standard (no PPR)", {**SLEEPER_DEFAULT_SCORING, "rec": 0}),
}
