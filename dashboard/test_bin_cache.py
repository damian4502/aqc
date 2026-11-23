"""Bin cache reuses closed resample buckets when a chart range grows."""

import unittest
from datetime import datetime

import pandas as pd

from dashboard.bin_cache import (
    _DictCache,
    day_section_key,
    floor_bin,
    invalidate_bins,
    label_frame,
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


def _hourly_rows(start, hours, room_id=1, parameter_id=5):
    rows = []
    origin = pd.Timestamp(start)
    for hour in range(hours):
        stamp = origin + pd.Timedelta(hours=hour, minutes=1)
        rows.append((stamp, float(hour), room_id, parameter_id))
    return rows


def _cache_reads(cache, prefix):
    return [key for batch in cache.reads for key in batch if str(key).startswith(prefix)]


class DaySectionTests(unittest.TestCase):
    def test_five_hours_reads_one_entry_per_hour(self):
        rows = _hourly_rows("2026-01-01 10:00", 6)
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        now = datetime(2026, 1, 3)
        start = datetime(2026, 1, 1, 10, 0)
        end = datetime(2026, 1, 1, 15, 0)
        first, _means = load_binned_pairs(
            [(1, 5)], start, end, 60, now=now, fetch_rows=fetch, cache_backend=cache
        )
        self.assertEqual(len(first), 5)
        self.assertFalse(any(key.startswith("aqc:day:") for key in cache.data))

        calls.clear()
        cache.reads.clear()
        second, _means = load_binned_pairs(
            [(1, 5)], start, end, 60, now=now, fetch_rows=fetch, cache_backend=cache
        )
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(len(_cache_reads(cache, "aqc:bins:")), 5)
        self.assertEqual(_cache_reads(cache, "aqc:day:"), [])
        self.assertTrue(all((call[1] - call[0]).total_seconds() < 1 for call in calls))

    def test_thirty_days_at_one_minute_reads_thirty_day_sections(self):
        rows = _hourly_rows("2026-01-01", 30 * 24)
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        now = datetime(2026, 2, 2)
        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 31)
        cold, _means = load_binned_pairs(
            [(1, 5)], start, end, 1, now=now, fetch_rows=fetch, cache_backend=cache
        )
        self.assertEqual(
            len([key for key in cache.data if key.startswith("aqc:day:v1:")]),
            30,
        )

        calls.clear()
        cache.reads.clear()
        warm, _means = load_binned_pairs(
            [(1, 5)], start, end, 1, now=now, fetch_rows=fetch, cache_backend=cache
        )
        pd.testing.assert_frame_equal(cold, warm)
        self.assertEqual(len(_cache_reads(cache, "aqc:day:")), 30)
        self.assertEqual(_cache_reads(cache, "aqc:bins:"), [])
        self.assertTrue(all((call[1] - call[0]).total_seconds() < 1 for call in calls))

        calls.clear()
        cache.reads.clear()
        load_binned_pairs(
            [(1, 5)],
            datetime(2026, 1, 10, 10, 0),
            datetime(2026, 1, 10, 15, 0),
            1,
            now=now,
            fetch_rows=fetch,
            cache_backend=cache,
        )
        self.assertEqual(len(_cache_reads(cache, "aqc:bins:")), 5)
        self.assertEqual(_cache_reads(cache, "aqc:day:"), [])

    def test_open_day_is_still_one_section(self):
        rows = _hourly_rows("2026-01-01", 31 * 24)
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        now = datetime(2026, 1, 31, 15, 20)
        start = datetime(2026, 1, 1)
        end = datetime(2026, 2, 15)
        load_binned_pairs(
            [(1, 5)], start, end, 1, now=now, fetch_rows=fetch, cache_backend=cache
        )
        calls.clear()
        cache.reads.clear()
        load_binned_pairs(
            [(1, 5)], start, end, 1, now=now, fetch_rows=fetch, cache_backend=cache
        )
        self.assertEqual(len(_cache_reads(cache, "aqc:day:")), 31)
        self.assertEqual(_cache_reads(cache, "aqc:bins:"), [])
        self.assertTrue(all((call[1] - call[0]).total_seconds() < 1 for call in calls))

    def test_partial_days_are_one_section_each(self):
        rows = _hourly_rows("2026-01-01", 31 * 24)
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        now = datetime(2026, 2, 2)
        start = datetime(2026, 1, 1, 10, 0)
        end = datetime(2026, 1, 31, 10, 0)
        load_binned_pairs(
            [(1, 5)], start, end, 60, now=now, fetch_rows=fetch, cache_backend=cache
        )
        calls.clear()
        cache.reads.clear()
        load_binned_pairs(
            [(1, 5)], start, end, 60, now=now, fetch_rows=fetch, cache_backend=cache
        )
        self.assertEqual(len(_cache_reads(cache, "aqc:day:")), 31)
        self.assertEqual(_cache_reads(cache, "aqc:bins:"), [])
        self.assertTrue(all((call[1] - call[0]).total_seconds() < 1 for call in calls))

    def test_invalidate_drops_the_day_section_and_refetches_that_hour(self):
        rows = _hourly_rows("2026-01-01", 48)
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        now = datetime(2026, 1, 5)
        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 3)
        load_binned_pairs(
            [(1, 5)], start, end, 60, now=now, fetch_rows=fetch, cache_backend=cache
        )
        self.assertIn(day_section_key(1, 5, 60, "2026-01-01"), cache.data)
        self.assertIn(day_section_key(1, 5, 60, "2026-01-02"), cache.data)

        invalidate_bins(1, 5, datetime(2026, 1, 1, 12, 0), cache_backend=cache)
        self.assertNotIn(day_section_key(1, 5, 60, "2026-01-01"), cache.data)
        self.assertIn(day_section_key(1, 5, 60, "2026-01-02"), cache.data)

        calls.clear()
        again, _means = load_binned_pairs(
            [(1, 5)], start, end, 60, now=now, fetch_rows=fetch, cache_backend=cache
        )
        self.assertEqual(len(again), 48)
        fetched = {(call[0], call[1]) for call in calls}
        self.assertIn(
            (
                pd.Timestamp("2026-01-01 12:00", tz="Europe/Ljubljana"),
                pd.Timestamp("2026-01-01 13:00", tz="Europe/Ljubljana"),
            ),
            fetched,
        )
        self.assertNotIn(
            (
                pd.Timestamp("2026-01-02 00:00", tz="Europe/Ljubljana"),
                pd.Timestamp("2026-01-03 00:00", tz="Europe/Ljubljana"),
            ),
            fetched,
        )
        self.assertIn(day_section_key(1, 5, 60, "2026-01-01"), cache.data)


