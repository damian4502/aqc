import json
import time
import paho.mqtt.client as mqtt
from django.utils import timezone
from django.db import transaction
import os
import django

# Nastavi Django okolje
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from sensors.models import MqttSubscription
from measurements.models import Measurement

class MQTTListener:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.broker = "broker.hivemq.com"          # uporabi ime servisa iz docker-compose
        self.port = 1883
        self.max_retries = 30
        self.retry_delay = 2

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("MQTT Listener: Uspešno povezan na broker")
            subscriptions = MqttSubscription.objects.all()
            for sub in subscriptions:
                client.subscribe(sub.topic, qos=sub.qos)
                print(f"  → Naročen na topic: {sub.topic} ({sub.parameter.name})")
        else:
            print(f"MQTT Listener: Napaka pri povezavi (rc={rc})")

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8').strip()
            
            print(f"MQTT → {topic}: {payload}")

            subscription = MqttSubscription.objects.select_related('sensor', 'parameter').get(topic=topic)
            
            try:
                value = float(payload)
            except ValueError:
                print(f"  Napaka: '{payload}' ni številka")
                return

            with transaction.atomic():
                Measurement.objects.create(
                    sensor=subscription.sensor,
                    parameter=subscription.parameter,
                    timestamp=timezone.now(),
                    value=value
                )
            print(f"  ✓ Shranjeno: {subscription.sensor} | {subscription.parameter.name} = {value}")

        except MqttSubscription.DoesNotExist:
            print(f"  ⚠ Ni subscriptiona za topic: {topic}")
        except Exception as e:
            print(f"  ❌ Napaka pri obdelavi: {e}")

    def start(self):
        print("MQTT Listener se zaganja...")

        for attempt in range(1, self.max_retries + 1):
            try:
                print(f"Povezovanje na MQTT broker ({attempt}/{self.max_retries})...")
                self.client.connect(self.broker, self.port, 60)
                print("Povezava uspešna!")
                self.client.loop_forever()
                return
            except Exception as e:
                print(f"Napaka pri povezavi: {e}")
                if attempt < self.max_retries:
                    print(f"Poskus ponovno čez {self.retry_delay} sekund...")
                    time.sleep(self.retry_delay)
                else:
                    print("Doseženo maksimalno število poskusov. Končujem.")
                    break

if __name__ == "__main__":
    listener = MQTTListener()
    listener.start()