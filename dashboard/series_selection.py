"""Room × parameter selection shared by Compare and Correlations.

Checked rooms and parameters still expand to every combination. Extra
``series=room_id:parameter_id`` values add only that pair, so a few
specific traces do not require the full cross product. A trailing ``:0``
(or ``|0`` on a calculation) keeps the chip but leaves it out of the chart.
"""

from __future__ import annotations

# Query tokens stay URL-safe. ``+`` would be decoded as a space.
OP_TOKENS = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "div": "/",
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
}
TOKEN_FOR_OP = {"+": "add", "-": "sub", "*": "mul", "/": "div"}
OP_SYMBOLS = {"+": "+", "-": "−", "*": "×", "/": "÷"}
MAX_FORMULA_TERMS = 8


def parse_id_list(values) -> list[int]:
    ids = []
    for raw in values or []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def parse_series_pairs(values, valid_room_ids, valid_parameter_ids) -> list[tuple[int, int]]:
    """Parse enabled ``room:parameter`` tokens into unique pairs (input order)."""
    return [
        pair
        for pair, enabled in parse_series_entries(values, valid_room_ids, valid_parameter_ids)
        if enabled
    ]


def pair_key(room_id, parameter_id) -> str:
    """Stable column key for one room/parameter pair."""
    return f"{int(room_id)}:{int(parameter_id)}"


def parse_series_entries(values, valid_room_ids, valid_parameter_ids):
    """Parse ``room:parameter`` or ``room:parameter:0`` into ``(pair, enabled)``."""
    rooms = set(valid_room_ids)
    parameters = set(valid_parameter_ids)
    entries = []
    seen = set()
    for raw in values or []:
        parts = str(raw).strip().split(":")
        if len(parts) < 2:
            continue
        try:
            room_id = int(parts[0])
            parameter_id = int(parts[1])
        except (TypeError, ValueError):
            continue
        if room_id not in rooms or parameter_id not in parameters:
            continue
        enabled = len(parts) < 3 or parts[2] not in ("0", "off", "false")
        key = (room_id, parameter_id)
        if key in seen:
            continue
        seen.add(key)
        entries.append((key, enabled))
    return entries


def encode_series_entry(pair, enabled=True) -> str:
    key = pair_key(pair[0], pair[1])
    return key if enabled else f"{key}:0"


def _one_pair(token, valid_room_ids, valid_parameter_ids):
    found = parse_series_entries([token], valid_room_ids, valid_parameter_ids)
    if len(found) != 1:
        return None
    return found[0][0]


def parse_difference_specs(values, valid_room_ids, valid_parameter_ids):
    """Enabled calculations as ``(terms, ops)``.

    ``terms`` is a tuple of ``(room, parameter)`` pairs. ``ops`` has one of
    ``+``, ``-``, ``*``, ``/`` between each pair. A saved ``a|b`` token is
    still A − B.
    """
    return [spec for spec, enabled in parse_difference_entries(values, valid_room_ids, valid_parameter_ids) if enabled]


def parse_difference_entries(values, valid_room_ids, valid_parameter_ids):
    """Parse calculation tokens into ``(spec, enabled)`` entries.

    ``spec`` is ``(terms, ops)`` with at least two terms. ``a|b`` and
    ``a|b|0`` stay valid as a disabled or enabled subtraction. Newer tokens
    look like ``a|add|b|mul|c``.
    """
    entries = []
    seen = set()
    for raw in values or []:
        text = str(raw).strip()
        if not text:
            continue
        enabled = True
        if text.endswith("|0"):
            enabled = False
            text = text[:-2]
        tokens = [part for part in text.split("|") if part != ""]
        if len(tokens) < 2:
            continue
        if len(tokens) == 2:
            left = _one_pair(tokens[0], valid_room_ids, valid_parameter_ids)
            right = _one_pair(tokens[1], valid_room_ids, valid_parameter_ids)
            if left is None or right is None or left == right:
                continue
            spec = ((left, right), ("-",))
        else:
            if len(tokens) % 2 == 0:
                continue
            first = _one_pair(tokens[0], valid_room_ids, valid_parameter_ids)
            if first is None:
                continue
            terms = [first]
            ops = []
            valid = True
            for index in range(1, len(tokens), 2):
                op = OP_TOKENS.get(tokens[index])
                pair = _one_pair(tokens[index + 1], valid_room_ids, valid_parameter_ids)
                if op is None or pair is None:
                    valid = False
                    break
                ops.append(op)
                terms.append(pair)
            if not valid or len(terms) < 2 or len(terms) > MAX_FORMULA_TERMS:
                continue
            spec = (tuple(terms), tuple(ops))
        if spec in seen:
            continue
        seen.add(spec)
        entries.append((spec, enabled))
    return entries


