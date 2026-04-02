from django.contrib import admin
from .models import Sensor

@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = ['room', 'parameter', 'location', 'name']
    list_filter = ['room', 'parameter', 'location']
    search_fields = ['room__name', 'parameter__name', 'name']
    autocomplete_fields = ['room', 'parameter']
