from django.contrib import admin
from .models import *

@admin.register(Dashboard)
class Dashboard(admin.ModelAdmin):
    list_display = ['name']
    date_hierarchy = 'created_at'

@admin.register(DashboardWidget)
class DashboardWidget(admin.ModelAdmin):
    list_display = ['title']
