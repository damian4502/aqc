from django.db import models
from django.contrib.auth.models import User
from rooms.models import Room
from parameters.models import Parameter

class Dashboard(models.Model):
    name = models.CharField(max_length=100, default="Moj dashboard")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dashboards')
    is_default = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.user.username})"

class DashboardWidget(models.Model):
    WIDGET_TYPES = [
        ('room_card', 'Kartica prostora'),
        ('mini_chart', 'Mini graf'),
        ('average', 'Povprečje parametra'),
        ('datetime', 'Trenutni čas in datum'),
        ('single_value', 'Ena vrednost parametra'),
        # Kasneje lahko dodaš: alarm, weather, stats itd.
    ]

    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name='widgets')
    widget_type = models.CharField(max_length=50, choices=WIDGET_TYPES)
    title = models.CharField(max_length=100, blank=True)
    
    # Konfiguracija widgeta (JSON)
    config = models.JSONField(default=dict, blank=True)

    # Razporeditev
    row = models.PositiveIntegerField(default=0)
    column = models.PositiveIntegerField(default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['row', 'column', 'order']

    def __str__(self):
        return f"{self.get_widget_type_display()} - {self.title or 'Brez naslova'}"