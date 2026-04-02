# Air Quality Control (AQC)

Spletna aplikacija za nadzor in analizo kakovosti zraka v notranjih prostorih.

## Funkcionalnosti

- Upravljanje prostorov, parametrov in senzorjev
- Uvoz meritev iz CSV/Excel z validacijo
- Napreden pregled in analiza podatkov
- Interaktivni grafi (časovni trend, korelacija, dnevni/tedenski vzorci, heatmap)
- Izvoz podatkov v CSV z resamplingom in ravnanjem z manjkajočimi vrednostmi
- Podpora za več senzorjev v istem prostoru

## Tehnologije

- **Backend**: Django 6 + PostgreSQL
- **Vizualizacije**: Plotly
- **Containerizacija**: Docker + docker-compose
- **Analiza**: pandas

## Namestitev (razvoj)

```bash
docker compose up --build -d

Dostop:

Aplikacija: http://localhost:8000
Admin: http://localhost:8000/admin

Struktura

dashboard/ – glavni dashboard in analize
import_data/ – uvoz meritev
rooms/, parameters/, sensors/, measurements/ – osnovni modeli

Status
Projekt je v aktivnem razvoju.
