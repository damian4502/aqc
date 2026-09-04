from django.urls import path
from .views import *

urlpatterns = [
    path('', dashboard_overview, name='dashboard'),
    path('room/<int:room_id>/', room_detail, name='room_detail'),
    path('room/<int:room_id>/export/', export_room_csv, name='export_room_csv'),
    path('api/voc_states/<str:sensor>/', get_last_voc_states, name='get_last_voc_states'),
    path('parameter/<int:parameter_id>/', parameter_detail, name='parameter_detail'),
    path('room/<int:room_id>/graph/', room_graph_fragment, name='room_graph_fragment'),
    path('api/chart-data/', chart_data, name='chart_data'),
    path('api/latest-measurements/', latest_measurements_api, name='latest_measurements_api'),
    path('api/room/<int:room_id>/live/', room_live_data, name='room_live_data'),
    path('parameter/<int:parameter_id>/export/', export_parameter_csv, name='export_parameter_csv'),
    path('trends/', trends_view, name='trends'),
    path('correlations/', correlations_view, name='correlations'),
    path('monitor/', monitor, name='monitor'),
    path('differential-pressure/', differential_pressure_view, name='differential_pressure'),
    path('custom/<int:dashboard_id>/', custom_dashboard, name='custom_dashboard'),
    path('pressure-calibration/', pressure_calibration_view, name='pressure_calibration'),
    path('api/set-pressure-offset/', set_pressure_offset_api, name='set_pressure_offset_api'),
    path('debug-mqtt/', debug_mqtt, name='debug_mqtt'),
    path('serial-monitor/', serial_monitor, name='serial_monitor'),
    path('events/new/', event_create, name='event_create'),
]
