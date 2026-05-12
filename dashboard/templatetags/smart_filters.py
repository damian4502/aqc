from django import template
from measurements.models import Measurement
from django.core.cache import cache

register = template.Library()

@register.filter
def smart_float(value, parameter_name=""):
    """Pametno prikaže številko glede na parameter"""
    try:
        float_value = float(value)
        
        # Parametri, ki naj imajo vedno 1 decimalko
        decimal_params = ['temp', 'vlag', 'pm2.5', 'pm10', 'pm1']
        
        if any(p in parameter_name.lower() for p in decimal_params):
            return f"{float_value:.1f}"
        else:
            # Ostali parametri (AQI, CO2, tlak...) → cela števila
            if float_value.is_integer():
                return int(float_value)
            else:
                return f"{float_value:.1f}"
    except (ValueError, TypeError):
        return value
        
@register.simple_tag
def latest_measurement  (room, param):
    key = "last_value" + str(room) + "_" + str(param)
    
    
    try:
        value = cache.get(key, 0)
    except:
        value = 0
    
    #cache.set(key, value, 3600)

    return value