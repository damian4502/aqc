"""Room × parameter selection shared by Compare and Correlations.

Checked rooms and parameters still expand to every combination. Extra
``series=room_id:parameter_id`` values add only that pair, so a few
specific traces do not require the full cross product. A trailing ``:0``
(or ``|0`` on a calculation) keeps the chip but leaves it out of the chart.
"""

from __future__ import annotations

import math
import re

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
FN_NAMES = ("min", "max", "avg", "range")
MAX_FORMULA_TERMS = 24
_PREC = {"+": 1, "-": 1, "*": 2, "/": 2}
_FX_WORD = {"+": "add", "-": "sub", "*": "mul", "/": "div"}


class FormulaError(ValueError):
    """A typed calculation could not be parsed."""


def _is_series(node) -> bool:
    return (
        isinstance(node, tuple)
        and len(node) == 2
        and not isinstance(node[0], bool)
        and isinstance(node[0], int)
        and isinstance(node[1], int)
    )


def _is_x_series(node) -> bool:
    """``("x", parameter_id)`` is the current room, not a named room."""
    return (
        isinstance(node, tuple)
        and len(node) == 2
        and node[0] == "x"
        and not isinstance(node[1], bool)
        and isinstance(node[1], int)
    )


_X_ROOM = re.compile(r"(?:^|[^0-9A-Za-z_])x\s*/", re.IGNORECASE)


def formula_is_room_bound(text) -> bool:
    """True when a formula uses ``x/Parameter`` for the current room.

    A room literally named ``x`` loses: the placeholder is reserved.
    """
    return _X_ROOM.search(str(text or "")) is not None


def _is_leaf(node) -> bool:
    return is_constant(node) or _is_series(node) or _is_x_series(node)


def _is_fn(node) -> bool:
    return (
        isinstance(node, tuple)
        and len(node) == 3
        and node[0] == "fn"
        and node[1] in FN_NAMES
        and isinstance(node[2], tuple)
        and len(node[2]) >= 1
    )


def _is_op(node) -> bool:
    return (
        isinstance(node, tuple)
        and len(node) == 4
        and node[0] == "op"
        and node[1] in _PREC
    )


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
    """A ``room:parameter`` pair, an ``x:parameter`` current-room leaf, or ``n:<number>``."""
    text = str(token).strip()
    folded = text.lower()
    if folded.startswith("n:"):
        number = _parse_constant_value(text[2:])
        if number is None:
            return None
        return ("n", number)
    if folded.startswith("x:"):
        try:
            parameter_id = int(text[2:])
        except (TypeError, ValueError):
            return None
        if parameter_id not in set(valid_parameter_ids):
            return None
        return ("x", parameter_id)
    return _one_pair(text, valid_room_ids, valid_parameter_ids)


def _encode_term(term) -> str:
    if is_constant(term):
        return "n:" + format_constant(term[1])
    if _is_x_series(term):
        return "x:" + str(int(term[1]))
    return pair_key(*term)


def _fold_left(terms, ops):
    """Left-associative chain ``((a op b) op c)``."""
    node = terms[0]
    for op, term in zip(ops, terms[1:]):
        node = ("op", op, node, term)
    return node


def _left_chain(node):
    """``(terms, ops)`` when every operation's right side is a leaf, else None."""
    ops = []
    rights = []
    current = node
    while _is_op(current) and _is_leaf(current[3]):
        ops.append(current[1])
        rights.append(current[3])
        current = current[2]
    if not _is_leaf(current):
        return None
    return (current, *reversed(rights)), tuple(reversed(ops))


def iter_series(node):
    """Series leaves of a formula, left to right. The same pair can repeat.

    An ``x`` leaf is ``("x", parameter_id)`` until :func:`bind_room` replaces it.
    """
    if _is_series(node) or _is_x_series(node):
        yield node
    elif _is_op(node):
        yield from iter_series(node[2])
        yield from iter_series(node[3])
    elif _is_fn(node):
        for arg in node[2]:
            yield from iter_series(arg)


def bind_room(node, room_id):
    """Replace ``x/Parameter`` leaves with ``(room_id, parameter)``.

    Named rooms stay as written. Call this before :func:`evaluate_formula`.
    """
    room_id = int(room_id)
    if _is_x_series(node):
        return (room_id, int(node[1]))
    if _is_op(node):
        return ("op", node[1], bind_room(node[2], room_id), bind_room(node[3], room_id))
    if _is_fn(node):
        return ("fn", node[1], tuple(bind_room(arg, room_id) for arg in node[2]))
    return node


