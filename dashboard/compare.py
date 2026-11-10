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
from django.shortcuts import redirect, render
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
    """Per-column min / max / mean / std / last / n from a raw (unnormalized) frame.

    std is the sample standard deviation (n − 1). A single sample has no std.
    """
    rows = []
    if frame is None or frame.empty:
        return rows
    for col in frame.columns:
        series = pd.to_numeric(frame[col], errors="coerce")
        valid = series.dropna()
        last = valid.iloc[-1] if not valid.empty else None
        std = None
        if valid.shape[0] >= 2:
            std = float(valid.std(ddof=1))
        rows.append(
            {
                "label": str(col),
                "n": int(valid.shape[0]),
                "min": None if valid.empty else float(valid.min()),
                "max": None if valid.empty else float(valid.max()),
                "mean": None if valid.empty else float(valid.mean()),
                "std": std,
                "last": None if last is None else float(last),
            }
        )
    return rows


def recorded_averages(frame, key_column="parameter", value_column="value") -> dict:
    """Mean of recorded measurements for each series, before resampling and fill.

    Duplicate timestamps both count. Forward-filled gaps do not.
    """
    if frame is None or getattr(frame, "empty", True):
        return {}
    if key_column not in frame.columns or value_column not in frame.columns:
        return {}
    values = pd.to_numeric(frame[value_column], errors="coerce")
    grouped = values.groupby(frame[key_column]).mean()
    return {
        str(key): float(value)
        for key, value in grouped.items()
        if pd.notna(value)
    }


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


def legend_row_count(labels, chars_per_row=64) -> int:
    """Estimate how many rows a horizontal legend needs.

    Long room/parameter names usually take a whole row, so the title needs
    margin of its own or it is drawn on top of the first legend item.
    """
    if labels is None:
        labels = []
    else:
        labels = list(labels)
    if not labels:
        return 1
    rows = 1
    used = 0
    for label in labels:
        need = min(len(str(label)) + 6, chars_per_row)
        if used and used + need > chars_per_row:
            rows += 1
            used = need
        else:
            used += need
    return rows


