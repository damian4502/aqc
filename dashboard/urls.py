from django.urls import path
from .views import dashboard_overview, room_detail, export_room_csv

urlpatterns = [
    path('', dashboard_overview, name='dashboard'),
    path('room/<int:room_id>/', room_detail, name='room_detail'),
    path('room/<int:room_id>/export/', export_room_csv, name='export_room_csv'),
]