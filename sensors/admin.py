from django.contrib import admin
from .models import Sensor, MqttSubscription

@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = ['room', 'parameter', 'location', 'name']
    list_filter = ['room', 'parameter', 'location']
    search_fields = ['room__name', 'parameter__name', 'name']
    autocomplete_fields = ['room', 'parameter']

@admin.register(MqttSubscription)
class MqttSubscriptionAdmin(admin.ModelAdmin):
    list_display = ['sensor', 'topic', 'parameter', 'qos']
    list_filter = ['sensor__room', 'parameter']
    search_fields = ['topic', 'sensor__name', 'parameter__name']
    autocomplete_fields = ['sensor', 'parameter']