from django.db import models

class Parameter(models.Model):
    name = models.CharField(max_length=50, unique=True)   # npr. "Temperatura", "CO2", "PM2.5"
    unit = models.CharField(max_length=20, blank=True)    # npr. "°C", "ppm", "µg/m³"
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(
        default=999,
        verbose_name="Vrstni red na karticah",
        help_text="Manjša številka = prikaže se višje na kompaktni kartici"
    )
    higher_is_worse = models.BooleanField(
        default=True,
        verbose_name="Višja vrednost = slabše",
        help_text="Označi, če višja vrednost parametra pomeni slabšo kakovost zraka "
                  "(npr. CO2, AQI, PM2.5). Za temperaturo označi False."
    )
    
    def __str__(self):
        return f"{self.name} ({self.unit})" if self.unit else self.name

    class Meta:
        ordering = ['name']
