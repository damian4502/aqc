from django import template
from django.utils import timezone
from datetime import timedelta

register = template.Library()

@register.filter
def time_ago(value):
    if not value:
        return "nikoli"

    now = timezone.now()
    diff = now - value

    if diff < timedelta(minutes=1):
        return "pravkar"
    elif diff < timedelta(hours=1):
        minutes = int(diff.total_seconds() / 60)
        return f"pred {minutes} min" if minutes > 1 else "pred 1 min"
    elif diff < timedelta(days=1):
        hours = int(diff.total_seconds() / 3600)
        return f"pred {hours} ur" if hours > 1 else "pred 1 uro"
    elif diff < timedelta(days=7):
        days = int(diff.total_seconds() / 86400)
        return f"pred {days} dnevi" if days > 1 else "včeraj"
    else:
        return value.strftime("%d.%m.%Y")
