"""Per-bin means for chart resampling.

Ranges are minute-precise and almost never repeat, so a cache entry for a whole
From/To window would miss every time. Closed resample bins are reused instead.
Sub-hour bins are packed into the clock hour so memcached is not one key per
minute. The value is the sum and count of recorded points. Spike removal,
ceiling, and gap fill depend on the requested window, so they run after the
bins are assembled.

A bin clipped by the request, and the bin that contains "now", is not taken
from that full-bin cache: it would include points outside the request or miss
samples that just arrived. Closed packs live for 7 days. The pack that contains
now expires in 30 seconds, and a Measurement write drops the packs it touches.
"""

from __future__ import annotations

import math

import pandas as pd

LOCAL_TZ = "Europe/Ljubljana"
CLOSED_TTL = 7 * 24 * 60 * 60
OPEN_TTL = 30
KNOWN_INTERVALS = (1, 5, 15, 60, 1440)
_GET_CHUNK = 200


class _DictCache:
    """In-memory stand-in used by tests."""

    def __init__(self):
        self.data = {}

    def get_many(self, keys):
        return {key: self.data[key] for key in keys if key in self.data}

    def set(self, key, value, timeout=None):
        self.data[key] = value

    def delete_many(self, keys):
        for key in keys:
            self.data.pop(key, None)


def normalize_interval(interval_minutes):
    """Snap to a chart interval. Unknown sizes follow the 15-minute resample."""
    try:
        interval = int(interval_minutes)
    except (TypeError, ValueError):
        return 15
    if interval <= 0:
        return 0
    if interval not in KNOWN_INTERVALS:
        return 15
    return interval


def to_local_naive(timestamp):
    """Europe/Ljubljana wall time without tz, matching resample_measurements."""
    stamp = pd.Timestamp(timestamp)
    if stamp.tzinfo is None:
        return stamp
    return stamp.tz_convert(LOCAL_TZ).tz_localize(None)


def local_to_aware(timestamp):
    """Local naive wall time as an aware datetime for an indexed query."""
    stamp = pd.Timestamp(timestamp)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(LOCAL_TZ)
    else:
        stamp = stamp.tz_convert(LOCAL_TZ)
    return stamp.to_pydatetime()


