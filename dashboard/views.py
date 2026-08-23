from django.shortcuts import render
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from measurements.models import Measurement
from parameters.models import Parameter
from sensors.models import Sensor
from rooms.models import Room
from django.db.models import Max

from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from datetime import timedelta

from django.shortcuts import render, get_object_or_404
from django.utils import timezone
import plotly.express as px
import pandas as pd
from datetime import datetime

import pandas as pd
from django.http import HttpResponse
from django.utils import timezone
from datetime import datetime
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import datetime, timedelta
import pandas as pd
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import datetime, timedelta
import pandas as pd
from django.http import JsonResponse

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from rooms.models import Room, Event

def room_live_data(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    
    # Zadnje meritve za ta prostor
    from measurements.models import Measurement
    latest_measurements = Measurement.objects.filter(
        sensor__room=room
    ).select_related('parameter', 'sensor').order_by('-timestamp')[:10]

    data = {
        'room_id': room.id,
        'room_name': room.name,
        'measurements': []
    }

    for m in latest_measurements:
        data['measurements'].append({
            'parameter': m.parameter.name,
            'value': float(m.value),
            'unit': m.parameter.unit or '',
            'time': m.timestamp.strftime("%H:%M:%S")
        })

    return JsonResponse(data)
    
def latest_measurements_api(request):
    from measurements.models import Measurement
    latest = Measurement.objects.select_related('sensor__room', 'parameter')\
        .order_by('-timestamp')[:20]

    ticker = []
    for m in latest:
        ticker.append({
            'room': m.sensor.room.name,
            'parameter': m.parameter.name,
            'value': float(m.value),
            'unit': m.parameter.unit or '',
            'time': m.timestamp.strftime("%H:%M:%S")
        })

    return JsonResponse({'ticker': ticker})
    
def apply_dark_theme(fig, animate=True):
    """Temna tema + neon efekti z varnostjo za različne tipe grafov"""
    
    # Osnovna temna tema (deluje za vse tipe grafov)
    fig.update_layout(
        template="plotly_dark",
        plot_bgcolor='rgba(15, 23, 42, 0.75)',
        paper_bgcolor='rgba(15, 23, 42, 0.0)',
        font=dict(color='#e2e8f0', size=13),
        title_font=dict(size=20, color='#e2e8f0'),
        margin=dict(l=50, r=30, t=70, b=60),
        
        legend=dict(
            bgcolor='rgba(15, 23, 42, 0.9)',
            bordercolor='rgba(59, 130, 246, 0.4)',
            font=dict(color='#e2e8f0')
        ),
        
        hoverlabel=dict(
            bgcolor='rgba(15, 23, 42, 0.95)',
            bordercolor='#3b82f6',
            font_size=13
        )
    )

    # Neon + animacije samo za line/scatter grafe (ne za imshow/heatmap)
    if hasattr(fig, 'data') and len(fig.data) > 0 and fig.data[0].type in ['scatter', 'scattergl']:
        for trace in fig.data:
            trace.update(
                line=dict(width=3.8),
                marker=dict(size=5)
            )
        
        if animate:
            fig.update_layout(
                transition=dict(duration=800, easing='cubic-in-out')
            )

    # Posebna obdelava za heatmap / imshow (correlation)
    if fig.data and fig.data[0].type == 'heatmap':
        fig.update_traces(
            hovertemplate='%{y}<br>%{x}<br>Vrednost: %{z:.2f}<extra></extra>'
        )

    return fig
    
def export_room_csv(request, room_id):
    from django.utils.dateparse import parse_datetime

    room = get_object_or_404(Room, id=room_id)

    # Date range: room_detail form sends start/end (datetime-local);
    # older clients may still send start_date/end_date (date-only).
    all_data = request.GET.get('all') == 'true'
    if all_data:
        start_date = None
        end_date = timezone.now()
    else:
        start_date_str = request.GET.get('start') or request.GET.get('start_date')
        end_date_str = request.GET.get('end') or request.GET.get('end_date')
        if start_date_str and end_date_str:
            try:
                start_date = parse_datetime(start_date_str)
                if start_date is None:
                    start_date = datetime.strptime(start_date_str[:10], '%Y-%m-%d')
                end_date = parse_datetime(end_date_str)
                if end_date is None:
                    end_date = datetime.strptime(end_date_str[:10], '%Y-%m-%d').replace(
                        hour=23, minute=59, second=59
                    )
                if timezone.is_naive(start_date):
                    start_date = timezone.make_aware(start_date)
                if timezone.is_naive(end_date):
                    end_date = timezone.make_aware(end_date)
            except (ValueError, TypeError):
                start_date = timezone.now() - timedelta(days=7)
                end_date = timezone.now()
        else:
            start_date = timezone.now() - timedelta(days=7)
            end_date = timezone.now()

    interval_minutes = int(request.GET.get('interval', 15) or 15)
    fill_method = request.GET.get('fill_method') or request.GET.get('fill', 'ffill')
    ignore_spikes = parse_bool_flag(request.GET.get('ignore_spikes'), default=False)

    qs = Measurement.objects.filter(sensor__room=room).select_related('sensor', 'parameter')
    if not all_data:
        qs = qs.filter(timestamp__gte=start_date, timestamp__lte=end_date)

    measurements = qs.order_by('timestamp')

    if not measurements.exists():
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{room.name}_no_data.csv"'
        return response

    df = pd.DataFrame(list(measurements.values(
        'timestamp',
        'value',
        'parameter__name'
    )))
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.rename(columns={'parameter__name': 'parameter'})

    resampled = resample_measurements(
        df, interval_minutes, fill_method, ignore_spikes=ignore_spikes
    )

    resampled = resampled.reset_index()
    if 'timestamp' in resampled.columns:
        resampled['timestamp'] = pd.to_datetime(resampled['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')

    safe_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in room.name.replace(' ', '_'))
    filename = f"{safe_name}_measurements_{interval_minutes}min.csv"

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    resampled.to_csv(response, index=False, encoding='utf-8')
    return response
    
from django.shortcuts import render, get_object_or_404
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime, timedelta
from django.utils import timezone

from django.shortcuts import render, get_object_or_404
from .models import Dashboard, DashboardWidget
from django.views.decorators.cache import cache_page

def custom_dashboard(request, dashboard_id):
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    if not dashboard:
        dashboard = Dashboard.objects.create(name="Moj dashboard", is_default=True)

    widgets = dashboard.widgets.all().order_by('row', 'column')


    context = {
        'dashboard': dashboard,
        'widgets': widgets,
    }
    return render(request, 'dashboard/custom_dashboard.html', context)
    
#@cache_page(60)
def monitor(request):
    context = {}
    context['parameters'] = Parameter.objects.all().order_by('name')
    context['rooms'] = Room.objects.all().order_by('order')
    
    from sensors.models import MqttSubscription
    context["mqtt_topics"] = MqttSubscription.objects.all()


    return render(request, 'dashboard/monitor.html', context)


def room_detail(request, room_id):
    from django.utils.dateparse import parse_datetime
    room = get_object_or_404(Room, id=room_id)
   
    view_type = request.GET.get('view', 'trend')
    # === Poenoteni datumski filter + hitri gumbi ===
    all_data = request.GET.get('all') == 'true'
    
    if all_data:
        start_date = None
        end_date = timezone.now()
        context_start = ''
        context_end = ''
    else:
        start_date_str = request.GET.get('start' )
        end_date_str = request.GET.get('end')
        
        # Hitri gumbi (24h, 7d, 30d)
        quick_days = request.GET.get('quick')
        quick_hours = request.GET.get('quickh')
        if quick_days:
            try:
                days = int(quick_days)
                start_date = timezone.now() - timedelta(days=days)
                end_date = timezone.now()
            except:
                start_date = timezone.now() - timedelta(days=1)
                end_date = timezone.now()
        elif quick_hours:
            try:
                hours = int(quick_hours)
                start_date = timezone.now() - timedelta(hours=hours)
                end_date = timezone.now()
            except:
                start_date = timezone.now() - timedelta(days=1)
                end_date = timezone.now()
        elif start_date_str and end_date_str:
            try:
                start_date = parse_datetime(start_date_str)
                end_date = parse_datetime(end_date_str)
            except ValueError:
                start_date = timezone.now() - timedelta(days=1)
                end_date = timezone.now()
        else:
            # privzeto
            start_date = timezone.now() - timedelta(days=1)
            end_date = timezone.now()

        context_start = start_date.strftime('%Y-%m-%d') if start_date else ''
        context_end = end_date.strftime('%Y-%m-%d') if end_date else ''
        request.session['chart_start_date'] = context_start
        request.session['chart_end_date'] = context_end
    
    # Zadnje meritve za vrh kartice

    context = {
        'room': room,
        'start_date': context_start,
        'end_date': context_end,
        'view_type': view_type,
        'all_data': all_data,
        'start': start_date,
        'end': end_date,
    }

    interval_minutes = int(request.GET.get('interval', request.session.get('resample_interval', 15)))
    fill_method = request.GET.get('fill_method', request.session.get('resample_fill_method', 'ffill'))
    if 'ignore_spikes' in request.GET:
        ignore_spikes = parse_bool_flag(request.GET.get('ignore_spikes'), default=False)
    else:
        ignore_spikes = bool(request.session.get('ignore_spikes', False))

    context['interval'] = interval_minutes
    context['fill_method'] = fill_method
    context['ignore_spikes'] = ignore_spikes

    """
    # === Meritve za grafe ===
    qs = Measurement.objects.filter(sensor__room=room)
    if not all_data and start_date:
        qs = qs.filter(timestamp__gte=start_date, timestamp__lte=end_date)

    measurements = qs.select_related('parameter').order_by('timestamp')

    if not measurements.exists():
        context['no_data'] = True
        return render(request, 'dashboard/room_detail.html', context)

    # Priprava DataFrame
    df = pd.DataFrame(list(measurements.values('timestamp', 'value', 'parameter__name')))
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.rename(columns={'parameter__name': 'parameter'})

    # === Shranjevanje / branje resampling nastavitev iz session ===
    if request.method == 'GET':
        # Če uporabnik pošlje nove vrednosti → shrani v session
        if 'interval' in request.GET:
            request.session['resample_interval'] = int(request.GET.get('interval'))
        if 'fill_method' in request.GET:
            request.session['resample_fill_method'] = request.GET.get('fill_method')

    # Resampling parametri
    interval_minutes = int(request.GET.get('interval', request.session.get('resample_interval', 15)))
    fill_method = request.GET.get('fill_method', request.session.get('resample_fill_method', 'ffill'))
    if 'ignore_spikes' in request.GET:
        ignore_spikes = parse_bool_flag(request.GET.get('ignore_spikes'), default=False)
    else:
        ignore_spikes = bool(request.session.get('ignore_spikes', False))

    # Shrani v session
    request.session['resample_interval'] = interval_minutes
    request.session['resample_fill_method'] = fill_method
    request.session['ignore_spikes'] = ignore_spikes

    context['interval'] = interval_minutes
    context['fill_method'] = fill_method
    context['ignore_spikes'] = ignore_spikes
    
    # === Glavna logika po view_type ===
    if view_type == 'trend':
        resampled = resample_measurements(df, interval_minutes, fill_method, ignore_spikes)
        if not resampled.empty:
            fig = px.line(resampled, x=resampled.index, y=resampled.columns,
                         title=f'Časovni trend - {room.name}',
                         height=700)
            fig = apply_dark_theme(fig)

            for trace in fig.data:
                if trace.name.lower() not in ['pm10', 'pm1', 'pm2.5']:
                    trace.visible = 'legendonly'
        
            events = Event.objects.filter(
                rooms=room,
                timestamp__gte=start_date,
                timestamp__lte=end_date
            ).distinct()


            for event in events:
                event_ts = timezone.localtime(event.timestamp).replace(tzinfo=None)
                
                fig.add_vline(
                    x=event_ts,
                    line_width=2.5,
                    line_dash="dashdot",
                    line_color=event.color,
                )
                
                fig.add_annotation(
                    x=event_ts,
                    yref="paper",
                    y=1.06,
                    text=event.title,
                    showarrow=False,
                    xanchor="center",
                    yanchor="bottom",
                    font=dict(size=13, color=event.color),
                    bgcolor="rgba(15, 23, 42, 0.92)",
                    bordercolor=event.color,
                    borderwidth=1,
                    borderpad=4,
                )

            context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

    elif view_type == 'correlation':
        resampled = resample_measurements(df, interval_minutes, fill_method, ignore_spikes)
        
        if resampled.empty or len(resampled.columns) < 2:
            context['no_data'] = True
        else:
            corr_matrix = resampled.corr().round(2)
            
            fig = px.imshow(
                corr_matrix,
                text_auto=True,
                aspect="auto",
                color_continuous_scale='RdBu_r',
                title=f'Korelacija parametrov - {room.name} (interval: {interval_minutes} min)'
            )
            fig.update_layout(height=650)
            fig = apply_dark_theme(fig)
            context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

    elif view_type == 'hourly':
        df['hour'] = df['timestamp'].dt.hour
        hourly = df.groupby(['hour', 'parameter'])['value'].mean().reset_index()
        fig = px.line(hourly, x='hour', y='value', color='parameter',
                     title='Dnevni vzorec (povprečje po uri)', markers=True)
        fig.update_layout(xaxis=dict(tickmode='linear', dtick=1))
        fig = apply_dark_theme(fig)
        context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

    elif view_type == 'weekly':
        df['dayofweek'] = df['timestamp'].dt.day_name()
        df['weekday'] = df['timestamp'].dt.weekday
       
        # Tedenski line chart
        weekly = df.groupby(['weekday', 'dayofweek', 'parameter'])['value'].mean().reset_index()
        fig_weekly = px.line(weekly, x='dayofweek', y='value', color='parameter',
                            title='Tedenski vzorec (line)',
                            category_orders={"dayofweek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]})
        context['fig_weekly'] = fig_weekly.to_html(full_html=False, include_plotlyjs='cdn')

        # Tedenski heatmap
        heatmap_data = df.groupby(['dayofweek', 'parameter'])['value'].mean().unstack()
        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        heatmap_data = heatmap_data.reindex(day_order)
       
        fig_heatmap = px.imshow(
            heatmap_data.T,
            text_auto=True,
            aspect="auto",
            color_continuous_scale='RdYlGn_r',
            title='Tedenski heatmap vzorec (povprečne vrednosti)'
        )
        fig_heatmap.update_layout(height=600)
        context['fig_heatmap'] = fig_heatmap.to_html(full_html=False, include_plotlyjs='cdn')
    """
    
    fig = px.line([],
                 title=f'Časovni trend - {room.name}',
                 height=700)
    fig = apply_dark_theme(fig)
    context["fig"] = fig
    return render(request, 'dashboard/room_detail.html', context)

def pressure_calibration_view(request):
    """Stran za kalibracijo senzorjev tlaka (primerjava 2 senzorjev + MQTT offset)."""
    pressure_param = Parameter.objects.filter(id=11).first()

    sensors = Sensor.objects.filter(
        parameter=pressure_param
    ).select_related('room') if pressure_param else Sensor.objects.none()

    context = {
        'sensors': sensors,
        'pressure_parameter': pressure_param,
    }
    return render(request, 'dashboard/pressure_calibration_v2.html', context)
    
import json
from pathlib import Path
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404
@require_POST
@csrf_exempt
def set_pressure_offset_api(request):
    """
    Posodobi pressure_offsets v offsets.json.
    Novo izmerjeno delta PRISTEJE k obstoječemu offsetu v JSONu,
    ker senzor že objavlja vrednost Z upoštevanim trenutnim offsetom.
    """
    try:
        payload = json.loads(request.body)
        sensor_id = int(payload.get("sensor_id"))
        delta = float(payload.get("offset"))          # to je avgDelta iz kalibracije (ref - cal)
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"success": False, "error": "Neveljaven vhod"}, status=400)

    # Poišči senzor
    try:
        from sensors.models import Sensor
        sensor = get_object_or_404(Sensor, id=sensor_id)
    except Exception:
        from django.apps import apps
        Sensor = apps.get_model('sensors', 'Sensor')
        sensor = get_object_or_404(Sensor, id=sensor_id)

    room = sensor.room

    # Ključ v JSON datoteki
    key = room.name.lower()
    key = (key.replace("č", "c")
              .replace("š", "s")
              .replace("ž", "z")
              .replace("ć", "c")
              .replace(" ", "_")
              .replace("đ", "d"))

    offsets_path = Path(settings.BASE_DIR) / "static" / "offsets.json"
    if not offsets_path.exists():
        return JsonResponse({"success": False, "error": f"offsets.json ni najden: {offsets_path}"}, status=500)

    with open(offsets_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "pressure_offsets" not in config:
        config["pressure_offsets"] = {}

    current_offset = config["pressure_offsets"].get(key, 0.0)

    # === KLJUČNO: seštejemo obstoječi offset + novo izmerjeno delta ===
    new_total_offset = current_offset + delta

    config["pressure_offsets"][key] = round(new_total_offset, 2)

    with open(offsets_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return JsonResponse({
        "success": True,
        "room": room.name,
        "json_key": key,
        "previous_total_offset": round(current_offset, 2),
        "delta_applied": round(delta, 2),
        "new_total_offset": round(new_total_offset, 2),
        "message": f"Za '{key}': {current_offset:.2f} + {delta:.2f} = {new_total_offset:.2f} Pa"
    })
    
    
import pandas as pd
from django.utils import timezone
def resample_measurements(df, interval_minutes=15, fill_method='ffill', ignore_spikes=False):
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    if df['timestamp'].dt.tz is not None:
        df['timestamp'] = df['timestamp'].dt.tz_convert('Europe/Ljubljana').dt.tz_localize(None)
    #df.tz_localize("CET")
    df = df.set_index('timestamp')

    # Pivot
    pivot = df.pivot_table(index=df.index, columns='parameter' if 'parameter' in df.columns else 'room', 
                          values='value', aggfunc='mean')

    # Resampling
    freq_map = {1: '1min', 5: '5min', 15: '15min', 60: 'h', 1440: 'D'}
    freq = freq_map.get(interval_minutes, '15min')
    resampled = pivot.resample(freq).mean()

    # === ODSTRANJEVANJE SKOKOV (outlier removal) ===
    if ignore_spikes and not resampled.empty:
        for col in resampled.columns:
            series = resampled[col]
            # IQR metoda
            Q1 = series.quantile(0.25)
            Q3 = series.quantile(0.75)
            IQR = Q3 - Q1
            lower_bound = Q1 - 2.5 * IQR
            upper_bound = Q3 + 2.5 * IQR
            
            # Zamenjamo ekstremne vrednosti z rolling median (okno 5 točk)
            rolling_median = series.rolling(window=5, center=True, min_periods=1).median()
            resampled[col] = series.where((series >= lower_bound) & (series <= upper_bound), rolling_median)

    # Polnjenje manjkajočih vrednosti
    if fill_method == 'ffill':
        resampled = resampled.ffill()
    elif fill_method == 'bfill':
        resampled = resampled.bfill()
    elif fill_method == 'interpolate':
        resampled = resampled.interpolate(method='linear')
    elif fill_method == 'zero':
        resampled = resampled.fillna(0)
    
    return resampled

import plotly.graph_objects as go


def parse_bool_flag(value, default=False):
    """Parse common truthy/falsey query values (on/off, 1/0, true/false)."""
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'y')

def chart_data(request):
    from django.utils.dateparse import parse_datetime
    """Vrača Plotly JSON za dinamično nalaganje v JS."""
    room = None
    parameter = None

    room_id = request.GET.get('room_id')
    if room_id:
        room = get_object_or_404(Room, pk=room_id)

    parameter_id = request.GET.get('parameter_id')
    if parameter_id:
        parameter = get_object_or_404(Parameter, pk=parameter_id)

    # --- Parse parametrov (enako kot sedaj) ---
    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    interval = int(request.GET.get('interval', 60))  # minute
    fill_method = request.GET.get('fill', 'ffill')
    all_data = request.GET.get('all_data') == 'true'
    show_params = request.GET.get('show_params')
    context = {}
    
    if not show_params and parameter:
        show_params = ['*']
    elif not show_params:
        show_params = ['pm10', 'pm size']
    else:
        show_params = show_params.split(',')

    # default: zadnjih 24h
    if not start_str:
        end = timezone.now()
        start = end - timedelta(hours=24)
    else:
        start = parse_datetime(start_str) or (timezone.now() - timedelta(hours=24))
        end = parse_datetime(end_str) or timezone.now()

    # === Meritve za grafe ===
    qs = Measurement.objects.all()
    if room:
        qs = qs.filter(sensor__room=room)
    if parameter:
        qs = qs.filter(parameter=parameter)
        
    if not all_data and start:
        qs = qs.filter(timestamp__gte=start, timestamp__lte=end)

    measurements = qs.select_related('parameter').order_by('timestamp')

    if not measurements.exists():
        context['no_data'] = True
        return render(request, 'dashboard/room_detail.html', context)

    # Priprava DataFrame
    if room:
        df = pd.DataFrame(list(measurements.values('timestamp', 'value', 'parameter__name')))
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.rename(columns={'parameter__name': 'parameter'})
    if parameter:
        df = pd.DataFrame(list(measurements.values('timestamp', 'value', 'sensor__room__name')))
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.rename(columns={'sensor__room__name': 'parameter'})

    # === Shranjevanje / branje resampling nastavitev iz session ===
    if request.method == 'GET':
        # Če uporabnik pošlje nove vrednosti → shrani v session
        if 'interval' in request.GET:
            request.session['resample_interval'] = int(request.GET.get('interval'))
        if 'fill_method' in request.GET:
            request.session['resample_fill_method'] = request.GET.get('fill_method')

    # Resampling parameters
    interval_minutes = int(request.GET.get('interval', request.session.get('resample_interval', 15)))
    fill_method = (
        request.GET.get('fill_method')
        or request.GET.get('fill')
        or request.session.get('resample_fill_method', 'ffill')
    )
    if 'ignore_spikes' in request.GET:
        ignore_spikes = parse_bool_flag(request.GET.get('ignore_spikes'), default=False)
    else:
        ignore_spikes = bool(request.session.get('ignore_spikes', False))

    # Persist in session
    request.session['resample_interval'] = interval_minutes
    request.session['resample_fill_method'] = fill_method
    request.session['ignore_spikes'] = ignore_spikes

    context['interval'] = interval_minutes
    context['fill_method'] = fill_method
    context['ignore_spikes'] = ignore_spikes

    resampled = resample_measurements(df, interval_minutes, fill_method, ignore_spikes)
    if not resampled.empty:
        fig = px.line(resampled, x=resampled.index, y=resampled.columns,
                     title=f'Časovni trend',
                     height=700)

        for trace in fig.data:
            if trace.name.lower() not in show_params and '*' not in show_params:
                trace.visible = 'legendonly'

        #context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

    qs = Measurement.objects.filter(
        sensor__room=room,
        timestamp__gte=start,
        timestamp__lte=end
    ).select_related('parameter').order_by('timestamp')

    # ... tvoja koda za df in traces ...

    #fig = go.Figure()
    # ... dodaj traces z visible='legendonly' kjer hočeš ...

    # --- Dogodki (events) kot vlines + annotations ---
    events = Event.objects.filter(
        timestamp__gte=start,
        timestamp__lte=end
    ).distinct()
    
    if room:
        events = events.filter(rooms=room)
    if parameter:
        events = events.filter(parameters=parameter)

    for event in events:
        event_ts = timezone.localtime(event.timestamp).replace(tzinfo=None)
        fig.add_vline(
            x=event_ts,
            line_width=2.5,
            line_dash="dashdot",
            line_color=event.color,
        )
        fig.add_annotation(
            x=event_ts,
            yref="paper",
            y=1.06,
            text=event.title,
            showarrow=False,
            xanchor="center",
            yanchor="bottom",
            font=dict(size=13, color=event.color),
            bgcolor="rgba(15, 23, 42, 0.92)",
            bordercolor=event.color,
            borderwidth=1,
            borderpad=4,
        )

    # --- Ključno: vrni čisti Plotly JSON ---
    fig = apply_dark_theme(fig)
    import plotly.io as pio
    return HttpResponse(
            pio.to_json(fig, validate=False, engine='json'),
            content_type='application/json'
        )
    return JsonResponse(fig.to_dict(), safe=False)
    
    
def room_graph_fragment(request, room_id):
    """Vrača SAMO graf za HTMX (fragment)"""
    room = get_object_or_404(Room, id=room_id)
    view_type = request.GET.get('view', 'trend')
    all_data = request.GET.get('all') == 'true'

    # Datumski filter
    if all_data:
        start_date = None
        end_date = timezone.now()
    else:
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        days = request.GET.get('days')

        if days:
            try:
                days_int = int(days)
                start_date = timezone.now() - timedelta(days=days_int)
                end_date = timezone.now()
            except:
                start_date = timezone.now() - timedelta(days=14)
                end_date = timezone.now()
        elif start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            except ValueError:
                start_date = timezone.now() - timedelta(days=14)
                end_date = timezone.now()
        else:
            start_date = timezone.now() - timedelta(days=14)
            end_date = timezone.now()

    # Pridobi meritve
    qs = Measurement.objects.filter(sensor__room=room)
    if not all_data:
        qs = qs.filter(timestamp__gte=start_date, timestamp__lte=end_date)

    measurements = qs.select_related('parameter').order_by('timestamp')

    if not measurements.exists():
        return HttpResponse('<div class="text-center py-20 text-slate-400">Za izbrano obdobje ni podatkov.</div>')

    df = pd.DataFrame(list(measurements.values('timestamp', 'value', 'parameter__name')))
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.rename(columns={'parameter__name': 'parameter'})

    fig = None

    if view_type == 'trend':
        fig = px.line(df, x='timestamp', y='value', color='parameter',
                     title=f'Časovni trend - {room.name}', height=700)

    elif view_type == 'correlation':
        pivot = df.pivot_table(index='timestamp', columns='parameter', values='value')
        corr_matrix = pivot.corr().round(2)

        # Opcija 1: Izboljšan Heatmap
        fig = px.imshow(corr_matrix, 
                       text_auto=True, 
                       aspect="auto", 
                       color_continuous_scale='RdBu_r',
                       title='Korelacija med parametri')
        fig.update_traces(hovertemplate='%{y} in %{x}<br>Korelacija: %{z:.2f}<extra></extra>')

    elif view_type == 'correlation_network':
        # Network Graph alternativa
        corr_matrix = df.pivot_table(index='timestamp', columns='parameter', values='value').corr()
        
        import networkx as nx
        G = nx.Graph()
        
        params = corr_matrix.columns
        for i in range(len(params)):
            for j in range(i+1, len(params)):
                corr = abs(corr_matrix.iloc[i,j])
                if corr > 0.3:  # prikaži samo pomembne korelacije
                    G.add_edge(params[i], params[j], weight=corr)

        pos = nx.spring_layout(G, seed=42)
        
        edge_x = []
        edge_y = []
        for edge in G.edges():
            x0, y0 = pos[edge[0]]
            x1, y1 = pos[edge[1]]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])

        edge_trace = go.Scatter(
            x=edge_x, y=edge_y,
            line=dict(width=2, color='#60a5fa'),
            hoverinfo='none',
            mode='lines')

        node_x = []
        node_y = []
        node_text = []
        for node in G.nodes():
            x, y = pos[node]
            node_x.append(x)
            node_y.append(y)
            node_text.append(node)

        node_trace = go.Scatter(
            x=node_x, y=node_y,
            mode='markers+text',
            text=node_text,
            textposition="top center",
            marker=dict(
                size=25,
                color='#3b82f6',
                line=dict(width=2, color='#1e40af')
            ),
            hoverinfo='text'
        )

        fig = go.Figure(data=[edge_trace, node_trace])
        fig.update_layout(
            title='Omrežje korelacij med parametri',
            height=700,
            showlegend=False,
            plot_bgcolor='rgba(15,23,42,0.7)'
        )

    elif view_type == 'hourly':
        df['hour'] = df['timestamp'].dt.hour
        hourly = df.groupby(['hour', 'parameter'])['value'].mean().reset_index()
        fig = px.line(hourly, x='hour', y='value', color='parameter',
                     title='Dnevni vzorec (povprečje po uri)', markers=True)
        fig.update_layout(xaxis=dict(tickmode='linear', dtick=1))

    elif view_type == 'weekly':
        df['dayofweek'] = df['timestamp'].dt.day_name()
        weekly = df.groupby(['dayofweek', 'parameter'])['value'].mean().reset_index()
        fig = px.line(weekly, x='dayofweek', y='value', color='parameter',
                     title='Tedenski vzorec',
                     category_orders={"dayofweek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]})

    if fig:
        fig = apply_dark_theme(fig)
        return HttpResponse(fig.to_html(full_html=False, include_plotlyjs='cdn'))

    return HttpResponse('<div class="text-center py-20 text-slate-400">Ni grafičnih podatkov.</div>')

def create_aqi_gauge(aqi_value):
    """Manjši AQI Gauge z barvnim kodiranjem"""
    if not aqi_value:
        aqi_value = 0
    
    # Barvno kodiranje
    if aqi_value <= 50:
        color = "#10b981"      # Zelena
        status = "Dobro"
    elif aqi_value <= 100:
        color = "#eab308"      # Rumena
        status = "Zmerno"
    elif aqi_value <= 150:
        color = "#f97316"      # Oranžna
        status = "Slabo"
    elif aqi_value <= 200:
        color = "#ef4444"      # Rdeča
        status = "Zelo slabo"
    else:
        color = "#7c3aed"      # Vijolična
        status = "Nevarno"

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=aqi_value,
        domain={'x': [0, 1], 'y': [0, 1]},
        #title={'text': "AQI", 'font': {'size': 16}},
        gauge={
            'axis': {'range': [0, 500], 'tickwidth': 1, 'tickcolor': "#64748b"},
            'bar': {'color': color},
            'bgcolor': "rgba(15,23,42,0.6)",
            'borderwidth': 1,
            'bordercolor': "#475569",
            'steps': [
                {'range': [0, 50], 'color': 'rgba(16,185,129,0.25)'},
                {'range': [50, 100], 'color': 'rgba(234,179,8,0.25)'},
                {'range': [100, 150], 'color': 'rgba(249,115,22,0.25)'},
                {'range': [150, 200], 'color': 'rgba(239,68,68,0.25)'},
                {'range': [200, 300], 'color': 'rgba(124,58,237,0.25)'},
            ],
            'threshold': {
                'line': {'color': "white", 'width': 3},
                'thickness': 0.8,
                'value': aqi_value
            }
        }
    ))

    fig.update_layout(
        height=80,                    # manjša višina
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#e2e8f0', size=10)
    )

    return fig.to_html(full_html=False, include_plotlyjs='cdn')
    
