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
