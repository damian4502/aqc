"""Correlation analysis for room × parameter time series.

Pure helpers (no Django requests) so the math can be unit-tested without a
database. Views resample measurements, then call pairwise_correlations().
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

MIN_OVERLAP = 10
MAX_SERIES = 40
SCATTER_MAX_POINTS = 400


def strength_label(r: float) -> str:
    """Human-readable |r| band for Pearson/Spearman coefficients."""
    ar = abs(float(r))
    if ar >= 0.8:
        return "Very strong"
    if ar >= 0.6:
        return "Strong"
    if ar >= 0.4:
        return "Moderate"
    if ar >= 0.2:
        return "Weak"
    return "Negligible"


def make_series_label(
    room_name: str,
    parameter_name: str,
    *,
    include_room: bool,
    include_parameter: bool,
) -> str:
    """Build a heatmap axis label. Drop the dimension that does not vary."""
    if include_room and include_parameter:
        return f"{room_name} · {parameter_name}"
    if include_room:
        return room_name
    return parameter_name


def format_pvalue(p) -> str:
    """Compact p-value for tables. None/NaN → em dash."""
    if p is None:
        return "—"
    try:
        pf = float(p)
    except (TypeError, ValueError):
        return "—"
    if np.isnan(pf):
        return "—"
    if pf < 0.001:
        return "< 0.001"
    if pf < 0.01:
        return f"{pf:.3f}"
    return f"{pf:.2f}"


def downsample_series(resampled: pd.DataFrame, max_points: int = SCATTER_MAX_POINTS) -> dict:
    """JSON-friendly {label: {t: [...], v: [...]}} for click-to-scatter."""
    payload = {}
    if resampled is None or resampled.empty:
        return payload
    for col in resampled.columns:
        series = resampled[col]
        if len(series) > max_points:
            step = max(1, len(series) // max_points)
            series = series.iloc[::step]
        times = []
        values = []
        for idx, val in series.items():
            ts = pd.Timestamp(idx)
            times.append(None if pd.isna(ts) else ts.isoformat())
            if val is None or (isinstance(val, float) and np.isnan(val)) or pd.isna(val):
                values.append(None)
            else:
                values.append(float(val))
        payload[str(col)] = {"t": times, "v": values}
    return payload


def _corr_with_p(x: np.ndarray, y: np.ndarray, method: str):
    """Return (r, p). Uses SciPy; falls back to pandas r with p=None."""
    if method == "spearman":
        try:
            from scipy.stats import spearmanr

            r, p = spearmanr(x, y, nan_policy="omit")
            return float(r), float(p)
        except Exception:
            r = pd.Series(x).corr(pd.Series(y), method="spearman")
            return (float(r) if r is not None and not np.isnan(r) else None), None
    try:
        from scipy.stats import pearsonr

        r, p = pearsonr(x, y)
        return float(r), float(p)
    except Exception:
        r = pd.Series(x).corr(pd.Series(y), method="pearson")
        return (float(r) if r is not None and not np.isnan(r) else None), None


def pairwise_correlations(
    resampled: pd.DataFrame,
    method: str = "pearson",
    min_overlap: int = MIN_OVERLAP,
):
    """Pearson/Spearman matrix plus ranked off-diagonal pairs.

    Returns (matrix, pairs, overlap_counts). Diagonal is 1.0 (or NaN if a
    series is constant / empty). Pairs with fewer than min_overlap aligned
    samples are omitted from the ranking and stored as NaN in the matrix.
    """
    if method not in ("pearson", "spearman"):
        method = "pearson"
    try:
        min_overlap = int(min_overlap)
    except (TypeError, ValueError):
        min_overlap = MIN_OVERLAP
    min_overlap = max(3, min_overlap)

    if resampled is None or resampled.empty:
        empty = pd.DataFrame()
        return empty, [], empty

    frame = resampled.copy()
    # Drop columns that cannot produce a correlation
    frame = frame.dropna(axis=1, how="all")
    cols = [str(c) for c in frame.columns]
    frame.columns = cols
    n = len(cols)
    matrix = pd.DataFrame(np.eye(n), index=cols, columns=cols, dtype=float)
    counts = pd.DataFrame(np.nan, index=cols, columns=cols, dtype=float)
    pairs = []

    for col in cols:
        counts.loc[col, col] = float(frame[col].dropna().shape[0])
        series = frame[col].dropna()
        if len(series) < min_overlap or series.std(ddof=0) == 0:
            matrix.loc[col, col] = np.nan

    for a, b in combinations(cols, 2):
        subset = frame[[a, b]].dropna()
        overlap = int(len(subset))
        counts.loc[a, b] = counts.loc[b, a] = overlap
        if overlap < min_overlap:
            matrix.loc[a, b] = matrix.loc[b, a] = np.nan
            continue
        sa = subset[a]
        sb = subset[b]
        if sa.std(ddof=0) == 0 or sb.std(ddof=0) == 0:
            matrix.loc[a, b] = matrix.loc[b, a] = np.nan
            continue
        r, p = _corr_with_p(sa.to_numpy(), sb.to_numpy(), method)
        if r is None or (isinstance(r, float) and np.isnan(r)):
            matrix.loc[a, b] = matrix.loc[b, a] = np.nan
            continue
        r = float(r)
        # Clamp numerical noise just outside [-1, 1]
        if r > 1.0:
            r = 1.0
        elif r < -1.0:
            r = -1.0
        p_val = None if p is None or (isinstance(p, float) and np.isnan(p)) else float(p)
        matrix.loc[a, b] = matrix.loc[b, a] = r
        pairs.append(
            {
                "a": a,
                "b": b,
                "r": r,
                "abs_r": abs(r),
                "p": p_val,
                "p_display": format_pvalue(p_val),
                "n": overlap,
                "strength": strength_label(r),
                "direction": "Positive" if r >= 0 else "Negative",
            }
        )

    pairs.sort(key=lambda item: (-item["abs_r"], item["a"], item["b"]))
    return matrix, pairs, counts
