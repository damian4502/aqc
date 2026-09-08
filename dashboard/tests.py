"""Tests for correlation helpers (no database required)."""

from django.test import SimpleTestCase
import numpy as np
import pandas as pd

from dashboard.correlations import (
    downsample_series,
    format_pvalue,
    make_series_label,
    pairwise_correlations,
    strength_label,
)


class StrengthLabelTests(SimpleTestCase):
    def test_bands(self):
        self.assertEqual(strength_label(0.95), "Very strong")
        self.assertEqual(strength_label(-0.81), "Very strong")
        self.assertEqual(strength_label(0.6), "Strong")
        self.assertEqual(strength_label(0.45), "Moderate")
        self.assertEqual(strength_label(0.2), "Weak")
        self.assertEqual(strength_label(0.05), "Negligible")


class SeriesLabelTests(SimpleTestCase):
    def test_both_dimensions(self):
        self.assertEqual(
            make_series_label("Kitchen", "CO2", include_room=True, include_parameter=True),
            "Kitchen · CO2",
        )

    def test_room_only(self):
        self.assertEqual(
            make_series_label("Kitchen", "CO2", include_room=True, include_parameter=False),
            "Kitchen",
        )

    def test_parameter_only(self):
        self.assertEqual(
            make_series_label("Kitchen", "CO2", include_room=False, include_parameter=True),
            "CO2",
        )


class PairwiseCorrelationTests(SimpleTestCase):
    def test_perfect_positive(self):
        x = list(range(20))
        df = pd.DataFrame({"a": x, "b": x})
        matrix, pairs, counts = pairwise_correlations(df, method="pearson", min_overlap=10)
        self.assertAlmostEqual(matrix.loc["a", "b"], 1.0, places=5)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["strength"], "Very strong")
        self.assertEqual(pairs[0]["direction"], "Positive")
        self.assertEqual(pairs[0]["n"], 20)
        self.assertEqual(counts.loc["a", "b"], 20)

    def test_perfect_negative(self):
        x = np.arange(20, dtype=float)
        df = pd.DataFrame({"up": x, "down": -x})
        matrix, pairs, _ = pairwise_correlations(df, method="pearson")
        self.assertAlmostEqual(matrix.loc["up", "down"], -1.0, places=5)
        self.assertEqual(pairs[0]["direction"], "Negative")

    def test_skips_insufficient_overlap(self):
        df = pd.DataFrame(
            {
                "a": [1.0, 2.0, np.nan, np.nan, np.nan, np.nan],
                "b": [np.nan, np.nan, np.nan, np.nan, 3.0, 4.0],
            }
        )
        matrix, pairs, _ = pairwise_correlations(df, min_overlap=5)
        self.assertTrue(np.isnan(matrix.loc["a", "b"]))
        self.assertEqual(pairs, [])

    def test_skips_constant_series(self):
        df = pd.DataFrame({"flat": [1.0] * 20, "moving": list(range(20))})
        matrix, pairs, _ = pairwise_correlations(df)
        self.assertTrue(np.isnan(matrix.loc["flat", "moving"]))
        self.assertEqual(pairs, [])

    def test_spearman_monotonic(self):
        x = np.arange(1, 25, dtype=float)
        df = pd.DataFrame({"linear": x, "squared": x ** 2})
        pearson_m, _, _ = pairwise_correlations(df, method="pearson")
        spearman_m, spearman_pairs, _ = pairwise_correlations(df, method="spearman")
        self.assertGreater(spearman_m.loc["linear", "squared"], pearson_m.loc["linear", "squared"])
        self.assertAlmostEqual(spearman_m.loc["linear", "squared"], 1.0, places=5)
        self.assertEqual(spearman_pairs[0]["strength"], "Very strong")

    def test_empty_frame(self):
        matrix, pairs, counts = pairwise_correlations(pd.DataFrame())
        self.assertTrue(matrix.empty)
        self.assertEqual(pairs, [])
        self.assertTrue(counts.empty)

    def test_ranking_order(self):
        rng = np.random.default_rng(0)
        a = rng.normal(size=40)
        df = pd.DataFrame(
            {
                "a": a,
                "strong": a + rng.normal(scale=0.05, size=40),
                "weak": rng.normal(size=40),
            }
        )
        _, pairs, _ = pairwise_correlations(df)
        self.assertEqual(len(pairs), 3)
        self.assertEqual(pairs[0]["a"], "a")
        self.assertEqual(pairs[0]["b"], "strong")
        self.assertGreater(pairs[0]["abs_r"], pairs[-1]["abs_r"])


