import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
class LiveDataConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("live_updates", self.channel_name)
        await self.accept()
        print(f"[WS] Client connected: {self.channel_name}")
        await self.send_initial_data()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("live_updates", self.channel_name)
        print(f"[WS] Client disconnected: {self.channel_name}")

    async def live_update(self, event):
        print(f"[WS] Received broadcast, sending to client")
        await self.send(text_data=json.dumps({
            'type': 'live_update',
            'data': event['data']
        }))

    async def send_initial_data(self):
        data = await database_sync_to_async(self.get_latest_data)()
        print(f"[WS] Sending initial data with {len(data['ticker'])} items")
        await self.send(text_data=json.dumps({
            'type': 'live_update',
            'data': data
        }))

    def get_latest_data(self):
        from measurements.models import Measurement
        latest = Measurement.objects.select_related('sensor__room', 'parameter')\
            .order_by('-timestamp')[:20]

        ticker = []
        for m in latest:
            ticker.append({
                'room': m.sensor.room.name,
                'parameter': m.parameter.name,
                'value': float(m.value),
                'unit': m.parameter.unit or '',
                'time': m.timestamp.strftime("%H:%M:%S")
            })
        return {'ticker': ticker}