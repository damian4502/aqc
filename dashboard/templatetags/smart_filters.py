from django import template

register = template.Library()

@register.filter
def smart_float(value, parameter_name=""):
    """Pametno prikaže številko glede na parameter"""
    try:
        float_value = float(value)
        
        # Parametri, ki naj imajo vedno 1 decimalko
        decimal_params = ['temperatura', 'vlaga', 'pm2.5', 'pm10', 'tvoc', 'pm1']
        
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