def encode_diff_entry(spec, enabled=True) -> str:
    """Encode ``(terms, ops)``. Two-term subtraction keeps the old ``a|b`` form."""
    terms, ops = spec
    if len(terms) == 2 and tuple(ops) == ("-",):
        key = f"{pair_key(*terms[0])}|{pair_key(*terms[1])}"
    else:
        parts = [pair_key(*terms[0])]
        for op, term in zip(ops, terms[1:]):
            parts.append(TOKEN_FOR_OP[op])
            parts.append(pair_key(*term))
        key = "|".join(parts)
    return key if enabled else f"{key}|0"


def formula_label(names, ops) -> str:
    """Human label, evaluated-order, e.g. ``A − B × C``."""
    parts = [str(names[0])]
    for op, name in zip(ops, list(names)[1:]):
        parts.append(OP_SYMBOLS.get(op, op))
        parts.append(str(name))
    return " ".join(parts)


def formula_unit(units, ops) -> str:
    """Unit for a left-to-right formula.

    Addition and subtraction keep the shared unit, or ``Δ`` when the units
    differ. Multiplication and division are left unlabeled.
    """
    units = [str(unit or "") for unit in units]
    if not ops or any(op not in "+-" for op in ops):
        return ""
    if len(set(units)) == 1 and units[0]:
        return units[0]
    return "Δ"


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


def _apply_op(left, right, op):
    import pandas as pd

    if op == "+":
        result = left + right
    elif op == "-":
        result = left - right
    elif op == "*":
        result = left * right
    elif op == "/":
        result = left / right
    else:
        return pd.Series(dtype="float64")
    numeric = pd.to_numeric(result, errors="coerce")
    return numeric.where(numeric.abs() < float("inf"))


def _nearest_values(base, other, tolerance):
    """Values of ``other`` on ``base`` timestamps, NaN outside ``tolerance``."""
    import pandas as pd

    other = pd.to_numeric(other, errors="coerce").dropna().sort_index()
    if len(base) == 0 or other.empty:
        return pd.Series(dtype="float64", index=base.index)
    base_frame = pd.DataFrame({"timestamp": pd.DatetimeIndex(base.index)})
    other_frame = other.rename("value").reset_index()
    other_frame.columns = ["timestamp", "value"]
    merged = pd.merge_asof(
        base_frame.sort_values("timestamp"),
        other_frame.sort_values("timestamp"),
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(tolerance),
    )
    return pd.Series(merged["value"].to_numpy(), index=pd.DatetimeIndex(base.index))


def combine_aligned(parts, ops, tolerance=None):
    """Combine series left to right with ``+``, ``-``, ``*``, ``/``.

    Without ``tolerance``, operations use the shared index and are NaN
    wherever an operand is missing. With a pandas offset such as ``"2min"``,
    later series are matched to the nearest sample of the first series.
    Division by zero becomes NaN.
    """
    import pandas as pd

    parts = [pd.to_numeric(part, errors="coerce") for part in parts]
    ops = tuple(ops)
    if len(parts) < 2 or len(ops) != len(parts) - 1:
        return pd.Series(dtype="float64")
    if not tolerance:
        result = parts[0]
        for op, other in zip(ops, parts[1:]):
            result = _apply_op(result, other, op)
        return result

    base = parts[0].dropna().sort_index()
    if base.empty:
        return pd.Series(dtype="float64")
    aligned = [base]
    for other in parts[1:]:
        aligned.append(_nearest_values(base, other, tolerance))
    frame = pd.concat(aligned, axis=1)
    frame = frame.dropna()
    if frame.empty:
        return pd.Series(dtype="float64")
    result = frame.iloc[:, 0]
    for index, op in enumerate(ops):
        result = _apply_op(result, frame.iloc[:, index + 1], op)
    return result.dropna()


def subtract_aligned(left, right, tolerance=None):
    """Return left − right. See ``combine_aligned`` for alignment rules."""
    return combine_aligned([left, right], ("-",), tolerance=tolerance)


def pairs_filter(pairs):
    """ORM filter matching only the given (room, parameter) pairs."""
    from django.db.models import Q

    query = Q()
    for room_id, parameter_id in pairs:
        query |= Q(sensor__room_id=room_id, parameter_id=parameter_id)
    return query
