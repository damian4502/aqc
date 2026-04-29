from django.urls import path
from .views import *

urlpatterns = [
    path('', dashboard_overview, name='dashboard'),
    path('room/<int:room_id>/', room_detail, name='room_detail'),
    path('room/<int:room_id>/export/', export_room_csv, name='export_room_csv'),
    path('parameter/<int:parameter_id>/', parameter_detail, name='parameter_detail'),
    path('room/<int:room_id>/graph/', room_graph_fragment, name='room_graph_fragment'),
    path('api/latest-measurements/', latest_measurements_api, name='latest_measurements_api'),
    path('api/room/<int:room_id>/live/', room_live_data, name='room_live_data'),
    path('parameter/<int:parameter_id>/export/', export_parameter_csv, name='export_parameter_csv'),
    path('trends/', trends_view, name='trends'),
    path('monitor/', monitor, name='monitor'),
    path('differential-pressure/', differential_pressure_view, name='differential_pressure'),
    path('custom/', custom_dashboard, name='custom_dashboard'),
]