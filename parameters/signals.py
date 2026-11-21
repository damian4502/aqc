from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Parameter


@receiver(post_save, sender=Parameter)
@receiver(post_delete, sender=Parameter)
def parameter_catalog_changed(sender, **kwargs):
    cache.delete("all_parameters")
    cache.delete("all_parameters_v2")
