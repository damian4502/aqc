"""Tests for decay detection and the exponential ACH fit. No database."""

import unittest

import numpy as np
import pandas as pd

from dashboard.ventilation import (
    ach_band,
    analyze_series,
    detect_decay_spans,
    fit_exponential_decay,
    period_min_floor,
    summarize_decays,
)


def _series(values, start="2026-03-01 08:00", step_min=5):
    index = pd.date_range(start, periods=len(values), freq=f"{step_min}min")
    return pd.Series(np.asarray(values, dtype=float), index=index)


def _exponential(ach, c_base, c_start, hours, step_min=5):
    t = np.arange(0, hours + 1e-9, step_min / 60.0)
    values = c_base + (c_start - c_base) * np.exp(-ach * t)
    return t, values


def _rise_then_decay(ach=1.2, c_base=420.0, c_peak=1600.0, rise_h=1.0, decay_h=6.0, step_min=5):
    rise_n = int(rise_h * 60 / step_min)
    rise = np.linspace(c_base + 40, c_peak, rise_n, endpoint=False)
    _t, decay = _exponential(ach, c_base, c_peak, decay_h, step_min=step_min)
    return _series(np.concatenate([rise, decay]), step_min=step_min)


class FitTests(unittest.TestCase):
    def test_recovers_known_ach_and_baseline(self):
        t, values = _exponential(0.8, 420.0, 1500.0, hours=5, step_min=5)
        fit = fit_exponential_decay(t, values, min_r2=0.9)
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit["ach"], 0.8, delta=0.03)
        self.assertAlmostEqual(fit["c_base"], 420.0, delta=15)
        self.assertGreater(fit["r2"], 0.99)
        self.assertAlmostEqual(fit["half_life_h"], np.log(2) / fit["ach"], places=6)

    def test_rejects_a_flat_line(self):
        t = np.arange(0, 3, 5 / 60)
        self.assertIsNone(fit_exponential_decay(t, np.full_like(t, 700.0)))

    def test_rejects_a_poor_shape(self):
        t = np.linspace(0, 4, 40)
        # A straight climb is not a decay anchored at the first sample.
        self.assertIsNone(fit_exponential_decay(t, np.linspace(400, 900, 40)))

    def test_short_linear_drop_does_not_invent_a_negative_baseline(self):
        # 527 → 454 in 73 min is almost a straight line. The asymptote is
        # not identified; the old search reported C_base ≈ -426.
        t = np.linspace(0, 73 / 60.0, 74)
        values = np.linspace(527.3, 454.0, 74)
        self.assertIsNone(fit_exponential_decay(t, values))

    def test_curved_drop_keeps_a_positive_baseline(self):
        t = np.linspace(0, 73 / 60.0, 74)
        values = 440.0 + (527.3 - 440.0) * np.exp(-1.4 * t)
        fit = fit_exponential_decay(t, values, min_r2=0.9)
        self.assertIsNotNone(fit)
        self.assertGreaterEqual(fit["c_base"], 0.0)
        self.assertAlmostEqual(fit["c_base"], 440.0, delta=20)
        self.assertAlmostEqual(fit["ach"], 1.4, delta=0.15)

    def test_zero_asymptote_stays_non_negative(self):
        t, values = _exponential(1.5, 0.0, 40.0, hours=3, step_min=5)
        fit = fit_exponential_decay(t, values, min_r2=0.9)
        self.assertIsNotNone(fit)
        self.assertGreaterEqual(fit["c_base"], 0.0)
        self.assertAlmostEqual(fit["c_base"], 0.0, delta=1.0)
        self.assertAlmostEqual(fit["ach"], 1.5, delta=0.2)

    def test_max_baseline_rejects_a_higher_floor(self):
        t, values = _exponential(0.8, 420.0, 1500.0, hours=5, step_min=5)
        self.assertIsNone(fit_exponential_decay(t, values, min_r2=0.9, max_baseline=400))
        kept = fit_exponential_decay(t, values, min_r2=0.9, max_baseline=450)
        self.assertIsNotNone(kept)
        self.assertLessEqual(kept["c_base"], 450)
        self.assertIsNotNone(fit_exponential_decay(t, values, min_r2=0.9, max_baseline=None))

    def test_min_baseline_rejects_a_lower_floor(self):
        t, values = _exponential(0.8, 420.0, 1500.0, hours=5, step_min=5)
        self.assertIsNone(fit_exponential_decay(t, values, min_r2=0.9, min_baseline=500))
        kept = fit_exponential_decay(t, values, min_r2=0.9, min_baseline=400)
        self.assertIsNotNone(kept)
        self.assertGreaterEqual(kept["c_base"], 400)
        self.assertIsNotNone(fit_exponential_decay(t, values, min_r2=0.9, min_baseline=None))

    def test_period_floor_keeps_a_baseline_just_under_the_lowest_sample(self):
        lowest = 459.0
        floor = period_min_floor(lowest)
        self.assertLess(floor, lowest)
        self.assertGreater(floor, lowest - 5)
        t, values = _exponential(1.2, 420.0, 1400.0, hours=6, step_min=5)
        fit = fit_exponential_decay(
            t, values, min_r2=0.9, min_baseline=period_min_floor(float(np.min(values)))
        )
        self.assertIsNotNone(fit)
        self.assertGreaterEqual(fit["c_base"], period_min_floor(float(np.min(values))))