def dashboard_overview(request):
    rooms = Room.objects.all().order_by('order')
    
    room_data = []
    from django.core.cache import cache
    for room in rooms:
        # Zadnje meritve (vse parametre še vedno prikažemo v tabeli)
        #latest = Measurement.objects.filter(sensor__room=room)\
        #    .select_related('parameter', 'sensor')\
        #    .order_by('parameter__order', '-timestamp')\
        #    .distinct('parameter__order')[:8]
        
        tparams = Parameter.objects.all().order_by('order')
        params = [{'name':x.name, 'id':x.id, 'unit':x.unit} for x in tparams if cache.get("last_value" + str(room.id) + "_" + str(x.id), 0)]
        
        """
        # Mini graf - samo AQI za zadnjih 24 ur
        mini_fig = None
        aqi_measurements = Measurement.objects.filter(
            sensor__room=room,
            parameter__name__iexact="AQI",   # iščemo parameter z imenom AQI (ne glede na velike/male črke)
            timestamp__gte=timezone.now() - timedelta(hours=24)
        ).order_by('timestamp')
        
        if aqi_measurements.exists() and 1 == 2:
            df = pd.DataFrame(list(aqi_measurements.values('timestamp', 'value')))
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            fig = px.line(df, x='timestamp', y='value', 
                         title='', 
                         height=140,
                         line_shape='linear')
            
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                showlegend=False,
                xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                yaxis=dict(showgrid=True, gridcolor='rgba(148,163,184,0.2)', title=None),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                font=dict(size=10, color='#94a3b8')
            )
            
            # Dodaj rahlo zeleno/barvno kodiranje za AQI
            fig.update_traces(line=dict(color='#10b981', width=2.5))
            
            mini_fig = fig.to_html(full_html=False, include_plotlyjs='cdn')

        # AQI Gauge za kartico
        aqi_gauge = None
        latest_aqi = Measurement.objects.filter(
            sensor__room=room,
            parameter__name__iexact="AQI"
        ).order_by('-timestamp').first()
        
        if latest_aqi:
            aqi_gauge = create_aqi_gauge(latest_aqi.value)
        """
            
        room_data.append({
            'room': room,
            'latest': params,
            #'mini_fig': mini_fig,
            #'aqi_gauge': aqi_gauge,
            #'has_aqi': aqi_measurements.exists()
        })
    
    context = {'room_data': room_data}
    return render(request, 'dashboard/overview.html', context)
    
