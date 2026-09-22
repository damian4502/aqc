"""Tests for individual room/parameter series selection."""

from django.test import SimpleTestCase

from dashboard.series_selection import expand_series_pairs, parse_series_pairs


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
