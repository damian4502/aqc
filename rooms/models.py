from django.db import models
from random import randrange

from django.core.cache import cache
from parameters.models import Parameter

class RoomGroup(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="Ime skupine")
    description = models.TextField(blank=True, verbose_name="Opis")
    rooms = models.ManyToManyField(
        'Room',
        related_name='groups',      
        blank=True,
        verbose_name="Prostori v skupini"
    )

    class Meta:
        verbose_name = "Skupina prostorov"
        verbose_name_plural = "Skupine prostorov"
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_rooms_list(self):
        return ", ".join(r.name for r in self.rooms.all())
        
class Room(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    order = models.PositiveIntegerField(
        default=999,
    )
    
    
    def get_aqi(self):
        index = cache.get_or_set("room_get_aqi_%s" % self.id, randrange(100), 120)
        return randrange(100)


    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

class Event(models.Model):
    rooms = models.ManyToManyField(
        'Room',
        related_name='events',
        blank=True,
        verbose_name="Prostori"
    )
    parameters = models.ManyToManyField(
        'parameters.Parameter',
        related_name='events',
        blank=True,
        verbose_name="Parametri"
    )
    

    timestamp = models.DateTimeField(verbose_name="Datum in čas dogodka")
    title = models.CharField(max_length=200, verbose_name="Naziv dogodka")
    description = models.TextField(blank=True, verbose_name="Opis / opomba")
    color = models.CharField(
        max_length=7,
        default="#10b981",
        help_text="Barva črte na grafu (hex)",
        verbose_name="Barva"
    )

    class Meta:
        verbose_name = "Dogodek"
        verbose_name_plural = "Dogodki"
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.title} — {self.timestamp.strftime('%d.%m.%Y %H:%M')}"