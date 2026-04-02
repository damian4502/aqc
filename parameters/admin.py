from django.contrib import admin
from .models import Parameter

@admin.register(Parameter)
class ParameterAdmin(admin.ModelAdmin):
    list_display = ['name', 'unit', 'description']
    search_fields = ['name']
    list_filter = ['name']
