"""Tests for individual room/parameter series selection."""

from django.test import SimpleTestCase
import pandas as pd

from dashboard.series_selection import (
    combine_aligned,
    encode_diff_entry,
    encode_series_entry,
    expand_series_pairs,
    formula_label,
    formula_unit,
    parse_difference_entries,
    parse_difference_specs,
    parse_series_entries,
    parse_series_pairs,
    subtract_aligned,
)


class SeriesSelectionTests(SimpleTestCase):
    def test_parse_keeps_known_unique_pairs_in_order(self):
        pairs = parse_series_pairs(
            ["2:5", "bad", "2:5", "9:1", "1:5", "2:99"],
            valid_room_ids={1, 2},
            valid_parameter_ids={5},
        )
        self.assertEqual(pairs, [(2, 5), (1, 5)])

    def test_disabled_token_stays_listed_but_is_not_plotted(self):
        entries = parse_series_entries(
            ["1:5", "2:5:0", "2:5"],
            valid_room_ids={1, 2},
            valid_parameter_ids={5},
        )
        self.assertEqual(entries, [((1, 5), True), ((2, 5), False)])
        self.assertEqual(
            parse_series_pairs(["1:5", "2:5:0"], {1, 2}, {5}),
            [(1, 5)],
        )
        self.assertEqual(encode_series_entry((2, 5), False), "2:5:0")

    def test_disabled_difference_token(self):
        entries = parse_difference_entries(
            ["1:5|2:5|0", "1:5|3:5"],
            valid_room_ids={1, 2, 3},
            valid_parameter_ids={5},
        )
        self.assertEqual(
            entries,
            [
                ((((1, 5), (2, 5)), ("-",)), False),
                ((((1, 5), (3, 5)), ("-",)), True),
            ],
        )
        self.assertEqual(
            parse_difference_specs(["1:5|2:5|0", "1:5|3:5"], {1, 2, 3}, {5}),
            [(((1, 5), (3, 5)), ("-",))],
        )
        self.assertEqual(encode_diff_entry((((1, 5), (2, 5)), ("-",)), False), "1:5|2:5|0")

    def test_expand_unions_cross_product_with_extra_pairs(self):
        pairs = expand_series_pairs(
            room_ids=[1],
            parameter_ids=[10, 11],
            explicit_pairs=[(2, 10), (1, 10)],
        )
        self.assertEqual(pairs, [(1, 10), (1, 11), (2, 10)])

    def test_expand_explicit_only(self):
        pairs = expand_series_pairs([], [], [(3, 7), (4, 8)])
        self.assertEqual(pairs, [(3, 7), (4, 8)])

    def test_parse_difference_skips_same_pair_and_duplicates(self):
        specs = parse_difference_specs(
            ["1:5|2:5", "1:5|2:5", "1:5|1:5", "bad", "3:5|9:5"],
            valid_room_ids={1, 2, 3},
            valid_parameter_ids={5},
        )
        self.assertEqual(specs, [(((1, 5), (2, 5)), ("-",))])

    def test_formula_ops_round_trip_and_reject_bad_tokens(self):
        rooms = {1, 2, 3}
        params = {5}
        raw = "1:5|add|2:5|mul|3:5"
        specs = parse_difference_specs(
            [raw, "1:5|sub|2:5", "1:5|nope|2:5", raw],
            rooms,
            params,
        )
        self.assertEqual(
            specs,
            [
                (((1, 5), (2, 5), (3, 5)), ("+", "*")),
                (((1, 5), (2, 5)), ("-",)),
            ],
        )
        self.assertEqual(encode_diff_entry(specs[0]), raw)
        self.assertEqual(encode_diff_entry(specs[1]), "1:5|2:5")
        self.assertEqual(formula_label(["A", "B", "C"], ("-", "*")), "A − B × C")
        self.assertEqual(formula_unit(["°C", "°C", "°C"], ("-",)), "°C")
        self.assertEqual(formula_unit(["°C", "Pa"], ("-",)), "Δ")
        self.assertEqual(formula_unit(["°C", "°C"], ("/",)), "")
        self.assertEqual(formula_unit(["°C", None], ("-",)), "°C")
        self.assertEqual(formula_unit([None, "°C"], ("*",)), "°C")
        self.assertEqual(formula_unit(["°C", "°C"], ("*",)), "")

    def test_constant_term_round_trip(self):
        rooms = {1, 2}
        params = {5}
        raw = "1:5|sub|n:21.5"
        specs = parse_difference_specs(
            [raw, "n:100|div|2:5", "n:1|add|n:2", "1:5|sub|n:nope", raw + "|0"],
            rooms,
            params,
        )
        self.assertEqual(
            specs,
            [
                (((1, 5), ("n", 21.5)), ("-",)),
                ((("n", 100.0), (2, 5)), ("/",)),
            ],
        )
        self.assertEqual(encode_diff_entry(specs[0]), raw)
        self.assertEqual(encode_diff_entry(specs[1]), "n:100|div|2:5")
        disabled = parse_difference_entries([raw + "|0"], rooms, params)
        self.assertEqual(disabled[0][1], False)

    def test_combine_with_constant(self):
        index = pd.date_range("2026-01-01", periods=2, freq="h")
        series = pd.Series([23.5, 20.0], index=index)
        lowered = combine_aligned([series, 2.5], ("-",))
        self.assertEqual(float(lowered.iloc[0]), 21.0)
        self.assertEqual(float(lowered.iloc[1]), 17.5)
        offset = combine_aligned([100, series], ("-",))
        self.assertEqual(float(offset.iloc[0]), 76.5)
        scaled = combine_aligned([series, 2], ("/",))
        self.assertEqual(float(scaled.iloc[1]), 10.0)
        other = pd.Series([1.5], index=pd.to_datetime(["2026-01-01 00:01"]))
        aligned = combine_aligned([series.iloc[:1], other, 2], ("-", "/"), tolerance="2min")
        self.assertEqual(len(aligned), 1)
        self.assertEqual(float(aligned.iloc[0]), (23.5 - 1.5) / 2)

    def test_subtract_shared_index(self):
        index = pd.date_range("2026-01-01", periods=3, freq="h")
        left = pd.Series([10.0, 12.0, 15.0], index=index)
        right = pd.Series([1.0, None, 4.0], index=index)
        diff = subtract_aligned(left, right)
        self.assertEqual(float(diff.iloc[0]), 9.0)
        self.assertTrue(pd.isna(diff.iloc[1]))
        self.assertEqual(float(diff.iloc[2]), 11.0)

    def test_subtract_nearest_within_tolerance(self):
        left = pd.Series([10.0], index=pd.to_datetime(["2026-01-01 00:00"]))
        right = pd.Series([3.0], index=pd.to_datetime(["2026-01-01 00:01"]))
        diff = subtract_aligned(left, right, tolerance="2min")
        self.assertEqual(len(diff), 1)
        self.assertEqual(float(diff.iloc[0]), 7.0)
        far = subtract_aligned(left, right, tolerance="30s")
        self.assertTrue(far.dropna().empty)

    def test_combine_left_to_right_and_divide_by_zero(self):
        index = pd.date_range("2026-01-01", periods=2, freq="h")
        first = pd.Series([10.0, 8.0], index=index)
        second = pd.Series([2.0, 0.0], index=index)
        third = pd.Series([3.0, 4.0], index=index)
        values = combine_aligned([first, second, third], ("/", "+"))
        self.assertEqual(float(values.iloc[0]), 8.0)
        self.assertTrue(pd.isna(values.iloc[1]))

    def test_combine_three_terms_nearest(self):
        base = pd.Series([10.0], index=pd.to_datetime(["2026-01-01 00:00"]))
        second = pd.Series([4.0], index=pd.to_datetime(["2026-01-01 00:01"]))
        third = pd.Series([2.0], index=pd.to_datetime(["2026-01-01 00:01:30"]))
        values = combine_aligned([base, second, third], ("-", "/"), tolerance="2min")
        self.assertEqual(len(values), 1)
        self.assertEqual(float(values.iloc[0]), 3.0)
        far = combine_aligned([base, second, third], ("-", "/"), tolerance="30s")
        self.assertTrue(far.dropna().empty)