def dashboard(request):
    # Pridobi zadnjih 7 dni podatkov (lahko spremeniš)
    measurements = Measurement.objects.select_related('sensor', 'sensor__room', 'parameter')\
        .order_by('timestamp')\
        .filter(timestamp__gte=pd.Timestamp.now() - pd.Timedelta(days=7))

    if not measurements.exists():
        return render(request, 'dashboard/dashboard.html', {'no_data': True})

    df = pd.DataFrame(list(measurements.values(
        'timestamp', 
        'value', 
        'parameter__name', 
        'sensor__room__name',
        'sensor__name'
    )))

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.rename(columns={
        'parameter__name': 'parameter',
        'sensor__room__name': 'room'
    })

    # Glavni graf: vsi parametri skozi čas
    fig_main = px.line(
        df, 
        x='timestamp', 
        y='value', 
        color='parameter',
        facet_col='room',
        title='Meritve po prostorih in parametrih (zadnjih 7 dni)',
        labels={'value': 'Vrednost', 'timestamp': 'Čas'}
    )
    fig_main.update_layout(height=700)

    # Zadnje vrednosti (tabela)
    latest = df.groupby(['room', 'parameter']).last().reset_index()

    context = {
        'fig_main': fig_main.to_html(full_html=False, include_plotlyjs='cdn'),
        'latest_data': latest.to_dict('records'),
        'rooms': Sensor.objects.values_list('room__name', flat=True).distinct()
    }

    return render(request, 'dashboard/dashboard.html', context)


