from django.urls import path
from .views import dashboard_overview, room_detail, export_room_csv, parameter_detail, room_graph_fragment

urlpatterns = [
    path('', dashboard_overview, name='dashboard'),
    path('room/<int:room_id>/', room_detail, name='room_detail'),
    path('room/<int:room_id>/export/', export_room_csv, name='export_room_csv'),
    path('parameter/<int:parameter_id>/', parameter_detail, name='parameter_detail'),
    path('room/<int:room_id>/graph/', room_graph_fragment, name='room_graph_fragment'),
]