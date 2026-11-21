"""Bin cache reuses closed resample buckets when a chart range grows."""

import unittest
from datetime import datetime

import pandas as pd

from dashboard.bin_cache import (
    _DictCache,
    floor_bin,
    load_binned_pairs,
    local_to_aware,
)
from dashboard.series_selection import pair_key


def _rows():
    stamps = pd.date_range("2026-01-01 13:00", "2026-01-01 16:00", freq="min")
    rows = []
    for stamp in stamps:
        rows.append((pd.Timestamp(stamp), float(stamp.minute or 60), 1, 5))
    return rows


def _fetch_factory(rows, calls):
    def fetch(pairs, start, end):
        calls.append((pd.Timestamp(start), pd.Timestamp(end), tuple(pairs)))
        kept = []
        for timestamp, value, room_id, parameter_id in rows:
            aware = local_to_aware(timestamp)
            if start <= aware < end:
                kept.append((timestamp, value, room_id, parameter_id))
        return kept

    return fetch


class FloorBinTests(unittest.TestCase):
    def test_floor_matches_pandas_resample(self):
        stamps = pd.date_range("2026-01-01 00:03", periods=40, freq="7min")
        frame = pd.DataFrame({"value": range(len(stamps))}, index=stamps)
        for interval, freq in ((1, "1min"), (5, "5min"), (15, "15min"), (60, "h"), (1440, "D")):
            for stamp in frame.resample(freq).mean().index:
                self.assertEqual(floor_bin(stamp, interval), pd.Timestamp(stamp))
                self.assertEqual(
                    floor_bin(stamp + pd.Timedelta(seconds=30), interval),
                    pd.Timestamp(stamp),
                )


class BinCacheTests(unittest.TestCase):
    def test_expanded_range_reuses_closed_hours(self):
        rows = _rows()
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        start = datetime(2026, 1, 1, 13, 0)
        first_end = datetime(2026, 1, 1, 15, 0)
        now = datetime(2026, 1, 2, 0, 0)
        frame, means = load_binned_pairs(
            [(1, 5)],
            start,
            first_end,
            60,
            now=now,
            fetch_rows=fetch,
            cache_backend=cache,
        )
        # 15:00 itself sits in the next bin, so the closed hours are 13 and 14
        # and the endpoint is a one-instant partial bin.
        self.assertEqual(
            list(frame.index),
            [
                pd.Timestamp("2026-01-01 13:00"),
                pd.Timestamp("2026-01-01 14:00"),
                pd.Timestamp("2026-01-01 15:00"),
            ],
        )
        self.assertAlmostEqual(float(frame.iloc[0, 0]), 30.5)
        self.assertAlmostEqual(float(frame.iloc[1, 0]), 30.5)
        self.assertAlmostEqual(float(means[pair_key(1, 5)]), (30.5 * 120 + 60) / 121)

        calls.clear()
        frame, _means = load_binned_pairs(
            [(1, 5)],
            start,
            datetime(2026, 1, 1, 16, 0),
            60,
            now=now,
            fetch_rows=fetch,
            cache_backend=cache,
        )
        self.assertEqual(len(frame), 4)
        fetched = {(call[0], call[1]) for call in calls}
        self.assertIn(
            (
                pd.Timestamp("2026-01-01 15:00", tz="Europe/Ljubljana"),
                pd.Timestamp("2026-01-01 16:00", tz="Europe/Ljubljana"),
            ),
            fetched,
        )
        self.assertNotIn(
            (
                pd.Timestamp("2026-01-01 13:00", tz="Europe/Ljubljana"),
                pd.Timestamp("2026-01-01 15:00", tz="Europe/Ljubljana"),
            ),
            fetched,
        )
        self.assertTrue(all(call[0] >= pd.Timestamp("2026-01-01 15:00", tz="Europe/Ljubljana") for call in calls))

    def test_partial_edge_does_not_overwrite_a_full_bin(self):
        rows = _rows()
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        now = datetime(2026, 1, 2, 0, 0)
        partial, _means = load_binned_pairs(
            [(1, 5)],
            datetime(2026, 1, 1, 13, 30),
            datetime(2026, 1, 1, 14, 0),
            60,
            now=now,
            fetch_rows=fetch,
            cache_backend=cache,
        )
        self.assertEqual(len(cache.data), 0)
        self.assertAlmostEqual(float(partial.iloc[0, 0]), sum(range(30, 60)) / 30)

        full, _means = load_binned_pairs(
            [(1, 5)],
            datetime(2026, 1, 1, 13, 0),
            datetime(2026, 1, 1, 14, 0),
            60,
            now=now,
            fetch_rows=fetch,
            cache_backend=cache,
        )
        self.assertAlmostEqual(float(full.iloc[0, 0]), (sum(range(1, 60)) + 60) / 60)
        stored = next(iter(cache.data.values()))["bins"]["20260101T1300"]
        self.assertAlmostEqual(stored[0] / stored[1], float(full.iloc[0, 0]))

        calls.clear()
        again, _means = load_binned_pairs(
            [(1, 5)],
            datetime(2026, 1, 1, 13, 30),
            datetime(2026, 1, 1, 14, 0),
            60,
            now=now,
            fetch_rows=fetch,
            cache_backend=cache,
        )
        self.assertAlmostEqual(float(again.iloc[0, 0]), sum(range(30, 60)) / 30)
        self.assertTrue(calls)
        self.assertAlmostEqual(next(iter(cache.data.values()))["bins"]["20260101T1300"][0] / stored[1], float(full.iloc[0, 0]))

    def test_empty_hour_is_cached_and_not_queried_again(self):
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory([], calls)
        now = datetime(2026, 1, 2, 0, 0)
        frame, means = load_binned_pairs(
            [(1, 5)],
            datetime(2026, 1, 1, 13, 0),
            datetime(2026, 1, 1, 14, 0),
            60,
            now=now,
            fetch_rows=fetch,
            cache_backend=cache,
        )
        self.assertTrue(frame.empty)
        self.assertEqual(means, {})
        self.assertIn("20260101T1300", next(iter(cache.data.values()))["bins"])
        self.assertIsNone(next(iter(cache.data.values()))["bins"]["20260101T1300"])
        calls.clear()
        load_binned_pairs(
            [(1, 5)],
            datetime(2026, 1, 1, 13, 0),
            datetime(2026, 1, 1, 14, 0),
            60,
            now=now,
            fetch_rows=fetch,
            cache_backend=cache,
        )
        # The closed empty hour stays cached. Only the 14:00 instant is partial.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], pd.Timestamp("2026-01-01 14:00", tz="Europe/Ljubljana"))
        self.assertLess((calls[0][1] - calls[0][0]).total_seconds(), 0.001)

    def test_open_bin_ignores_a_full_cached_hour(self):
        rows = [
            (pd.Timestamp("2026-01-01 13:10"), 10.0, 1, 5),
            (pd.Timestamp("2026-01-01 13:40"), 100.0, 1, 5),
        ]
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        frame, means = load_binned_pairs(
            [(1, 5)],
            datetime(2026, 1, 1, 13, 0),
            datetime(2026, 1, 1, 18, 0),
            60,
            now=datetime(2026, 1, 1, 13, 30),
            fetch_rows=fetch,
            cache_backend=cache,
        )
        self.assertAlmostEqual(float(frame.iloc[0, 0]), 10.0)
        self.assertAlmostEqual(means[pair_key(1, 5)], 10.0)
        self.assertEqual(len(cache.data), 0)


if __name__ == "__main__":
    unittest.main()
