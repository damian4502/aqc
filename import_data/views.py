import pandas as pd
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.generic import View
from django.http import JsonResponse
from django.db import transaction
from django.db.utils import IntegrityError
import time

from parameters.models import Parameter
from sensors.models import Sensor
from measurements.models import Measurement

class ImportMeasurementsView(View):
    template_name = 'import_data/import.html'

    def get(self, request):
        sensors = Sensor.objects.select_related('room', 'parameter').order_by('room__name', 'name')
        context = {'sensors': sensors}
        return render(request, self.template_name, context)

    def post(self, request):
        if not request.FILES.get('file'):
            messages.error(request, "Prosimo, izberite datoteko!")
            return redirect('import_measurements')

        file = request.FILES['file']
        sensor_id = request.POST.get('sensor_id')
        timezone_str = request.POST.get('timezone', 'Europe/Ljubljana')   # privzeto Ljubljana

        if not sensor_id:
            messages.error(request, "Prosimo, izberite senzor!")
            return redirect('import_measurements')

        try:
            sensor = Sensor.objects.select_related('room').get(id=sensor_id)
        except Sensor.DoesNotExist:
            messages.error(request, "Izbran senzor ne obstaja!")
            return redirect('import_measurements')

        try:
            import pytz
            tz = pytz.timezone(timezone_str)

            # Preberi datoteko
            if file.name.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(file)
            else:
                df = pd.read_csv(file)

            # Poišči časovni stolpec
            time_col = next((col for col in df.columns if col.lower() in ['timestamp', 'time', 'datetime', 'date']), None)
            if not time_col:
                messages.error(request, "Datoteka mora vsebovati stolpec 'timestamp' ali 'time'!")
                return redirect('import_measurements')

            df = df.rename(columns={time_col: 'timestamp'})
            
            # Pomembno: pretvorba v aware datetime z izbrano časovno cono
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            
            if df['timestamp'].isna().any():
                messages.error(request, "Nekateri časovni podatki niso veljavni!")
                return redirect('import_measurements')

            # Pretvorba v UTC (najboljša praksa za shranjevanje)
            df['timestamp'] = df['timestamp'].apply(lambda x: tz.localize(x).astimezone(pytz.UTC) if x.tzinfo is None else x)

            # Preveri parametre
            parameter_columns = [col for col in df.columns if col != 'timestamp']
            existing_params = {p.name.lower(): p for p in Parameter.objects.all()}

            missing_params = [col for col in parameter_columns if col.lower() not in existing_params]
            if missing_params:
                messages.error(request, f"Naslednji parametri ne obstajajo: {', '.join(missing_params)}")
                return redirect('import_measurements')

            # Uvoz
            imported_count = 0
            duplicate_count = 0
            skipped = 0

            with transaction.atomic():
                for _, row in df.iterrows():
                    timestamp = row['timestamp']

                    for col in parameter_columns:
                        value = row[col]
                        if pd.isna(value) or str(value).strip() == '':
                            skipped += 1
                            continue

                        try:
                            value = float(value)
                        except (ValueError, TypeError):
                            skipped += 1
                            continue

                        param = existing_params.get(col.lower())
                        if not param:
                            continue

                        exists = Measurement.objects.filter(
                            sensor=sensor,
                            parameter=param,
                            timestamp=timestamp
                        ).exists()

                        if exists:
                            duplicate_count += 1
                            continue

                        Measurement.objects.create(
                            sensor=sensor,
                            parameter=param,
                            timestamp=timestamp,
                            value=value
                        )
                        imported_count += 1

            messages.success(request, 
                f"Uspešno uvoženih **{imported_count}** meritev za senzor **{sensor}**.\n"
                f"Preskočenih **{duplicate_count}** podvojenih in **{skipped}** praznih vrednosti.\n"
                f"Časovna cona: {timezone_str}")
            
            return redirect('import_measurements')

        except Exception as e:
            messages.error(request, f"Napaka pri uvozu: {str(e)}")
            return redirect('import_measurements')