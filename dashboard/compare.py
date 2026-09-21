"""Overlay comparison helpers for room × parameter time series.

Pure functions (no Django requests) so normalize / axis assignment can be
unit-tested without a database. The view lives here too to keep views.py
from growing further; urls.py imports compare_view directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from dashboard.correlations import make_series_label

MAX_SERIES = 24
MAX_EVENTS = 30
VALID_NORMALIZE = ("off", "minmax", "zscore")
DEFAULT_NORMALIZE = "minmax"

# Distinct colours that stay readable on the dark dashboard theme.
SERIES_COLORS = [
    "#38bdf8",
    "#34d399",
    "#f59e0b",
    "#f472b6",
    "#a78bfa",
    "#f87171",
    "#22d3ee",
    "#84cc16",
    "#fb7185",
    "#818cf8",
    "#2dd4bf",
    "#fbbf24",
    "#e879f9",
    "#60a5fa",
    "#4ade80",
    "#fb923c",
    "#c084fc",
    "#67e8f9",
    "#facc15",
    "#f43f5e",
    "#93c5fd",
    "#86efac",
    "#fdba74",
    "#d8b4fe",
]


def parse_normalize(value, default=DEFAULT_NORMALIZE) -> str:
    """Return a valid normalize mode."""
    mode = str(value or default).strip().lower()
    if mode in ("raw", "none", ""):
        return "off"
    if mode in VALID_NORMALIZE:
        return mode
    return default


def series_stats(frame: pd.DataFrame) -> list:
    """Per-column min / max / mean / last / n from a raw (unnormalized) frame."""
    rows = []
    if frame is None or frame.empty:
        return rows
    for col in frame.columns:
        series = pd.to_numeric(frame[col], errors="coerce")
        valid = series.dropna()
        last = valid.iloc[-1] if not valid.empty else None
        rows.append(
            {
                "label": str(col),
                "n": int(valid.shape[0]),
                "min": None if valid.empty else float(valid.min()),
                "max": None if valid.empty else float(valid.max()),
                "mean": None if valid.empty else float(valid.mean()),
                "last": None if last is None else float(last),
            }
        )
    return rows


def normalize_frame(frame: pd.DataFrame, mode: str = DEFAULT_NORMALIZE) -> pd.DataFrame:
    """Return a copy of `frame` scaled according to `mode`.

    off     — values unchanged
    minmax  — each column independently mapped to [0, 1]; constant → 0.5
    zscore  — (x - mean) / std (population); constant → 0
    """
    mode = parse_normalize(mode)
    if frame is None or frame.empty or mode == "off":
        return frame.copy() if frame is not None else pd.DataFrame()

    out = frame.copy()
    for col in out.columns:
        series = pd.to_numeric(out[col], errors="coerce")
        valid = series.dropna()
        if valid.empty:
            out[col] = series
            continue
        if mode == "minmax":
            lo = float(valid.min())
            hi = float(valid.max())
            if hi == lo:
                out[col] = series.map(lambda v: np.nan if pd.isna(v) else 0.5)
            else:
                out[col] = (series - lo) / (hi - lo)
        else:  # zscore
            mu = float(valid.mean())
            sigma = float(valid.std(ddof=0))
            if sigma == 0 or np.isnan(sigma):
                out[col] = series.map(lambda v: np.nan if pd.isna(v) else 0.0)
            else:
                out[col] = (series - mu) / sigma
    return out


def unit_axis_map(units_by_label: dict, mode: str):
    """Assign Plotly y-axis ids when plotting raw values.

    Dual axis only when normalize is off and exactly two distinct units
    are present. Otherwise every series uses yaxis 'y'.
    Returns (axis_by_label, axis_titles, dual).
    """
    mode = parse_normalize(mode)
    labels = list(units_by_label.keys())
    if mode != "off":
        return {label: "y" for label in labels}, {}, False

    unique = []
    for unit in units_by_label.values():
        key = unit or ""
        if key not in unique:
            unique.append(key)
    if len(unique) != 2:
        return {label: "y" for label in labels}, {}, False

    axis_for_unit = {unique[0]: "y", unique[1]: "y2"}
    axis_by_label = {
        label: axis_for_unit[unit or ""] for label, unit in units_by_label.items()
    }
    titles = {"y": unique[0] or "Value", "y2": unique[1] or "Value"}
    return axis_by_label, titles, True


def downsample_frame(frame: pd.DataFrame, max_points: int = 2500) -> pd.DataFrame:
    """Thin a resampled frame so Plotly stays responsive on long ranges."""
    if frame is None or frame.empty:
        return frame
    n = len(frame)
    if n <= max_points:
        return frame
    step = max(1, n // max_points)
    return frame.iloc[::step]


def _yaxis_title(mode: str, dual: bool, single_unit: str) -> str:
    if mode == "minmax":
        return "Min–max (0–1)"
    if mode == "zscore":
        return "Z-score"
    if dual:
        return single_unit or "Value"
    return single_unit or "Value"
