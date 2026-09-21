"""Tests for compare-series helpers (no database required)."""

from django.test import SimpleTestCase
import numpy as np
import pandas as pd

from dashboard.compare import (
    _as_float,
    _fmt_hover_raw,
    build_compare_figure,
    compare_view,
    downsample_frame,
    normalize_frame,
    parse_normalize,
    series_stats,
    unit_axis_map,
)


class ParseNormalizeTests(SimpleTestCase):
    def test_aliases(self):
        self.assertEqual(parse_normalize("raw"), "off")
        self.assertEqual(parse_normalize("OFF"), "off")
        self.assertEqual(parse_normalize("none"), "off")
        self.assertEqual(parse_normalize("ZSCORE"), "zscore")
        self.assertEqual(parse_normalize("nope"), "minmax")


class NormalizeFrameTests(SimpleTestCase):
    def setUp(self):
        idx = pd.date_range("2026-01-01", periods=10, freq="h")
        self.frame = pd.DataFrame(
            {
                "a": np.linspace(10, 20, 10),
                "b": np.linspace(100, 200, 10),
                "flat": [5.0] * 10,
            },
            index=idx,
        )

    def test_minmax_range_and_constant(self):
        scaled = normalize_frame(self.frame, "minmax")
        self.assertAlmostEqual(float(scaled["a"].min()), 0.0)
        self.assertAlmostEqual(float(scaled["a"].max()), 1.0)
        self.assertAlmostEqual(float(scaled["b"].min()), 0.0)
        self.assertTrue(all(abs(v - 0.5) < 1e-9 for v in scaled["flat"]))

    def test_zscore_and_constant(self):
        scaled = normalize_frame(self.frame, "zscore")
        self.assertAlmostEqual(float(scaled["a"].mean()), 0.0, places=6)
        self.assertAlmostEqual(float(scaled["a"].std(ddof=0)), 1.0, places=6)
        self.assertTrue(all(abs(v) < 1e-9 for v in scaled["flat"]))

    def test_off_passthrough(self):
        raw = normalize_frame(self.frame, "off")
        self.assertEqual(list(raw["a"]), list(self.frame["a"]))

    def test_empty(self):
        empty = normalize_frame(pd.DataFrame(), "minmax")
        self.assertTrue(empty.empty)


class AxisAndStatsTests(SimpleTestCase):
    def test_dual_axis_two_units(self):
        mapping, titles, dual = unit_axis_map(
            {"Kitchen · CO2": "ppm", "Kitchen · Temp": "°C"},
            "off",
        )
        self.assertTrue(dual)
        self.assertEqual(mapping["Kitchen · CO2"], "y")
        self.assertEqual(mapping["Kitchen · Temp"], "y2")
        self.assertEqual(titles["y"], "ppm")
        self.assertEqual(titles["y2"], "°C")

    def test_same_unit_single_axis(self):
        mapping, titles, dual = unit_axis_map(
            {"Kitchen · CO2": "ppm", "Office · CO2": "ppm"},
            "off",
        )
        self.assertFalse(dual)
        self.assertEqual(mapping["Office · CO2"], "y")
        self.assertEqual(titles, {})

    def test_normalized_forces_single_axis(self):
        mapping, _, dual = unit_axis_map(
            {"Kitchen · CO2": "ppm", "Kitchen · Temp": "°C"},
            "minmax",
        )
        self.assertFalse(dual)
        self.assertEqual(mapping["Kitchen · Temp"], "y")

    def test_three_units_single_axis(self):
        _, _, dual = unit_axis_map(
            {"a": "ppm", "b": "°C", "c": "%"},
            "off",
        )
        self.assertFalse(dual)

    def test_stats_and_downsample(self):
        idx = pd.date_range("2026-01-01", periods=10, freq="h")
        frame = pd.DataFrame({"a": np.linspace(10, 20, 10)}, index=idx)
        rows = series_stats(frame)
        self.assertEqual(rows[0]["n"], 10)
        self.assertEqual(rows[0]["min"], 10)
        self.assertEqual(rows[0]["max"], 20)
        thinned = downsample_frame(pd.DataFrame({"a": range(10000)}), max_points=100)
        self.assertLessEqual(len(thinned), 101)


class FigureTests(SimpleTestCase):
    def _frame(self):
        idx = pd.date_range("2026-01-01", periods=8, freq="h")
        return pd.DataFrame(
            {"Kitchen · CO2": np.linspace(400, 800, 8), "Kitchen · Temp": np.linspace(18, 24, 8)},
            index=idx,
        )

    def test_empty_figure(self):
        fig = build_compare_figure(pd.DataFrame())
        self.assertEqual(len(fig.data), 0)

    def test_dual_axis_raw(self):
        frame = self._frame()
        units = {"Kitchen · CO2": "ppm", "Kitchen · Temp": "°C"}
        fig = build_compare_figure(frame, raw=frame, units_by_label=units, mode="off")
        self.assertEqual(len(fig.data), 2)
        self.assertEqual(fig.data[0].yaxis, "y")
        self.assertEqual(fig.data[1].yaxis, "y2")
        self.assertIn("yaxis2", fig.layout)

    def test_normalized_single_axis(self):
        frame = self._frame()
        units = {"Kitchen · CO2": "ppm", "Kitchen · Temp": "°C"}
        plotted = normalize_frame(frame, "minmax")
        fig = build_compare_figure(plotted, raw=frame, units_by_label=units, mode="minmax")
        self.assertEqual(fig.data[0].yaxis, "y")
        self.assertEqual(fig.data[1].yaxis, "y")
        self.assertNotIn("yaxis2", fig.layout)

    def test_events_capped(self):
        frame = self._frame()
        events = [
            {"timestamp": frame.index[i], "title": f"E{i}", "color": "#10b981"}
            for i in range(8)
        ]
        fig = build_compare_figure(frame, events=events)
        self.assertGreaterEqual(len(fig.layout.annotations), 8)

    def test_hover_raw_formatting(self):
        self.assertEqual(_fmt_hover_raw(None), "—")
        self.assertEqual(_as_float(float("nan")), None)
        self.assertEqual(_fmt_hover_raw(412.3456), "412.3")
        self.assertEqual(_fmt_hover_raw(1.2345), "1.234")


class ImportTests(SimpleTestCase):
    def test_compare_view_is_callable(self):
        self.assertTrue(callable(compare_view))
