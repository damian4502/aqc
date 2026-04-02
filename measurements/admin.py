from django.contrib import admin
from .models import Measurement

@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display = ['sensor', 'parameter', 'value', 'timestamp']
    list_filter = ['parameter', 'timestamp', 'sensor__room']
    search_fields = ['sensor__room__name', 'parameter__name']
    date_hierarchy = 'timestamp'