def compare_chrome(labels) -> tuple[int, int]:
    """Top margin and figure height that keep the title above the legend."""
    rows = legend_row_count(labels)
    top = 72 + 36 * rows
    height = 540 + top
    return top, height


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
    individual_timeline=False,
    dash_labels=None,
):
    """Dark overlay of selected series. Hover always includes the raw value.

    individual_timeline plots each series on its own timestamps (no shared
    bin index), which is what "all recorded values" needs.
    """
    if plotted is None or plotted.empty:
        return go.Figure()

    mode = parse_normalize(mode)
    units_by_label = units_by_label or {str(col): "" for col in plotted.columns}
    axis_by_label, axis_titles, dual = unit_axis_map(units_by_label, mode)
    raw_frame = raw if raw is not None else plotted
    dashed = set(dash_labels or ())

    unique_units = []
    for unit in units_by_label.values():
        key = unit or ""
        if key not in unique_units:
            unique_units.append(key)
    primary_unit = unique_units[0] if unique_units else ""

    fig = go.Figure()
    for index, col in enumerate(plotted.columns):
        label = str(col)
        raw_col = raw_frame[col] if col in getattr(raw_frame, "columns", []) else plotted[col]
        series_y = plotted[col]
        if individual_timeline:
            series_y = series_y.dropna()
            if hasattr(raw_col, "reindex"):
                raw_col = raw_col.reindex(series_y.index)
            x_values = list(series_y.index)
        else:
            x_values = list(plotted.index)
        unit = units_by_label.get(label, "") or ""
        color = SERIES_COLORS[index % len(SERIES_COLORS)]
        custom = [[_fmt_hover_raw(value), unit] for value in raw_col]
        fig.add_trace(
            go.Scattergl(
                x=x_values,
                y=[_as_float(value) for value in series_y],
                name=label,
                mode="lines",
                line=dict(width=2.4, color=color, dash="dash" if label in dashed else "solid"),
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

    top, height = compare_chrome(plotted.columns)
    fig.update_layout(
        title=dict(
            text=title,
            x=0,
            xanchor="left",
            xref="container",
            y=1,
            yanchor="top",
            yref="container",
            pad=dict(t=8, l=4, b=0),
        ),
        height=height,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, xanchor="left"),
        margin=dict(l=60, r=70 if dual else 40, t=top, b=50),
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

    from dashboard.chart_range import active_quick_key, resolve_chart_range, save_chart_range_to_session
    from dashboard.chart_templates import (
        active_template_name,
        clean_template_name,
        delete_named_template,
        find_named_template,
        list_templates,
        remember_active_template,
        save_named_template,
        url_for_selection,
        url_without,
    )
    from dashboard.series_selection import (
        encode_diff_entry,
        encode_series_entry,
        expand_series_pairs,
        pair_key,
        pairs_filter,
        parse_difference_entries,
        parse_id_list,
        parse_series_entries,
        subtract_aligned,
    )
    from dashboard.views import (
        apply_dark_theme,
        is_raw_interval,
        parse_interval_minutes,
        parse_spike_factor,
        resample_measurements,
    )
    from measurements.models import Measurement
    from parameters.models import Parameter
    from rooms.models import Event, Room, RoomGroup

    rooms = list(Room.objects.all().order_by("order", "name"))
    parameters = list(Parameter.objects.all().order_by("order", "name"))
    groups = list(RoomGroup.objects.prefetch_related("rooms").order_by("name"))
    valid_room_ids = {room.id for room in rooms}
    valid_parameter_ids = {param.id for param in parameters}

    TEMPLATE_KEY = "compare_templates"
    ACTIVE_TEMPLATE_KEY = "compare_active_template"
    KEEP_WITH_TEMPLATE = (
        "start",
        "end",
        "all",
        "interval",
        "fill_method",
        "spike_factor",
        "normalize",
    )

    selected_room_ids = parse_id_list(request.GET.getlist("room"))
    selected_parameter_ids = parse_id_list(request.GET.getlist("parameter"))
    series_entries = parse_series_entries(
        request.GET.getlist("series"), valid_room_ids, valid_parameter_ids
    )
    diff_entries = parse_difference_entries(
        request.GET.getlist("diff"), valid_room_ids, valid_parameter_ids
    )

    save_name = clean_template_name(request.GET.get("template_name"))
    delete_name = clean_template_name(request.GET.get("template_delete"))
    load_name = clean_template_name(request.GET.get("load_template"))
    if request.GET.get("template_action") == "save":
        if save_name:
            save_named_template(
                request.session,
                TEMPLATE_KEY,
                save_name,
                {
                    "rooms": selected_room_ids,
                    "parameters": selected_parameter_ids,
                    "series": [encode_series_entry(pair, enabled) for pair, enabled in series_entries],
                    "diffs": [encode_diff_entry(spec, enabled) for spec, enabled in diff_entries],
                },
            )
            remember_active_template(request.session, ACTIVE_TEMPLATE_KEY, save_name)
        return redirect(url_without(request))
    if delete_name:
        delete_named_template(request.session, TEMPLATE_KEY, delete_name)
        if clean_template_name(request.session.get(ACTIVE_TEMPLATE_KEY)) == delete_name:
            remember_active_template(request.session, ACTIVE_TEMPLATE_KEY, "")
        return redirect(url_without(request))
    if load_name:
        stored = find_named_template(request.session, TEMPLATE_KEY, load_name)
        if not stored:
            return redirect(url_without(request))
        remember_active_template(request.session, ACTIVE_TEMPLATE_KEY, load_name)
        selected_room_ids = parse_id_list(stored.get("rooms") or [])
        selected_parameter_ids = parse_id_list(stored.get("parameters") or [])
        series_entries = parse_series_entries(
            stored.get("series") or [], valid_room_ids, valid_parameter_ids
        )
        diff_entries = parse_difference_entries(
            stored.get("diffs") or [], valid_room_ids, valid_parameter_ids
        )
        return redirect(
            url_for_selection(
                request,
                selected_room_ids,
                selected_parameter_ids,
                [encode_series_entry(pair, enabled) for pair, enabled in series_entries],
                [encode_diff_entry(spec, enabled) for spec, enabled in diff_entries],
                KEEP_WITH_TEMPLATE,
            )
        )

    enabled_explicit = [pair for pair, enabled in series_entries if enabled]
    enabled_diffs = [spec for spec, enabled in diff_entries if enabled]

    has_query_selection = bool(
        selected_room_ids or selected_parameter_ids or series_entries or diff_entries
    )
    explicit_form = has_query_selection or request.GET.get("selection") == "1"
    if explicit_form:
        request.session["compare_rooms"] = selected_room_ids
        request.session["compare_parameters"] = selected_parameter_ids
        request.session["compare_series"] = [
            encode_series_entry(pair, enabled) for pair, enabled in series_entries
        ]
        request.session["compare_diffs"] = [
            encode_diff_entry(spec, enabled) for spec, enabled in diff_entries
        ]
    elif not request.GET:
        selected_room_ids = parse_id_list(request.session.get("compare_rooms") or [])
        selected_parameter_ids = parse_id_list(request.session.get("compare_parameters") or [])
        series_entries = parse_series_entries(
            request.session.get("compare_series") or [],
            valid_room_ids,
            valid_parameter_ids,
        )
        diff_entries = parse_difference_entries(
            request.session.get("compare_diffs") or [],
            valid_room_ids,
            valid_parameter_ids,
        )
        enabled_explicit = [pair for pair, enabled in series_entries if enabled]
        enabled_diffs = [spec for spec, enabled in diff_entries if enabled]

    if "normalize" in request.GET:
        normalize = parse_normalize(request.GET.get("normalize"))
        request.session["compare_normalize"] = normalize
    else:
        normalize = parse_normalize(request.session.get("compare_normalize"))

    start_date, end_date, all_data = resolve_chart_range(request, default_hours=168)
    if start_date and end_date:
        save_chart_range_to_session(request, start_date, end_date)

    interval_raw = request.GET.get("interval", None)
    if interval_raw is None:
        interval_raw = request.session.get("resample_interval", 15)
    interval_minutes = parse_interval_minutes(interval_raw, default=15)
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

    room_by_id = {room.id: room for room in rooms}
    parameter_by_id = {param.id: param for param in parameters}
    series_pairs = expand_series_pairs(
        selected_room_ids, selected_parameter_ids, enabled_explicit
    )
    pair_room_ids = {room_id for room_id, _param_id in series_pairs}
    pair_parameter_ids = {param_id for _room_id, param_id in series_pairs}
    pair_rooms = [room for room in rooms if room.id in pair_room_ids]
    pair_parameters = [param for param in parameters if param.id in pair_parameter_ids]
    include_room = len(pair_rooms) != 1
    include_parameter = len(pair_parameters) != 1
    difference_specs = enabled_diffs
    n_series_selected = len(series_pairs) + len(difference_specs)
    selected_series = [
        {
            "room_id": room_id,
            "parameter_id": param_id,
            "room_name": room_by_id[room_id].name,
            "parameter_name": parameter_by_id[param_id].name,
            "unit": parameter_by_id[param_id].unit or "",
            "key": pair_key(room_id, param_id),
            "enabled": enabled,
        }
        for (room_id, param_id), enabled in series_entries
    ]

    def _pair_title(pair):
        room = room_by_id[pair[0]]
        param = parameter_by_id[pair[1]]
        unit = f" ({param.unit})" if param.unit else ""
        return f"{room.name} · {param.name}{unit}"

    selected_diffs = [
        {
            "key": f"{pair_key(*left)}|{pair_key(*right)}",
            "left": _pair_title(left),
            "right": _pair_title(right),
            "enabled": enabled,
        }
        for (left, right), enabled in diff_entries
    ]
    query_pairs = list(series_pairs)
    seen_pairs = set(series_pairs)
    event_room_ids = set(pair_room_ids)
    for left, right in difference_specs:
        event_room_ids.add(left[0])
        event_room_ids.add(right[0])
        for pair in (left, right):
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                query_pairs.append(pair)

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
            f"(limit is {MAX_SERIES}). Uncheck some rooms or parameters, "
            "or add only the individual series you need."
        )
        should_compute = False
    elif has_query_selection and n_series_selected == 0 and (series_entries or diff_entries):
        compute_error = "Every individual series is disabled."
    elif has_query_selection and n_series_selected == 0:
        compute_error = "Select at least one room and one parameter, or add an individual series."

    resampled = None
    units_by_label = {}
    sample_average_by_label = {}
    dash_labels = set()

    if should_compute:
        query = Measurement.objects.filter(pairs_filter(query_pairs)).select_related(
            "sensor__room", "parameter"
        )
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
                        "sensor__room_id",
                        "parameter_id",
                        "parameter__unit",
                    )
                )
            )
            frame["timestamp"] = pd.to_datetime(frame["timestamp"])
            frame["parameter"] = [
                pair_key(room_id, param_id)
                for room_id, param_id in zip(frame["sensor__room_id"], frame["parameter_id"])
            ]
            recorded_mean = recorded_averages(frame)

            resampled = resample_measurements(
                frame, interval_minutes, fill_method, spike_factor=spike_factor
            )
            if resampled is None or resampled.empty:
                compute_error = (
                    "No recorded samples in this period."
                    if is_raw_interval(interval_minutes)
                    else "No samples after resampling."
                )
            else:
                keyed = resampled.dropna(axis=1, how="all")
                display = pd.DataFrame(index=keyed.index)
                used_labels = set()
                for room_id, param_id in series_pairs:
                    key = pair_key(room_id, param_id)
                    if key not in keyed.columns:
                        continue
                    label = make_series_label(
                        room_by_id[room_id].name,
                        parameter_by_id[param_id].name,
                        include_room=include_room,
                        include_parameter=include_parameter,
                    )
                    if label in used_labels:
                        label = f"{label} ({key})"
                    used_labels.add(label)
                    display[label] = keyed[key]
                    units_by_label[label] = parameter_by_id[param_id].unit or ""
                    if key in recorded_mean:
                        sample_average_by_label[label] = recorded_mean[key]

                raw_mode = is_raw_interval(interval_minutes)
                missing_diffs = []
                for left, right in difference_specs:
                    left_key = pair_key(*left)
                    right_key = pair_key(*right)
                    left_name = f"{room_by_id[left[0]].name} · {parameter_by_id[left[1]].name}"
                    right_name = f"{room_by_id[right[0]].name} · {parameter_by_id[right[1]].name}"
                    label = f"Δ {left_name} − {right_name}"
                    if left_key not in keyed.columns or right_key not in keyed.columns:
                        missing_diffs.append(label)
                        continue
                    series = subtract_aligned(
                        keyed[left_key],
                        keyed[right_key],
                        tolerance="2min" if raw_mode else None,
                    )
                    if series.dropna().empty:
                        missing_diffs.append(label)
                        continue
                    if label in used_labels:
                        label = f"{label} ({left_key}|{right_key})"
                    used_labels.add(label)
                    display = display.reindex(display.index.union(series.index))
                    display[label] = series.reindex(display.index)
                    unit_left = parameter_by_id[left[1]].unit or ""
                    unit_right = parameter_by_id[right[1]].unit or ""
                    units_by_label[label] = unit_left if unit_left and unit_left == unit_right else "Δ"
                    dash_labels.add(label)
                    diff_mean = series.dropna().mean()
                    if pd.notna(diff_mean):
                        sample_average_by_label[label] = float(diff_mean)

                display = display.dropna(axis=1, how="all")
                if missing_diffs:
                    note = "No overlapping samples for " + "; ".join(missing_diffs) + "."
                    unit_notice = f"{unit_notice} {note}".strip() if unit_notice else note
                resampled = display
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
            mixed = (
                "Three or more units share one axis. Switch to Min-max or "
                "Z-score to compare shapes across different scales."
            )
            unit_notice = f"{unit_notice} {mixed}".strip() if unit_notice else mixed
        _, _, dual_axis = unit_axis_map(
            {str(col): units_by_label.get(str(col), "") for col in resampled.columns},
            normalize,
        )
        stats_rows = series_stats(resampled)
        for row in stats_rows:
            row["unit"] = units_by_label.get(row["label"], "") or ""
            row["average"] = sample_average_by_label.get(row["label"], row["mean"])

        if request.GET.get("format") == "csv":
            response = HttpResponse(content_type="text/csv")
            filename = f"compare_{normalize}_{'all_points' if is_raw_interval(interval_minutes) else str(interval_minutes) + 'min'}.csv"
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            export = resampled.copy()
            export.index.name = "timestamp"
            export.to_csv(response, float_format="%.4f")
            return response

        events_payload = []
        if event_room_ids and start_date and end_date:
            event_qs = (
                Event.objects.filter(
                    rooms__in=event_room_ids,
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

        raw_mode = is_raw_interval(interval_minutes)
        if raw_mode:
            plotted = normalize_frame(resampled, normalize)
            raw_plot = resampled
            sample_count = int(resampled.notna().sum().sum())
        else:
            plotted = downsample_frame(normalize_frame(resampled, normalize))
            raw_plot = downsample_frame(resampled)

        if include_room and include_parameter:
            title = "Compare series"
        elif include_room and pair_parameters:
            title = f"Compare rooms — {pair_parameters[0].name}"
        elif include_parameter and pair_rooms:
            title = f"Compare parameters — {pair_rooms[0].name}"
        else:
            title = "Compare series"
        if raw_mode:
            title = f"{title} (all recorded values)"

        fig = build_compare_figure(
            plotted,
            raw=raw_plot,
            units_by_label=units_by_label,
            mode=normalize,
            events=events_payload,
            title=title,
            individual_timeline=raw_mode,
            dash_labels=dash_labels,
        )
        fig = apply_dark_theme(fig, animate=False)
        # Theme helper is tuned for single-series room charts: restore overlay
        # line width and leave room for a second Y-axis / event labels.
        for trace in fig.data:
            if getattr(trace, "type", None) in ("scatter", "scattergl"):
                color = None
                dash = None
                if getattr(trace, "line", None) is not None:
                    color = getattr(trace.line, "color", None)
                    dash = getattr(trace.line, "dash", None)
                line = {"width": 2.4}
                if color:
                    line["color"] = color
                if dash and dash != "solid":
                    line["dash"] = dash
                trace.update(line=line)
        top, height = compare_chrome([getattr(trace, "name", "") for trace in fig.data])
        fig.update_layout(
            height=height,
            margin=dict(l=60, r=70 if dual_axis else 40, t=top, b=50),
            title=dict(
                x=0,
                xanchor="left",
                xref="container",
                y=1,
                yanchor="top",
                yref="container",
                pad=dict(t=8, l=4, b=0),
            ),
        )
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
        "selected_series": selected_series,
        "selected_diffs": selected_diffs,
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
        "sample_label": "points" if is_raw_interval(interval_minutes) else "bins",
        "event_count": event_count,
        "dual_axis": dual_axis,
        "period_label": period_label,
        "n_series_selected": n_series_selected,
        "max_series": MAX_SERIES,
        "templates": list_templates(request.session, TEMPLATE_KEY),
        "active_template": active_template_name(request, ACTIVE_TEMPLATE_KEY),
        "active_quick": active_quick_key(start_date, end_date, all_data),
    }
    return render(request, "dashboard/compare.html", context)
