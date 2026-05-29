from .models import Room, Parameter, Event, RoomGroup   # dodaj nova modela
from django.contrib import admin
from .models import Room

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'created_at']
    search_fields = ['name']
    list_filter = ['created_at']


@admin.register(RoomGroup)
class RoomGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'get_rooms_list', 'description')
    search_fields = ('name', 'description')
    filter_horizontal = ('rooms',)          # lepši vnos M2M
    ordering = ('name',)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'timestamp', 'get_rooms_list', 'get_parameters_list', 'color')
    list_filter = ('timestamp', 'rooms', 'parameters')
    search_fields = ('title', 'description')
    filter_horizontal = ('rooms', 'parameters')     # super uporaben za M2M
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)
    save_as = True

    readonly_fields = ('get_rooms_list', 'get_parameters_list')

    def get_rooms_list(self, obj):
        return ", ".join(r.name for r in obj.rooms.all()) or "-"
    get_rooms_list.short_description = "Prostori"

    def get_parameters_list(self, obj):
        return ", ".join(p.name for p in obj.parameters.all()) or "-"
    get_parameters_list.short_description = "Parametri"