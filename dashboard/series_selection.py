"""Room × parameter selection shared by Compare and Correlations.

Checked rooms and parameters still expand to every combination. Extra
``series=room_id:parameter_id`` values add only that pair, so a few
specific traces do not require the full cross product.
"""

from __future__ import annotations


def parse_id_list(values) -> list[int]:
    ids = []
    for raw in values or []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def parse_series_pairs(values, valid_room_ids, valid_parameter_ids) -> list[tuple[int, int]]:
    """Parse ``room:parameter`` tokens into unique, known pairs (input order)."""
    rooms = set(valid_room_ids)
    parameters = set(valid_parameter_ids)
    pairs = []
    seen = set()
    for raw in values or []:
        text = str(raw).strip()
        if ":" not in text:
            continue
        left, right = text.split(":", 1)
        try:
            room_id = int(left)
            parameter_id = int(right)
        except (TypeError, ValueError):
            continue
        if room_id not in rooms or parameter_id not in parameters:
            continue
        key = (room_id, parameter_id)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def expand_series_pairs(room_ids, parameter_ids, explicit_pairs) -> list[tuple[int, int]]:
    """Union of the checkbox cross product and individually added pairs."""
    pairs = []
    seen = set()
    for room_id in room_ids:
        for parameter_id in parameter_ids:
            key = (room_id, parameter_id)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
    for key in explicit_pairs:
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def pair_key(room_id, parameter_id) -> str:
    """Stable column key for one room/parameter pair."""
    return f"{int(room_id)}:{int(parameter_id)}"


def parse_difference_specs(values, valid_room_ids, valid_parameter_ids) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Parse ``room:parameter|room:parameter`` tokens into unique A − B specs."""
    specs = []
    seen = set()
    for raw in values or []:
        text = str(raw).strip()
        if "|" not in text:
            continue
        left, right = text.split("|", 1)
        left_pairs = parse_series_pairs([left], valid_room_ids, valid_parameter_ids)
        right_pairs = parse_series_pairs([right], valid_room_ids, valid_parameter_ids)
        if len(left_pairs) != 1 or len(right_pairs) != 1:
            continue
        spec = (left_pairs[0], right_pairs[0])
        if spec[0] == spec[1] or spec in seen:
            continue
        seen.add(spec)
        specs.append(spec)
    return specs


def subtract_aligned(left, right, tolerance=None):
    """Return left − right.

    ``tolerance`` (pandas offset, e.g. ``"2min"``) matches the nearest sample
    when the two series do not share timestamps. Without it, subtraction uses
    the shared index and is NaN wherever either side is missing.
    """
    import pandas as pd

    left = pd.to_numeric(left, errors="coerce")
    right = pd.to_numeric(right, errors="coerce")
    if not tolerance:
        return left - right

    left = left.dropna().sort_index()
    right = right.dropna().sort_index()
    if left.empty or right.empty:
        return pd.Series(dtype="float64")

    left_frame = left.rename("left").reset_index()
    right_frame = right.rename("right").reset_index()
    left_frame.columns = ["timestamp", "left"]
    right_frame.columns = ["timestamp", "right"]
    merged = pd.merge_asof(
        left_frame.sort_values("timestamp"),
        right_frame.sort_values("timestamp"),
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(tolerance),
    )
    merged = merged.dropna(subset=["left", "right"])
    if merged.empty:
        return pd.Series(dtype="float64")
    return pd.Series(
        merged["left"].to_numpy() - merged["right"].to_numpy(),
        index=pd.DatetimeIndex(merged["timestamp"]),
    )


def pairs_filter(pairs):
    """ORM filter matching only the given (room, parameter) pairs."""
    from django.db.models import Q

    query = Q()
    for room_id, parameter_id in pairs:
        query |= Q(sensor__room_id=room_id, parameter_id=parameter_id)
    return query

