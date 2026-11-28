"""Chart color selection and live-monitor formula trees."""

import unittest

from dashboard.chart_colors import PALETTE, distinct_line_colors, normalize_color, pick_series_color
from dashboard.series_selection import formula_ast_json, parse_formula_text


class ColorTests(unittest.TestCase):
    def test_blank_stays_blank(self):
        self.assertEqual(normalize_color(""), "")
        self.assertEqual(normalize_color("nope"), "")
        self.assertEqual(normalize_color("#39c"), "#3399cc")
        self.assertEqual(normalize_color("38BDF8"), "#38bdf8")

    def test_palette_stops_while_colors_still_separate(self):
        self.assertEqual(
            list(PALETTE),
            ["#1ec63e", "#f056fc", "#f48218", "#1db1f3", "#fa1a46", "#b3a919", "#6b78fb", "#00e6c8"],
        )

    def test_room_color_wins_when_comparing_rooms(self):
        self.assertEqual(pick_series_color("#112233", "#abcdef", prefer="room"), "#112233")
        self.assertEqual(pick_series_color("", "#abcdef", prefer="room"), "#abcdef")

    def test_parameter_color_wins_when_comparing_parameters(self):
        self.assertEqual(pick_series_color("#112233", "#abcdef", prefer="parameter"), "#abcdef")
        self.assertEqual(pick_series_color("#112233", "", prefer="parameter"), "#112233")

    def test_shared_parameter_color_is_not_repeated(self):
        colors = distinct_line_colors(["#abcdef", "#abcdef", "#112233", ""])
        self.assertEqual(colors[0], "#abcdef")
        self.assertEqual(colors[2], "#112233")
        self.assertNotEqual(colors[1], colors[0])
        self.assertEqual(len(set(colors)), 4)
        self.assertEqual(distinct_line_colors(["", ""]), ["", ""])
        rooms = [(1, "Kitchen"), (9, "x"), (3, "Outdoor")]
        params = [(2, "PM10")]
        node = parse_formula_text("(x/PM10 / Outdoor/PM10) * 100", rooms, params)
        self.assertEqual(
            formula_ast_json(node),
            {
                "t": "op",
                "o": "*",
                "l": {
                    "t": "op",
                    "o": "/",
                    "l": {"t": "x", "p": 2},
                    "r": {"t": "s", "r": 3, "p": 2},
                },
                "r": {"t": "n", "v": 100.0},
            },
        )


if __name__ == "__main__":
    unittest.main()
