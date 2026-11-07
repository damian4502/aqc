"""Tests for individual room/parameter series selection."""

from django.test import SimpleTestCase
import pandas as pd

from dashboard.series_selection import (
    expand_series_pairs,
    parse_difference_specs,
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
        self.assertEqual(specs, [((1, 5), (2, 5))])

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