class DetectTests(unittest.TestCase):
    def test_one_decay_after_a_rise(self):
        series = _rise_then_decay()
        found = analyze_series(series, label="Office", unit="ppm", min_r2=0.9)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["ach"], 1.2, delta=0.08)
        self.assertAlmostEqual(found[0]["c_base"], 420.0, delta=30)
        self.assertGreater(found[0]["r2"], 0.95)
        self.assertEqual(found[0]["unit"], "ppm")

    def test_series_that_opens_already_falling(self):
        _t, decay = _exponential(0.6, 450.0, 1800.0, hours=5, step_min=5)
        found = analyze_series(_series(decay), label="Hall", min_r2=0.9)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["ach"], 0.6, delta=0.08)

    def test_two_separate_decays(self):
        first = _rise_then_decay(ach=0.5, decay_h=4)
        second = _rise_then_decay(ach=1.5, c_peak=1400.0, decay_h=3)
        second.index = second.index + pd.Timedelta(hours=8)
        series = pd.concat([first, second])
        series = series[~series.index.duplicated(keep="first")].sort_index()
        found = analyze_series(series, label="Room", min_r2=0.85, min_duration_min=30)
        self.assertEqual(len(found), 2)
        rates = sorted(item["ach"] for item in found)
        self.assertAlmostEqual(rates[0], 0.5, delta=0.12)
        self.assertAlmostEqual(rates[1], 1.5, delta=0.15)

    def test_flat_and_rising_series_have_no_decays(self):
        flat = _series(np.full(80, 550.0))
        rising = _series(np.linspace(400, 1100, 80))
        self.assertEqual(analyze_series(flat, label="A"), [])
        self.assertEqual(analyze_series(rising, label="B"), [])

    def test_short_blip_is_rejected(self):
        _t, decay = _exponential(2.0, 400.0, 1200.0, hours=0.15, step_min=1)
        spans, _cleaned = detect_decay_spans(_series(decay, step_min=1), min_duration_min=30)
        self.assertEqual(spans, [])

    def test_noisy_decay_stays_close_to_the_true_rate(self):
        rng = np.random.default_rng(7)
        _t, decay = _exponential(0.9, 420.0, 1600.0, hours=4, step_min=5)
        noisy = decay + rng.normal(0, 8, size=len(decay))
        # A short rise so the window does not start mid-noise.
        rise = np.linspace(500, noisy[0], 12, endpoint=False)
        series = _series(np.concatenate([rise, noisy]))
        found = analyze_series(series, label="Noisy", min_r2=0.8)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["ach"], 0.9, delta=0.2)

    def test_max_baseline_filters_detected_decays(self):
        series = _rise_then_decay()
        self.assertEqual(
            analyze_series(series, label="Office", min_r2=0.9, max_baseline=400),
            [],
        )
        found = analyze_series(series, label="Office", min_r2=0.9, max_baseline=500)
        self.assertEqual(len(found), 1)
        self.assertLessEqual(found[0]["c_base"], 500)

    def test_min_baseline_filters_detected_decays(self):
        series = _rise_then_decay()
        self.assertEqual(
            analyze_series(series, label="Office", min_r2=0.9, min_baseline=500),
            [],
        )
        found = analyze_series(series, label="Office", min_r2=0.9, min_baseline=400)
        self.assertEqual(len(found), 1)
        self.assertGreaterEqual(found[0]["c_base"], 400)


class SummaryTests(unittest.TestCase):
    def test_median_and_band(self):
        series = _rise_then_decay(ach=0.4)
        found = analyze_series(series, label="Quiet", min_r2=0.9)
        rows = summarize_decays(found, ["Quiet", "Empty"])
        by_label = {row["label"]: row for row in rows}
        self.assertEqual(by_label["Empty"]["n"], 0)
        self.assertEqual(by_label["Empty"]["band"], "—")
        self.assertEqual(by_label["Quiet"]["n"], 1)
        self.assertEqual(by_label["Quiet"]["band"], ach_band(by_label["Quiet"]["median_ach"]))
        self.assertEqual(ach_band(0.2), "Slow")
        self.assertEqual(ach_band(4.0), "Very fast")

    def test_empty_series_with_gaps_is_safe(self):
        series = pd.Series([np.nan, np.nan], index=pd.date_range("2026-01-01", periods=2, freq="5min"))
        self.assertEqual(analyze_series(series, label="Gap"), [])


if __name__ == "__main__":
    unittest.main()
