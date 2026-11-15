"""Automatic decay detection and exponential clearance-rate fit.

Pure helpers (no Django requests) so the math can be unit-tested without a
database. The view resamples one series per room × parameter, then calls
analyze_series().

A decay starts at a peak — or at the first sample when the window already
opens on a falling series — and ends when the value climbs again or the
segment reaches its maximum length. Each kept segment is fit to

    C(t) = C_base + (C_start - C_base) * exp(-λ t)

with t in hours. λ is the clearance rate (1/h). For a well-mixed tracer
with no indoor source during the drop, that rate is the air-change rate.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

MAX_SERIES = 16
MIN_POINTS = 5
DEFAULT_MIN_DURATION_MIN = 30
DEFAULT_MAX_DURATION_H = 18
DEFAULT_BASELINE_PERCENTILE = 10
DEFAULT_MIN_R2 = 0.70
DEFAULT_MIN_ACH = 0.05
DEFAULT_MAX_ACH = 30.0

_PALETTE = (
    "#38bdf8",
    "#34d399",
    "#fbbf24",
    "#f472b6",
    "#a78bfa",
    "#fb7185",
    "#22d3ee",
    "#a3e635",
    "#fdba74",
    "#818cf8",
    "#2dd4bf",
    "#e879f9",
)


def ach_band(ach: float) -> str:
    """Rough clearance-speed label. Not an air-quality grade."""
    if ach < 0.5:
        return "Slow"
    if ach < 1.5:
        return "Moderate"
    if ach < 3.0:
        return "Fast"
    return "Very fast"


def format_duration(hours: float) -> str:
    minutes = int(round(float(hours) * 60))
    if minutes < 90:
        return f"{minutes} min"
    whole, rem = divmod(minutes, 60)
    if rem == 0:
        return f"{whole} h"
    return f"{whole} h {rem} min"


def format_half_life(hours: float) -> str:
    return format_duration(hours)


def _finite(value, default, lo, hi):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(hi, max(lo, number))


def _robust_noise(values: np.ndarray) -> float:
    """Typical step-to-step scatter, from the MAD of first differences."""
    if len(values) < 3:
        return 0.0
    diffs = np.diff(values.astype(float))
    center = np.median(diffs)
    mad = np.median(np.abs(diffs - center))
    return float(1.4826 * mad)


def _auto_min_rise(values: np.ndarray) -> float:
    """Smallest peak height that is treated as a real excursion."""
    p10, p90 = np.percentile(values, [10, 90])
    spread = float(p90 - p10)
    noise = _robust_noise(values)
    scale = abs(float(np.median(values)))
    return max(spread * 0.18, noise * 5.0, scale * 1e-4, 1e-6)


def _as_datetime_series(series: pd.Series) -> pd.Series:
    frame = pd.to_numeric(series, errors="coerce")
    frame = frame.dropna()
    if frame.empty:
        return frame
    frame = frame.sort_index()
    frame.index = pd.to_datetime(frame.index)
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_convert("Europe/Ljubljana").tz_localize(None)
    # One value per timestamp. A duplicate stamp would break the walk.
    frame = frame.groupby(level=0).mean()
    return frame


def _score_baseline(t_hours: np.ndarray, values: np.ndarray, c_base: float):
    """Anchored exponential score for one baseline guess.

    The curve is forced through the first sample. λ is the through-origin
    slope of ln((C - C_base) / (C_start - C_base)) versus time.
    """
    c0 = float(values[0])
    if not math.isfinite(c_base) or c_base >= c0:
        return None
    if np.any(values <= c_base):
        return None
    excess0 = c0 - c_base
    if excess0 <= 0:
        return None
    ratio = (values - c_base) / excess0
    if np.any(ratio <= 0):
        return None
    log_ratio = np.log(ratio)
    denom = float(np.dot(t_hours, t_hours))
    if denom <= 0:
        return None
    lam = -float(np.dot(t_hours, log_ratio)) / denom
    if not math.isfinite(lam) or lam <= 0:
        return None
    pred = c_base + excess0 * np.exp(-lam * t_hours)
    resid = values - pred
    ss_res = float(np.dot(resid, resid))
    centered = values - float(np.mean(values))
    ss_tot = float(np.dot(centered, centered))
    if ss_tot <= 0:
        return None
    r2 = 1.0 - ss_res / ss_tot
    return {"ach": lam, "c_base": float(c_base), "c_start": c0, "r2": float(r2)}


def fit_exponential_decay(
    t_hours,
    values,
    *,
    min_r2: float = DEFAULT_MIN_R2,
    min_ach: float = DEFAULT_MIN_ACH,
    max_ach: float = DEFAULT_MAX_ACH,
    max_baseline: float | None = None,
):
    """Fit C_base and λ. Returns None when the curve is not a clean decay.

    C_base is searched below the lowest sample. The guess with the best
    coefficient of determination on the original scale is kept.

    A short, nearly linear drop does not identify the asymptote: C_base and
    λ trade off, and the search then sits on its lower rail (which can be
    largely negative). Those fits are rejected. A positive series also
    cannot return a negative baseline.
    """
    t_hours = np.asarray(t_hours, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(values) < MIN_POINTS or len(t_hours) != len(values):
        return None
    if np.any(~np.isfinite(values)) or np.any(~np.isfinite(t_hours)):
        return None
    if t_hours[0] != 0:
        t_hours = t_hours - t_hours[0]
    if np.any(np.diff(t_hours) < 0):
        return None

    c0 = float(values[0])
    vmin = float(np.min(values))
    if vmin >= c0:
        return None
    span = c0 - vmin
    upper = vmin - max(span * 0.002, 1e-9)
    lower = vmin - span * 12.0
    # Keep a true baseline of 0 inside the window, but do not search
    # hundreds of units below a series that never went negative.
    if vmin > 0:
        lower = max(lower, -max(span * 0.05, 1e-6))
    if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
        return None

    best = None
    for c_base in np.linspace(lower, upper, 31):
        scored = _score_baseline(t_hours, values, float(c_base))
        if scored is None:
            continue
        if best is None or scored["r2"] > best["r2"]:
            best = scored
    if best is None:
        return None

    step = (upper - lower) / 30.0
    refine_lo = max(lower, best["c_base"] - step)
    refine_hi = min(upper, best["c_base"] + step)
    if refine_hi > refine_lo:
        for c_base in np.linspace(refine_lo, refine_hi, 21):
            scored = _score_baseline(t_hours, values, float(c_base))
            if scored is None:
                continue
            if scored["r2"] > best["r2"]:
                best = scored

    # On the lower rail the data still want a lower asymptote, so λ is not
    # identified. This is the negative-baseline case.
    if best["c_base"] <= lower + step * 0.75:
        return None
    if vmin >= 0 and best["c_base"] < 0:
        if best["c_base"] < -max(span * 0.05, 1e-6):
            return None
        rescored = _score_baseline(t_hours, values, 0.0)
        if rescored is None:
            return None
        best = rescored

    if best["r2"] < min_r2 or best["ach"] < min_ach or best["ach"] > max_ach:
        return None
    if max_baseline is not None and math.isfinite(max_baseline) and best["c_base"] > max_baseline:
        return None
    best["half_life_h"] = math.log(2.0) / best["ach"]
    return best


def _candidate_starts(values: np.ndarray, baseline: float, min_rise: float, min_points: int):
    """Indexes where a decay is allowed to begin."""
    n = len(values)
    starts = []
    if n >= min_points and values[0] - baseline >= min_rise:
        early = values[:min_points]
        if values[0] >= float(np.max(early)) - min_rise * 0.05 and values[0] > float(np.median(early)):
            starts.append(0)

    window = 5 if n >= 7 else 3
    smooth = pd.Series(values).rolling(window, center=True, min_periods=1).median().to_numpy()
    for i in range(1, n - 1):
        if smooth[i] < smooth[i - 1] or smooth[i] <= smooth[i + 1]:
            continue
        if values[i] - baseline < min_rise:
            continue
        if values[i] < values[i - 1] and values[i] < values[i + 1]:
            continue
        starts.append(i)
    return starts


def _segment_end(
    times: np.ndarray,
    values: np.ndarray,
    start: int,
    *,
    baseline: float,
    min_rise: float,
    max_duration_h: float,
    noise: float,
    gap_limit_h: float,
):
    """Last index of the decay that begins at ``start``."""
    n = len(values)
    peak = float(values[start])
    tol = max(noise * 2.0, min_rise * 0.03, 1e-9)
    # Skip only a short flat shoulder. A slow exponential must stay in the fit.
    shoulder_end = times[start] + np.timedelta64(20, "m")
    while start + 1 < n and times[start + 1] <= shoulder_end and values[start + 1] >= peak - tol:
        start += 1
        if values[start] > peak:
            peak = float(values[start])

    excess = max(peak - baseline, min_rise)
    restart = max(min_rise * 0.22, excess * 0.18, tol * 3.0)
    min_drop = max(min_rise * 0.25, excess * 0.20)

    floor = peak
    floor_i = start
    end = start
    t0 = times[start]
    for i in range(start + 1, n):
        gap_h = (times[i] - times[i - 1]) / np.timedelta64(1, "h")
        if gap_h > gap_limit_h:
            break
        elapsed_h = (times[i] - t0) / np.timedelta64(1, "h")
        if elapsed_h > max_duration_h:
            break
        value = float(values[i])
        if value < floor:
            floor = value
            floor_i = i
            end = i
            continue
        dropped = peak - floor
        if dropped >= min_drop and value > floor + restart:
            end = floor_i
            break
        end = i
    return start, end


def detect_decay_spans(
    series: pd.Series,
    *,
    baseline_percentile: float = DEFAULT_BASELINE_PERCENTILE,
    min_rise: float | None = None,
    min_points: int = MIN_POINTS,
    min_duration_min: float = DEFAULT_MIN_DURATION_MIN,
    max_duration_h: float = DEFAULT_MAX_DURATION_H,
):
    """Return non-overlapping (start_index, end_index) spans into ``series``.

    Indexes refer to the cleaned series (NaNs dropped, time-sorted). The
    cleaned series is the second return value.
    """
    cleaned = _as_datetime_series(series)
    empty = []
    if len(cleaned) < min_points:
        return empty, cleaned

    values = cleaned.to_numpy(dtype=float)
    times = cleaned.index.to_numpy()
    baseline = float(np.percentile(values, baseline_percentile))
    rise = float(min_rise) if min_rise is not None and min_rise > 0 else _auto_min_rise(values)
    noise = _robust_noise(values)
    if len(times) >= 3:
        steps = np.diff(times) / np.timedelta64(1, "h")
        typical = float(np.median(steps[steps > 0])) if np.any(steps > 0) else 0.25
    else:
        typical = 0.25
    gap_limit_h = max(typical * 3.0, 0.5)
    covered_until = -1
    spans = []

    for raw_start in _candidate_starts(values, baseline, rise, min_points):
        if raw_start <= covered_until:
            continue
        start, end = _segment_end(
            times,
            values,
            raw_start,
            baseline=baseline,
            min_rise=rise,
            max_duration_h=max_duration_h,
            noise=noise,
            gap_limit_h=gap_limit_h,
        )
        if end - start + 1 < min_points:
            continue
        duration_h = (times[end] - times[start]) / np.timedelta64(1, "h")
        if duration_h * 60.0 < min_duration_min:
            continue
        drop = float(values[start] - values[end])
        if drop <= 0:
            continue
        if drop < 0.25 * max(float(values[start]) - baseline, rise):
            continue
        spans.append((start, end))
        covered_until = end
    return spans, cleaned


def _fit_span(cleaned: pd.Series, start: int, end: int, **fit_kwargs):
    """Fit one span, re-anchored on an early peak if noise put it off index 0."""
    window = cleaned.iloc[start : end + 1]
    values = window.to_numpy(dtype=float)
    times = window.index.to_numpy()
    if len(values) < MIN_POINTS:
        return None
    peak = int(np.argmax(values))
    if 0 < peak <= max(2, int(0.25 * len(values))):
        values = values[peak:]
        times = times[peak:]
        start = start + peak
    if len(values) < MIN_POINTS:
        return None
    t_hours = (times - times[0]) / np.timedelta64(1, "h")
    t_hours = np.asarray(t_hours, dtype=float)
    fit = fit_exponential_decay(t_hours, values, **fit_kwargs)
    if fit is None:
        return None
    pred = fit["c_base"] + (fit["c_start"] - fit["c_base"]) * np.exp(-fit["ach"] * t_hours)
    return {
        "start_index": start,
        "end_index": start + len(values) - 1,
        "start": pd.Timestamp(times[0]),
        "end": pd.Timestamp(times[-1]),
        "t_hours": t_hours,
        "values": values,
        "predicted": pred,
        "fit": fit,
    }


def analyze_series(
    series: pd.Series,
    *,
    label: str,
    unit: str = "",
    baseline_percentile: float = DEFAULT_BASELINE_PERCENTILE,
    min_rise: float | None = None,
    min_points: int = MIN_POINTS,
    min_duration_min: float = DEFAULT_MIN_DURATION_MIN,
    max_duration_h: float = DEFAULT_MAX_DURATION_H,
    min_r2: float = DEFAULT_MIN_R2,
    min_ach: float = DEFAULT_MIN_ACH,
    max_ach: float = DEFAULT_MAX_ACH,
    max_baseline: float | None = None,
) -> list[dict]:
    """Detect decay spans and keep those with an acceptable exponential fit."""
    spans, cleaned = detect_decay_spans(
        series,
        baseline_percentile=baseline_percentile,
        min_rise=min_rise,
        min_points=min_points,
        min_duration_min=min_duration_min,
        max_duration_h=max_duration_h,
    )
    fit_kwargs = {
        "min_r2": min_r2,
        "min_ach": min_ach,
        "max_ach": max_ach,
        "max_baseline": max_baseline,
    }
    found = []
    for start, end in spans:
        fitted = _fit_span(cleaned, start, end, **fit_kwargs)
        if fitted is None:
            continue
        fit = fitted["fit"]
        duration_h = float(fitted["t_hours"][-1]) if len(fitted["t_hours"]) else 0.0
        found.append(
            {
                "label": label,
                "unit": unit or "",
                "start": fitted["start"],
                "end": fitted["end"],
                "duration_h": duration_h,
                "n": int(len(fitted["values"])),
                "c_start": float(fitted["values"][0]),
                "c_end": float(fitted["values"][-1]),
                "c_base": fit["c_base"],
                "ach": fit["ach"],
                "half_life_h": fit["half_life_h"],
                "r2": fit["r2"],
                "t_hours": fitted["t_hours"],
                "values": fitted["values"],
                "predicted": fitted["predicted"],
            }
        )
    return found


def summarize_decays(segments: list[dict], labels: list[str] | None = None) -> list[dict]:
    """One row per series: how many decays, and the median fit."""
    grouped: dict[str, list[dict]] = {}
    for segment in segments:
        grouped.setdefault(segment["label"], []).append(segment)
    if labels is None:
        labels = list(grouped.keys())

    rows = []
    for label in labels:
        items = grouped.get(label, [])
        unit = items[0]["unit"] if items else ""
        if not items:
            rows.append(
                {
                    "label": label,
                    "unit": unit,
                    "n": 0,
                    "median_ach": None,
                    "median_half_life_h": None,
                    "median_r2": None,
                    "band": "—",
                }
            )
            continue
        ach = np.array([item["ach"] for item in items], dtype=float)
        half = np.array([item["half_life_h"] for item in items], dtype=float)
        r2 = np.array([item["r2"] for item in items], dtype=float)
        median_ach = float(np.median(ach))
        rows.append(
            {
                "label": label,
                "unit": unit,
                "n": len(items),
                "median_ach": median_ach,
                "median_half_life_h": float(np.median(half)),
                "median_r2": float(np.median(r2)),
                "band": ach_band(median_ach),
            }
        )

    rows.sort(key=lambda row: (row["n"] == 0, -(row["median_ach"] or 0.0), row["label"]))
    return rows


def segments_frame(segments: list[dict]) -> pd.DataFrame:
    """Flat table for CSV export. Curve samples are omitted."""
    rows = []
    for segment in segments:
        rows.append(
            {
                "series": segment["label"],
                "unit": segment.get("unit") or "",
                "start": pd.Timestamp(segment["start"]).strftime("%Y-%m-%d %H:%M:%S"),
                "end": pd.Timestamp(segment["end"]).strftime("%Y-%m-%d %H:%M:%S"),
                "duration_h": round(float(segment["duration_h"]), 4),
                "n": segment["n"],
                "c_start": round(float(segment["c_start"]), 4),
                "c_end": round(float(segment["c_end"]), 4),
                "c_base": round(float(segment["c_base"]), 4),
                "ach": round(float(segment["ach"]), 4),
                "half_life_h": round(float(segment["half_life_h"]), 4),
                "r2": round(float(segment["r2"]), 4),
            }
        )
    return pd.DataFrame(rows)


def _dark(fig, height: int):
    fig.update_layout(
        template="plotly_dark",
        plot_bgcolor="rgba(15, 23, 42, 0.75)",
        paper_bgcolor="rgba(15, 23, 42, 0.0)",
        font=dict(color="#e2e8f0", size=13),
        title_font=dict(size=18, color="#e2e8f0"),
        height=height,
        margin=dict(l=60, r=30, t=60, b=50),
        legend=dict(bgcolor="rgba(15, 23, 42, 0.9)", font=dict(color="#e2e8f0")),
    )
    return fig


def build_summary_figure(summary: list[dict]):
    """Horizontal bars of median ACH. Empty when nothing was accepted."""
    import plotly.graph_objects as go

    rows = [row for row in summary if row["n"] and row["median_ach"] is not None]
    if not rows:
        return None
    # Plotly draws the first category at the bottom; reverse so the fastest
    # clearance (already sorted first) sits at the top.
    rows = list(reversed(rows))
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(rows))]
    fig = go.Figure(
        go.Bar(
            y=[row["label"] for row in rows],
            x=[row["median_ach"] for row in rows],
            orientation="h",
            marker_color=colors,
            text=[f"{row['median_ach']:.2f}" for row in rows],
            textposition="outside",
            cliponaxis=False,
            customdata=list(zip(
                [row["n"] for row in rows],
                [row["band"] for row in rows],
                [row["median_half_life_h"] for row in rows],
            )),
            hovertemplate=(
                "%{y}<br>median ACH %{x:.2f} /h"
                "<br>%{customdata[1]}"
                "<br>%{customdata[0]} decays"
                "<br>median half-life %{customdata[2]:.2f} h"
                "<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        title="Median clearance rate by series",
        xaxis_title="Median ACH (1/h)",
        yaxis_title="",
        bargap=0.35,
    )
    return _dark(fig, height=max(280, 70 + 42 * len(rows)))


def build_fit_figure(segments: list[dict], limit: int = 12):
    """One panel per series: the decay with the highest R² and its fit."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    best: dict[str, dict] = {}
    for segment in segments:
        current = best.get(segment["label"])
        if current is None or segment["r2"] > current["r2"]:
            best[segment["label"]] = segment
    chosen = sorted(best.values(), key=lambda item: (-item["r2"], item["label"]))[:limit]
    if not chosen:
        return None

    cols = 2 if len(chosen) > 1 else 1
    rows = math.ceil(len(chosen) / cols)
    titles = [
        f"{item['label']} · ACH {item['ach']:.2f} · R² {item['r2']:.2f}"
        for item in chosen
    ]
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles, vertical_spacing=0.12)
    for i, item in enumerate(chosen):
        r, c = divmod(i, cols)
        color = _PALETTE[i % len(_PALETTE)]
        fig.add_trace(
            go.Scatter(
                x=item["t_hours"],
                y=item["values"],
                mode="markers",
                name="Measured",
                legendgroup="measured",
                showlegend=(i == 0),
                marker=dict(size=6, color=color),
                hovertemplate="t %{x:.2f} h<br>%{y:.2f}<extra></extra>",
            ),
            row=r + 1,
            col=c + 1,
        )
        fig.add_trace(
            go.Scatter(
                x=item["t_hours"],
                y=item["predicted"],
                mode="lines",
                name="Exponential fit",
                legendgroup="fit",
                showlegend=(i == 0),
                line=dict(color="#e2e8f0", width=2),
                hovertemplate="fit %{y:.2f}<extra></extra>",
            ),
            row=r + 1,
            col=c + 1,
        )
        fig.update_xaxes(title_text="Hours from peak", row=r + 1, col=c + 1)
        unit = item.get("unit") or ""
        fig.update_yaxes(title_text=unit, row=r + 1, col=c + 1)
    fig.update_layout(title="Best exponential fit per series")
    return _dark(fig, height=max(360, 280 * rows))