def parameter_detail(request, parameter_id):
    from django.utils.dateparse import parse_datetime
    parameter = get_object_or_404(Parameter, id=parameter_id)
    
    view_type = request.GET.get('view', 'trend')
    # === Poenoteni datumski filter + hitri gumbi ===
    all_data = request.GET.get('all') == 'true'
    
    if all_data:
        start_date = None
        end_date = timezone.now()
        context_start = ''
        context_end = ''
    else:
        start_date_str = request.GET.get('start' )
        end_date_str = request.GET.get('end')
        
        # Hitri gumbi (24h, 7d, 30d)
        quick_days = request.GET.get('quick')
        quick_hours = request.GET.get('quickh')
        if quick_days:
            try:
                days = int(quick_days)
                start_date = timezone.now() - timedelta(days=days)
                end_date = timezone.now()
            except:
                start_date = timezone.now() - timedelta(days=1)
                end_date = timezone.now()
        elif quick_hours:
            try:
                hours = int(quick_hours)
                start_date = timezone.now() - timedelta(hours=hours)
                end_date = timezone.now()
            except:
                start_date = timezone.now() - timedelta(days=1)
                end_date = timezone.now()
        elif start_date_str and end_date_str:
            try:
                start_date = parse_datetime(start_date_str)
                end_date = parse_datetime(end_date_str)
            except ValueError:
                start_date = timezone.now() - timedelta(days=1)
                end_date = timezone.now()
        else:
            # privzeto
            start_date = timezone.now() - timedelta(days=1)
            end_date = timezone.now()

        context_start = start_date.strftime('%Y-%m-%d') if start_date else ''
        context_end = end_date.strftime('%Y-%m-%d') if end_date else ''
        request.session['chart_start_date'] = context_start
        request.session['chart_end_date'] = context_end

    # Zadnje meritve za ta parameter (po prostorih)
        

    context = {
        'parameter': parameter,
        'start_date': context_start,
        'end_date': context_end,
        'view_type': view_type,
        'all_data': all_data,
        'start': start_date,
        'end': end_date,
        'rooms':Room.objects.all()
    }

    # === Shranjevanje / branje resampling nastavitev iz session ===
    if request.method == 'GET':
        # Če uporabnik pošlje nove vrednosti → shrani v session
        if 'interval' in request.GET:
            request.session['resample_interval'] = int(request.GET.get('interval'))
        if 'fill_method' in request.GET:
            request.session['resample_fill_method'] = request.GET.get('fill_method')

    # Resampling parametri
    interval_minutes = int(request.GET.get('interval', request.session.get('resample_interval', 15)))
    fill_method = request.GET.get('fill_method', request.session.get('resample_fill_method', 'ffill'))
    if 'ignore_spikes' in request.GET:
        ignore_spikes = parse_bool_flag(request.GET.get('ignore_spikes'), default=False)
    else:
        ignore_spikes = bool(request.session.get('ignore_spikes', False))

    # Shrani v session
    request.session['resample_interval'] = interval_minutes
    request.session['resample_fill_method'] = fill_method
    request.session['ignore_spikes'] = ignore_spikes

    context['interval'] = interval_minutes
    context['fill_method'] = fill_method
    context['ignore_spikes'] = ignore_spikes
    
    # Meritve za grafe
    qs = Measurement.objects.filter(parameter=parameter)

    if not all_data:
        qs = qs.filter(timestamp__gte=start_date, timestamp__lte=end_date)

    measurements = qs.select_related('sensor__room').order_by('timestamp')

    if measurements.exists():
        df = pd.DataFrame(list(measurements.values(
            'timestamp', 
            'value', 
            'sensor__room__name'
        )))
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.rename(columns={'sensor__room__name': 'room'})

        if view_type == 'trend':
            resampled = resample_measurements(df, interval_minutes, fill_method, ignore_spikes=ignore_spikes)

            fig = px.line(resampled, x=resampled.index, y=resampled.columns,
                         title=f'Časovni trend - {parameter.name}',
                         height=700)
            fig = apply_dark_theme(fig)

            events = Event.objects.filter(
                parameters=parameter,
                timestamp__gte=start_date,
                timestamp__lte=end_date
            )


            for event in events:
                event_ts = timezone.localtime(event.timestamp).replace(tzinfo=None)
                
                fig.add_vline(
                    x=event_ts,
                    line_width=2.5,
                    line_dash="dashdot",
                    line_color=event.color,
                )
                
                fig.add_annotation(
                    x=event_ts,
                    yref="paper",
                    y=1.06,
                    text=event.title,
                    showarrow=False,
                    xanchor="center",
                    yanchor="bottom",
                    font=dict(size=13, color=event.color),
                    bgcolor="rgba(15, 23, 42, 0.92)",
                    bordercolor=event.color,
                    borderwidth=1,
                    borderpad=4,
                )

            context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

        elif view_type == 'correlation':
            resampled = resample_measurements(df, interval_minutes, fill_method, ignore_spikes=ignore_spikes)
            
            if resampled.empty or len(resampled.columns) < 2:
                context['no_data'] = True
            else:
                corr_matrix = resampled.corr().round(2)
                
                fig = px.imshow(
                    corr_matrix,
                    text_auto=True,
                    aspect="auto",
                    color_continuous_scale='RdBu_r',
                    title=f'Korelacija parametrov - {parameter.name} (interval: {interval_minutes} min)'
                )
                fig.update_layout(height=650)
                fig = apply_dark_theme(fig)
                context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

        elif view_type == 'hourly':
            df['hour'] = df['timestamp'].dt.hour
            hourly = df.groupby(['hour', 'room'])['value'].mean().reset_index()
            fig = px.line(hourly, x='hour', y='value', color='room',
                         title=f'Dnevni vzorec za {parameter.name}', markers=True)
            fig.update_layout(xaxis=dict(tickmode='linear', dtick=1))
            fig = apply_dark_theme(fig)
            context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

        elif view_type == 'weekly':
            df['dayofweek'] = df['timestamp'].dt.day_name()
            weekly = df.groupby(['dayofweek', 'room'])['value'].mean().reset_index()
            fig = px.line(weekly, x='dayofweek', y='value', color='room',
                         title=f'Tedenski vzorec za {parameter.name}',
                         category_orders={"dayofweek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]})
            fig = apply_dark_theme(fig)
            context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

    else:
        context['no_data'] = True

    return render(request, 'dashboard/parameter_detail.html', context)

def export_parameter_csv(request, parameter_id):
    from django.utils.dateparse import parse_datetime

    parameter = get_object_or_404(Parameter, id=parameter_id)

    # Datumski filter
    # Form from parameter_detail sends start/end (datetime-local);
    # older clients may still send start_date/end_date (date-only).
    all_data = request.GET.get('all') == 'true'
    if all_data:
        start_date = None
        end_date = timezone.now()
    else:
        start_date_str = request.GET.get('start') or request.GET.get('start_date')
        end_date_str = request.GET.get('end') or request.GET.get('end_date')
        if start_date_str and end_date_str:
            try:
                start_date = parse_datetime(start_date_str)
                if start_date is None:
                    start_date = datetime.strptime(start_date_str[:10], '%Y-%m-%d')
                end_date = parse_datetime(end_date_str)
                if end_date is None:
                    end_date = datetime.strptime(end_date_str[:10], '%Y-%m-%d').replace(
                        hour=23, minute=59, second=59
                    )
                if timezone.is_naive(start_date):
                    start_date = timezone.make_aware(start_date)
                if timezone.is_naive(end_date):
                    end_date = timezone.make_aware(end_date)
            except (ValueError, TypeError):
                start_date = timezone.now() - timedelta(days=30)
                end_date = timezone.now()
        else:
            start_date = timezone.now() - timedelta(days=30)
            end_date = timezone.now()

    # Resampling parametri
    interval_minutes = int(request.GET.get('interval', 15))
    fill_method = request.GET.get('fill_method') or request.GET.get('fill', 'ffill')
    ignore_spikes = parse_bool_flag(request.GET.get('ignore_spikes'), default=False)

    # Pridobi meritve
    qs = Measurement.objects.filter(parameter=parameter).select_related('sensor__room')
    if not all_data:
        qs = qs.filter(timestamp__gte=start_date, timestamp__lte=end_date)

    measurements = qs.order_by('timestamp')

    if not measurements.exists():
        # Prazen CSV
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{parameter.name}_no_data.csv"'
        return response

    df = pd.DataFrame(list(measurements.values(
        'timestamp', 'value', 'sensor__room__name'
    )))
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.rename(columns={'sensor__room__name': 'room'})

    # Resampling — pivot column is auto-detected (parameter vs room)
    resampled = resample_measurements(
        df, interval_minutes, fill_method, ignore_spikes=ignore_spikes
    )

    # Priprava za CSV
    resampled = resampled.reset_index()
    if 'timestamp' in resampled.columns:
        resampled['timestamp'] = pd.to_datetime(resampled['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')

    # Ime datoteke
    filename = f"{parameter.name.replace(' ', '_')}_{interval_minutes}min.csv"

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    resampled.to_csv(response, index=False, encoding='utf-8')

    return response

from django.db.models import Avg
import numpy as np
from scipy.stats import linregress

def trends_view(request):
    parameters = Parameter.objects.all().order_by('name')
    trends_data = []
    
    for param in parameters:
        measurements = Measurement.objects.filter(
            parameter=param,
            timestamp__gte=timezone.now() - timedelta(days=30)
        ).select_related('sensor__room')
        
        if not measurements.exists():
            continue
            
        rooms_data = []
        
        for room in Room.objects.all():
            room_measurements = measurements.filter(sensor__room=room)
            if room_measurements.count() < 5:
                continue
                
            df = pd.DataFrame(list(room_measurements.values('timestamp', 'value')))
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp')
            
            x = np.arange(len(df))
            y = df['value'].values
            slope, _, r_value, _, _ = linregress(x, y)
            
            current_avg = room_measurements.filter(
                timestamp__gte=timezone.now() - timedelta(days=7)
            ).aggregate(Avg('value'))['value__avg'] or 0
            
            prev_avg = room_measurements.filter(
                timestamp__gte=timezone.now() - timedelta(days=14),
                timestamp__lt=timezone.now() - timedelta(days=7)
            ).aggregate(Avg('value'))['value__avg'] or current_avg
            
            change_7d = ((current_avg - prev_avg) / prev_avg * 100) if prev_avg != 0 else 0

            # === PAMETNO BARVANJE glede na higher_is_worse ===
            if param.higher_is_worse:
                # Višje = slabše → negativen slope je dober
                trend_color = 'text-emerald-400' if slope <= 0 else 'text-red-400'
            else:
                # Višje = bolje (npr. temperatura) → pozitiven slope je lahko dober
                trend_color = 'text-emerald-400' if slope >= 0 else 'text-amber-400'

            rooms_data.append({
                'room': room,
                'current_value': round(current_avg, 2),
                'slope': round(slope, 4),
                'change_7d': round(change_7d, 1),
                'r_squared': round(r_value**2, 3),
                'trend_color': trend_color,
            })
        
        # Sortiranje: za "slabe" parametre želimo najmanjši slope na vrhu
        if param.higher_is_worse:
            rooms_data.sort(key=lambda x: x['slope'])           # manjši (bolj negativen) = boljši
        else:
            rooms_data.sort(key=lambda x: x['slope'], reverse=True)

        trends_data.append({
            'parameter': param,
            'rooms': rooms_data[:12]
        })
    
    context = {'trends_data': trends_data}
    return render(request, 'dashboard/trends.html', context)


def differential_pressure_view(request):
    pressure_param = Parameter.objects.filter(id=11).first()

    sensors = Sensor.objects.filter(
        parameter=pressure_param
    ).select_related('room') if pressure_param else Sensor.objects.none()

    context = {
        'sensors': sensors,
        'pressure_parameter': pressure_param,
    }
    return render(request, 'dashboard/differential_pressure.html', context)
    
    from django.db import models

def get_last_voc_states(request, sensor):
    data = {}
    return JsonResponse(data)

    

    
    try:
        data['state0'] = Measurement.objects.filter(parameter__identifier="voc_index_state0").filter(timestamp__gte=datetime.now()-timedelta(minutes=10)).filter(sensor__name__icontains=sensor).values('value', 'timestamp').order_by('-id')[0]
        data['state1'] = Measurement.objects.filter(parameter__identifier="voc_index_state1").filter(timestamp__gte=datetime.now()-timedelta(minutes=10)).filter(sensor__name__icontains=sensor).values('value', 'timestamp').order_by('-id')[0]
    except:
        pass
    
    from django.http import JsonResponse
    return JsonResponse(data)


def debug_mqtt(request):
    """Debug stran za MQTT – naročanje na topice in pošiljanje sporočil."""
    return render(request, 'dashboard/debug_mqtt.html')


def serial_monitor(request):
    """ESP32 serial monitor – Web Serial API COM port message log."""
    return render(request, 'dashboard/serial_monitor.html')


def event_create(request):
    """Add a new Event (Dogodek). Supports quick (title + time) and full form modes."""
    from django.contrib import messages
    from django.shortcuts import redirect
    from parameters.models import Parameter
    from django.utils.dateparse import parse_datetime
    from datetime import datetime as dt

    rooms = Room.objects.all().order_by('order', 'name')
    parameters = Parameter.objects.all().order_by('order', 'name')
    recent_events = Event.objects.prefetch_related('rooms', 'parameters').order_by('-timestamp')[:15]

    # Default timestamp: now in local timezone, formatted for datetime-local input
    now_local = timezone.localtime(timezone.now())
    default_timestamp = now_local.strftime('%Y-%m-%dT%H:%M')

    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        timestamp_raw = (request.POST.get('timestamp') or '').strip()
        description = (request.POST.get('description') or '').strip()
        color = (request.POST.get('color') or '#10b981').strip()
        room_ids = request.POST.getlist('rooms')
        parameter_ids = request.POST.getlist('parameters')
        mode = request.POST.get('mode', 'quick')

        errors = []
        if not title:
            errors.append('Naziv dogodka je obvezen.')
        if not timestamp_raw:
            errors.append('Datum in čas sta obvezna.')

        event_ts = None
        if timestamp_raw:
            # datetime-local yields "YYYY-MM-DDTHH:MM" (no timezone)
            try:
                if 'T' in timestamp_raw:
                    naive = dt.strptime(timestamp_raw[:16], '%Y-%m-%dT%H:%M')
                else:
                    naive = dt.strptime(timestamp_raw[:16], '%Y-%m-%d %H:%M')
                event_ts = timezone.make_aware(naive, timezone.get_current_timezone())
            except (ValueError, TypeError):
                errors.append('Neveljaven format datuma in časa.')

        # Validate color as simple hex
        if color and not (color.startswith('#') and len(color) in (4, 7)):
            color = '#10b981'

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            event = Event.objects.create(
                title=title,
                timestamp=event_ts,
                description=description,
                color=color or '#10b981',
            )
            if room_ids:
                event.rooms.set(Room.objects.filter(id__in=room_ids))
            if parameter_ids:
                event.parameters.set(Parameter.objects.filter(id__in=parameter_ids))

            messages.success(
                request,
                f'Dogodek „{event.title}“ shranjen '
                f'({timezone.localtime(event.timestamp).strftime("%d.%m.%Y %H:%M")}).'
            )
            # Stay on page for quick successive adds (esp. mobile)
            return redirect('event_create')

    context = {
        'rooms': rooms,
        'parameters': parameters,
        'recent_events': recent_events,
        'default_timestamp': default_timestamp,
        'color_presets': [
            ('#10b981', 'Zelena'),
            ('#3b82f6', 'Modra'),
            ('#f59e0b', 'Oranžna'),
            ('#ef4444', 'Rdeča'),
            ('#a855f7', 'Vijolična'),
            ('#06b6d4', 'Cian'),
            ('#eab308', 'Rumena'),
            ('#f43f5e', 'Roza'),
        ],
    }
    return render(request, 'dashboard/event_form.html', context)
