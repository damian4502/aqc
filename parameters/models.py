from django.db import models

class Parameter(models.Model):
    name = models.CharField(max_length=50, unique=True)   # npr. "Temperatura", "CO2", "PM2.5"
    unit = models.CharField(max_length=20, blank=True)    # npr. "°C", "ppm", "µg/m³"
    description = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} ({self.unit})" if self.unit else self.name

    class Meta:
        ordering = ['name']
