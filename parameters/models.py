from django.db import models
import datetime
from django.core.cache import cache
from random import randrange
from django.utils import timezone

class Parameter(models.Model):
    identifier = models.CharField(max_length=50, unique=True)
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
    format_decimals = models.PositiveIntegerField(
        default=2,
        verbose_name="Number of decimal places on room cards etc.",
    )
    formula = models.TextField(
        blank=True,
        default="",
        verbose_name="Formula",
        help_text=(
            "Leave empty for a measured parameter. A formula is plotted and not stored. "
            "Same syntax as Compare: Room/Parameter, numbers, + − * /, parentheses, "
            "min(), max(), and avg(). The unit is the Unit field. "
            "Names are matched as written, so rename a room or parameter and update the formula."
        ),
    )

    def __str__(self):
        return f"{self.name} ({self.unit})" if self.unit else self.name

    @property
    def is_calculated(self):
        return bool((self.formula or "").strip())

    def live_rooms(self):
        from measurements.models import Measurement

        rooms = cache.get_or_set("1liverooms_param_%s" % self.id, [x['sensor__room'] for x in Measurement.objects.filter(parameter=self).filter(timestamp__gte=timezone.now() - datetime.timedelta(hours=24)).values('sensor__room').annotate(dcount=models.Count('sensor__room'))], randrange(1000))

        return rooms

    class Meta:
        ordering = ['name']
