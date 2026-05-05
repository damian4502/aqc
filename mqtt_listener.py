import time
import paho.mqtt.client as mqtt
from django.utils import timezone
from django.db import transaction
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from sensors.models import MqttSubscription
from measurements.models import Measurement

class MQTTListener:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.broker = "mqtt"
        self.port = 1883

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("MQTT Listener: Uspešno povezan na HiveMQ broker")
            subscriptions = MqttSubscription.objects.select_related('sensor', 'parameter').all()
            for sub in subscriptions:
                client.subscribe(sub.topic, qos=sub.qos)
                print(f"  → Naročen na: {sub.topic} → {sub.parameter.name} (senzor: {sub.sensor})")
        else:
            print(f"MQTT Listener: Napaka pri povezavi (rc={rc})")

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8').strip()

            print(f"MQTT → {topic}: {payload}")

            subscription = MqttSubscription.objects.select_related('sensor', 'parameter').get(topic=topic)
            
            value = float(payload)

            with transaction.atomic():
                measurement = Measurement.objects.create(
                    sensor=subscription.sensor,
                    parameter=subscription.parameter,
                    timestamp=timezone.now(),
                    value=value
                )

            print(f"  ✓ Shranjeno → {subscription.sensor.room.name} | {subscription.parameter.name} = {value}")

            # Sinhroni "broadcast" - samo logging za zdaj
            self.broadcast_update(measurement)

        except MqttSubscription.DoesNotExist:
            print(f"  ⚠ Ni najdenega subscriptiona za topic: {topic}")
        except Exception as e:
            print(f"  ❌ Napaka pri obdelavi sporočila: {e}")

    def broadcast_update(self, measurement):
        """Pošlje novo meritev preko WebSocket"""
        try:
            data = {
                'room': measurement.sensor.room.name,
                'parameter': measurement.parameter.name,
                'value': float(measurement.value),
                'sensor_id': measurement.sensor.id,
                'unit': measurement.parameter.unit or '',
                'time': measurement.timestamp.strftime("%H:%M:%S"),
                'room_id': measurement.sensor.room.id,
                'parameter_id': measurement.parameter.id
            }
            
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            
            async_to_sync(channel_layer.group_send)(
                'live_updates',
                {
                    'type': 'live_update',
                    'data': data          # pošljemo posamezno meritev, ne ticker
                }
            )
            #print(f"  → Broadcastano v WebSocket: {data['room']} | {data['parameter']} = {data['value']}")
            
        except Exception as e:
            print(f"  Napaka pri broadcastu: {e}")
            
    def start(self):
        print("MQTT Listener se zaganja proti HiveMQ...")
        
        for attempt in range(1, 31):
            try:
                print(f"Povezovanje na HiveMQ ({attempt}/30)...")
                self.client.connect(self.broker, self.port, 60)
                print("Povezava uspešna!")
                self.client.loop_forever()
                return
            except Exception as e:
                print(f"Napaka pri povezavi: {e}")
                time.sleep(3)

if __name__ == "__main__":
    listener = MQTTListener()
    listener.start()