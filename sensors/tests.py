from unittest.mock import MagicMock

from django.core.cache import cache
from django.test import TestCase, override_settings

from measurements.models import Measurement
from parameters.models import Parameter
from rooms.models import Room
from sensors.models import MqttSubscription, Sensor
from sensors.mqtt_reload import (
    MQTT_SUBSCRIPTIONS_REVISION_KEY,
    bump_mqtt_subscriptions_revision,
)


LOC_MEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "mqtt-reload-tests",
    }
}


@override_settings(CACHES=LOC_MEM_CACHE)
class MqttReloadSignalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.room = Room.objects.create(name="Lab")
        self.parameter = Parameter.objects.create(
            identifier="co2", name="Carbon dioxide"
        )
        self.sensor = Sensor.objects.create(
            room=self.room, parameter=self.parameter, name="CO2 sensor"
        )

    def _create_subscription(self, topic="house/lab/co2"):
        return MqttSubscription.objects.create(
            sensor=self.sensor,
            topic=topic,
            parameter=self.parameter,
        )

    def test_create_bumps_revision_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._create_subscription()
        self.assertIsNotNone(cache.get(MQTT_SUBSCRIPTIONS_REVISION_KEY))

    def test_update_bumps_revision_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            sub = self._create_subscription()
        cache.delete(MQTT_SUBSCRIPTIONS_REVISION_KEY)

        with self.captureOnCommitCallbacks(execute=True):
            sub.topic = "house/lab/co2-v2"
            sub.save()
        self.assertIsNotNone(cache.get(MQTT_SUBSCRIPTIONS_REVISION_KEY))

    def test_delete_bumps_revision_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            sub = self._create_subscription()
        cache.delete(MQTT_SUBSCRIPTIONS_REVISION_KEY)

        with self.captureOnCommitCallbacks(execute=True):
            sub.delete()
        self.assertIsNotNone(cache.get(MQTT_SUBSCRIPTIONS_REVISION_KEY))

    def test_bump_helper_writes_timestamp(self):
        bump_mqtt_subscriptions_revision()
        revision = cache.get(MQTT_SUBSCRIPTIONS_REVISION_KEY)
        self.assertIsInstance(revision, float)


