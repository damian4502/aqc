"""Daily and hourly pattern helpers (no Django requests).

Each calendar hour is averaged first so a burst of samples in one hour
does not overweight that slot, then slots are grouped by hour-of-day and
weekday in Europe/Ljubljana.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def to_local_naive(timestamps: pd.Series) -> pd.Series:
    """Convert a timestamp series to naive Europe/Ljubljana wall time."""
    ts = pd.to_datetime(timestamps)
    tz = getattr(ts.dt, "tz", None)
    if tz is not None:
        ts = ts.dt.tz_convert("Europe/Ljubljana").dt.tz_localize(None)
    return ts


def calendar_hour_means(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse raw samples to one mean per local calendar hour.

    Expects columns timestamp, value. Returns timestamp (hour start), value.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["timestamp", "value"])
    frame = pd.DataFrame(
        {
            "timestamp": to_local_naive(df["timestamp"]),
            "value": pd.to_numeric(df["value"], errors="coerce"),
        }
    ).dropna()
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "value"])
    frame["bucket"] = frame["timestamp"].dt.floor("h")
    out = frame.groupby("bucket", as_index=False)["value"].mean()
    return out.rename(columns={"bucket": "timestamp"})


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Add hour (0-23) and weekday (0=Monday) columns."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["timestamp", "value", "hour", "weekday", "day_name"])
    out = pd.DataFrame(
        {
            "timestamp": to_local_naive(df["timestamp"]),
            "value": pd.to_numeric(df["value"], errors="coerce"),
        }
    ).dropna()
    if out.empty:
        return pd.DataFrame(columns=["timestamp", "value", "hour", "weekday", "day_name"])
    out["hour"] = out["timestamp"].dt.hour.astype(int)
    out["weekday"] = out["timestamp"].dt.weekday.astype(int)
    out["day_name"] = out["weekday"].map(lambda i: WEEKDAY_NAMES[int(i)])
    return out


def _profile(df: pd.DataFrame, key: str, index) -> pd.DataFrame:
    if df is None or df.empty:
        grouped = pd.DataFrame(
            index=pd.Index(list(index), name=key),
            columns=["mean", "std", "count", "min", "max"],
        )
    else:
        grouped = df.groupby(key)["value"].agg(["mean", "std", "count", "min", "max"])
        grouped = grouped.reindex(index)
        grouped.index.name = key
    grouped["count"] = pd.to_numeric(grouped["count"], errors="coerce").fillna(0).astype(int)
    return grouped.reset_index()


def hourly_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Mean/std/count by hour of day. Always 24 rows (0-23)."""
    result = _profile(df, "hour", range(24))
    return result


def weekday_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Mean/std/count by weekday. Always 7 rows, Monday first."""
    result = _profile(df, "weekday", range(7))
    result["day_name"] = [WEEKDAY_NAMES[int(i)] for i in result["weekday"]]
    return result


def weekday_hour_matrices(df: pd.DataFrame):
    """Return (mean, count) DataFrames, index=weekday name, columns='00'..'23'."""
    hours = [f"{h:02d}" for h in range(24)]
    empty_mean = pd.DataFrame(np.nan, index=WEEKDAY_NAMES, columns=hours)
    empty_count = pd.DataFrame(0, index=WEEKDAY_NAMES, columns=hours, dtype=int)
    if df is None or df.empty:
        return empty_mean, empty_count

    work = df.copy()
    work["hour_label"] = work["hour"].map(lambda h: f"{int(h):02d}")
    mean = work.pivot_table(
        index="weekday", columns="hour_label", values="value", aggfunc="mean"
    )
    count = work.pivot_table(
        index="weekday", columns="hour_label", values="value", aggfunc="count"
    )
    mean = mean.reindex(index=range(7), columns=hours)
    count = count.reindex(index=range(7), columns=hours).fillna(0).astype(int)
    mean.index = WEEKDAY_NAMES
    count.index = WEEKDAY_NAMES
    return mean, count


def _best_row(profile: pd.DataFrame, how: str):
    """Return the row with max/min mean, or None if no data."""
    if profile is None or profile.empty:
        return None
    valid = profile.dropna(subset=["mean"])
    if valid.empty:
        return None
    idx = valid["mean"].idxmax() if how == "max" else valid["mean"].idxmin()
    return valid.loc[idx]


def summarize_patterns(hourly: pd.DataFrame, weekly: pd.DataFrame) -> dict:
    """Peak/trough hour and weekday plus overall mean."""
    peak_h = _best_row(hourly, "max")
    quiet_h = _best_row(hourly, "min")
    peak_d = _best_row(weekly, "max")
    quiet_d = _best_row(weekly, "min")
    overall = None
    if hourly is not None and not hourly.empty and hourly["count"].sum() > 0:
        weights = hourly["count"].astype(float)
        means = hourly["mean"]
        mask = means.notna() & (weights > 0)
        if mask.any():
            overall = float(np.average(means[mask], weights=weights[mask]))

    def hour_label(row):
        if row is None:
            return None
        return f"{int(row['hour']):02d}:00"

    def day_label(row):
        if row is None:
            return None
        return WEEKDAY_NAMES[int(row["weekday"])]

    def mean_of(row):
        if row is None or pd.isna(row["mean"]):
            return None
        return float(row["mean"])

    return {
        "overall_mean": overall,
        "peak_hour": hour_label(peak_h),
        "peak_hour_value": mean_of(peak_h),
        "quiet_hour": hour_label(quiet_h),
        "quiet_hour_value": mean_of(quiet_h),
        "peak_day": day_label(peak_d),
        "peak_day_value": mean_of(peak_d),
        "quiet_day": day_label(quiet_d),
        "quiet_day_value": mean_of(quiet_d),
        "hour_bins": int(hourly["count"].sum()) if hourly is not None and not hourly.empty else 0,
    }