def _series_count(node) -> int:
    return sum(1 for _ in iter_series(node))


def _parse_fx(body, valid_room_ids, valid_parameter_ids):
    """Parse ``add(1:5,2:5)`` and ``avg(1:5,2:5)`` bodies from :func:`_encode_fx`."""
    pos = 0

    def parse_node():
        nonlocal pos
        for word, op in (("add(", "+"), ("sub(", "-"), ("mul(", "*"), ("div(", "/")):
            if body.startswith(word, pos):
                pos += len(word)
                left = parse_node()
                if left is None or pos >= len(body) or body[pos] != ",":
                    return None
                pos += 1
                right = parse_node()
                if right is None or pos >= len(body) or body[pos] != ")":
                    return None
                pos += 1
                return ("op", op, left, right)
        for name in FN_NAMES:
            word = name + "("
            if not body.startswith(word, pos):
                continue
            pos += len(word)
            args = []
            while True:
                arg = parse_node()
                if arg is None:
                    return None
                args.append(arg)
                if pos < len(body) and body[pos] == ",":
                    pos += 1
                    continue
                break
            if pos >= len(body) or body[pos] != ")" or not args:
                return None
            if name != "range" and len(args) < 2:
                return None
            pos += 1
            return ("fn", name, tuple(args))
        end = pos
        while end < len(body) and body[end] not in ",)":
            end += 1
        token = body[pos:end]
        pos = end
        if not token:
            return None
        return parse_formula_term(token, valid_room_ids, valid_parameter_ids)

    node = parse_node()
    if node is None or pos != len(body):
        return None
    if _series_count(node) < 1 or _series_count(node) > MAX_FORMULA_TERMS:
        return None
    return node


def _encode_fx(node) -> str:
    if _is_op(node):
        return f"{_FX_WORD[node[1]]}({_encode_fx(node[2])},{_encode_fx(node[3])})"
    if _is_fn(node):
        return node[1] + "(" + ",".join(_encode_fx(arg) for arg in node[2]) + ")"
    return _encode_term(node)


def parse_difference_specs(values, valid_room_ids, valid_parameter_ids):
    """Enabled calculations as expression trees.

    A leaf is ``(room, parameter)`` or ``("n", value)``. An operation is
    ``("op", "+", left, right)``. ``min``, ``max``, ``avg``, and ``range`` are
    ``("fn", name, args)``. ``range`` is max minus min. ``*`` and ``/`` bind tighter than ``+``
    and ``-`` when the formula was typed. A saved ``a|b`` token is still
    left-associative A − B, and ``a|add|b|mul|c`` is still ``(a + b) * c``.
    """
    return [
        spec
        for spec, enabled in parse_difference_entries(values, valid_room_ids, valid_parameter_ids)
        if enabled
    ]


