# 2. Web Scraping — Tipo de Cambio Oficial SUNAT

Un **solo script** (`sunat_tipo_cambio.py`) que descarga el tipo de cambio oficial
(compra / venta) publicado por la SUNAT **desde enero 2024 hasta el mes actual** y
lo consolida en un CSV y un Excel.

- **Fuente:** https://e-consulta.sunat.gob.pe/cl-at-ittipcam/tcS01Alias

```
02_sunat_tipo_cambio/
├── sunat_tipo_cambio.py     # TODO el código, con comentarios "# PASO N"
├── requirements.txt
├── run_sunat.bat            # lanzador para Task Scheduler
├── scripts/register_task.ps1
└── salida/                  # se genera al ejecutar
    ├── tipo_cambio_sunat_AAAAMM_AAAAMM.csv
    ├── tipo_cambio_sunat_AAAAMM_AAAAMM.xlsx
    └── scraping.log
```

## Instalación

```bash
cd 02_sunat_tipo_cambio
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt        # openpyxl solo si quieres Excel; selenium solo para --metodo selenium
```

## Uso

```bash
python sunat_tipo_cambio.py                          # enero 2024 -> mes actual, CSV + Excel
python sunat_tipo_cambio.py --desde 2024-01 --hasta 2024-12
python sunat_tipo_cambio.py --formato csv            # solo CSV
python sunat_tipo_cambio.py --sin-relleno            # no arrastrar valor del día anterior
python sunat_tipo_cambio.py --metodo selenium        # automatizar el navegador (WebDriverWait)
```

## Cómo funciona (resumen)

El calendario de la web de SUNAT llama por debajo a un endpoint JSON:

```
POST /cl-at-ittipcam/tcS01Alias/listarTipoCambio
body: {"anio": 2024, "mes": 0, "token": "<texto>"}      (mes es 0-based: 0 = enero)
resp: [{"fecPublica":"01/01/2024","valTipo":"3.705","codTipo":"C"}, ...]   C=compra, V=venta
```

El script (método `api`, por defecto) recorre mes a mes ese endpoint con `requests`,
con reintentos y una pausa entre peticiones para no saturar el servidor. Es más
robusto y rápido que raspar el HTML. El método `selenium` hace lo mismo desde el
navegador real con `WebDriverWait` (útil para la demostración en video); si falla
por red o driver, el script cae automáticamente al método `api`.

### Salida

Una fila por **día calendario** del rango:

| columna | significado |
|---|---|
| `fecha`, `anio`, `mes`, `mes_nombre`, `dia`, `dia_semana`, `es_fin_de_semana` | descomposición de la fecha |
| `compra`, `venta` | tipo de cambio |
| `publicado` | `True` si SUNAT publicó cotización ese día |
| `fecha_valor_usada` | de qué fecha se tomó el valor (en días sin publicación se arrastra el anterior, regla oficial de SUNAT) |

El resumen final informa: rango, días totales, días publicados, y la lista de
**días hábiles sin publicación** (posibles feriados; p. ej. 2024‑07‑29 y 2024‑07‑30).

### Última corrida

`enero 2024 → agosto 2026` · 972 días · 964 con cotización publicada · 4 días hábiles sin publicación.

## Ejecución automática — Windows Task Scheduler

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Time "07:00"
Start-ScheduledTask   -TaskName "SUNAT_TipoCambio_Diario"
Get-ScheduledTaskInfo -TaskName "SUNAT_TipoCambio_Diario"   # LastTaskResult = 0 -> OK
```

`run_sunat.bat` no abre navegador (usa el método `api`), así que corre perfecto
de forma desatendida. La evidencia para el video: `salida\task_scheduler.out` +
el CSV/Excel con fecha nueva.
