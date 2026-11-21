"""Optional chart colors from Room and Parameter.

An empty color keeps whatever the chart already does. A set color is used
for that series: the room color when the chart compares rooms, the parameter
color when it compares parameters. If only one of the two is set, that one
is used.
"""

from __future__ import annotations

import re

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_color(value) -> str:
    """``#rrggbb`` or an empty string when the value is blank or invalid."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("#"):
        text = "#" + text
    if not _HEX.match(text):
        return ""
    if len(text) == 4:
        text = "#" + "".join(ch * 2 for ch in text[1:])
    return text.lower()


def pick_series_color(room_color, parameter_color, *, prefer="room") -> str:
    """Color for one room/parameter trace. Empty means keep the chart default."""
    room = normalize_color(room_color)
    parameter = normalize_color(parameter_color)
    if prefer == "parameter":
        return parameter or room
    return room or parameter


def paint_figure(fig, color_by_name):
    """Set line/marker colors for traces whose name is in ``color_by_name``.

    Names that are missing or blank are left alone, so automatic colors stay.
    """
    if fig is None or not color_by_name:
        return fig
    for trace in getattr(fig, "data", []) or []:
        name = str(getattr(trace, "name", "") or "")
        color = color_by_name.get(name) or color_by_name.get(name.split(" (", 1)[0])
        color = normalize_color(color)
        if not color:
            continue
        kind = getattr(trace, "type", "")
        if kind in ("scatter", "scattergl"):
            trace.update(line={"color": color})
        elif kind == "bar":
            # One bar trace holds every series. Callers color those themselves.
            continue
    return fig


def preserve_line_style(trace, width=3.8):
    """Line update that keeps a color and dash already chosen for the trace."""
    line = {"width": width}
    existing = getattr(trace, "line", None)
    if existing is not None:
        color = normalize_color(getattr(existing, "color", None))
        dash = getattr(existing, "dash", None)
        if color:
            line["color"] = color
        if dash:
            line["dash"] = dash
    return line
