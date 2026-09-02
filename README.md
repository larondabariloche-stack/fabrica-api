# Fábrica La Ronda — API

API sin dependencias (stdlib puro) para tareas, fechas y cultivo de La Ronda Bariloche.
Despliegue en Render: `python api_cultivo.py` (ver render.yaml).

Variables de entorno:
- `GOOGLE_TOKEN_JSON`: JSON del token OAuth de Google (secret, se configura en Render).
- `FABRICA_DRIVE=1`: guarda fabrica.json (tareas/fechas) en Google Drive.
- `API_KEY` + `API_REQUIRE_KEY=1`: exige header `X-API-Key` en las llamadas.
