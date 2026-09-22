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


def pairs_filter(pairs):
    """ORM filter matching only the given (room, parameter) pairs."""
    from django.db.models import Q

    query = Q()
    for room_id, parameter_id in pairs:
        query |= Q(sensor__room_id=room_id, parameter_id=parameter_id)
    return query
