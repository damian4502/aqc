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
    room = get_object_or_404(Room, id=room_id)
    
    # Pridobi parametre iz forme
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    interval_str = request.GET.get('interval', '60')
    fill_method = request.GET.get('fill', 'ffill')

    # Datumski filter
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            end_date = end_date.replace(hour=23, minute=59, second=59)
        except ValueError:
            start_date = timezone.now() - timedelta(days=7)
            end_date = timezone.now()
    else:
        start_date = timezone.now() - timedelta(days=7)
        end_date = timezone.now()

    # Pridobi meritve
    measurements = Measurement.objects.filter(
        sensor__room=room,
        timestamp__gte=start_date,
        timestamp__lte=end_date
    ).select_related('sensor', 'parameter')\
     .order_by('timestamp')

    if not measurements.exists():
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{room.name}_brez_podatki.csv"'
        return response

    df = pd.DataFrame(list(measurements.values(
        'timestamp', 
        'value', 
        'parameter__name'
    )))

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.rename(columns={'parameter__name': 'parameter'})

    # === RESAMPLING + RAVNANJE Z MANJKAJOČIMI VREDNOSTMI ===
    interval_min = int(interval_str)
    
    if interval_min > 0:
        df = df.set_index('timestamp')
        
        # Pivot tabelo (vsak parameter = svoj stolpec)
        pivot = df.pivot_table(
            index=df.index,
            columns='parameter',
            values='value',
            aggfunc='mean'
        )

        # Določitev frekvence
        freq_map = {
            1: 'min',
            5: '5min',
            15: '15min',
            60: 'H',
            1440: 'D'
        }
        freq = freq_map.get(interval_min, f'{interval_min}min')

        # Resampling
        resampled = pivot.resample(freq).mean()

        # Ravnanje z manjkajočimi vrednostmi
        if fill_method == 'ffill':
            resampled = resampled.ffill()
        elif fill_method == 'bfill':
            resampled = resampled.bfill()
        elif fill_method == 'interpolate':
            resampled = resampled.interpolate(method='linear')
        elif fill_method == 'zero':
            resampled = resampled.fillna(0)
        elif fill_method == 'none':
            pass  # pusti NaN vrednosti
        # else: privzeto ffill

        df = resampled.reset_index()

    # Priprava CSV datoteke
    response = HttpResponse(content_type='text/csv')
    filename = f"{room.name.replace(' ', '_')}_meritve_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # Lepši format datuma
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')

    df.to_csv(response, index=False, encoding='utf-8')
    return response
    
from django.shortcuts import render, get_object_or_404
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime, timedelta
from django.utils import timezone

