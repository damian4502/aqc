from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import MqttSubscription
from .mqtt_reload import bump_mqtt_subscriptions_revision


def _bump_after_commit(**kwargs):
    # Wait until the row is visible to the mqtt-listener process.
    transaction.on_commit(bump_mqtt_subscriptions_revision)


@receiver(post_save, sender=MqttSubscription)
@receiver(post_delete, sender=MqttSubscription)
def mqtt_subscription_changed(sender, **kwargs):
    _bump_after_commit()
