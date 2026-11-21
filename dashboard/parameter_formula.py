"""Saved parameter formulas.

A calculated parameter is one series. The text uses the same Room/Parameter
language as Compare. The number is plotted, not written to Measurement.
The unit is whatever is set on the parameter.
"""

from __future__ import annotations

from dashboard.series_selection import (
    FormulaError,
    evaluate_formula,
    iter_series,
    pair_key,
    parse_formula_text,
)

__all__ = [
    "FormulaError",
    "collect_measured_pairs",
    "evaluate_calculated_series",
    "evaluate_saved_formula",
    "load_keyed_with_calculations",
    "partition_parameters",
    "save_parameter_formula",
    "validate_parameter_formula",
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


def collect_measured_pairs(parameter_id, formulas, rooms, parameters, stack=None):
    """Measured leaves of a calculated parameter, following nested formulas.

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
    node = parse_formula_text(text, rooms, parameters)
    found = []
    seen = set()
    stack.add(parameter_id)
    try:
        for room_id, param_id in iter_series(node):
            param_id = int(param_id)
            if str((formulas or {}).get(param_id) or "").strip():
                pairs = collect_measured_pairs(param_id, formulas, rooms, parameters, stack)
            else:
                pairs = [(int(room_id), param_id)]
            for pair in pairs:
                if pair in seen:
                    continue
                seen.add(pair)
                found.append(pair)
    finally:
        stack.discard(parameter_id)
    return found


def validate_parameter_formula(text, rooms, parameters, formulas, parameter_id):
    """Parse ``text`` and reject a cycle. Empty text is a measured parameter."""
    text = str(text or "").strip()
    if not text:
        return None
    parameter_id = int(parameter_id)
    stored = dict(formulas or {})
    stored[parameter_id] = text
    parse_formula_text(text, rooms, parameters)
    collect_measured_pairs(parameter_id, stored, rooms, parameters)
    return text


def evaluate_calculated_series(
    parameter_ids,
    keyed,
    formulas,
    rooms,
    parameters,
    tolerance=None,
):
    """Evaluate calculated parameters against pair-key columns in ``keyed``.

    Nested calculated parameters are evaluated once. A measured leaf that has
    no column becomes an empty series, so the formula is missing rather than
    invented.
    """
    import pandas as pd

    formulas = formulas or {}
    cache = {}
    stack = set()
    parsed = {}

    def node_for(param_id):
        if param_id not in parsed:
            parsed[param_id] = parse_formula_text(
                str(formulas.get(param_id) or "").strip(),
                rooms,
                parameters,
            )
        return parsed[param_id]

    def measured(room_id, param_id):
        if keyed is None or getattr(keyed, "empty", True):
            return pd.Series(dtype="float64")
        key = pair_key(room_id, param_id)
        if key not in keyed.columns:
            return pd.Series(dtype="float64")
        return keyed[key]

    def ensure(param_id):
        param_id = int(param_id)
        if param_id in cache:
            return cache[param_id]
        if param_id in stack:
            raise FormulaError("This formula refers back to itself.")
        if not str(formulas.get(param_id) or "").strip():
            return pd.Series(dtype="float64")
        stack.add(param_id)
        try:
            series = evaluate_formula(node_for(param_id), get_series, tolerance=tolerance)
        finally:
            stack.discard(param_id)
        if series is None:
            series = pd.Series(dtype="float64")
        cache[param_id] = series
        return series

    def get_series(room_id, param_id):
        if str(formulas.get(int(param_id)) or "").strip():
            return ensure(param_id)
        return measured(room_id, param_id)

    result = {}
    for parameter_id in parameter_ids or []:
        parameter_id = int(parameter_id)
        if not str(formulas.get(parameter_id) or "").strip():
            continue
        result[parameter_id] = ensure(parameter_id)
    return result


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
    tolerance=None,
    now=None,
):
    """Load measured pairs once, then evaluate calculated parameters on top.

    Returns ``(keyed, raw_means, calculated)``. ``keyed`` uses pair keys.
    ``calculated`` maps a parameter id to its series.
    """
    import pandas as pd

    from dashboard.bin_cache import load_resampled_pairs

    rooms = list(rooms or [])
    parameters = list(parameters or [])
    formulas = dict(formulas or {})
    load_pairs = []
    seen = set()
    for pair in measured_pairs or []:
        key = (int(pair[0]), int(pair[1]))
        if key in seen:
            continue
        seen.add(key)
        load_pairs.append(key)
    for parameter_id in calculated_ids or []:
        for pair in collect_measured_pairs(parameter_id, formulas, rooms, parameters):
            if pair in seen:
                continue
            seen.add(pair)
            load_pairs.append(pair)
    if not load_pairs:
        keyed, raw_means = pd.DataFrame(), {}
    else:
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
    calculated = evaluate_calculated_series(
        calculated_ids,
        keyed,
        formulas,
        rooms,
        parameters,
        tolerance=tolerance,
    )
    return keyed, raw_means, calculated


def formula_catalog(rooms, parameters):
    """``(rooms, parameters, formulas)`` for the parser, from model instances."""
    return (
        [(room.id, room.name) for room in rooms],
        [(param.id, param.name) for param in parameters],
        {param.id: param.formula or "" for param in parameters},
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
    from rooms.models import Room

    rooms = list(Room.objects.values_list("id", "name"))
    parameters = list(Parameter.objects.values_list("id", "name"))
    formulas = {
        parameter_id: formula or ""
        for parameter_id, formula in Parameter.objects.values_list("id", "formula")
    }
    return rooms, parameters, formulas


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

    rooms, parameters, formulas = _django_catalog()
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
        tolerance=tolerance,
        now=now,
    )
    series = calculated.get(parameter.id)
    if series is None:
        return pd.Series(dtype="float64")
    return series


def save_parameter_formula(parameter, text):
    """Validate and store a formula. Empty text makes the parameter measured."""
    rooms, parameters, formulas = _django_catalog()
    name = parameter.name
    parameters = [
        (parameter_id, name if parameter_id == parameter.id else param_name)
        for parameter_id, param_name in parameters
    ]
    if parameter.id is None:
        parameters.append((0, name))
    parameter_id = parameter.id or 0
    cleaned = validate_parameter_formula(text, rooms, parameters, formulas, parameter_id)
    parameter.formula = cleaned or ""
    parameter.save(update_fields=["formula"])
    return parameter.formula
