from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Measurement


@receiver(post_save, sender=Measurement)
@receiver(post_delete, sender=Measurement)
def measurement_bins_changed(sender, instance, **kwargs):
    """Closed resample bins stay valid until a sample in that pack changes."""
    from dashboard.bin_cache import invalidate_bins

    room_id = None
    sensor_id = getattr(instance, "sensor_id", None)
    if sensor_id:
        try:
            room_id = instance.sensor.room_id
        except Exception:
            room_id = None
    invalidate_bins(room_id, getattr(instance, "parameter_id", None), getattr(instance, "timestamp", None))
