import json
import os
import threading
import time

import django
import paho.mqtt.client as mqtt
from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from measurements.models import Measurement
from parameters.models import Parameter
from sensors.models import MqttSubscription
from sensors.mqtt_reload import MQTT_SUBSCRIPTIONS_REVISION_KEY

# How often the listener checks the cache flag set by Django signals.
RELOAD_POLL_SECONDS = 2
# Safety net: reload from the database even if no signal was received.
PERIODIC_REFRESH_SECONDS = 30


class MQTTListener:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message_new

        self.broker = "mqtt"
        self.port = 1883
        self.topics = {}
        self.parameters = {}
        self.update_timestamps = {}
        self.subscribed_topics = set()
        self._connected = False
        self._refresh_lock = threading.Lock()
        self._seen_revision = None
        self._last_periodic_refresh = 0.0

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            # A new MQTT session has no subscriptions (clean session).
            self.subscribed_topics = set()
            print("MQTT listener: connected to broker")
            try:
                self.refresh_subscriptions(reason="connect")
            except Exception as e:
                print(f"MQTT listener: failed to load subscriptions on connect: {e}")
        else:
            self._connected = False
            print(f"MQTT listener: connection failed (rc={rc})")

    def on_disconnect(self, client, userdata, rc):
        self._connected = False
        print(f"MQTT listener: disconnected from broker (rc={rc})")

    def refresh_subscriptions(self, reason=""):
        """Reload MqttSubscription rows and subscribe/unsubscribe as needed.

        Returns True when the broker session was updated, False if skipped
        because the client is not connected.
        """
        close_old_connections()

        with self._refresh_lock:
            if not self._connected:
                return False

            subscriptions = list(
                MqttSubscription.objects.select_related(
                    "sensor", "sensor__room", "parameter"
                ).all()
            )

            desired = {}
            for sub in subscriptions:
                topic = (sub.topic or "").strip()
                if not topic:
                    continue
                desired[topic] = {
                    "sensor_id": sub.sensor_id,
                    "room_id": sub.sensor.room_id,
                    "parameter_id": sub.parameter_id,
                    "qos": sub.qos,
                }

            current = set(self.subscribed_topics)
            wanted = set(desired.keys())
            to_add = wanted - current
            to_remove = current - wanted
            to_update_qos = {
                topic
                for topic in (wanted & current)
                if self.topics.get(topic, {}).get("qos") != desired[topic]["qos"]
            }

            # Update the lookup cache before new messages can arrive.
            for topic, info in desired.items():
                self.topics[topic] = info
            for topic in to_remove:
                self.topics.pop(topic, None)

            for topic in to_remove:
                self.client.unsubscribe(topic)
                print(f"MQTT: unsubscribed from {topic}")

            for topic in to_add | to_update_qos:
                qos = desired[topic]["qos"]
                self.client.subscribe(topic, qos=qos)
                print(f"MQTT: subscribed to {topic} (qos={qos})")

            self.subscribed_topics = wanted
            self.parameters = {
                p.identifier: p.id
                for p in Parameter.objects.exclude(identifier__isnull=True).exclude(
                    identifier=""
                )
            }

            if to_add or to_remove or to_update_qos:
                print(
                    f"MQTT: subscriptions updated ({reason}): "
                    f"{len(wanted)} active, +{len(to_add)} -{len(to_remove)}"
                )
            elif reason == "connect":
                print(f"MQTT: listening on {len(wanted)} topic(s)")

            return True

    def _maybe_refresh_subscriptions(self):
        """Reload when Django signals bump the revision, or on a periodic timer."""
        revision = cache.get(MQTT_SUBSCRIPTIONS_REVISION_KEY)
        now = time.time()
        reason = None

        if revision is not None and revision != self._seen_revision:
            reason = "subscription change"
        elif now - self._last_periodic_refresh >= PERIODIC_REFRESH_SECONDS:
            reason = "periodic"

        if reason is None:
            return

        if not self.refresh_subscriptions(reason=reason):
            return

        self._last_periodic_refresh = now
        if revision is not None:
            self._seen_revision = revision

    def aaon_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode("utf-8").strip()

            print(f"MQTT → {topic}: {payload}")

            subscription = MqttSubscription.objects.select_related(
                "sensor", "parameter"
            ).get(topic=topic)

            value = float(payload)

            measurement = Measurement.objects.create(
                sensor=subscription.sensor,
                parameter=subscription.parameter,
                timestamp=timezone.now(),
                value=value,
            )

            print(
                f"  ✓ Shranjeno → {subscription.sensor.room.name} | {subscription.parameter.name} = {value}"
            )

            # Sinhroni "broadcast" - samo logging za zdaj
            self.broadcast_update(measurement)

        except MqttSubscription.DoesNotExist:
            print(f"  ⚠ Ni najdenega subscriptiona za topic: {topic}")
        except Exception as e:
            print(f"  ❌ Napaka pri obdelavi sporočila: {e}")

    def on_message_new(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload_str = msg.payload.decode("utf-8").strip()

            # Try to parse as JSON
            try:
                data = json.loads(payload_str)

                if isinstance(data, dict):
                    # JSON object → multiple parameters or {"value": ...}
                    if "value" in data and len(data) <= 3:
                        value = data["value"]
                        param_name = data.get("parameter") or topic.split("/")[-1]
                        self._save_measurement(topic, param_name, value)
                    else:
                        # Multiple parameters in one message
                        for key, value in data.items():
                            self._save_measurement(topic, key, value)
                    return

                else:
                    # JSON was a primitive (number or string)
                    self._save_measurement(topic, None, data)
                    return

            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # Not JSON → continue with plain payload

            # Plain payload (e.g. "23.5")
            self._save_measurement(topic, None, payload_str)

        except Exception as e:
            print(f"[MQTT] Error while processing message: {e}")

    def _save_measurement(self, topic, param_name, value):
        """Persist a measurement using the in-memory topic/parameter cache."""
        try:
            try:
                value = float(value)
            except (ValueError, TypeError):
                print(
                    f"[MQTT] Invalid value: {value} for {param_name} (topic: {topic})"
                )
                return

            if not param_name:
                topic_parts = topic.split("/")
                param_name = topic_parts[-1] if topic_parts else "unknown"

            info = self.topics.get(topic)
            if info is None:
                subscription = MqttSubscription.objects.select_related(
                    "sensor", "parameter"
                ).get(topic=topic)
                info = {
                    "sensor_id": subscription.sensor.id,
                    "room_id": subscription.sensor.room.id,
                    "parameter_id": subscription.parameter_id,
                    "qos": subscription.qos,
                }
                self.topics[topic] = info

            sensor_id = info["sensor_id"]

            parameter_id = self.parameters.get(param_name)
            if parameter_id is None:
                parameter = Parameter.objects.get(identifier=param_name)
                parameter_id = parameter.id
                self.parameters[param_name] = parameter_id

            try:
                time_diff = time.time() - self.update_timestamps[(sensor_id * 23) + parameter_id]
            except Exception:
                time_diff = 999999

            if time_diff < 10:
                print(
                    f"[MQTT] Discard: {param_name} = {value} (topic: {topic}, {time_diff}s < 1)"
                )
                return

            Measurement.objects.create(
                sensor_id=sensor_id,
                parameter_id=parameter_id,
                timestamp=timezone.now(),
                value=value,
            )

            self.update_timestamps[(sensor_id * 23) + parameter_id] = time.time()

            key = "last_value" + str(info["room_id"]) + "_" + str(parameter_id)
            cache.set(key, value, 3600 * 24)

            print(f"[MQTT] Saved: {param_name} = {value} (topic: {topic}, {time_diff}s)")

        except Exception as e:
            print(f"[MQTT] Error while saving measurement: {e}")

    def broadcast_update(self, measurement):
        return True
        """Pošlje novo meritev preko WebSocket"""
        try:
            data = {
                "room": measurement.sensor.room.name,
                "parameter": measurement.parameter.name,
                "value": float(measurement.value),
                "sensor_id": measurement.sensor.id,
                "unit": measurement.parameter.unit or "",
                "time": measurement.timestamp.strftime("%H:%M:%S"),
                "room_id": measurement.sensor.room.id,
                "parameter_id": measurement.parameter.id,
            }

            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()

            async_to_sync(channel_layer.group_send)(
                "live_updates",
                {
                    "type": "live_update",
                    "data": data,
                },
            )

        except Exception as e:
            print(f"  Napaka pri broadcastu: {e}")

    def _run_reload_loop(self):
        while True:
            try:
                self._maybe_refresh_subscriptions()
            except Exception as e:
                print(f"MQTT: error while refreshing subscriptions: {e}")
            time.sleep(RELOAD_POLL_SECONDS)

    def start(self):
        print("MQTT listener starting...")

        for attempt in range(1, 31):
            try:
                print(f"Connecting to MQTT broker ({attempt}/30)...")
                self.client.connect(self.broker, self.port, 60)
                print("Connection successful!")
                self.client.loop_start()
                self._run_reload_loop()
                return
            except Exception as e:
                print(f"Connection error: {e}")
                time.sleep(3)

        print("MQTT listener: giving up after 30 connection attempts")


if __name__ == "__main__":
    listener = MQTTListener()
    listener.start()
