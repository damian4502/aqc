import time
import paho.mqtt.client as mqtt
from django.utils import timezone
from django.db import transaction
import os
import django, json
from django.core.cache import cache

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from sensors.models import MqttSubscription
from measurements.models import Measurement
from parameters.models import Parameter

class MQTTListener:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message_new

        self.broker = "mqtt"
        self.port = 1883
        self.topics = {}
        self.parameters = {}
        self.update_timestamps = {}

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("MQTT Listener: Uspešno povezan na HiveMQ broker")
            subscriptions = MqttSubscription.objects.select_related('sensor', 'parameter').all()
            for sub in subscriptions:
                client.subscribe(sub.topic, qos=sub.qos)
                print(f"  → Naročen na: {sub.topic} → {sub.parameter.name} (senzor: {sub.sensor})")
        else:
            print(f"MQTT Listener: Napaka pri povezavi (rc={rc})")

    def aaon_message(self, client, userdata, msg):
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

    def on_message_new(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload_str = msg.payload.decode('utf-8').strip()
            #print(payload_str)

            # Poskusi parsati kot JSON
            try:
                data = json.loads(payload_str)

                if isinstance(data, dict):
                    # JSON objekt → več parametrov ali {"value": ...}
                    if 'value' in data and len(data) <= 3:  # npr. {"value": 23.5, "unit": "C"}
                        value = data['value']
                        param_name = data.get('parameter') or topic.split('/')[-1]
                        self._save_measurement(topic, param_name, value)
                    else:
                        # Več parametrov v enem sporočilu
                        for key, value in data.items():
                            self._save_measurement(topic, key, value)
                    return

                else:
                    # JSON je bil primitiv (število ali niz)
                    self._save_measurement(topic, None, data)
                    return

            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # Ni JSON → nadaljuj z navadnim načinom

            # Navaden payload (npr. "23.5")
            self._save_measurement(topic, None, payload_str)

        except Exception as e:
            print(f"[MQTT] Napaka pri obdelavi sporočila: {e}")

    def _save_measurement(self, topic, param_name, value):
        """Shrani meritvijo - prilagodi glede na tvojo obstoječo logiko"""
        try:
            # Pretvori vrednost v float
            try:
                value = float(value)
            except (ValueError, TypeError):
                print(f"[MQTT] Neveljavna vrednost: {value} za {param_name} (topic: {topic})")
                return

            # Če param_name ni podan, ga vzemi iz zadnjega dela topica
            if not param_name:
                topic_parts = topic.split('/')
                param_name = topic_parts[-1] if topic_parts else 'unknown'


            try:
                sensor_id = self.topics[topic]['sensor_id']
            except:
                subscription = MqttSubscription.objects.select_related('sensor', 'parameter').get(topic=topic)
                self.topics[topic] = {}
                self.topics[topic]['sensor_id'] = subscription.sensor.id
                self.topics[topic]['room_id'] = subscription.sensor.room.id
                raise

            try:
                parameter_id = self.parameters[param_name]
            except:
                parameter = Parameter.objects.get(identifier=param_name)
                self.parameters[param_name] = parameter.id
                raise
                parameter = subscription.parameter

            try:
                time_diff = time.time() - self.update_timestamps[(sensor_id*23) + parameter_id]
            except:
                time_diff = 999999
            
            if (time_diff < 1):
                print(f"[MQTT] Discard: {param_name} = {value} (topic: {topic}, {time_diff}s < 1)")
                return

            #with transaction.atomic():
            #if parameter.identifier == 'voc_index_state0':
            #    print (Measurement.objects.filter(parameter=parameter).filter(sensor=subscription.sensor).count())
            measurement = Measurement.objects.create(
                sensor_id=self.topics[topic]['sensor_id'],
                parameter_id=self.parameters[param_name],
                timestamp=timezone.now(),
                value=value
            )
            

            self.update_timestamps[(sensor_id*23) + parameter_id] = time.time()
            
            key = "last_value" + str(self.topics[topic]['room_id']) + "_" + str(self.parameters[param_name])
            cache.set(key, value, 3600*24)
            
            #self.broadcast_update(measurement)

            print(f"[MQTT] Shranjeno: {param_name} = {value} (topic: {topic}, {time_diff}s)")

        except Exception as e:
            print(f"[MQTT] Napaka pri shranjevanju meritve: {e}")
        
    def broadcast_update(self, measurement):
        return True
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