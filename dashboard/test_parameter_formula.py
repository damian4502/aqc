"""Calculated parameters: global series, per-room x, nested formulas, cycles."""

import unittest

import pandas as pd

from dashboard.parameter_formula import (
    apply_calculations,
    collect_measured_pairs,
    evaluate_calculated_series,
    partition_parameters,
    validate_parameter_formula,
    x_measured_parameter_ids,
)
from dashboard.series_selection import (
    FormulaError,
    bind_room,
    formula_is_room_bound,
    formula_source,
    pair_key,
    parse_formula_text,
)


ROOMS = [(1, "Kitchen"), (2, "Bath")]
PARAMS = [(1, "CO2"), (3, "AvgCO2"), (4, "Double"), (5, "LoopA"), (6, "LoopB")]

RATIO_ROOMS = [(1, "Kitchen"), (2, "Bath"), (3, "Outdoor"), (9, "x")]
RATIO_PARAMS = [(2, "PM10"), (7, "Ratio"), (8, "Eff"), (9, "DoubleEff"), (10, "KitchenRatio")]
RATIO_TEXT = "(x/PM10 / Outdoor/PM10) * 100"


class FormulaGraphTests(unittest.TestCase):
    def test_cycle_is_rejected(self):
        formulas = {5: "Kitchen/LoopB", 6: "Kitchen/LoopA"}
        with self.assertRaises(FormulaError):
            validate_parameter_formula("Kitchen/LoopB", ROOMS, PARAMS, formulas, 5)

    def test_nested_formula_uses_measured_leaves_once(self):
        formulas = {
            1: "",
            3: "avg(Kitchen/CO2, Bath/CO2)",
            4: "Kitchen/AvgCO2 * 2",
        }
        leaves = collect_measured_pairs(4, formulas, ROOMS, PARAMS)
        self.assertEqual(leaves, [(1, 1), (2, 1)])
        index = pd.date_range("2026-01-01", periods=3, freq="h")
        keyed = pd.DataFrame(
            {
                pair_key(1, 1): [10.0, 20.0, 30.0],
                pair_key(2, 1): [30.0, 40.0, 50.0],
            },
            index=index,
        )
        series = evaluate_calculated_series([4], keyed, formulas, ROOMS, PARAMS)
        self.assertEqual(list(series[4].to_numpy()), [40.0, 60.0, 80.0])

    def test_checkbox_does_not_cross_calculated_parameters_with_rooms(self):
        measured_ids, explicit, calculated = partition_parameters(
            [1, 3],
            [(2, 3), (1, 1), (2, 4)],
            calculated_ids={3, 4},
        )
        self.assertEqual(measured_ids, [1])
        self.assertEqual(explicit, [(1, 1)])
        self.assertEqual(calculated, [3, 4])


class RoomBoundFormulaTests(unittest.TestCase):
    def test_x_is_the_current_room_even_when_a_room_is_named_x(self):
        self.assertTrue(formula_is_room_bound(RATIO_TEXT))
        self.assertTrue(formula_is_room_bound("X / PM10"))
        self.assertFalse(formula_is_room_bound("avg(Kitchen/PM10, Outdoor/PM10)"))
        self.assertFalse(formula_is_room_bound("Box/PM10"))
        node = parse_formula_text("X / PM10 / Outdoor / PM10 * 100", RATIO_ROOMS, RATIO_PARAMS)
        self.assertEqual(
            node,
            ("op", "*", ("op", "/", ("x", 2), (3, 2)), ("n", 100.0)),
        )
        self.assertEqual(bind_room(node, 1)[2][2], (1, 2))

        def label(room, param):
            if room == "x":
                return f"x/{dict(RATIO_PARAMS)[param]}"
            return f"{dict(RATIO_ROOMS)[room]}/{dict(RATIO_PARAMS)[param]}"

        self.assertEqual(formula_source(node, label), "x/PM10 / Outdoor/PM10 * 100")
        self.assertEqual(
            parse_formula_text(formula_source(node, label), RATIO_ROOMS, RATIO_PARAMS),
            node,
        )

    def test_two_rooms_share_the_outdoor_leaf(self):
        formulas = {2: "", 7: RATIO_TEXT, 8: RATIO_TEXT, 9: "x/Eff * 2", 10: "Kitchen/Ratio"}
        self.assertEqual(
            x_measured_parameter_ids(7, formulas, RATIO_ROOMS, RATIO_PARAMS),
            [2],
        )
        self.assertEqual(
            collect_measured_pairs(7, formulas, RATIO_ROOMS, RATIO_PARAMS),
            [(3, 2)],
        )
        self.assertEqual(
            collect_measured_pairs(7, formulas, RATIO_ROOMS, RATIO_PARAMS, room_id=1),
            [(1, 2), (3, 2)],
        )
        index = pd.to_datetime(["2026-01-01 13:00", "2026-01-01 14:00"])
        keyed = pd.DataFrame(
            {
                pair_key(1, 2): [10.0, 12.0],
                pair_key(2, 2): [30.0, 40.0],
                pair_key(3, 2): [20.0, 20.0],
            },
            index=index,
        )
        keyed, calculated = apply_calculations(
            keyed, [(1, 7), (2, 7)], [], formulas, RATIO_ROOMS, RATIO_PARAMS
        )
        self.assertEqual(list(keyed[pair_key(1, 7)].dropna()), [50.0, 60.0])
        self.assertEqual(list(keyed[pair_key(2, 7)].dropna()), [150.0, 200.0])
        nested = apply_calculations(
            keyed, [(1, 9)], [], formulas, RATIO_ROOMS, RATIO_PARAMS
        )[0]
        self.assertEqual(list(nested[pair_key(1, 9)].dropna()), [100.0, 120.0])
        global_series = evaluate_calculated_series(
            [10], keyed, formulas, RATIO_ROOMS, RATIO_PARAMS
        )
        self.assertEqual(list(global_series[10].dropna()), [50.0, 60.0])

    def test_x_self_cycle_is_rejected(self):
        with self.assertRaises(FormulaError):
            validate_parameter_formula("x/LoopA", ROOMS, PARAMS, {5: "x/LoopA"}, 5)

    def test_callers_keep_per_room_parameters_in_the_cross_product(self):
        measured_ids, explicit, calculated = partition_parameters(
            [1, 7],
            [(2, 7)],
            calculated_ids=set(),
        )
        self.assertEqual(measured_ids, [1, 7])
        self.assertEqual(explicit, [(2, 7)])
        self.assertEqual(calculated, [])


if __name__ == "__main__":
    unittest.main()
