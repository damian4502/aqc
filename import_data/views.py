import pandas as pd
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.generic import View
from django.db import transaction
from django.db.utils import IntegrityError

from parameters.models import Parameter
from sensors.models import Sensor
from measurements.models import Measurement

class ImportMeasurementsView(View):
    template_name = 'import_data/import.html'

    def get(self, request):
        sensors = Sensor.objects.select_related('room').order_by('room__name', 'name')
        context = {'sensors': sensors}
        return render(request, self.template_name, context)

    def post(self, request):
        if not request.FILES.get('file'):
            messages.error(request, "Prosimo, izberite CSV ali Excel datoteko!")
            return redirect('import_measurements')

        file = request.FILES['file']
        sensor_id = request.POST.get('sensor_id')

        if not sensor_id:
            messages.error(request, "Prosimo, izberite senzor!")
            return redirect('import_measurements')

        try:
            sensor = Sensor.objects.select_related('room').get(id=sensor_id)
        except Sensor.DoesNotExist:
            messages.error(request, "Izbran senzor ne obstaja!")
            return redirect('import_measurements')

        try:
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
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')

            if df['timestamp'].isna().any():
                messages.error(request, "Nekateri časovni podatki niso veljavni!")
                return redirect('import_measurements')

            # Preveri parametre
            parameter_columns = [col for col in df.columns if col != 'timestamp']
            existing_params = {p.name.lower(): p for p in Parameter.objects.all()}

            missing_params = [col for col in parameter_columns if col.lower() not in existing_params]
            if missing_params:
                messages.error(request, f"Naslednji parametri ne obstajajo: {', '.join(missing_params)}")
                return redirect('import_measurements')

            # Uvoz z preverjanjem podvajanja
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

                        # Preveri, ali že obstaja enaka meritev
                        exists = Measurement.objects.filter(
                            sensor=sensor,
                            parameter=param,
                            timestamp=timestamp
                        ).exists()

                        if exists:
                            duplicate_count += 1
                            continue

                        # Ustvari novo meritev
                        try:
                            Measurement.objects.create(
                                sensor=sensor,
                                parameter=param,
                                timestamp=timestamp,
                                value=value
                            )
                            imported_count += 1
                        except IntegrityError:
                            # Če UniqueConstraint vseeno "ujame" (race condition)
                            duplicate_count += 1

            # Obvestilo uporabniku
            msg = f"Uspešno uvoženih **{imported_count}** novih meritev za senzor **{sensor}**."
            if duplicate_count > 0:
                msg += f"\nPreskočenih **{duplicate_count}** meritev zaradi podvajanja (že obstajajo za isti čas in parameter)."
            if skipped > 0:
                msg += f"\nPreskočenih {skipped} praznih ali neveljavnih vrednosti."

            messages.success(request, msg)
            return redirect('import_measurements')

        except Exception as e:
            messages.error(request, f"Napaka pri uvozu: {str(e)}")
            return redirect('import_measurements')