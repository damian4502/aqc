"""Shared From/To chart range parsing and Django session persistence.

Room and parameter detail pages share one session range so navigating
between them keeps the same From/To (including ranges set via the
1h/4h/8h/... quick buttons).
"""

from datetime import datetime, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

SESSION_START_KEY = "chart_start_datetime"
SESSION_END_KEY = "chart_end_datetime"
SESSION_MODE_KEY = "chart_range_mode"
LEGACY_START_KEY = "chart_start_date"
LEGACY_END_KEY = "chart_end_date"

# Matches the "All" quick button on the detail pages.
ALL_DATA_START = "2020-01-01T00:00"
# Live mode keeps the query window open so the chart can keep polling.
LIVE_HORIZON = timedelta(days=365)


def format_chart_datetime(dt):
    """Format a datetime for datetime-local inputs and session storage."""
    if not dt:
        return ""
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime("%Y-%m-%dT%H:%M")


def parse_chart_datetime(value):
    """Parse a datetime-local / ISO / date-only string into an aware datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        dt = parse_datetime(raw)
        if dt is None:
            try:
                dt = datetime.strptime(raw[:16].replace("T", " "), "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                try:
                    dt = datetime.strptime(raw[:10], "%Y-%m-%d")
                except (ValueError, TypeError):
                    return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def normalize_range_mode(value):
    """Return 'live' or 'fixed'. Anything else is a fixed From/To range."""
    return "live" if str(value or "").strip().lower() == "live" else "fixed"


def save_chart_range_mode(request, mode):
    request.session[SESSION_MODE_KEY] = normalize_range_mode(mode)


def load_chart_range_mode(request):
    session = getattr(request, "session", None) or {}
    raw = session.get(SESSION_MODE_KEY)
    if raw in ("live", "fixed"):
        return raw
    return "fixed"


def live_horizon_end(now=None):
    """End timestamp used by From–Now so the chart stays in the future."""
    return (now or timezone.now()) + LIVE_HORIZON


def save_chart_range_to_session(request, start, end):
    """Store the chart From/To range on the Django session."""
    if not start or not end:
        return
    start_s = format_chart_datetime(start)
    end_s = format_chart_datetime(end)
    if not start_s or not end_s or start_s > end_s:
        return
    request.session[SESSION_START_KEY] = start_s
    request.session[SESSION_END_KEY] = end_s
    # Keep the older date-only keys in sync for any other readers.
    request.session[LEGACY_START_KEY] = start_s[:10]
    request.session[LEGACY_END_KEY] = end_s[:10]


def load_chart_range_from_session(request):
    """Return (start, end) from session, or (None, None) if missing/invalid."""
    session = getattr(request, "session", None) or {}
    start = parse_chart_datetime(session.get(SESSION_START_KEY))
    end = parse_chart_datetime(session.get(SESSION_END_KEY))
    if start and end and start <= end:
        return start, end

    start = parse_chart_datetime(session.get(LEGACY_START_KEY))
    end = parse_chart_datetime(session.get(LEGACY_END_KEY))
    if start and end and start <= end:
        return start, end
    return None, None


def resolve_chart_range(request, default_hours=24):
    """Resolve (start, end, all_data) from GET, then session, then default.

    Explicit GET values (start/end, quick, quickh, all) are written to the
    session so the next room/parameter page can restore them.
    """
    get = request.GET
    all_data = get.get("all") == "true" or get.get("all_data") == "true"
    start_str = get.get("start")
    end_str = get.get("end")
    quick_days = get.get("quick")
    quick_hours = get.get("quickh")

    start = end = None

    if all_data:
        end = live_horizon_end()
        start = parse_chart_datetime(ALL_DATA_START)
        save_chart_range_to_session(request, start, end)
        save_chart_range_mode(request, "live")
        return start, end, True

    elif start_str and end_str:
        start = parse_chart_datetime(start_str)
        end = parse_chart_datetime(end_str)
        mode = get.get("range_mode")
        if mode:
            save_chart_range_mode(request, mode)
    elif quick_days not in (None, ""):
        try:
            days = int(quick_days)
            if days == 0:
                start = parse_chart_datetime(ALL_DATA_START)
            else:
                start = timezone.now() - timedelta(days=days)
            end = live_horizon_end()
            save_chart_range_mode(request, "live")
        except (TypeError, ValueError):
            start = end = None
    elif quick_hours not in (None, ""):
        try:
            hours = int(quick_hours)
            start = timezone.now() - timedelta(hours=hours)
            end = live_horizon_end()
            save_chart_range_mode(request, "live")
        except (TypeError, ValueError):
            start = end = None

    if start and end and start <= end:
        save_chart_range_to_session(request, start, end)
        return start, end, False

    saved_start, saved_end = load_chart_range_from_session(request)
    if saved_start and saved_end:
        return saved_start, saved_end, False

    end = timezone.now()
    start = end - timedelta(hours=default_hours)
    return start, end, False


# Presets shown on the quick-range buttons. Tolerance covers minute rounding.
_QUICK_PRESETS = (
    ("1h", 1 * 3600),
    ("4h", 4 * 3600),
    ("8h", 8 * 3600),
    ("24h", 24 * 3600),
    ("7d", 7 * 24 * 3600),
    ("30d", 30 * 24 * 3600),
    ("90d", 90 * 24 * 3600),
)


def active_quick_key(start, end, all_data=False, live=False) -> str:
    """Which quick button matches the resolved window, or '' if none does.

    Highlighting follows the actual From/To span, not a sticky ``quick`` flag.
    In live mode the stored end is one year ahead, so the span is measured
    back from that horizon to the moment the range was chosen.
    """
    if all_data:
        return "all"
    if not start or not end:
        return ""
    if live:
        end = end - LIVE_HORIZON
        if end <= start:
            return ""
    try:
        seconds = abs((end - start).total_seconds())
    except (TypeError, ValueError, AttributeError):
        return ""
    best_key = ""
    best_delta = None
    for key, expected in _QUICK_PRESETS:
        # Day buttons can be an hour off across a daylight-saving change.
        tolerance = 90 * 60 if expected >= 24 * 3600 else 90
        delta = abs(seconds - expected)
        if delta <= tolerance and (best_delta is None or delta < best_delta):
            best_key = key
            best_delta = delta
    return best_key

