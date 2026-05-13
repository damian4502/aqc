from django.db import models
from random import randrange

from django.core.cache import cache

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
