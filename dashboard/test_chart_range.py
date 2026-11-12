"""Tests for shared chart From/To session helpers (no database required)."""

from types import SimpleNamespace
from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from dashboard.chart_range import (
    SESSION_START_KEY,
    active_quick_key,
    format_chart_datetime,
    load_chart_range_from_session,
    load_chart_range_mode,
    parse_chart_datetime,
    resolve_chart_range,
    save_chart_range_to_session,
)


class ChartRangeTests(SimpleTestCase):
    def test_parse_and_format_datetime_local(self):
        dt = parse_chart_datetime("2026-09-21T13:00")
        self.assertIsNotNone(dt)
        self.assertTrue(timezone.is_aware(dt))
        self.assertEqual(format_chart_datetime(dt), "2026-09-21T13:00")

    def test_parse_invalid_returns_none(self):
        self.assertIsNone(parse_chart_datetime(""))
        self.assertIsNone(parse_chart_datetime("not-a-date"))

    def test_save_and_load_session(self):
        start = parse_chart_datetime("2026-09-21T13:00")
        end = parse_chart_datetime("2026-09-21T14:00")
        request = SimpleNamespace(session={})
        save_chart_range_to_session(request, start, end)
        loaded_start, loaded_end = load_chart_range_from_session(request)
        self.assertEqual(format_chart_datetime(loaded_start), "2026-09-21T13:00")
        self.assertEqual(format_chart_datetime(loaded_end), "2026-09-21T14:00")

    def test_resolve_get_start_end_persists_for_next_page(self):
        request = SimpleNamespace(
            session={},
            GET={"start": "2026-09-21T13:00", "end": "2026-09-21T14:00"},
        )
        start, end, all_data = resolve_chart_range(request)
        self.assertFalse(all_data)
        self.assertEqual(format_chart_datetime(start), "2026-09-21T13:00")
        self.assertEqual(format_chart_datetime(end), "2026-09-21T14:00")

        next_page = SimpleNamespace(session=request.session, GET={})
        start2, end2, _ = resolve_chart_range(next_page)
        self.assertEqual(format_chart_datetime(start2), "2026-09-21T13:00")
        self.assertEqual(format_chart_datetime(end2), "2026-09-21T14:00")

    def test_resolve_quick_hours_persists_absolute_range(self):
        request = SimpleNamespace(session={}, GET={"quickh": "1"})
        start, end, _ = resolve_chart_range(request)
        # 1h back, end one year ahead (live mode)
        self.assertAlmostEqual(
            (end - start).total_seconds(),
            365 * 24 * 3600 + 3600,
            delta=5,
        )
        self.assertEqual(request.session["chart_range_mode"], "live")

        next_page = SimpleNamespace(session=request.session, GET={})
        start2, end2, _ = resolve_chart_range(next_page)
        self.assertEqual(format_chart_datetime(start2), format_chart_datetime(start))
        self.assertEqual(format_chart_datetime(end2), format_chart_datetime(end))

    def test_resolve_quick_days_persists(self):
        request = SimpleNamespace(session={}, GET={"quick": "7"})
        start, end, _ = resolve_chart_range(request)
        self.assertAlmostEqual(
            (end - start).total_seconds(),
            365 * 24 * 3600 + 7 * 24 * 3600,
            delta=5,
        )
        self.assertEqual(request.session["chart_range_mode"], "live")

        next_page = SimpleNamespace(session=request.session, GET={})
        start2, end2, _ = resolve_chart_range(next_page)
        self.assertEqual(format_chart_datetime(start2), format_chart_datetime(start))
        self.assertEqual(format_chart_datetime(end2), format_chart_datetime(end))

    def test_explicit_range_mode_is_shared(self):
        request = SimpleNamespace(
            session={},
            GET={
                "start": "2026-09-21T13:00",
                "end": "2027-09-21T13:00",
                "range_mode": "live",
            },
        )
        resolve_chart_range(request)
        self.assertEqual(request.session["chart_range_mode"], "live")
        self.assertEqual(load_chart_range_mode(request), "live")

    def test_default_does_not_write_session(self):
        request = SimpleNamespace(session={}, GET={})
        start, end, all_data = resolve_chart_range(request)
        self.assertFalse(all_data)
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertNotIn(SESSION_START_KEY, request.session)

    def test_explicit_start_end_beats_sticky_quick_flag(self):
        request = SimpleNamespace(
            session={},
            GET={
                "start": "2026-09-01T00:00",
                "end": "2026-09-08T00:00",
                "quick": "1",
            },
        )
        start, end, all_data = resolve_chart_range(request)
        self.assertFalse(all_data)
        self.assertEqual(format_chart_datetime(start), "2026-09-01T00:00")
        self.assertEqual(format_chart_datetime(end), "2026-09-08T00:00")
        self.assertEqual(active_quick_key(start, end, all_data), "7d")

    def test_active_quick_key_follows_the_span(self):
        start = parse_chart_datetime("2026-09-01T00:00")
        day = parse_chart_datetime("2026-09-02T00:00")
        custom = parse_chart_datetime("2026-09-01T10:00")
        self.assertEqual(active_quick_key(start, day), "24h")
        self.assertEqual(active_quick_key(start, custom), "")
        self.assertEqual(active_quick_key(start, day, all_data=True), "all")

    def test_active_quick_key_ignores_live_horizon(self):
        start = parse_chart_datetime("2026-09-21T13:00")
        end = start + timedelta(hours=1) + timedelta(days=365)
        self.assertEqual(active_quick_key(start, end, live=True), "1h")
        self.assertEqual(active_quick_key(start, end, live=False), "")