@override_settings(CACHES=LOC_MEM_CACHE)
class MqttListenerRefreshTests(TestCase):
    def setUp(self):
        cache.clear()
        self.room = Room.objects.create(name="Lab")
        self.parameter = Parameter.objects.create(
            identifier="co2", name="Carbon dioxide"
        )
        self.sensor = Sensor.objects.create(
            room=self.room, parameter=self.parameter, name="CO2 sensor"
        )
        from mqtt_listener import MQTTListener

        self.listener = MQTTListener()
        self.listener.client = MagicMock()
        self.listener._connected = True

    def _create_subscription(self, topic="house/lab/co2", qos=0, sensor=None):
        return MqttSubscription.objects.create(
            sensor=sensor or self.sensor,
            topic=topic,
            parameter=self.parameter,
            qos=qos,
        )

    def test_refresh_subscribes_to_existing_topics(self):
        self._create_subscription("house/lab/co2")
        self.listener.refresh_subscriptions(reason="test")

        self.listener.client.subscribe.assert_called_once_with("house/lab/co2", qos=0)
        self.listener.client.unsubscribe.assert_not_called()
        self.assertEqual(self.listener.topics["house/lab/co2"]["sensor_id"], self.sensor.id)
        self.assertEqual(self.listener.topics["house/lab/co2"]["room_id"], self.room.id)
        self.assertEqual(self.listener.parameters["co2"], self.parameter.id)

    def test_new_topic_is_subscribed_without_restart(self):
        self._create_subscription("house/lab/co2")
        self.listener.refresh_subscriptions(reason="connect")
        self.listener.client.reset_mock()

        self._create_subscription("house/lab/temp")
        self.listener.refresh_subscriptions(reason="subscription change")

        self.listener.client.subscribe.assert_called_once_with("house/lab/temp", qos=0)
        self.listener.client.unsubscribe.assert_not_called()
        self.assertIn("house/lab/co2", self.listener.subscribed_topics)
        self.assertIn("house/lab/temp", self.listener.subscribed_topics)

    def test_removed_topic_is_unsubscribed(self):
        sub = self._create_subscription("house/lab/co2")
        self.listener.refresh_subscriptions(reason="connect")
        self.listener.client.reset_mock()

        sub.delete()
        self.listener.refresh_subscriptions(reason="subscription change")

        self.listener.client.unsubscribe.assert_called_once_with("house/lab/co2")
        self.listener.client.subscribe.assert_not_called()
        self.assertNotIn("house/lab/co2", self.listener.topics)
        self.assertNotIn("house/lab/co2", self.listener.subscribed_topics)

    def test_renamed_topic_unsubscribes_old_and_subscribes_new(self):
        sub = self._create_subscription("house/lab/co2")
        self.listener.refresh_subscriptions(reason="connect")
        self.listener.client.reset_mock()

        sub.topic = "house/lab/co2-new"
        sub.save()
        self.listener.refresh_subscriptions(reason="subscription change")

        self.listener.client.unsubscribe.assert_called_once_with("house/lab/co2")
        self.listener.client.subscribe.assert_called_once_with(
            "house/lab/co2-new", qos=0
        )
        self.assertNotIn("house/lab/co2", self.listener.topics)
        self.assertIn("house/lab/co2-new", self.listener.topics)

    def test_qos_change_resubscribes(self):
        sub = self._create_subscription("house/lab/co2", qos=0)
        self.listener.refresh_subscriptions(reason="connect")
        self.listener.client.reset_mock()

        sub.qos = 1
        sub.save()
        self.listener.refresh_subscriptions(reason="subscription change")

        self.listener.client.subscribe.assert_called_once_with("house/lab/co2", qos=1)
        self.listener.client.unsubscribe.assert_not_called()

    def test_sensor_room_change_updates_cached_room_id(self):
        self._create_subscription("house/lab/co2")
        self.listener.refresh_subscriptions(reason="connect")

        new_room = Room.objects.create(name="Office")
        self.sensor.room = new_room
        self.sensor.save()
        self.listener.refresh_subscriptions(reason="periodic")

        self.assertEqual(self.listener.topics["house/lab/co2"]["room_id"], new_room.id)

    def test_skips_refresh_when_disconnected(self):
        self._create_subscription("house/lab/co2")
        self.listener._connected = False
        self.listener.refresh_subscriptions(reason="test")
        self.listener.client.subscribe.assert_not_called()

    def test_maybe_refresh_on_revision_change(self):
        self._create_subscription("house/lab/co2")
        cache.set(MQTT_SUBSCRIPTIONS_REVISION_KEY, 1.0, None)

        self.listener._maybe_refresh_subscriptions()

        self.listener.client.subscribe.assert_called_once_with("house/lab/co2", qos=0)
        self.assertEqual(self.listener._seen_revision, 1.0)

    def test_maybe_refresh_ignores_same_revision(self):
        self._create_subscription("house/lab/co2")
        cache.set(MQTT_SUBSCRIPTIONS_REVISION_KEY, 1.0, None)
        self.listener._seen_revision = 1.0
        self.listener._last_periodic_refresh = 10**12

        self.listener._maybe_refresh_subscriptions()

        self.listener.client.subscribe.assert_not_called()

    def test_on_connect_reloads_subscriptions(self):
        self._create_subscription("house/lab/co2")
        self.listener._connected = False
        self.listener.subscribed_topics = {"stale/topic"}

        self.listener.on_connect(self.listener.client, None, None, 0)

        self.assertTrue(self.listener._connected)
        self.listener.client.subscribe.assert_called_once_with("house/lab/co2", qos=0)
        self.assertNotIn("stale/topic", self.listener.subscribed_topics)

    def test_first_message_on_unknown_topic_is_saved(self):
        """Cache miss must load the mapping and still persist the measurement."""
        self._create_subscription("house/lab/co2")
        self.listener._save_measurement("house/lab/co2", "co2", 412.0)

        self.assertEqual(Measurement.objects.count(), 1)
        saved = Measurement.objects.get()
        self.assertEqual(saved.value, 412.0)
        self.assertEqual(saved.sensor_id, self.sensor.id)
        self.assertEqual(saved.parameter_id, self.parameter.id)
        self.assertEqual(self.listener.topics["house/lab/co2"]["sensor_id"], self.sensor.id)
