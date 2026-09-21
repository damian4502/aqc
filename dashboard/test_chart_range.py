"""Tests for shared chart From/To session helpers (no database required)."""

from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from dashboard.chart_range import (
    SESSION_START_KEY,
    format_chart_datetime,
    load_chart_range_from_session,
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
        self.assertAlmostEqual((end - start).total_seconds(), 3600, delta=2)

        next_page = SimpleNamespace(session=request.session, GET={})
        start2, end2, _ = resolve_chart_range(next_page)
        self.assertEqual(format_chart_datetime(start2), format_chart_datetime(start))
        self.assertEqual(format_chart_datetime(end2), format_chart_datetime(end))

    def test_resolve_quick_days_persists(self):
        request = SimpleNamespace(session={}, GET={"quick": "7"})
        start, end, _ = resolve_chart_range(request)
        self.assertAlmostEqual((end - start).total_seconds(), 7 * 24 * 3600, delta=2)

        next_page = SimpleNamespace(session=request.session, GET={})
        start2, end2, _ = resolve_chart_range(next_page)
        self.assertEqual(format_chart_datetime(start2), format_chart_datetime(start))
        self.assertEqual(format_chart_datetime(end2), format_chart_datetime(end))

    def test_default_does_not_write_session(self):
        request = SimpleNamespace(session={}, GET={})
        start, end, all_data = resolve_chart_range(request)
        self.assertFalse(all_data)
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertNotIn(SESSION_START_KEY, request.session)
