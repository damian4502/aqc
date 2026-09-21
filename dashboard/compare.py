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
    return single_unit or "Value"


def _as_float(value):
    """JSON/Plotly-safe float. NaN and None become None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or np.isinf(number):
        return None
    return number


def _fmt_hover_raw(value) -> str:
    """Compact raw-value label for hover customdata."""
    number = _as_float(value)
    if number is None:
        return "—"
    if abs(number) >= 100:
        return f"{number:.1f}"
    return f"{number:.3f}"


def build_compare_figure(
    plotted,
    raw=None,
    units_by_label=None,
    mode="minmax",
    events=None,
    title="Compare series",
):
    """Dark overlay of selected series. Hover always includes the raw value."""
    if plotted is None or plotted.empty:
        return go.Figure()

    mode = parse_normalize(mode)
    units_by_label = units_by_label or {str(col): "" for col in plotted.columns}
    axis_by_label, axis_titles, dual = unit_axis_map(units_by_label, mode)
    raw_frame = raw if raw is not None else plotted

    unique_units = []
    for unit in units_by_label.values():
        key = unit or ""
        if key not in unique_units:
            unique_units.append(key)
    primary_unit = unique_units[0] if unique_units else ""

    fig = go.Figure()
    x_values = list(plotted.index)
    for index, col in enumerate(plotted.columns):
        label = str(col)
        raw_col = raw_frame[col] if col in getattr(raw_frame, "columns", []) else plotted[col]
        unit = units_by_label.get(label, "") or ""
        color = SERIES_COLORS[index % len(SERIES_COLORS)]
        custom = [[_fmt_hover_raw(value), unit] for value in raw_col]
        fig.add_trace(
            go.Scattergl(
                x=x_values,
                y=[_as_float(value) for value in plotted[col]],
                name=label,
                mode="lines",
                line=dict(width=2.4, color=color),
                yaxis=axis_by_label.get(label, "y"),
                customdata=custom,
                hovertemplate=(
                    "%{fullData.name}<br>"
                    "%{x|%d %b %Y %H:%M}<br>"
                    "Plotted: %{y:.3f}<br>"
                    "Raw: %{customdata[0]} %{customdata[1]}<extra></extra>"
                ),
            )
        )

    y_title = axis_titles.get("y") if dual else _yaxis_title(mode, dual, primary_unit)
    layout_axes = dict(
        xaxis=dict(title="Time", gridcolor="rgba(148,163,184,0.15)", zeroline=False),
        yaxis=dict(
            title=y_title or "Value",
            gridcolor="rgba(148,163,184,0.15)",
            zeroline=False,
            side="left",
        ),
    )
    if dual:
        layout_axes["yaxis2"] = dict(
            title=axis_titles.get("y2") or "Value",
            overlaying="y",
            side="right",
            gridcolor="rgba(148,163,184,0.08)",
            zeroline=False,
        )

    fig.update_layout(
        title=title,
        height=650,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=60, r=70 if dual else 40, t=90, b=50),
        **layout_axes,
    )

    for event in (events or [])[:MAX_EVENTS]:
        ts = event.get("timestamp")
        if ts is None:
            continue
        color = event.get("color") or "#10b981"
        title_text = event.get("title") or "Event"
        fig.add_vline(x=ts, line_width=2.0, line_dash="dashdot", line_color=color)
        fig.add_annotation(
            x=ts,
            yref="paper",
            y=1.06,
            text=title_text,
            showarrow=False,
            xanchor="center",
            yanchor="bottom",
            font=dict(size=12, color=color),
            bgcolor="rgba(15, 23, 42, 0.92)",
            bordercolor=color,
            borderwidth=1,
            borderpad=4,
        )
    return fig


def compare_view(request):
    """Plot any room × parameter combination on a single overlay chart."""
    import json

    from dashboard.chart_range import resolve_chart_range, save_chart_range_to_session
    from dashboard.views import apply_dark_theme, parse_spike_factor, resample_measurements
    from measurements.models import Measurement
    from parameters.models import Parameter
    from rooms.models import Event, Room, RoomGroup

    rooms = list(Room.objects.all().order_by("order", "name"))
    parameters = list(Parameter.objects.all().order_by("order", "name"))
    groups = list(RoomGroup.objects.prefetch_related("rooms").order_by("name"))

    selected_room_ids = []
    for raw_id in request.GET.getlist("room"):
        try:
            selected_room_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    selected_parameter_ids = []
    for raw_id in request.GET.getlist("parameter"):
        try:
            selected_parameter_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    has_query_selection = bool(
        request.GET.getlist("room") or request.GET.getlist("parameter")
    )
    if has_query_selection:
        request.session["compare_rooms"] = selected_room_ids
        request.session["compare_parameters"] = selected_parameter_ids
    elif not request.GET:
        selected_room_ids = list(request.session.get("compare_rooms") or [])
        selected_parameter_ids = list(request.session.get("compare_parameters") or [])

    if "normalize" in request.GET:
        normalize = parse_normalize(request.GET.get("normalize"))
        request.session["compare_normalize"] = normalize
    else:
        normalize = parse_normalize(request.session.get("compare_normalize"))

    start_date, end_date, all_data = resolve_chart_range(request, default_hours=168)
    if start_date and end_date:
        save_chart_range_to_session(request, start_date, end_date)

    try:
        interval_minutes = int(
            request.GET.get("interval", request.session.get("resample_interval", 15)) or 15
        )
    except (TypeError, ValueError):
        interval_minutes = 15
    fill_method = request.GET.get(
        "fill_method", request.session.get("resample_fill_method", "ffill")
    )
    if "spike_factor" in request.GET:
        spike_factor = parse_spike_factor(request.GET.get("spike_factor"))
    elif "ignore_spikes" in request.GET:
        spike_factor = parse_spike_factor(request.GET.get("ignore_spikes"), default=0.0)
    else:
        spike_factor = parse_spike_factor(request.session.get("spike_factor"), default=0.0)
        if spike_factor == 0.0 and request.session.get("ignore_spikes"):
            spike_factor = 2.5

    request.session["resample_interval"] = interval_minutes
    request.session["resample_fill_method"] = fill_method
    request.session["spike_factor"] = spike_factor

    selected_rooms = [room for room in rooms if room.id in selected_room_ids]
    selected_parameters = [param for param in parameters if param.id in selected_parameter_ids]
    include_room = len(selected_rooms) != 1
    include_parameter = len(selected_parameters) != 1
    n_series_selected = len(selected_rooms) * len(selected_parameters)

    compute_error = None
    unit_notice = None
    fig_html = None
    stats_rows = []
    series_count = 0
    sample_count = 0
    event_count = 0
    dual_axis = False
    should_compute = has_query_selection and n_series_selected >= 1

    if should_compute and n_series_selected > MAX_SERIES:
        compute_error = (
            f"That selection would build {n_series_selected} series "
            f"(limit is {MAX_SERIES}). Narrow the rooms or parameters."
        )
        should_compute = False
    elif has_query_selection and n_series_selected == 0:
        compute_error = "Select at least one room and one parameter."

    resampled = None
    units_by_label = {}

    if should_compute:
        query = Measurement.objects.filter(
            sensor__room_id__in=selected_room_ids,
            parameter_id__in=selected_parameter_ids,
        ).select_related("sensor__room", "parameter")
        if start_date and end_date:
            query = query.filter(timestamp__gte=start_date, timestamp__lte=end_date)

        measurements = query.order_by("timestamp")
        if not measurements.exists():
            compute_error = "No measurements in this period for the selected series."
        else:
            frame = pd.DataFrame(
                list(
                    measurements.values(
                        "timestamp",
                        "value",
                        "sensor__room__name",
                        "parameter__name",
                        "parameter__unit",
                    )
                )
            )
            frame["timestamp"] = pd.to_datetime(frame["timestamp"])
            labels = [
                make_series_label(
                    room_name,
                    param_name,
                    include_room=include_room,
                    include_parameter=include_parameter,
                )
                for room_name, param_name in zip(
                    frame["sensor__room__name"], frame["parameter__name"]
                )
            ]
            frame["parameter"] = labels
            for label, unit in zip(labels, frame["parameter__unit"]):
                units_by_label[label] = unit or ""

            resampled = resample_measurements(
                frame, interval_minutes, fill_method, spike_factor=spike_factor
            )
            if resampled is None or resampled.empty:
                compute_error = "No samples after resampling."
            else:
                resampled = resampled.dropna(axis=1, how="all")
                series_count = len(resampled.columns)
                sample_count = int(len(resampled))
                if series_count < 1:
                    compute_error = "No series have data in this period."

    if resampled is not None and not resampled.empty and compute_error is None:
        unique_units = []
        for col in resampled.columns:
            unit = units_by_label.get(str(col), "") or ""
            if unit not in unique_units:
                unique_units.append(unit)
        if normalize == "off" and len(unique_units) >= 3:
            unit_notice = (
                "Three or more units share one axis. Switch to Min-max or "
                "Z-score to compare shapes across different scales."
            )
        _, _, dual_axis = unit_axis_map(
            {str(col): units_by_label.get(str(col), "") for col in resampled.columns},
            normalize,
        )
        stats_rows = series_stats(resampled)
        for row in stats_rows:
            row["unit"] = units_by_label.get(row["label"], "") or ""

        if request.GET.get("format") == "csv":
            response = HttpResponse(content_type="text/csv")
            filename = f"compare_{normalize}_{interval_minutes}min.csv"
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            export = resampled.copy()
            export.index.name = "timestamp"
            export.to_csv(response, float_format="%.4f")
            return response

        events_payload = []
        if selected_room_ids and start_date and end_date:
            event_qs = (
                Event.objects.filter(
                    rooms__in=selected_room_ids,
                    timestamp__gte=start_date,
                    timestamp__lte=end_date,
                )
                .distinct()
                .order_by("timestamp")
            )
            for event in event_qs[:MAX_EVENTS]:
                event_ts = event.timestamp
                if timezone.is_aware(event_ts):
                    event_ts = timezone.localtime(event_ts).replace(tzinfo=None)
                events_payload.append(
                    {
                        "timestamp": event_ts,
                        "title": event.title,
                        "color": event.color or "#10b981",
                    }
                )
            event_count = len(events_payload)

        plotted = downsample_frame(normalize_frame(resampled, normalize))
        raw_plot = downsample_frame(resampled)

        if include_room and include_parameter:
            title = "Compare series"
        elif include_room and selected_parameters:
            title = f"Compare rooms — {selected_parameters[0].name}"
        elif include_parameter and selected_rooms:
            title = f"Compare parameters — {selected_rooms[0].name}"
        else:
            title = "Compare series"

        fig = build_compare_figure(
            plotted,
            raw=raw_plot,
            units_by_label=units_by_label,
            mode=normalize,
            events=events_payload,
            title=title,
        )
        fig = apply_dark_theme(fig, animate=False)
        # Theme helper is tuned for single-series room charts: restore overlay
        # line width and leave room for a second Y-axis / event labels.
        for trace in fig.data:
            if getattr(trace, "type", None) in ("scatter", "scattergl"):
                color = None
                if getattr(trace, "line", None) is not None:
                    color = getattr(trace.line, "color", None)
                trace.update(line=dict(width=2.4, color=color) if color else dict(width=2.4))
        fig.update_layout(margin=dict(l=60, r=70 if dual_axis else 40, t=90, b=50))
        fig_html = fig.to_html(full_html=False, include_plotlyjs="cdn")

    period_label = "all data" if all_data else None
    if not period_label and start_date and end_date:
        period_label = (
            f"{timezone.localtime(start_date).strftime('%d %b %Y %H:%M')} – "
            f"{timezone.localtime(end_date).strftime('%d %b %Y %H:%M')}"
        )

    groups_payload = [
        {
            "id": group.id,
            "name": group.name,
            "room_ids": [room.id for room in group.rooms.all()],
        }
        for group in groups
    ]

    context = {
        "rooms": rooms,
        "parameters": parameters,
        "groups": groups,
        "groups_json": json.dumps(groups_payload),
        "selected_room_ids": set(selected_room_ids),
        "selected_parameter_ids": set(selected_parameter_ids),
        "normalize": normalize,
        "start": start_date,
        "end": end_date,
        "all_data": all_data,
        "interval": interval_minutes,
        "fill_method": fill_method,
        "spike_factor": spike_factor,
        "fig": fig_html,
        "stats_rows": stats_rows,
        "compute_error": compute_error,
        "unit_notice": unit_notice,
        "has_query_selection": has_query_selection,
        "series_count": series_count,
        "sample_count": sample_count,
        "event_count": event_count,
        "dual_axis": dual_axis,
        "period_label": period_label,
        "n_series_selected": n_series_selected,
        "max_series": MAX_SERIES,
    }
    return render(request, "dashboard/compare.html", context)
