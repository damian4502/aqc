from django.urls import path
from .views import ImportMeasurementsView

urlpatterns = [
    path('import/', ImportMeasurementsView.as_view(), name='import_measurements'),
]