def present_segment(segment: dict) -> dict:
    """Template-friendly copy without the curve arrays."""
    return {
        "label": segment["label"],
        "unit": segment.get("unit") or "",
        "start_display": pd.Timestamp(segment["start"]).strftime("%d %b %Y %H:%M"),
        "end_display": pd.Timestamp(segment["end"]).strftime("%d %b %Y %H:%M"),
        "duration_display": format_duration(segment["duration_h"]),
        "n": segment["n"],
        "c_start_display": f"{segment['c_start']:.1f}",
        "c_end_display": f"{segment['c_end']:.1f}",
        "c_base_display": f"{segment['c_base']:.1f}",
        "ach_display": f"{segment['ach']:.2f}",
        "half_life_display": format_half_life(segment["half_life_h"]),
        "r2_display": f"{segment['r2']:.2f}",
        "band": ach_band(segment["ach"]),
    }


def present_summary(row: dict) -> dict:
    if not row["n"]:
        return {
            "label": row["label"],
            "unit": row.get("unit") or "",
            "n": 0,
            "ach_display": "—",
            "half_life_display": "—",
            "r2_display": "—",
            "band": "—",
        }
    return {
        "label": row["label"],
        "unit": row.get("unit") or "",
        "n": row["n"],
        "ach_display": f"{row['median_ach']:.2f}",
        "half_life_display": format_half_life(row["median_half_life_h"]),
        "r2_display": f"{row['median_r2']:.2f}",
        "band": row["band"],
    }


