from django.db import models
from rooms.models import Room

class Sensor(models.Model):
    LOCATION_CHOICES = [
        ('floor', 'Tla'),
        ('ceiling', 'Strop'),
        ('wall', 'Stena'),
        ('table', 'Miza'),
        ('other', 'Drugo'),
    ]

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='sensors')
    parameter = models.ForeignKey('parameters.Parameter', on_delete=models.CASCADE, related_name='sensors')
    location = models.CharField(max_length=20, choices=LOCATION_CHOICES, default='other')
    name = models.CharField(max_length=100, blank=True)   # npr. "CO2 senzor na stropu"

    def __str__(self):
        return f"{self.name or self.parameter} v {self.room} ({self.get_location_display()})"

    class Meta:
        unique_together = ('room', 'parameter', 'location')
        ordering = ['room', 'parameter']

class MqttSubscription(models.Model):
    sensor = models.ForeignKey(
        'Sensor', 
        on_delete=models.CASCADE, 
        related_name='mqtt_subscriptions'
    )
    topic = models.CharField(
        max_length=255, 
        unique=True,
        help_text="MQTT topic za poslušanje (npr. house/livingroom/co2)"
    )
    parameter = models.ForeignKey(
        'parameters.Parameter', 
        on_delete=models.CASCADE,
        help_text="Kateri parameter ta topic predstavlja"
    )
    qos = models.IntegerField(default=0, choices=[(0, '0'), (1, '1'), (2, '2')])

    def __str__(self):
        return f"{self.sensor} → {self.topic} ({self.parameter.name})"

    class Meta:
        verbose_name = "MQTT Subscription"
        verbose_name_plural = "MQTT Subscriptions"
        unique_together = ('sensor', 'topic')