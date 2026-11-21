"""Calculated parameters: one series, nested formulas, cycle detection."""

import unittest

import pandas as pd

from dashboard.parameter_formula import (
    collect_measured_pairs,
    evaluate_calculated_series,
    partition_parameters,
    validate_parameter_formula,
)
from dashboard.series_selection import FormulaError, pair_key


ROOMS = [(1, "Kitchen"), (2, "Bath")]
PARAMS = [(1, "CO2"), (3, "AvgCO2"), (4, "Double"), (5, "LoopA"), (6, "LoopB")]


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


if __name__ == "__main__":
    unittest.main()