def parse_difference_entries(values, valid_room_ids, valid_parameter_ids):
    """Parse calculation tokens into ``(ast, enabled)`` entries.

    ``a|b`` and ``a|b|0`` stay valid as a subtraction. Newer left-to-right
    tokens look like ``a|add|b|mul|c`` or ``a|sub|n:21.5``. Formulas that
    need real parentheses are ``fx:div(add(1:5,2:5),n:3)``.
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
        if text.startswith("fx:"):
            node = _parse_fx(text[3:], valid_room_ids, valid_parameter_ids)
            if node is None:
                continue
        else:
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
                node = _fold_left((left, right), ("-",))
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
                if not any(_is_series(term) or _is_x_series(term) for term in terms):
                    continue
                node = _fold_left(terms, ops)
        if node in seen:
            continue
        seen.add(node)
        entries.append((node, enabled))
    return entries


def encode_diff_entry(node, enabled=True) -> str:
    """Encode an expression. A left-associative chain keeps the old pipe form."""
    chain = _left_chain(node)
    if chain is not None:
        terms, ops = chain
        if (
            len(terms) == 2
            and ops == ("-",)
            and _is_series(terms[0])
            and _is_series(terms[1])
        ):
            key = f"{pair_key(*terms[0])}|{pair_key(*terms[1])}"
        else:
            parts = [_encode_term(terms[0])]
            for op, term in zip(ops, terms[1:]):
                parts.append(TOKEN_FOR_OP[op])
                parts.append(_encode_term(term))
            key = "|".join(parts)
    else:
        key = "fx:" + _encode_fx(node)
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


def formula_unit_of(node, unit_of_series):
    """Unit of an expression tree. ``None`` means a constant.

    ``unit_of_series(room_id, parameter_id)`` returns the series unit.
    """
    if is_constant(node):
        return None
    if _is_series(node) or _is_x_series(node):
        return unit_of_series(node[0], node[1]) or ""
    if _is_fn(node):
        units = [formula_unit_of(arg, unit_of_series) for arg in node[2]]
        if all(unit is None for unit in units):
            return None
        return formula_unit(units, tuple("+" for _ in units[1:]))
    if not _is_op(node):
        return ""
    return formula_unit(
        [
            formula_unit_of(node[2], unit_of_series),
            formula_unit_of(node[3], unit_of_series),
        ],
        (node[1],),
    )


def formula_source(node, series_name) -> str:
    """Text that parses back to ``node``. ``series_name(room, parameter)`` is ``Room/Parameter``."""

    def emit(current, parent_prec, is_right):
        if is_constant(current):
            return format_constant(current[1])
        if _is_x_series(current) or _is_series(current):
            return series_name(current[0], current[1])
        if _is_fn(current):
            args = ", ".join(emit(arg, 0, False) for arg in current[2])
            return f"{current[1]}({args})"
        if not _is_op(current):
            return ""
        op = current[1]
        if (
            op == "-"
            and is_constant(current[2])
            and float(current[2][1]) == 0.0
        ):
            text = "-" + emit(current[3], 3, False)
            prec = 3
        else:
            prec = _PREC[op]
            text = emit(current[2], prec, False) + " " + op + " " + emit(current[3], prec, True)
        if prec < parent_prec or (prec == parent_prec and is_right):
            return "(" + text + ")"
        return text

    return emit(node, 0, False)


def formula_ast_json(node):
    """JSON-ready tree for the live monitor. ``None`` is not a formula node."""
    if is_constant(node):
        return {"t": "n", "v": float(node[1])}
    if _is_x_series(node):
        return {"t": "x", "p": int(node[1])}
    if _is_series(node):
        return {"t": "s", "r": int(node[0]), "p": int(node[1])}
    if _is_op(node):
        return {
            "t": "op",
            "o": node[1],
            "l": formula_ast_json(node[2]),
            "r": formula_ast_json(node[3]),
        }
    if _is_fn(node):
        return {
            "t": "fn",
            "n": node[1],
            "a": [formula_ast_json(arg) for arg in node[2]],
        }
    return None


def _name_boundary(text, index) -> bool:
    if index >= len(text):
        return True
    char = text[index]
    return char.isspace() or char in "+-*/(),×÷−"


def parse_formula_text(text, rooms, parameters):
    """Parse ``avg(Kuhinja/CO2, Kopalnica/CO2)`` into an expression tree.

    ``/`` between a known room and parameter is the series, and ``/`` between
    values divides. ``x/Parameter`` (any case, spaces allowed) is the current
    room and wins over a room that is actually named ``x``. ``*`` and ``/``
    bind tighter than ``+`` and ``-``. Parentheses group. ``min``, ``max``,
    and ``avg`` take two or more values. ``range`` takes one or more and is
    the highest minus the lowest. Arguments are separated by commas and are
    computed at each timestamp. At least one series is required.
    """
    raw = str(text or "")
    if not raw.strip():
        raise FormulaError("Enter a formula.")
    room_list = sorted((rooms or []), key=lambda item: len(item[1] or ""), reverse=True)
    param_list = sorted((parameters or []), key=lambda item: len(item[1] or ""), reverse=True)
    tokens = []
    index = 0
    length = len(raw)

    def skip_space(pos):
        while pos < length and raw[pos].isspace():
            pos += 1
        return pos

    while True:
        index = skip_space(index)
        if index >= length:
            break
        call = _match_call(raw, index)
        if call is not None:
            name, index = call
            tokens.append(("fn", name))
            continue
        matched = None
        if raw[index] in "xX" and _name_boundary(raw, index + 1):
            slash = skip_space(index + 1)
            if slash < length and raw[slash] == "/":
                name_at = skip_space(slash + 1)
                for param_id, param_name in param_list:
                    param_name = str(param_name or "")
                    if not param_name:
                        continue
                    param_end = name_at + len(param_name)
                    if raw[name_at:param_end].casefold() != param_name.casefold():
                        continue
                    if not _name_boundary(raw, param_end):
                        continue
                    matched = (("x", int(param_id)), param_end)
                    break
        if matched is None:
            for room_id, room_name in room_list:
                room_name = str(room_name or "")
                if not room_name:
                    continue
                end = index + len(room_name)
                if raw[index:end].casefold() != room_name.casefold():
                    continue
                slash = skip_space(end)
                if slash >= length or raw[slash] != "/":
                    continue
                name_at = skip_space(slash + 1)
                for param_id, param_name in param_list:
                    param_name = str(param_name or "")
                    if not param_name:
                        continue
                    param_end = name_at + len(param_name)
                    if raw[name_at:param_end].casefold() != param_name.casefold():
                        continue
                    if not _name_boundary(raw, param_end):
                        continue
                    matched = ((int(room_id), int(param_id)), param_end)
                    break
                if matched:
                    break
        if matched:
            tokens.append(matched[0])
            index = matched[1]
            continue
        number = _match_number(raw, index)
        if number is not None:
            value, index = number
            tokens.append(("n", value))
            continue
        char = raw[index]
        if char in "(),":
            tokens.append(char)
            index += 1
            continue
        op = OP_TOKENS.get(char)
        if op is None and char in "−×÷":
            op = {"−": "-", "×": "*", "÷": "/"}[char]
        if op in _PREC:
            tokens.append(op)
            index += 1
            continue
        snippet = raw[index:index + 12].strip()
        raise FormulaError(f"Can't read the formula near “{snippet}”.")

    node = _parse_expression_tokens(tokens)
    count = _series_count(node)
    if count < 1:
        raise FormulaError("Use at least one series, written as Room/Parameter.")
    if count > MAX_FORMULA_TERMS:
        raise FormulaError(f"A formula can use at most {MAX_FORMULA_TERMS} series.")
    return node


def _match_call(text, index):
    """``min``/``max``/``avg``/``range`` when the next non-space character is ``(``."""
    for name in FN_NAMES:
        end = index + len(name)
        if end > len(text) or text[index:end].casefold() != name:
            continue
        if not _name_boundary(text, end):
            continue
        cursor = end
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor < len(text) and text[cursor] == "(":
            return name, cursor
    return None


def _is_call_token(token) -> bool:
    return (
        isinstance(token, tuple)
        and len(token) == 2
        and token[0] == "fn"
        and token[1] in FN_NAMES
    )


def _match_number(text, index):
    import re

    found = re.match(r"\d+(?:[.,]\d+)?", text[index:])
    if not found:
        return None
    value = _parse_constant_value(found.group(0))
    if value is None:
        return None
    return value, index + found.end()


def _parse_expression_tokens(tokens):
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def parse_expr():
        node = parse_term()
        while peek() in ("+", "-"):
            op = tokens[pos]
            nonlocal_advance()
            node = ("op", op, node, parse_term())
        return node

    def nonlocal_advance():
        nonlocal pos
        pos += 1

    def parse_term():
        node = parse_unary()
        while peek() in ("*", "/"):
            op = tokens[pos]
            nonlocal_advance()
            node = ("op", op, node, parse_unary())
        return node

    def parse_unary():
        if peek() == "-":
            nonlocal_advance()
            return ("op", "-", ("n", 0.0), parse_unary())
        return parse_primary()

    def parse_primary():
        current = peek()
        if current is None:
            raise FormulaError("The formula ends unexpectedly.")
        if current == "(":
            nonlocal_advance()
            node = parse_expr()
            if peek() != ")":
                raise FormulaError("Close the parenthesis.")
            nonlocal_advance()
            return node
        if _is_call_token(current):
            name = current[1]
            nonlocal_advance()
            if peek() != "(":
                raise FormulaError(f"Expected '(' after {name}.")
            nonlocal_advance()
            need = 1 if name == "range" else 2
            if peek() in (None, ",", ")"):
                raise FormulaError(
                    f"{name} needs at least one value."
                    if need == 1
                    else f"{name} needs at least two values."
                )
            args = [parse_expr()]
            while peek() == ",":
                nonlocal_advance()
                if peek() in (None, ",", ")"):
                    raise FormulaError(f"{name} has an empty argument.")
                args.append(parse_expr())
            if peek() != ")":
                raise FormulaError("Close the parenthesis.")
            nonlocal_advance()
            if len(args) < need:
                raise FormulaError(
                    f"{name} needs at least one value."
                    if need == 1
                    else f"{name} needs at least two values."
                )
            if len(args) > MAX_FORMULA_TERMS:
                raise FormulaError(f"{name} can use at most {MAX_FORMULA_TERMS} values.")
            return ("fn", name, tuple(args))
        if is_constant(current) or _is_series(current) or _is_x_series(current):
            nonlocal_advance()
            return current
        raise FormulaError("Expected a series, a number, or '('.")

    node = parse_expr()
    if pos != len(tokens):
        raise FormulaError("Can't read the rest of the formula.")
    return node


def evaluate_formula(node, get_series, tolerance=None):
    """Evaluate an expression tree.

    ``get_series(room_id, parameter_id)`` returns a Series. Without
    ``tolerance``, operations use the shared index. With a pandas offset
    such as ``"2min"``, every series is matched to the nearest sample of
    the first series. Division by zero becomes NaN.
    """
    import pandas as pd

    pairs = list(dict.fromkeys(iter_series(node)))
    if not pairs:
        return pd.Series(dtype="float64")
    loaded = {}
    for room_id, param_id in pairs:
        series = get_series(room_id, param_id)
        if series is None:
            return pd.Series(dtype="float64")
        loaded[(room_id, param_id)] = pd.to_numeric(series, errors="coerce")

    def eval_in(environment):
        def value(current):
            if is_constant(current):
                return float(current[1])
            if _is_series(current) or _is_x_series(current):
                return environment[current]
            if _is_op(current):
                return _apply_op(value(current[2]), value(current[3]), current[1])
            if _is_fn(current):
                return _apply_fn(current[1], [value(arg) for arg in current[2]])
            return pd.Series(dtype="float64")

        return value(node)

    if not tolerance:
        result = eval_in(loaded)
        if not isinstance(result, pd.Series):
            return pd.Series(dtype="float64")
        return result

    base = loaded[pairs[0]].dropna().sort_index()
    if base.empty:
        return pd.Series(dtype="float64")
    aligned = {pairs[0]: base}
    for pair in pairs[1:]:
        aligned[pair] = _nearest_values(base, loaded[pair], tolerance)
    frame = pd.concat([aligned[pair] for pair in pairs], axis=1)
    frame = frame.dropna()
    if frame.empty:
        return pd.Series(dtype="float64")
    environment = {pair: frame.iloc[:, index] for index, pair in enumerate(pairs)}
    result = eval_in(environment)
    if not isinstance(result, pd.Series):
        return pd.Series(dtype="float64")
    return result.dropna()


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


def _apply_fn(name, values):
    """Pointwise ``min``, ``max``, ``avg``, or ``range``. A missing value stays missing.

    ``range`` is the highest value minus the lowest at that timestamp.
    """
    import pandas as pd

    series_values = [value for value in values if isinstance(value, pd.Series)]
    if not series_values:
        numbers = []
        for value in values:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return float("nan")
            if not math.isfinite(number):
                return float("nan")
            numbers.append(number)
        if name == "min":
            return min(numbers)
        if name == "max":
            return max(numbers)
        if name == "range":
            return max(numbers) - min(numbers)
        return sum(numbers) / len(numbers)

    index = series_values[0].index
    for series in series_values[1:]:
        index = index.union(series.index)
    columns = []
    for value in values:
        if isinstance(value, pd.Series):
            columns.append(pd.to_numeric(value, errors="coerce").reindex(index))
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = float("nan")
            if not math.isfinite(number):
                number = float("nan")
            columns.append(pd.Series(number, index=index, dtype="float64"))
    frame = pd.concat(columns, axis=1)
    if name == "min":
        result = frame.min(axis=1, skipna=False)
    elif name == "max":
        result = frame.max(axis=1, skipna=False)
    elif name == "range":
        result = frame.max(axis=1, skipna=False) - frame.min(axis=1, skipna=False)
    else:
        result = frame.mean(axis=1, skipna=False)
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