class ChartTimingTests(unittest.TestCase):
    def test_bins_log_names_the_slow_stages(self):
        rows = _rows()
        calls = []
        cache = _DictCache()
        fetch = _fetch_factory(rows, calls)
        with self.assertLogs("aqc.chart", level="INFO") as caught:
            load_binned_pairs(
                [(1, 5)],
                datetime(2026, 1, 1, 13, 0),
                datetime(2026, 1, 1, 15, 0),
                60,
                now=datetime(2026, 1, 2),
                fetch_rows=fetch,
                cache_backend=cache,
            )
        line = "\n".join(caught.output)
        self.assertIn("chart bins", line)
        self.assertIn("slowest=", line)
        for stage in ("plan=", "hour_get=", "hour_decode=", "db_fetch=", "aggregate=", "store=", "frame="):
            self.assertIn(stage, line)


class LabelFrameTests(unittest.TestCase):
    def test_duplicate_pairs_do_not_add_a_parenthetical_series(self):
        index = pd.to_datetime(["2026-01-01 13:00", "2026-01-01 14:00"])
        keyed = pd.DataFrame({pair_key(1, 2): [50.0, 51.0]}, index=index)
        pairs = [(1, 2), (1, 2), (1, 2)]
        frame = label_frame(keyed, pairs, lambda room_id, parameter_id: "Kopalnica")
        self.assertEqual(list(frame.columns), ["Kopalnica"])


if __name__ == "__main__":
    unittest.main()
