"""Room × parameter selection shared by Compare and Correlations.

Checked rooms and parameters still expand to every combination. Extra
``series=room_id:parameter_id`` values add only that pair, so a few
specific traces do not require the full cross product. A trailing ``:0``
(or ``|0`` on a calculation) keeps the chip but leaves it out of the chart.
"""

from __future__ import annotations

import math

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


def is_constant(term) -> bool:
    """True for a numeric formula term ``("n", value)``."""
    return isinstance(term, tuple) and len(term) == 2 and term[0] == "n"


def format_constant(value) -> str:
    """Stable text for a finite constant so keys round-trip."""
    number = float(value)
    if number == 0.0:
        return "0"
    return format(number, ".12g")


def _parse_constant_value(text):
    try:
        number = float(str(text).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def parse_formula_term(token, valid_room_ids, valid_parameter_ids):
    """A ``room:parameter`` pair or a ``n:<number>`` constant."""
    text = str(token).strip()
    if text.lower().startswith("n:"):
        number = _parse_constant_value(text[2:])
        if number is None:
            return None
        return ("n", number)
    return _one_pair(text, valid_room_ids, valid_parameter_ids)


def _encode_term(term) -> str:
    if is_constant(term):
        return "n:" + format_constant(term[1])
    return pair_key(*term)


def parse_difference_specs(values, valid_room_ids, valid_parameter_ids):
    """Enabled calculations as ``(terms, ops)``.

    ``terms`` is a tuple of ``(room, parameter)`` pairs or ``("n", value)``
    constants. ``ops`` has one of ``+``, ``-``, ``*``, ``/`` between each
    term. A saved ``a|b`` token is still A − B. At least one term must be a
    series.
    """
    return [spec for spec, enabled in parse_difference_entries(values, valid_room_ids, valid_parameter_ids) if enabled]


def parse_difference_entries(values, valid_room_ids, valid_parameter_ids):
    """Parse calculation tokens into ``(spec, enabled)`` entries.

    ``spec`` is ``(terms, ops)`` with at least two terms, and at least one
    series. ``a|b`` and ``a|b|0`` stay valid as a subtraction. Newer tokens
    look like ``a|add|b|mul|c`` or ``a|sub|n:21.5``.
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
            left = parse_formula_term(tokens[0], valid_room_ids, valid_parameter_ids)
            right = parse_formula_term(tokens[1], valid_room_ids, valid_parameter_ids)
            if (
                left is None
                or right is None
                or is_constant(left)
                or is_constant(right)
                or left == right
            ):
                continue
            spec = ((left, right), ("-",))
        else:
            if len(tokens) % 2 == 0:
                continue
            first = parse_formula_term(tokens[0], valid_room_ids, valid_parameter_ids)
            if first is None:
                continue
            terms = [first]
            ops = []
            valid = True
            for index in range(1, len(tokens), 2):
                op = OP_TOKENS.get(tokens[index])
                term = parse_formula_term(tokens[index + 1], valid_room_ids, valid_parameter_ids)
                if op is None or term is None:
                    valid = False
                    break
                ops.append(op)
                terms.append(term)
            if not valid or len(terms) < 2 or len(terms) > MAX_FORMULA_TERMS:
                continue
            if not any(not is_constant(term) for term in terms):
                continue
            spec = (tuple(terms), tuple(ops))
        if spec in seen:
            continue
        seen.add(spec)
        entries.append((spec, enabled))
    return entries


def encode_diff_entry(spec, enabled=True) -> str:
    """Encode ``(terms, ops)``. Two-series subtraction keeps the old ``a|b`` form."""
    terms, ops = spec
    if (
        len(terms) == 2
        and tuple(ops) == ("-",)
        and not is_constant(terms[0])
        and not is_constant(terms[1])
    ):
        key = f"{pair_key(*terms[0])}|{pair_key(*terms[1])}"
    else:
        parts = [_encode_term(terms[0])]
        for op, term in zip(ops, terms[1:]):
            parts.append(TOKEN_FOR_OP[op])
            parts.append(_encode_term(term))
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

    ``None`` marks a constant. Addition and subtraction keep a measured unit
    when the other side is a constant or the same unit, and use ``Δ`` when
    measured units differ or are blank. Scaling by a constant keeps the
    series unit. An operation between two series with ``*`` or ``/`` is
    unlabeled.
    """
    if not units:
        return ""

    def token(unit):
        if unit is None:
            return None
        return str(unit or "")

    current = token(units[0])
    for op, unit in zip(ops, list(units)[1:]):
        other = token(unit)
        if op in "*/":
            if current is not None and other is not None:
                current = ""
            else:
                current = current if current is not None else other
        elif current is None:
            current = other
        elif other is None:
            continue
        elif current != other:
            current = "Δ"
    if current:
        return current
    if any(op not in "+-" for op in ops):
        return ""
    if any(unit is not None for unit in units):
        return "Δ"
    return ""


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


def _is_number(part) -> bool:
    return isinstance(part, (int, float)) and not isinstance(part, bool)


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
    if isinstance(result, pd.Series):
        numeric = pd.to_numeric(result, errors="coerce")
        return numeric.where(numeric.abs() < float("inf"))
    try:
        value = float(result)
    except (TypeError, ValueError):
        return float("nan")
    if not math.isfinite(value):
        return float("nan")
    return value


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
    """Combine series and constants left to right with ``+``, ``-``, ``*``, ``/``.

    A number broadcasts across the series index. Without ``tolerance``,
    series operations use the shared index and are NaN wherever an operand
    is missing. With a pandas offset such as ``"2min"``, later series are
    matched to the nearest sample of the first series. Division by zero
    becomes NaN.
    """
    import pandas as pd

    prepared = []
    for part in parts:
        if _is_number(part):
            value = float(part)
            if not math.isfinite(value):
                return pd.Series(dtype="float64")
            prepared.append(value)
        else:
            prepared.append(pd.to_numeric(part, errors="coerce"))
    ops = tuple(ops)
    if len(prepared) < 2 or len(ops) != len(prepared) - 1:
        return pd.Series(dtype="float64")
    if not any(isinstance(part, pd.Series) for part in prepared):
        return pd.Series(dtype="float64")

    if not tolerance:
        result = prepared[0]
        for op, other in zip(ops, prepared[1:]):
            result = _apply_op(result, other, op)
        if not isinstance(result, pd.Series):
            return pd.Series(dtype="float64")
        return result

    anchor_at = next(index for index, part in enumerate(prepared) if isinstance(part, pd.Series))
    base = prepared[anchor_at].dropna().sort_index()
    if base.empty:
        return pd.Series(dtype="float64")
    aligned = []
    for index, part in enumerate(prepared):
        if index == anchor_at:
            aligned.append(base)
        elif isinstance(part, pd.Series):
            aligned.append(_nearest_values(base, part, tolerance))
        else:
            aligned.append(pd.Series(part, index=base.index, dtype="float64"))
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
