"""Resampling helpers, including the no-binning (all recorded values) mode."""

from datetime import datetime

from django.test import SimpleTestCase
import pandas as pd

from dashboard.compare import build_compare_figure
from dashboard.views import (
    build_time_series_figure,
    is_raw_interval,
    parse_interval_minutes,
    parse_spike_ceiling,
    resample_measurements,
)


def _frame():
    return pd.DataFrame(
        {
            "timestamp": [
                datetime(2026, 1, 1, 10, 0, 5),
                datetime(2026, 1, 1, 10, 2, 5),
                datetime(2026, 1, 1, 10, 0, 5),
                datetime(2026, 1, 1, 10, 7, 40),
            ],
            "value": [10.0, 30.0, 12.0, 40.0],
            "parameter": ["CO2", "CO2", "CO2", "Temp"],
        }
    )


class IntervalParseTests(SimpleTestCase):
    def test_zero_is_raw_not_default(self):
        self.assertTrue(is_raw_interval(0))
        self.assertTrue(is_raw_interval("0"))
        self.assertEqual(parse_interval_minutes(0, default=15), 0)
        self.assertEqual(parse_interval_minutes("0", default=15), 0)
        self.assertEqual(parse_interval_minutes("", default=15), 15)
        self.assertEqual(parse_interval_minutes(None, default=15), 15)
        self.assertFalse(is_raw_interval(15))


class RawResampleTests(SimpleTestCase):
    def test_raw_keeps_every_timestamp(self):
        out = resample_measurements(_frame(), interval_minutes=0, fill_method="ffill")
        self.assertEqual(list(out.columns), ["CO2", "Temp"])
        # Two CO2 stamps stay separate; the duplicate 10:00:05 is averaged.
        self.assertEqual(len(out), 3)
        co2 = out["CO2"].dropna()
        self.assertEqual(len(co2), 2)
        self.assertAlmostEqual(float(co2.iloc[0]), 11.0)
        self.assertAlmostEqual(float(co2.iloc[1]), 30.0)
        # Forward fill must not invent a CO2 value at the Temp-only stamp.
        self.assertTrue(pd.isna(out["CO2"].iloc[-1]))

    def test_ceiling_clips_values_the_iqr_filter_keeps(self):
        frame = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01", periods=6, freq="5min"),
                "value": [1.0, 2.0, 216.0, 730.0, 3310.0, 1.5],
                "parameter": ["PM10"] * 6,
            }
        )
        out = resample_measurements(
            frame, interval_minutes=5, fill_method="ffill", spike_factor=1.5, spike_ceiling=10
        )
        self.assertLessEqual(float(out["PM10"].max()), 10.0)
        self.assertAlmostEqual(float(out["PM10"].iloc[0]), 1.0)

    def test_ceiling_alone_clips_and_blank_is_off(self):
        frame = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01", periods=3, freq="5min"),
                "value": [1.0, 40.0, 2.0],
                "parameter": ["PM10"] * 3,
            }
        )
        capped = resample_measurements(
            frame, interval_minutes=5, fill_method="ffill", spike_factor=0, spike_ceiling="10,5"
        )
        self.assertAlmostEqual(float(capped["PM10"].max()), 10.5)
        self.assertAlmostEqual(float(capped["PM10"].iloc[0]), 1.0)
        open_ended = resample_measurements(
            frame, interval_minutes=5, fill_method="ffill", spike_factor=0, spike_ceiling=""
        )
        self.assertAlmostEqual(float(open_ended["PM10"].max()), 40.0)
        self.assertEqual(parse_spike_ceiling("0"), 0.0)
        self.assertEqual(parse_spike_ceiling("nope"), 0.0)

    def test_fifteen_minutes_still_bins(self):
        out = resample_measurements(_frame(), interval_minutes=15, fill_method="ffill")
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out["CO2"].iloc[0]), 20.5)

    def test_raw_figure_uses_each_series_own_timestamps(self):
        out = resample_measurements(_frame(), interval_minutes=0, fill_method="none")
        fig = build_time_series_figure(out, title="t", raw=True)
        self.assertEqual(len(fig.data), 2)
        self.assertEqual(len(fig.data[0].x), 2)
        self.assertEqual(len(fig.data[1].x), 1)

    def test_compare_raw_timeline_drops_gaps(self):
        idx = pd.to_datetime(
            ["2026-01-01 10:00:05", "2026-01-01 10:07:40"]
        )
        plotted = pd.DataFrame({"CO2": [11.0, pd.NA], "Temp": [pd.NA, 40.0]}, index=idx)
        fig = build_compare_figure(plotted, raw=plotted, individual_timeline=True)
        self.assertEqual(len(list(fig.data[0].x)), 1)
        self.assertEqual(len(list(fig.data[1].x)), 1)
