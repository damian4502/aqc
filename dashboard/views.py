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
from rooms.models import Room

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
            60: 'h',
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

def monitor(request):
    context = {}
    context['parameters'] = Parameter.objects.all().order_by('name')

    return render(request, 'dashboard/monitor.html', context)


def room_detail(request, room_id):
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
        start_date_str = request.GET.get('start_date', request.session.get('chart_start_date') )
        end_date_str = request.GET.get('end_date', request.session.get('chart_end_date') )
        
        # Hitri gumbi (24h, 7d, 30d)
        quick_days = request.GET.get('quick')
        if quick_days:
            try:
                days = int(quick_days)
                start_date = timezone.now() - timedelta(days=days)
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
            # privzeto
            start_date = timezone.now() - timedelta(days=14)
            end_date = timezone.now()

        context_start = start_date.strftime('%Y-%m-%d') if start_date else ''
        context_end = end_date.strftime('%Y-%m-%d') if end_date else ''
        request.session['chart_start_date'] = context_start
        request.session['chart_end_date'] = context_end
    
    # Zadnje meritve za vrh kartice
    latest_measurements = Measurement.objects.filter(
        sensor__room=room
    ).select_related('sensor', 'parameter')\
     .order_by('parameter_id', '-timestamp')\
     .distinct('parameter_id')

    # AQI Gauge
    latest_aqi = Measurement.objects.filter(
        sensor__room=room,
        parameter__name__iexact="AQI"
    ).order_by('-timestamp').first()

    aqi_gauge = create_aqi_gauge(latest_aqi.value) if latest_aqi else None

    context = {
        'room': room,
        'latest_measurements': latest_measurements,
        'start_date': context_start,
        'end_date': context_end,
        'view_type': view_type,
        'all_data': all_data,
        'aqi_gauge': aqi_gauge,
    }

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
    ignore_spikes = request.GET.get('ignore_spikes') == 'on' or request.session.get('ignore_spikes', False)

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

    return render(request, 'dashboard/room_detail.html', context)
    
import pandas as pd
from django.utils import timezone
def resample_measurements(df, interval_minutes=15, fill_method='ffill', ignore_spikes=False):
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('timestamp')

    # Pivot
    pivot = df.pivot_table(index=df.index, columns='parameter' if 'parameter' in df.columns else 'room', 
                          values='value', aggfunc='mean')

    # Resampling
    freq_map = {1: '1min', 5: '5min', 15: '15min', 60: 'H', 1440: 'D'}
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
    rooms = Room.objects.all().order_by('name')
    
    room_data = []
    
    for room in rooms:
        # Zadnje meritve (vse parametre še vedno prikažemo v tabeli)
        latest = Measurement.objects.filter(sensor__room=room)\
            .select_related('parameter', 'sensor')\
            .order_by('parameter_id', '-timestamp')\
            .distinct('parameter_id')[:8]
        
        # Mini graf - samo AQI za zadnjih 24 ur
        mini_fig = None
        aqi_measurements = Measurement.objects.filter(
            sensor__room=room,
            parameter__name__iexact="AQI",   # iščemo parameter z imenom AQI (ne glede na velike/male črke)
            timestamp__gte=timezone.now() - timedelta(hours=24)
        ).order_by('timestamp')
        
        if aqi_measurements.exists():
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
            
        room_data.append({
            'room': room,
            'latest': latest,
            'mini_fig': mini_fig,
            'aqi_gauge': aqi_gauge,
            'has_aqi': aqi_measurements.exists()
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
        start_date_str = request.GET.get('start_date', request.session.get('chart_start_date') )
        end_date_str = request.GET.get('end_date', request.session.get('chart_end_date') )
        
        # Hitri gumbi (24h, 7d, 30d)
        quick_days = request.GET.get('quick')
        if quick_days:
            try:
                days = int(quick_days)
                start_date = timezone.now() - timedelta(days=days)
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
            # privzeto
            start_date = timezone.now() - timedelta(days=14)
            end_date = timezone.now()

        context_start = start_date.strftime('%Y-%m-%d') if start_date else ''
        context_end = end_date.strftime('%Y-%m-%d') if end_date else ''
        request.session['chart_start_date'] = context_start
        request.session['chart_end_date'] = context_end

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
    ignore_spikes = request.GET.get('ignore_spikes') == 'on' or request.session.get('ignore_spikes', False)

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
    parameter = get_object_or_404(Parameter, id=parameter_id)

    # Datumski filter
    all_data = request.GET.get('all') == 'true'
    if all_data:
        start_date = None
        end_date = timezone.now()
    else:
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        if start_date_str and end_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        else:
            start_date = timezone.now() - timedelta(days=30)
            end_date = timezone.now()

    # Resampling parametri
    interval_minutes = int(request.GET.get('interval', 15))
    fill_method = request.GET.get('fill_method', 'ffill')

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

    # Resampling
    resampled = resample_measurements(df, interval_minutes, fill_method, column_name='room')

    # Priprava za CSV
    resampled = resampled.reset_index()
    resampled['timestamp'] = resampled['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')

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


from django.db import models
def differential_pressure_view(request):
    # Najdi parameter "Tlak" (lahko prilagodiš ime)
    pressure_param = Parameter.objects.filter(
        models.Q(name__icontains='tlak') | models.Q(name__icontains='pressure')
    ).first()

    sensors = Sensor.objects.filter(
        parameter=pressure_param
    ).select_related('room') if pressure_param else Sensor.objects.none()

    context = {
        'sensors': sensors,
        'pressure_parameter': pressure_param,
    }
    return render(request, 'dashboard/differential_pressure.html', context)