class FormatAndDownsampleTests(SimpleTestCase):
    def test_pvalue_display(self):
        self.assertEqual(format_pvalue(None), "—")
        self.assertEqual(format_pvalue(0.0004), "< 0.001")
        self.assertEqual(format_pvalue(0.023), "0.02")

    def test_downsample_caps_points(self):
        idx = pd.date_range("2026-01-01", periods=1000, freq="min")
        df = pd.DataFrame({"co2": np.linspace(400, 900, 1000)}, index=idx)
        payload = downsample_series(df, max_points=100)
        self.assertIn("co2", payload)
        self.assertLessEqual(len(payload["co2"]["t"]), 100 + 1)
        self.assertEqual(len(payload["co2"]["t"]), len(payload["co2"]["v"]))


class PatternHelperTests(SimpleTestCase):
    def _frame(self):
        """Two weeks of hourly samples: Monday 08:00 is 100, everything else 10."""
        # 2026-01-05 is a Monday
        idx = pd.date_range("2026-01-05", periods=14 * 24, freq="h")
        values = []
        for ts in idx:
            if ts.weekday() == 0 and ts.hour == 8:
                values.append(100.0)
            else:
                values.append(10.0)
        return pd.DataFrame({"timestamp": idx, "value": values})

    def test_hourly_peak_at_eight(self):
        from dashboard.patterns import calendar_hour_means, hourly_profile, prepare_frame

        prepared = prepare_frame(calendar_hour_means(self._frame()))
        hourly = hourly_profile(prepared)
        peak = hourly.dropna(subset=["mean"]).sort_values("mean", ascending=False).iloc[0]
        self.assertEqual(int(peak["hour"]), 8)
        self.assertGreater(peak["mean"], 15)

    def test_weekday_peak_monday(self):
        from dashboard.patterns import calendar_hour_means, prepare_frame, weekday_profile

        prepared = prepare_frame(calendar_hour_means(self._frame()))
        weekly = weekday_profile(prepared)
        peak = weekly.dropna(subset=["mean"]).sort_values("mean", ascending=False).iloc[0]
        self.assertEqual(int(peak["weekday"]), 0)
        self.assertEqual(peak["day_name"], "Monday")

    def test_heatmap_shape(self):
        from dashboard.patterns import calendar_hour_means, prepare_frame, weekday_hour_matrices

        prepared = prepare_frame(calendar_hour_means(self._frame()))
        mean, count = weekday_hour_matrices(prepared)
        self.assertEqual(list(mean.index), [
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        ])
        self.assertEqual(list(mean.columns), [f"{h:02d}" for h in range(24)])
        self.assertEqual(mean.shape, (7, 24))
        self.assertGreater(mean.loc["Monday", "08"], mean.loc["Tuesday", "08"])
        self.assertGreater(int(count.loc["Monday", "08"]), 0)

    def test_calendar_hour_equal_weight(self):
        from dashboard.patterns import calendar_hour_means

        # Ten samples of 0 at 10:00, one sample of 100 at 11:00 — hourly means
        # should be 0 and 100, not a raw-sample bias toward 10:00.
        rows = [{"timestamp": "2026-01-05 10:00:00", "value": 0.0}] * 10
        rows.append({"timestamp": "2026-01-05 11:00:00", "value": 100.0})
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        hourly = calendar_hour_means(df)
        self.assertEqual(len(hourly), 2)
        by_hour = {ts.hour: val for ts, val in zip(hourly["timestamp"], hourly["value"])}
        self.assertAlmostEqual(by_hour[10], 0.0)
        self.assertAlmostEqual(by_hour[11], 100.0)

    def test_empty(self):
        from dashboard.patterns import (
            calendar_hour_means,
            hourly_profile,
            prepare_frame,
            summarize_patterns,
            weekday_hour_matrices,
            weekday_profile,
        )

        empty = pd.DataFrame(columns=["timestamp", "value"])
        prepared = prepare_frame(calendar_hour_means(empty))
        hourly = hourly_profile(prepared)
        weekly = weekday_profile(prepared)
        mean, count = weekday_hour_matrices(prepared)
        summary = summarize_patterns(hourly, weekly)
        self.assertEqual(len(hourly), 24)
        self.assertEqual(len(weekly), 7)
        self.assertTrue(mean.isna().all().all())
        self.assertEqual(summary["hour_bins"], 0)
        self.assertIsNone(summary["peak_hour"])

    def test_summarize(self):
        from dashboard.patterns import (
            calendar_hour_means,
            hourly_profile,
            prepare_frame,
            summarize_patterns,
            weekday_profile,
        )

        prepared = prepare_frame(calendar_hour_means(self._frame()))
        summary = summarize_patterns(hourly_profile(prepared), weekday_profile(prepared))
        self.assertEqual(summary["peak_hour"], "08:00")
        self.assertEqual(summary["peak_day"], "Monday")
        self.assertIsNotNone(summary["overall_mean"])

