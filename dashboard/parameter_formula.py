"""Saved parameter formulas.

A formula that names every room is one series. ``x/Parameter`` is the current
room, so that formula is one series per room, like a measurement. The number
is plotted, not written to Measurement. The unit is whatever is set on the
parameter.
"""

from __future__ import annotations

from dashboard.series_selection import (
    FormulaError,
    _is_x_series,
    bind_room,
    evaluate_formula,
    formula_is_room_bound,
    iter_series,
    pair_key,
    parse_formula_text,
    normalize_group_rows,
)

__all__ = [
    "FormulaError",
    "apply_calculations",
    "collect_measured_pairs",
    "evaluate_calculated_series",
    "evaluate_saved_formula",
    "load_keyed_with_calculations",
    "partition_parameters",
    "save_parameter_formula",
    "validate_parameter_formula",
    "x_measured_parameter_ids",
]


def partition_parameters(parameter_ids, explicit_pairs, calculated_ids):
    """Split a checkbox selection into measured pairs and calculated parameters.

    A calculated parameter is one series. Checking it does not cross it with
    every selected room, and an explicit Room/Calculated chip does not add a
    second copy.
    """
    calculated = {int(parameter_id) for parameter_id in calculated_ids}
    measured_ids = [int(parameter_id) for parameter_id in parameter_ids if int(parameter_id) not in calculated]
    calculated_series = [int(parameter_id) for parameter_id in parameter_ids if int(parameter_id) in calculated]
    measured_explicit = []
    seen_pairs = set()
    seen_calculated = set(calculated_series)
    for room_id, parameter_id in explicit_pairs or []:
        room_id = int(room_id)
        parameter_id = int(parameter_id)
        if parameter_id in calculated:
            if parameter_id not in seen_calculated:
                seen_calculated.add(parameter_id)
                calculated_series.append(parameter_id)
            continue
        pair = (room_id, parameter_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        measured_explicit.append(pair)
    return measured_ids, measured_explicit, calculated_series


def collect_measured_pairs(parameter_id, formulas, rooms, parameters, stack=None, room_id=None, groups=None):
    """Measured leaves of a calculated parameter, following nested formulas.

    ``room_id`` binds ``x`` before the walk. An unbound ``x/Measured`` has no
    concrete pair yet. An unbound ``x/Calculated`` is walked so a cycle is
    still an error. ``Room/RoomBound`` binds ``x`` to that named room.
    ``Room/Global`` ignores the room prefix.

    Raises FormulaError when the text is unreadable or the chain refers back
    to itself.
    """
    stack = set(stack or ())
    parameter_id = int(parameter_id)
    if parameter_id in stack:
        raise FormulaError("This formula refers back to itself.")
    text = str((formulas or {}).get(parameter_id) or "").strip()
    if not text:
        return []
    node = parse_formula_text(text, rooms, parameters, groups)
    if room_id is not None and formula_is_room_bound(text):
        node = bind_room(node, room_id)
    found = []
    seen = set()
    stack.add(parameter_id)
    try:
        for leaf in iter_series(node):
            if _is_x_series(leaf):
                param_id = int(leaf[1])
                child = str((formulas or {}).get(param_id) or "").strip()
                if not child:
                    pairs = []
                else:
                    pairs = collect_measured_pairs(
                        param_id, formulas, rooms, parameters, stack, room_id=None, groups=groups
                    )
            else:
                leaf_room, param_id = int(leaf[0]), int(leaf[1])
                child = str((formulas or {}).get(param_id) or "").strip()
                if child:
                    bound_room = leaf_room if formula_is_room_bound(child) else None
                    pairs = collect_measured_pairs(
                        param_id, formulas, rooms, parameters, stack, room_id=bound_room, groups=groups
                    )
                else:
                    pairs = [(leaf_room, param_id)]
            for pair in pairs:
                if pair in seen:
                    continue
                seen.add(pair)
                found.append(pair)
    finally:
        stack.discard(parameter_id)
    return found


def x_measured_parameter_ids(parameter_id, formulas, rooms, parameters, stack=None, groups=None):
    """Measured parameter ids reached through an unbound ``x``.

    Those decide which rooms a per-room formula covers. Fixed rooms, and a
    global formula reached through ``x``, do not. An empty list means there
    is no such leaf, so the caller uses every catalog room.
    """
    stack = set(stack or ())
    parameter_id = int(parameter_id)
    if parameter_id in stack:
        raise FormulaError("This formula refers back to itself.")
    text = str((formulas or {}).get(parameter_id) or "").strip()
    if not text:
        return []
    node = parse_formula_text(text, rooms, parameters, groups)
    found = []
    seen = set()
    stack.add(parameter_id)
    try:
        for leaf in iter_series(node):
            param_id = int(leaf[1])
            child = str((formulas or {}).get(param_id) or "").strip()
            if _is_x_series(leaf):
                if not child:
                    ids = [param_id]
                elif formula_is_room_bound(child):
                    ids = x_measured_parameter_ids(
                        param_id, formulas, rooms, parameters, stack, groups=groups
                    )
                else:
                    collect_measured_pairs(param_id, formulas, rooms, parameters, stack, groups=groups)
                    ids = []
            elif child:
                if formula_is_room_bound(child):
                    x_measured_parameter_ids(param_id, formulas, rooms, parameters, stack, groups=groups)
                else:
                    collect_measured_pairs(param_id, formulas, rooms, parameters, stack, groups=groups)
                ids = []
            else:
                ids = []
            for item in ids:
                if item in seen:
                    continue
                seen.add(item)
                found.append(item)
    finally:
        stack.discard(parameter_id)
    return found


def validate_parameter_formula(text, rooms, parameters, formulas, parameter_id, groups=None):
    """Parse ``text`` and reject a cycle. Empty text is a measured parameter."""
    text = str(text or "").strip()
    if not text:
        return None
    parameter_id = int(parameter_id)
    stored = dict(formulas or {})
    stored[parameter_id] = text
    parse_formula_text(text, rooms, parameters, groups)
    collect_measured_pairs(parameter_id, stored, rooms, parameters, groups=groups)
    return text


def _make_evaluator(keyed, formulas, rooms, parameters, tolerance=None, groups=None):
    """``ensure(parameter_id, room_id)`` for one request.

    A global formula is cached by parameter id and ignores the room.
    A per-room formula is cached by ``(parameter_id, room_id)``. The cycle
    stack is parameter ids, so ``x/Self`` is the same loop as a named one.
    """
    import pandas as pd

    formulas = formulas or {}
    cache = {}
    stack = []
    parsed = {}

    def node_for(param_id):
        if param_id not in parsed:
            parsed[param_id] = parse_formula_text(
                str(formulas.get(param_id) or "").strip(),
                rooms,
                parameters,
                groups,
            )
        return parsed[param_id]

    def measured(room_id, param_id):
        if room_id == "x" or keyed is None or getattr(keyed, "empty", True):
            return pd.Series(dtype="float64")
        key = pair_key(room_id, param_id)
        if key not in keyed.columns:
            return pd.Series(dtype="float64")
        return keyed[key]

    def get_series(room_id, param_id):
        return ensure(param_id, room_id)

    def ensure(param_id, room_id):
        param_id = int(param_id)
        text = str(formulas.get(param_id) or "").strip()
        if not text:
            return measured(room_id, param_id)
        bound = formula_is_room_bound(text)
        if bound and room_id is None:
            return pd.Series(dtype="float64")
        cache_key = (param_id, int(room_id) if bound else None)
        if cache_key in cache:
            return cache[cache_key]
        if param_id in stack:
            raise FormulaError("This formula refers back to itself.")
        stack.append(param_id)
        try:
            node = node_for(param_id)
            if bound:
                node = bind_room(node, room_id)
            series = evaluate_formula(node, get_series, tolerance=tolerance)
        finally:
            stack.pop()
        if series is None:
            series = pd.Series(dtype="float64")
        cache[cache_key] = series
        return series

    return ensure


def _write_column(frame, key, series):
    """Attach a non-empty series. An empty result is omitted."""
    import pandas as pd

    if series is None:
        return frame
    values = series.dropna()
    if values.empty:
        return frame if frame is not None else pd.DataFrame()
    if frame is None or getattr(frame, "empty", True):
        frame = pd.DataFrame({key: values})
        frame.index.name = "timestamp"
        return frame
    frame = frame.reindex(frame.index.union(values.index))
    frame[key] = values.reindex(frame.index)
    return frame


def apply_calculations(
    keyed,
    bound_pairs,
    calculated_ids,
    formulas,
    rooms,
    parameters,
    tolerance=None,
    groups=None,
):
    """Evaluate global formulas and write per-room formulas onto ``keyed``.

    ``calculated_ids`` are global. A per-room id in that list is ignored.
    ``bound_pairs`` are ``(room_id, parameter_id)`` and become pair-key columns.
    """
    import pandas as pd

    if keyed is None:
        keyed = pd.DataFrame()
    formulas = formulas or {}
    ensure = _make_evaluator(keyed, formulas, rooms, parameters, tolerance=tolerance, groups=groups)
    calculated = {}
    for parameter_id in calculated_ids or []:
        parameter_id = int(parameter_id)
        text = str(formulas.get(parameter_id) or "").strip()
        if not text or formula_is_room_bound(text):
            continue
        calculated[parameter_id] = ensure(parameter_id, None)
    for room_id, parameter_id in bound_pairs or []:
        series = ensure(int(parameter_id), int(room_id))
        keyed = _write_column(keyed, pair_key(room_id, parameter_id), series)
    return keyed, calculated


def evaluate_calculated_series(
    parameter_ids,
    keyed,
    formulas,
    rooms,
    parameters,
    tolerance=None,
    groups=None,
):
    """Evaluate global calculated parameters against pair-key columns.

    Nested calculated parameters are evaluated once. A per-room formula is
    not a single series here: bind it with a room and use
    :func:`apply_calculations`. A measured leaf with no column becomes an
    empty series, so the formula is missing rather than invented.
    """
    _keyed, calculated = apply_calculations(
        keyed,
        [],
        parameter_ids,
        formulas,
        rooms,
        parameters,
        tolerance=tolerance,
        groups=groups,
    )
    return calculated


def load_keyed_with_calculations(
    measured_pairs,
    calculated_ids,
    start,
    end,
    interval_minutes,
    fill_method="ffill",
    spike_factor=0.0,
    spike_ceiling=None,
    rooms=None,
    parameters=None,
    formulas=None,
    groups=None,
    tolerance=None,
    now=None,
):
    """Load measured pairs once, then evaluate calculated parameters on top.

    Returns ``(keyed, raw_means, calculated)``. ``keyed`` uses pair keys.
    ``calculated`` maps a parameter id to its series.
    """
    import pandas as pd

    from dashboard.bin_cache import ChartSpan, load_resampled_pairs

    rooms = list(rooms or [])
    parameters = list(parameters or [])
    formulas = dict(formulas or {})
    real_pairs = []
    bound_pairs = []
    seen = set()
    for pair in measured_pairs or []:
        key = (int(pair[0]), int(pair[1]))
        if key in seen:
            continue
        seen.add(key)
        text = str(formulas.get(key[1]) or "").strip()
        if text and formula_is_room_bound(text):
            bound_pairs.append(key)
        elif text:
            continue
        else:
            real_pairs.append(key)
    load_pairs = list(real_pairs)
    seen_load = set(real_pairs)
    for parameter_id in calculated_ids or []:
        parameter_id = int(parameter_id)
        text = str(formulas.get(parameter_id) or "").strip()
        if not text or formula_is_room_bound(text):
            continue
        for pair in collect_measured_pairs(parameter_id, formulas, rooms, parameters, groups=groups):
            if pair in seen_load:
                continue
            seen_load.add(pair)
            load_pairs.append(pair)
    for room_id, parameter_id in bound_pairs:
        for pair in collect_measured_pairs(
            parameter_id, formulas, rooms, parameters, room_id=room_id, groups=groups
        ):
            if pair in seen_load:
                continue
            seen_load.add(pair)
            load_pairs.append(pair)
    clock = ChartSpan("formula")
    clock.meta["pairs"] = len(load_pairs)
    clock.meta["calculated"] = len(list(calculated_ids or []))
    if not load_pairs:
        keyed, raw_means = pd.DataFrame(), {}
    else:
        with clock.time("load"):
            keyed, raw_means = load_resampled_pairs(
                load_pairs,
                start,
                end,
                interval_minutes,
                fill_method=fill_method,
                spike_factor=spike_factor,
                spike_ceiling=spike_ceiling,
                now=now,
            )
    with clock.time("calc"):
        keyed, calculated = apply_calculations(
            keyed,
            bound_pairs,
            calculated_ids,
            formulas,
            rooms,
            parameters,
            groups=groups,
            tolerance=tolerance,
        )
    clock.meta["series"] = 0 if not calculated else len(calculated)
    clock.emit()
    return keyed, raw_means, calculated


def formula_catalog(rooms, parameters, groups=None):
    """``(rooms, parameters, formulas, groups)`` for the parser."""
    if groups is None:
        from rooms.models import RoomGroup

        groups = RoomGroup.objects.prefetch_related("rooms").order_by("name")
    return (
        [(room.id, room.name) for room in rooms],
        [(param.id, param.name) for param in parameters],
        {param.id: param.formula or "" for param in parameters},
        normalize_group_rows(groups),
    )


def add_calculated_columns(frame, calculated, parameter_by_id):
    """Append one column per calculated series. Empty results are skipped."""
    import pandas as pd

    if frame is None:
        frame = pd.DataFrame()
    for parameter_id, series in (calculated or {}).items():
        if series is None or getattr(series, "dropna", lambda: series)().empty:
            continue
        values = series.dropna()
        if values.empty:
            continue
        parameter = parameter_by_id.get(parameter_id)
        label = parameter.name if parameter is not None else str(parameter_id)
        if label in frame.columns:
            label = f"{label} (calculated)"
        if frame.empty:
            frame = pd.DataFrame({label: values})
            frame.index.name = "timestamp"
            continue
        frame = frame.reindex(frame.index.union(values.index))
        frame[label] = values.reindex(frame.index)
    return frame


def _django_catalog():
    from parameters.models import Parameter
    from rooms.models import Room, RoomGroup

    rooms = list(Room.objects.values_list("id", "name"))
    parameters = list(Parameter.objects.values_list("id", "name"))
    formulas = {
        parameter_id: formula or ""
        for parameter_id, formula in Parameter.objects.values_list("id", "formula")
    }
    groups = normalize_group_rows(
        RoomGroup.objects.prefetch_related("rooms").order_by("name")
    )
    return rooms, parameters, formulas, groups


def evaluate_saved_formula(
    parameter,
    start,
    end,
    interval_minutes,
    fill_method="ffill",
    spike_factor=0.0,
    spike_ceiling=None,
    now=None,
    tolerance=None,
):
    """Series for one saved formula over the requested window."""
    import pandas as pd

    rooms, parameters, formulas, groups = _django_catalog()
    formulas[parameter.id] = parameter.formula or ""
    if not str(parameter.formula or "").strip():
        return pd.Series(dtype="float64")
    _keyed, _means, calculated = load_keyed_with_calculations(
        [],
        [parameter.id],
        start,
        end,
        interval_minutes,
        fill_method=fill_method,
        spike_factor=spike_factor,
        spike_ceiling=spike_ceiling,
        rooms=rooms,
        parameters=parameters,
        formulas=formulas,
        groups=groups,
        tolerance=tolerance,
        now=now,
    )
    series = calculated.get(parameter.id)
    if series is None:
        return pd.Series(dtype="float64")
    return series


def save_parameter_formula(parameter, text):
    """Validate and store a formula. Empty text makes the parameter measured."""
    rooms, parameters, formulas, groups = _django_catalog()
    name = parameter.name
    parameters = [
        (parameter_id, name if parameter_id == parameter.id else param_name)
        for parameter_id, param_name in parameters
    ]
    if parameter.id is None:
        parameters.append((0, name))
    parameter_id = parameter.id or 0
    cleaned = validate_parameter_formula(text, rooms, parameters, formulas, parameter_id, groups)
    parameter.formula = cleaned or ""
    parameter.save(update_fields=["formula"])
    return parameter.formula
