from django.db import migrations

def create_initial_parameters(apps, schema_editor):
    Parameter = apps.get_model('parameters', 'Parameter')
    
    defaults = [
        {"name": "Temperatura", "unit": "°C", "description": "Temperatura zraka"},
        {"name": "Vlaga", "unit": "%", "description": "Relativna vlažnost zraka"},
        {"name": "AQI", "unit": "", "description": "Air Quality Index"},
        {"name": "CO2", "unit": "ppm", "description": "Koncentracija ogljikovega dioksida"},
        {"name": "PM2.5", "unit": "µg/m³", "description": "Delci PM2.5"},
        {"name": "PM10", "unit": "µg/m³", "description": "Delci PM10"},
        {"name": "TVOC", "unit": "ppb", "description": "Total Volatile Organic Compounds"},
    ]
    
    for param in defaults:
        Parameter.objects.get_or_create(
            name=param["name"],
            defaults={
                "unit": param["unit"],
                "description": param["description"]
            }
        )

class Migration(migrations.Migration):
    dependencies = [
        ('parameters', '0001_initial'),   # spremeni številko glede na tvojo zadnjo migracijo
    ]

    operations = [
        migrations.RunPython(create_initial_parameters),
    ]