from django.db import models
from sensors.models import Sensor
from parameters.models import Parameter

class Measurement(models.Model):
    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='measurements')
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    value = models.FloatField()

    def __str__(self):
        return f"{self.parameter} = {self.value} ob {self.timestamp}"

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['sensor', 'parameter', 'timestamp'],
                name='unique_measurement_per_sensor_parameter_time'
            )
        ]