def room_detail(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    
    view_type = request.GET.get('view', 'trend')
    all_data = request.GET.get('all') == 'true'

    # === Datumski filter + "Vsi podatki" ===
    if all_data:
        start_date = None
        end_date = timezone.now()
        context_start = ''
        context_end = ''
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

        context_start = start_date.strftime('%Y-%m-%d') if start_date else ''
        context_end = end_date.strftime('%Y-%m-%d') if end_date else ''

    # Zadnje meritve
    latest_measurements = Measurement.objects.filter(
        sensor__room=room
    ).select_related('sensor', 'parameter')\
     .order_by('parameter_id', '-timestamp')\
     .distinct('parameter_id')

    context = {
        'room': room,
        'latest_measurements': latest_measurements,
        'start_date': context_start,
        'end_date': context_end,
        'view_type': view_type,
        'all_data': all_data,
    }

    # Meritve za grafe
    qs = Measurement.objects.filter(sensor__room=room)
    if not all_data:
        qs = qs.filter(timestamp__gte=start_date, timestamp__lte=end_date)

    measurements = qs.select_related('parameter').order_by('timestamp')

    if measurements.exists():
        df = pd.DataFrame(list(measurements.values('timestamp', 'value', 'parameter__name')))
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.rename(columns={'parameter__name': 'parameter'})

        if view_type == 'trend':
            fig = px.line(df, x='timestamp', y='value', color='parameter',
                         title=f'Časovni trend - {room.name}',
                         height=700)
            fig = apply_dark_theme(fig)
            context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

        elif view_type == 'correlation':
            pivot = df.pivot_table(index='timestamp', columns='parameter', values='value')
            corr_matrix = pivot.corr().round(2)
            fig = px.imshow(corr_matrix, text_auto=True, aspect="auto", 
                           color_continuous_scale='RdBu_r',
                           title='Korelacija med parametri')
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
            
            # Tedenski vzorec - Line chart
            weekly = df.groupby(['weekday', 'dayofweek', 'parameter'])['value'].mean().reset_index()
            fig_weekly = px.line(weekly, x='dayofweek', y='value', color='parameter',
                                title='Tedenski vzorec (line)',
                                category_orders={"dayofweek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]})
            context['fig_weekly'] = fig_weekly.to_html(full_html=False, include_plotlyjs='cdn')

            # === NOVO: Heatmap tedenskega vzorca ===
            # Povprečje po dnevu v tednu in parametru
            heatmap_data = df.groupby(['dayofweek', 'parameter'])['value'].mean().unstack()
            
            # Lepši vrstni red dni
            day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            heatmap_data = heatmap_data.reindex(day_order)
            
            fig_heatmap = px.imshow(
                heatmap_data.T, 
                text_auto=True, 
                aspect="auto",
                color_continuous_scale='RdYlGn_r',   # rdeče = slabo, zeleno = dobro
                title='Tedenski heatmap vzorec (povprečne vrednosti)'
            )
            fig_heatmap.update_layout(height=600)
            context['fig_heatmap'] = fig_heatmap.to_html(full_html=False, include_plotlyjs='cdn')

    else:
        context['no_data'] = True

    return render(request, 'dashboard/room_detail.html', context)

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
        fig = px.imshow(corr_matrix, 
                       text_auto=True, 
                       aspect="auto", 
                       color_continuous_scale='RdBu_r',
                       title='Korelacija med parametri')

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
    
def dashboard_overview(request):
    rooms = Room.objects.prefetch_related('sensors__parameter').all().order_by('name')
    
    room_data = []
    
    for room in rooms:
        # Zadnje meritve za prostor
        latest = Measurement.objects.filter(sensor__room=room)\
            .select_related('parameter', 'sensor')\
            .order_by('parameter_id', '-timestamp')\
            .distinct('parameter_id')[:8]  # omejimo na 8 za preglednost
        
        # Mini graf zadnjih 24 ur
        last_24h = Measurement.objects.filter(
            sensor__room=room,
            timestamp__gte=timezone.now() - timedelta(hours=24)
        ).select_related('parameter').order_by('timestamp')
        
        mini_fig = None
        if last_24h.exists():
            df = pd.DataFrame(list(last_24h.values('timestamp', 'value', 'parameter__name')))
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.rename(columns={'parameter__name': 'parameter'})
            
            fig = px.line(df, x='timestamp', y='value', color='parameter', 
                         height=160, title='')
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                showlegend=False,
                xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                yaxis=dict(showgrid=True, gridcolor='rgba(148,163,184,0.2)'),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)'
            )
            fig = apply_dark_theme(fig)
            mini_fig = fig.to_html(full_html=False, include_plotlyjs=False)
        
        room_data.append({
            'room': room,
            'latest': latest,
            'mini_fig': mini_fig
        })
    
    context = {
        'room_data': room_data,
    }
    
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
    parameter = get_object_or_404(Parameter, id=parameter_id)
    
    view_type = request.GET.get('view', 'trend')
    all_data = request.GET.get('all') == 'true'

    # Datumski filter + "Vsi podatki"
    if all_data:
        start_date = None
        end_date = timezone.now()
        context_start = ''
        context_end = ''
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

        context_start = start_date.strftime('%Y-%m-%d') if start_date else ''
        context_end = end_date.strftime('%Y-%m-%d') if end_date else ''

    # Zadnje meritve za ta parameter (po prostorih)
    latest_measurements = Measurement.objects.filter(
        parameter=parameter
    ).select_related('sensor__room', 'sensor')\
     .order_by('sensor__room_id', '-timestamp')\
     .distinct('sensor__room')

    context = {
        'parameter': parameter,
        'latest_measurements': latest_measurements,
        'start_date': context_start,
        'end_date': context_end,
        'view_type': view_type,
        'all_data': all_data,
    }

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
            fig = px.line(df, x='timestamp', y='value', color='room',
                         title=f'Trend parameterja "{parameter.name}" po prostorih',
                         height=700)
            fig = apply_dark_theme(fig)
            context['fig'] = fig.to_html(full_html=False, include_plotlyjs='cdn')

        elif view_type == 'correlation':
            pivot = df.pivot_table(index='timestamp', columns='room', values='value')
            corr_matrix = pivot.corr().round(2)
            fig = px.imshow(corr_matrix, text_auto=True, aspect="auto", 
                           color_continuous_scale='RdBu_r',
                           title=f'Korelacija prostorov za parameter {parameter.name}')
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