def floor_bin(timestamp, interval_minutes):
    """Left edge of the resample bin. Origin is local midnight, label left."""
    stamp = pd.Timestamp(timestamp)
    interval = int(interval_minutes)
    if interval >= 1440:
        return stamp.normalize()
    if interval == 60:
        return stamp.floor("h")
    midnight = stamp.normalize()
    minutes = int((stamp - midnight) / pd.Timedelta(minutes=1))
    floored = (minutes // interval) * interval
    return midnight + pd.Timedelta(minutes=floored)


def bin_end(bin_start, interval_minutes):
    start = pd.Timestamp(bin_start)
    if int(interval_minutes) >= 1440:
        return start + pd.Timedelta(days=1)
    return start + pd.Timedelta(minutes=int(interval_minutes))


def _step(interval_minutes):
    if int(interval_minutes) >= 1440:
        return pd.Timedelta(days=1)
    return pd.Timedelta(minutes=int(interval_minutes))


def pack_start_of(bin_start, interval_minutes):
    """Hour that holds a sub-hour bin. Hour and day bins are their own pack."""
    stamp = pd.Timestamp(bin_start)
    if int(interval_minutes) >= 1440:
        return stamp.normalize()
    return stamp.floor("h")


def pack_end_of(pack_start, interval_minutes):
    start = pd.Timestamp(pack_start)
    if int(interval_minutes) >= 1440:
        return start + pd.Timedelta(days=1)
    return start + pd.Timedelta(hours=1)


def pack_key(room_id, parameter_id, interval_minutes, pack_start):
    label = pd.Timestamp(pack_start).strftime("%Y%m%dT%H%M")
    return f"aqc:bins:v1:{int(room_id)}:{int(parameter_id)}:{int(interval_minutes)}:{label}"


def bin_label(bin_start):
    return pd.Timestamp(bin_start).strftime("%Y%m%dT%H%M")


def _pack_bins(pack_start, interval_minutes):
    start = pd.Timestamp(pack_start)
    end = pack_end_of(start, interval_minutes)
    step = _step(interval_minutes)
    bins = []
    cursor = start
    while cursor < end:
        bins.append(cursor)
        cursor = cursor + step
    return bins


def _first_complete(start, end, interval):
    """First bin fully inside ``[start, end]``, or None."""
    cursor = floor_bin(start, interval)
    if cursor < start:
        cursor = bin_end(cursor, interval)
    if bin_end(cursor, interval) <= end:
        return cursor
    return None


def _last_complete(start, end, interval):
    """Last bin whose end is still inside ``end``."""
    cursor = floor_bin(end, interval)
    if bin_end(cursor, interval) > end:
        cursor = cursor - _step(interval)
    if cursor >= start and bin_end(cursor, interval) <= end:
        return cursor
    return None


def _bins_between(first, last, interval):
    step = _step(interval)
    cursor = pd.Timestamp(first)
    last = pd.Timestamp(last)
    while cursor <= last:
        yield cursor
        cursor = cursor + step


def _iter_packs(first_bin, last_bin, interval):
    step = pd.Timedelta(days=1) if int(interval) >= 1440 else pd.Timedelta(hours=1)
    cursor = pack_start_of(first_bin, interval)
    last = pack_start_of(last_bin, interval)
    while cursor <= last:
        yield cursor
        cursor = cursor + step


def _is_complete(bin_start, interval, start, end):
    return pd.Timestamp(bin_start) >= start and bin_end(bin_start, interval) <= end


def invalidate_bins(room_id, parameter_id, timestamp, cache_backend=None):
    """Drop every pack that contains this sample, for each chart interval."""
    if room_id is None or parameter_id is None or timestamp is None:
        return
    local = to_local_naive(timestamp)
    backend = cache_backend if cache_backend is not None else _django_cache()
    keys = []
    for interval in KNOWN_INTERVALS:
        pack = pack_start_of(floor_bin(local, interval), interval)
        keys.append(pack_key(room_id, parameter_id, interval, pack))
    try:
        backend.delete_many(keys)
    except Exception:
        return


def _django_cache():
    from django.core.cache import cache

    return cache


def _get_many(backend, keys):
    found = {}
    keys = list(keys)
    for offset in range(0, len(keys), _GET_CHUNK):
        chunk = keys[offset:offset + _GET_CHUNK]
        try:
            found.update(backend.get_many(chunk) or {})
        except Exception:
            continue
    return found


def _fetch_rows(pairs, start, end):
    """Recorded rows in ``[start, end)``. ``start`` and ``end`` are aware."""
    from dashboard.series_selection import pairs_filter
    from measurements.models import Measurement

    if not pairs or start is None or end is None or end <= start:
        return []
    return list(
        Measurement.objects.filter(
            pairs_filter(pairs),
            timestamp__gte=start,
            timestamp__lt=end,
        ).values_list("timestamp", "value", "sensor__room_id", "parameter_id")
    )


def _resolve_bounds(pairs, start, end):
    if start is not None and end is not None:
        return start, end
    from django.db.models import Max, Min

    from dashboard.series_selection import pairs_filter
    from measurements.models import Measurement

    if not pairs:
        return start, end
    query = Measurement.objects.filter(pairs_filter(pairs))
    if start is not None:
        query = query.filter(timestamp__gte=start)
    if end is not None:
        query = query.filter(timestamp__lte=end)
    found = query.aggregate(start=Min("timestamp"), end=Max("timestamp"))
    return start or found["start"], end or found["end"]


def _aggregate(rows, interval):
    totals = {}
    for timestamp, value, room_id, parameter_id in rows:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        bin_start = floor_bin(to_local_naive(timestamp), interval)
        key = (int(room_id), int(parameter_id), pd.Timestamp(bin_start))
        bucket = totals.get(key)
        if bucket is None:
            totals[key] = [number, 1]
        else:
            bucket[0] += number
            bucket[1] += 1
    return totals


def _mean_frame(totals, pairs):
    from dashboard.series_selection import pair_key

    wanted = {(int(room_id), int(parameter_id)) for room_id, parameter_id in pairs}
    columns = {}
    for (room_id, parameter_id, bin_start), (total, count) in totals.items():
        if (room_id, parameter_id) not in wanted or not count:
            continue
        key = pair_key(room_id, parameter_id)
        columns.setdefault(key, {})[pd.Timestamp(bin_start)] = total / count
    if not columns:
        return pd.DataFrame()
    frame = pd.DataFrame(columns)
    frame.index = pd.DatetimeIndex(frame.index)
    frame = frame.sort_index()
    frame.index.name = "timestamp"
    return frame


def _raw_means(totals, pairs):
    """Mean of the recorded points that landed in the included bins."""
    from dashboard.series_selection import pair_key

    wanted = {(int(room_id), int(parameter_id)) for room_id, parameter_id in pairs}
    sums = {}
    counts = {}
    for (room_id, parameter_id, _bin_start), (total, count) in totals.items():
        if (room_id, parameter_id) not in wanted or not count:
            continue
        key = pair_key(room_id, parameter_id)
        sums[key] = sums.get(key, 0.0) + float(total)
        counts[key] = counts.get(key, 0) + int(count)
    return {key: sums[key] / counts[key] for key in sums if counts.get(key)}


def _raw_pivot(rows, pairs):
    from dashboard.series_selection import pair_key

    grouped = {}
    sums = {}
    counts = {}
    for timestamp, value, room_id, parameter_id in rows:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        key = pair_key(int(room_id), int(parameter_id))
        stamp = to_local_naive(timestamp)
        bucket = grouped.setdefault(key, {}).setdefault(stamp, [0.0, 0])
        bucket[0] += number
        bucket[1] += 1
        sums[key] = sums.get(key, 0.0) + number
        counts[key] = counts.get(key, 0) + 1
    columns = {
        key: pd.Series({stamp: total / count for stamp, (total, count) in stamps.items()})
        for key, stamps in grouped.items()
    }
    if not columns:
        return pd.DataFrame(), {}
    frame = pd.DataFrame(columns).sort_index()
    frame.index = pd.DatetimeIndex(frame.index)
    frame.index.name = "timestamp"
    means = {key: sums[key] / counts[key] for key in sums if counts.get(key)}
    return frame, means


def _store_pack(backend, room_id, parameter_id, interval, pack_start, fresh, timeout):
    stored_bins = {}
    for bin_start in _pack_bins(pack_start, interval):
        found = fresh.get((room_id, parameter_id, pd.Timestamp(bin_start)))
        stored_bins[bin_label(bin_start)] = None if not found else [found[0], found[1]]
    try:
        backend.set(
            pack_key(room_id, parameter_id, interval, pack_start),
            {"bins": stored_bins},
            timeout,
        )
    except Exception:
        return


def load_binned_pairs(
    pairs,
    start,
    end,
    interval_minutes,
    now=None,
    fetch_rows=None,
    cache_backend=None,
):
    """Bin means for ``(room_id, parameter_id)`` pairs, reusing cached closed bins.

    Returns ``(frame, raw_means)``. ``frame`` is indexed by bin start with one
    column per pair key and holds means of the points inside the request.
    ``raw_means`` is the mean of those same points (not of filled bins).
    Interval 0 skips the cache and returns one row per recorded timestamp.
    The window is clipped at ``now`` so a live end a year ahead does not build
    future bins, and the bin that contains now stays partial.
    """
    pairs = list(dict.fromkeys((int(room_id), int(parameter_id)) for room_id, parameter_id in pairs))
    if not pairs:
        return pd.DataFrame(), {}
    interval = normalize_interval(interval_minutes)
    fetch = fetch_rows or _fetch_rows
    if start is None or end is None:
        start, end = _resolve_bounds(pairs, start, end)
    if start is None or end is None:
        return pd.DataFrame(), {}

    start_local = to_local_naive(start)
    end_local = to_local_naive(end)
    if end_local < start_local:
        return pd.DataFrame(), {}
    if interval <= 0:
        rows = fetch(
            pairs,
            local_to_aware(start_local),
            local_to_aware(end_local + pd.Timedelta(microseconds=1)),
        )
        return _raw_pivot(rows, pairs)

    now_local = to_local_naive(now if now is not None else pd.Timestamp.now(tz=LOCAL_TZ))
    covered_end = end_local if end_local <= now_local else now_local
    if covered_end < start_local:
        covered_end = start_local

    first = _first_complete(start_local, covered_end, interval)
    last = _last_complete(start_local, covered_end, interval)
    if first is None or last is None or last < first:
        first = last = None

    start_bin = floor_bin(start_local, interval)
    end_bin = floor_bin(covered_end, interval)
    partial = []
    if first is None or start_bin < first:
        partial.append(pd.Timestamp(start_bin))
    if end_bin != start_bin and (last is None or end_bin > last):
        partial.append(pd.Timestamp(end_bin))

    pack_starts = []
    if first is not None:
        pack_starts = list(_iter_packs(first, last, interval))

    backend = cache_backend if cache_backend is not None else _django_cache()
    key_info = {}
    for pack_start in pack_starts:
        for room_id, parameter_id in pairs:
            key_info[pack_key(room_id, parameter_id, interval, pack_start)] = (
                room_id,
                parameter_id,
                pd.Timestamp(pack_start),
            )
    cached = _get_many(backend, key_info) if key_info else {}

    totals = {}
    missing = []
    for key, (room_id, parameter_id, pack_start) in key_info.items():
        payload = cached.get(key)
        bins = payload.get("bins") if isinstance(payload, dict) else None
        labels = [bin_label(bin_start) for bin_start in _pack_bins(pack_start, interval)]
        if not isinstance(bins, dict) or any(label not in bins for label in labels):
            missing.append((room_id, parameter_id, pack_start))
            continue
        pack_last = _pack_bins(pack_start, interval)[-1]
        lo = max(first, pack_start)
        hi = min(last, pack_last)
        if lo > hi:
            continue
        for bin_start in _bins_between(lo, hi, interval):
            stored = bins.get(bin_label(bin_start))
            if stored:
                totals[(room_id, parameter_id, pd.Timestamp(bin_start))] = [float(stored[0]), int(stored[1])]

    if missing:
        by_pack = {}
        for room_id, parameter_id, pack_start in missing:
            by_pack.setdefault(pd.Timestamp(pack_start), []).append((int(room_id), int(parameter_id)))
        pack_step = pd.Timedelta(days=1) if interval >= 1440 else pd.Timedelta(hours=1)
        groups = []
        current = None
        for pack_start in sorted(by_pack):
            pair_key_set = tuple(sorted(set(by_pack[pack_start])))
            if (
                current
                and current["pairs"] == pair_key_set
                and pack_start == current["last"] + pack_step
            ):
                current["packs"].append(pack_start)
                current["last"] = pack_start
            else:
                current = {
                    "pairs": pair_key_set,
                    "packs": [pack_start],
                    "first": pack_start,
                    "last": pack_start,
                }
                groups.append(current)
        for group in groups:
            group_pairs = list(group["pairs"])
            fetch_end = pack_end_of(group["last"], interval)
            rows = fetch(
                group_pairs,
                local_to_aware(group["first"]),
                local_to_aware(fetch_end),
            )
            fresh = _aggregate(rows, interval)
            for pack_start in group["packs"]:
                open_pack = pack_end_of(pack_start, interval) > now_local
                timeout = OPEN_TTL if open_pack else CLOSED_TTL
                for room_id, parameter_id in group_pairs:
                    _store_pack(
                        backend,
                        room_id,
                        parameter_id,
                        interval,
                        pack_start,
                        fresh,
                        timeout,
                    )
                    pack_last = _pack_bins(pack_start, interval)[-1]
                    lo = max(first, pack_start)
                    hi = min(last, pack_last)
                    if lo > hi:
                        continue
                    for bin_start in _bins_between(lo, hi, interval):
                        found = fresh.get((room_id, parameter_id, pd.Timestamp(bin_start)))
                        if found:
                            totals[(room_id, parameter_id, pd.Timestamp(bin_start))] = found

    if partial:
        # A clipped edge must not use the full cached bin. Query only the
        # overlap, and do not write it back.
        groups = {}
        partial_set = set(partial)
        for bin_start in partial:
            span_start = max(pd.Timestamp(bin_start), start_local)
            span_end = min(bin_end(bin_start, interval), covered_end + pd.Timedelta(microseconds=1))
            if span_end <= span_start:
                continue
            groups.setdefault((span_start, span_end), []).append(pd.Timestamp(bin_start))
        for (span_start, span_end), _bins in groups.items():
            rows = fetch(
                pairs,
                local_to_aware(span_start),
                local_to_aware(span_end),
            )
            for key, value in _aggregate(rows, interval).items():
                if key[2] in partial_set and _overlaps_request(key[2], interval, start_local, covered_end):
                    totals[key] = value

    return _mean_frame(totals, pairs), _raw_means(totals, pairs)


def _overlaps_request(bin_start, interval, start, end):
    """Keep a partial aggregate only when it is one of the edge bins we asked for."""
    start_bin = floor_bin(start, interval)
    end_bin = floor_bin(end, interval)
    return pd.Timestamp(bin_start) in (pd.Timestamp(start_bin), pd.Timestamp(end_bin))


def load_resampled_pairs(
    pairs,
    start,
    end,
    interval_minutes,
    fill_method="ffill",
    spike_factor=0.0,
    spike_ceiling=None,
    now=None,
    fetch_rows=None,
    cache_backend=None,
):
    """Resampled wide frame (pair-key columns) plus raw means of the source points.

    Bin means are stitched first. ``resample_measurements`` then applies the
    same spike, ceiling, and fill rules as a chart that read the rows directly.
    One point per bin makes that second resample keep the mean and rebuild gaps.
    """
    binned, raw_means = load_binned_pairs(
        pairs,
        start,
        end,
        interval_minutes,
        now=now,
        fetch_rows=fetch_rows,
        cache_backend=cache_backend,
    )
    if binned is None or binned.empty:
        return pd.DataFrame(), raw_means
    long = binned.reset_index()
    index_name = long.columns[0]
    long = long.melt(id_vars=[index_name], var_name="parameter", value_name="value")
    long = long.dropna(subset=["value"])
    long = long.rename(columns={index_name: "timestamp"})
    from dashboard.views import resample_measurements

    resampled = resample_measurements(
        long,
        normalize_interval(interval_minutes),
        fill_method,
        spike_factor=spike_factor,
        spike_ceiling=spike_ceiling,
    )
    if resampled is None or getattr(resampled, "empty", True):
        return pd.DataFrame(), raw_means
    resampled.index.name = "timestamp"
    return resampled, raw_means


def distinct_measurement_pairs(start=None, end=None, room_id=None, parameter_id=None, all_data=False):
    """Cheap (room, parameter) pairs that actually have rows in the window."""
    from measurements.models import Measurement

    query = Measurement.objects.all()
    if room_id is not None:
        query = query.filter(sensor__room_id=room_id)
    if parameter_id is not None:
        query = query.filter(parameter_id=parameter_id)
    if not all_data:
        if start is not None:
            query = query.filter(timestamp__gte=start)
        if end is not None:
            query = query.filter(timestamp__lte=end)
    # Drop Measurement.ordering (timestamp). Otherwise DISTINCT keeps one row
    # per timestamp and the same room is plotted twice, once as "Name (id:id)".
    return list(
        query.order_by().values_list("sensor__room_id", "parameter_id").distinct()
    )


def label_frame(resampled, pairs, label_for):
    """Rename pair-key columns with ``label_for(room_id, parameter_id)``."""
    from dashboard.series_selection import pair_key

    if resampled is None or getattr(resampled, "empty", True):
        return pd.DataFrame()
    frame = pd.DataFrame(index=resampled.index)
    used = set()
    seen = set()
    for room_id, parameter_id in pairs:
        key = pair_key(room_id, parameter_id)
        if key in seen or key not in resampled.columns:
            continue
        seen.add(key)
        label = str(label_for(room_id, parameter_id))
        if label in used:
            label = f"{label} ({key})"
        used.add(label)
        frame[label] = resampled[key]
    frame.index.name = "timestamp"
    return frame
