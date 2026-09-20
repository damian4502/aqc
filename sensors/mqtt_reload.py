"""Notify the MQTT listener that subscription data has changed."""

MQTT_SUBSCRIPTIONS_REVISION_KEY = "mqtt_subscriptions_revision"


def bump_mqtt_subscriptions_revision():
    """Invalidate the listener's in-memory subscription snapshot.

    The listener polls this cache key and reloads topics from the database
    when the value changes. Cache failures are ignored so admin saves still
    succeed if the cache backend is down; a periodic DB refresh still picks
    up the change.
    """
    import time

    from django.core.cache import cache

    try:
        cache.set(MQTT_SUBSCRIPTIONS_REVISION_KEY, time.time(), None)
    except Exception:
        pass