def ventilation_view(request):
    """Compare clearance rates across rooms for the selected parameters."""
    import json

    from django.http import HttpResponse
    from django.shortcuts import render
    from django.utils import timezone

    from dashboard.chart_range import (
        active_quick_key,
        load_chart_range_mode,
        resolve_chart_range,
        save_chart_range_to_session,
    )
    from dashboard.correlations import make_series_label
    from dashboard.series_selection import expand_series_pairs, pairs_filter, parse_id_list
    from dashboard.views import (
        is_raw_interval,
        parse_interval_minutes,
        parse_spike_factor,
        resample_measurements,
    )
    from measurements.models import Measurement
    from parameters.models import Parameter
    from rooms.models import Room, RoomGroup

    rooms = list(Room.objects.all().order_by("order", "name"))
    parameters = list(Parameter.objects.all().order_by("order", "name"))
    groups = list(RoomGroup.objects.prefetch_related("rooms").order_by("name"))
    room_by_id = {room.id: room for room in rooms}
    parameter_by_id = {param.id: param for param in parameters}

    selected_room_ids = [rid for rid in parse_id_list(request.GET.getlist("room")) if rid in room_by_id]
    selected_parameter_ids = [
        pid for pid in parse_id_list(request.GET.getlist("parameter")) if pid in parameter_by_id
    ]
    has_query_selection = bool(selected_room_ids or selected_parameter_ids) or request.GET.get("selection") == "1"
    if has_query_selection:
        request.session["vent_rooms"] = selected_room_ids
        request.session["vent_parameters"] = selected_parameter_ids
    elif not request.GET:
        selected_room_ids = [
            rid for rid in parse_id_list(request.session.get("vent_rooms") or []) if rid in room_by_id
        ]
        selected_parameter_ids = [
            pid for pid in parse_id_list(request.session.get("vent_parameters") or []) if pid in parameter_by_id
        ]
        has_query_selection = False

    baseline_percentile = _finite(
        request.GET.get("baseline_pct", request.session.get("vent_baseline_pct", DEFAULT_BASELINE_PERCENTILE)),
        DEFAULT_BASELINE_PERCENTILE,
        1,
        40,
    )
    min_duration_min = _finite(
        request.GET.get("min_duration", request.session.get("vent_min_duration", DEFAULT_MIN_DURATION_MIN)),
        DEFAULT_MIN_DURATION_MIN,
        10,
        360,
    )
    min_r2 = _finite(
        request.GET.get("min_r2", request.session.get("vent_min_r2", DEFAULT_MIN_R2)),
        DEFAULT_MIN_R2,
        0.3,
        0.99,
    )
    raw_rise = request.GET.get("min_rise", None)
    if raw_rise is None and not has_query_selection:
        raw_rise = request.session.get("vent_min_rise")
    min_rise = None
    if raw_rise not in (None, ""):
        try:
            parsed_rise = float(raw_rise)
            if parsed_rise > 0 and math.isfinite(parsed_rise):
                min_rise = parsed_rise
        except (TypeError, ValueError):
            min_rise = None

    raw_max_baseline = request.GET.get("max_baseline", None)
    if raw_max_baseline is None and not has_query_selection:
        raw_max_baseline = request.session.get("vent_max_baseline")
    max_baseline = None
    if raw_max_baseline not in (None, ""):
        try:
            parsed_baseline = float(raw_max_baseline)
            if math.isfinite(parsed_baseline):
                max_baseline = parsed_baseline
        except (TypeError, ValueError):
            max_baseline = None

    if has_query_selection:
        request.session["vent_baseline_pct"] = baseline_percentile
        request.session["vent_min_duration"] = min_duration_min
        request.session["vent_min_r2"] = min_r2
        request.session["vent_min_rise"] = min_rise or ""
        request.session["vent_max_baseline"] = "" if max_baseline is None else max_baseline

    start_date, end_date, all_data = resolve_chart_range(request, default_hours=168)
    if start_date and end_date:
        save_chart_range_to_session(request, start_date, end_date)

    interval_raw = request.GET.get("interval", None)
    if interval_raw is None:
        interval_raw = request.session.get("resample_interval", 15)
    interval_minutes = parse_interval_minutes(interval_raw, default=15)
    fill_raw = request.GET.get("fill_method", None)
    if fill_raw is None:
        fill_raw = request.session.get("vent_fill_method", "none")
    fill_method = fill_raw if fill_raw in ("none", "ffill", "interpolate", "bfill", "zero") else "none"
    if "spike_factor" in request.GET:
        spike_factor = parse_spike_factor(request.GET.get("spike_factor"))
    else:
        spike_factor = parse_spike_factor(request.session.get("spike_factor"), default=0.0)

    if has_query_selection:
        request.session["resample_interval"] = interval_minutes
        request.session["vent_fill_method"] = fill_method
        request.session["spike_factor"] = spike_factor

    series_pairs = expand_series_pairs(selected_room_ids, selected_parameter_ids, [])
    pair_rooms = [room_by_id[rid] for rid, _pid in series_pairs]
    pair_parameters = [parameter_by_id[pid] for _rid, pid in series_pairs]
    unique_rooms = {room.id for room in pair_rooms}
    unique_parameters = {param.id for param in pair_parameters}
    include_room = len(unique_rooms) != 1
    include_parameter = len(unique_parameters) != 1

    compute_error = None
    submitted = has_query_selection and bool(request.GET)
    should_compute = submitted
    n_series = len(series_pairs)
    if should_compute and n_series > MAX_SERIES:
        compute_error = (
            f"That selection would build {n_series} series (limit is {MAX_SERIES}). "
            "Uncheck some rooms or parameters."
        )
        should_compute = False
    elif should_compute and n_series == 0:
        compute_error = "Select at least one room and one parameter."
        should_compute = False

    segments = []
    summary = []
    labels = []
    sample_count = 0
    bar_html = None
    fit_html = None

    if should_compute:
        qs = Measurement.objects.filter(pairs_filter(series_pairs)).select_related("sensor__room", "parameter")
        if not all_data and start_date:
            qs = qs.filter(timestamp__gte=start_date, timestamp__lte=end_date)
        if not qs.exists():
            compute_error = "No measurements in this period for the selected series."
        else:
            frame = pd.DataFrame(
                list(qs.order_by("timestamp").values(
                    "timestamp", "value", "sensor__room__name", "parameter__name", "parameter__unit"
                ))
            )
            frame["timestamp"] = pd.to_datetime(frame["timestamp"])
            labels_for_rows = [
                make_series_label(room, param, include_room=include_room, include_parameter=include_parameter)
                for room, param in zip(frame["sensor__room__name"], frame["parameter__name"])
            ]
            frame["parameter"] = labels_for_rows
            unit_by_label = {}
            for label, unit in zip(labels_for_rows, frame["parameter__unit"].tolist()):
                if not isinstance(unit, str):
                    unit = ""
                unit_by_label.setdefault(label, unit)

            resampled = resample_measurements(
                frame, interval_minutes, fill_method, spike_factor=spike_factor
            )
            if resampled is None or resampled.empty:
                compute_error = "No recorded samples in this period."
            else:
                sample_count = int(len(resampled))
                for column in resampled.columns:
                    label = str(column)
                    labels.append(label)
                    series = resampled[column].dropna()
                    segments.extend(
                        analyze_series(
                            series,
                            label=label,
                            unit=unit_by_label.get(label, ""),
                            baseline_percentile=baseline_percentile,
                            min_rise=min_rise,
                            min_duration_min=min_duration_min,
                            min_r2=min_r2,
                            max_baseline=max_baseline,
                        )
                    )
                summary = summarize_decays(segments, labels)
                if not segments and compute_error is None:
                    compute_error = (
                        "No decay segments matched the filters. "
                        "Try a longer period, a lower minimum R², or a shorter minimum duration."
                    )

                if request.GET.get("format") == "csv" and segments:
                    response = HttpResponse(content_type="text/csv")
                    filename = f"ventilation_ach_{interval_minutes}min.csv"
                    response["Content-Disposition"] = f'attachment; filename="{filename}"'
                    segments_frame(segments).to_csv(response, index=False)
                    return response

                bar = build_summary_figure(summary)
                fits = build_fit_figure(segments)
                if bar is not None:
                    bar_html = bar.to_html(full_html=False, include_plotlyjs="cdn")
                if fits is not None:
                    fit_html = fits.to_html(full_html=False, include_plotlyjs=False if bar_html else "cdn")

    period_label = "all data" if all_data else None
    if not period_label and start_date and end_date:
        period_label = (
            f"{timezone.localtime(start_date).strftime('%d %b %Y %H:%M')} – "
            f"{timezone.localtime(end_date).strftime('%d %b %Y %H:%M')}"
        )

    groups_payload = [
        {"id": group.id, "name": group.name, "room_ids": [room.id for room in group.rooms.all()]}
        for group in groups
    ]
    shown_segments = [present_segment(segment) for segment in segments[:300]]

    context = {
        "rooms": rooms,
        "parameters": parameters,
        "groups": groups,
        "groups_json": json.dumps(groups_payload),
        "selected_room_ids": set(selected_room_ids),
        "selected_parameter_ids": set(selected_parameter_ids),
        "start": start_date,
        "end": end_date,
        "all_data": all_data,
        "interval": interval_minutes,
        "fill_method": fill_method,
        "spike_factor": spike_factor,
        "baseline_pct": int(round(baseline_percentile)),
        "min_duration": int(round(min_duration_min)),
        "min_r2": f"{min_r2:.2f}",
        "min_rise": "" if min_rise is None else min_rise,
        "max_baseline": "" if max_baseline is None else max_baseline,
        "compute_error": None if segments else compute_error,
        "submitted": submitted,
        "summary": [present_summary(row) for row in summary],
        "segments": shown_segments,
        "segment_count": len(segments),
        "segments_truncated": len(segments) > 300,
        "series_count": n_series,
        "sample_count": sample_count,
        "sample_label": "timestamps" if is_raw_interval(interval_minutes) else "bins",
        "period_label": period_label,
        "bar_fig": bar_html,
        "fit_fig": fit_html,
        "max_series": MAX_SERIES,
        "active_quick": active_quick_key(
            start_date, end_date, all_data, live=load_chart_range_mode(request) == "live"
        ),
    }
    return render(request, "dashboard/ventilation.html", context)
