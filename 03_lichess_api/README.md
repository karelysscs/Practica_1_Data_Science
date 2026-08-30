# 3. Lichess API — Análisis de partidas y automatización de torneos

Dos scripts independientes que usan la **API de Lichess**:

| Script | Qué hace |
|---|---|
| `lichess_analysis.py`    | **Parte A.** Descarga las últimas N partidas de un usuario, las pasa a un `DataFrame` de pandas, calcula estadísticas (resultados, rating, color, modo), genera gráficos y exporta CSV. |
| `lichess_tournaments.py` | **Parte B.** Define un calendario semanal de torneos y los crea vía API. Calcula la próxima fecha/hora, salta los que ya pasaron, tiene modo *dry-run* y no se detiene si uno falla. |

```
03_lichess_api/
├── lichess_analysis.py      # Parte A  (comentarios "# PASO N")
├── lichess_tournaments.py   # Parte B  (comentarios "# PASO N")
├── requirements.txt
└── output/                  # se genera al ejecutar (CSV, PNG, logs) — evidencia
```

## Instalación

```bash
cd 03_lichess_api
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

## Token de Lichess

Generar un **token personal** en <https://lichess.org/account/oauth/token> y
exportarlo como variable de entorno antes de ejecutar:

```powershell
$env:LICHESS_TOKEN = "tu_token_aqui"      # dura mientras la terminal esté abierta
```

| Parte | Scope necesario | ¿Obligatorio? |
|---|---|---|
| A — análisis    | `game:read`         | No para partidas públicas, pero recomendado (límites de tráfico más laxos). |
| B — torneos     | `tournament:write`  | **Sí** para el modo real. En *dry-run* no hace falta. |

El token **nunca se escribe en el código** ni en el repo: se lee solo de
`LICHESS_TOKEN` y en los logs aparece enmascarado (`abcd...`).

---

## Parte A — `lichess_analysis.py`

### Uso

```bash
python lichess_analysis.py                                  # usuario y N por defecto
python lichess_analysis.py --usuario Zhigalko_Sergei --n 100
python lichess_analysis.py --usuario tu_usuario --sin-graficos
```

| Argumento | Por defecto | Descripción |
|---|---|---|
| `--usuario`      | `DrNykterstein` | cuenta de Lichess a analizar |
| `--n` / `--max`  | `50`            | número máximo de partidas |
| `--salida`       | `./output`     | carpeta de CSV y gráficos |
| `--sin-graficos` | —              | solo CSV, sin PNG |

### Qué hace (resumen de los PASOS)

1. Lee parámetros.
2. Prepara logging (consola + `output/analysis.log`).
3. Crea la sesión HTTP (añade `Authorization: Bearer` si hay token).
4. Descarga el *stream* NDJSON de `GET /api/games/user/{usuario}` con reintentos
   y respetando el límite de tráfico (HTTP 429 → espera ~1 min).
5. Convierte cada partida **al punto de vista del usuario** (color, resultado,
   rating propio y del rival, nº de jugadas, apertura…) → `DataFrame`.
6. Calcula estadísticas: victorias/derrotas/tablas y *win rate*, rating
   (media, mediana, mín, máx, desviación), rendimiento neto de rating,
   *win rate* por color y por modo de juego.
7. Genera 3 gráficos con matplotlib.
8. Exporta 3 CSV.
9. Imprime el resumen.

### Salidas (`output/`)

| Archivo | Contenido |
|---|---|
| `partidas.csv`                 | una fila por partida (datos crudos ya procesados) |
| `estadisticas.csv`             | métricas en formato largo (`categoria, metrica, valor`) |
| `estadisticas_por_modo.csv`    | tabla resumen por modo (`groupby`): partidas, V/D/T, win rate, rating medio |
| `grafico_resultados.png`       | barras de victoria / tablas / derrota |
| `grafico_rating.png`           | evolución del rating en el tiempo + media móvil |
| `grafico_modos.png`            | partidas por modo, apiladas por resultado |
| `analysis.log`                 | log de la ejecución |

---

## Parte B — `lichess_tournaments.py`

### Configuración

Editar dentro del archivo:

- **`DRY_RUN`** (arriba del todo). `True` = simula, no crea nada. `False` = crea
  de verdad. Se deja en `True` por defecto para no crear torneos duplicados por
  accidente; se puede forzar por línea de comandos con `--real` / `--dry-run`.
- **`CALENDARIO_SEMANAL`** — lista de torneos. Cada entrada:

  ```python
  {"nombre": "Blitz de los Lunes", "dia": "lunes", "hora": "20:00",
   "modo": "blitz", "duracion_min": 60, "variante": "standard", "rated": True}
  ```

- **`CONFIG_MODO`** — reloj base (minutos + incremento) para `bullet`, `blitz`,
  `rapid`, `classical`.

### Uso

```bash
python lichess_tournaments.py                 # usa DRY_RUN del archivo (True)
python lichess_tournaments.py --real          # crea los torneos de verdad
python lichess_tournaments.py --dry-run       # fuerza simulación
python lichess_tournaments.py --real --reprogramar   # los que ya pasaron -> semana siguiente
```

### Comportamiento (resumen de los PASOS)

1. Lee parámetros y decide el modo (dry-run / real).
2. Prepara logging (consola + `output/torneos_log.txt`) y lee el token.
3. Para cada torneo del calendario:
   - **3.1** calcula la próxima ocurrencia de ese día de la semana + hora;
   - **3.2** si esa hora **ya pasó esta semana** → lo **salta** (o lo mueve a la
     semana siguiente con `--reprogramar`); también respeta el mínimo de Lichess
     de 5 min en el futuro;
   - **3.3** construye el cuerpo de la petición (`clockTime`, `clockIncrement`,
     `minutes`, `startDate` en ms, `variant`, `rated`…);
   - **3.4** en *dry-run* solo imprime lo que enviaría; en real hace `POST
     /api/tournament`, y si ese torneo falla (401, 400, red, 429…) **registra el
     error y sigue** con el resto.
4. Imprime el resumen: en el calendario / creados / simulados / saltados / con error.

### Evidencia

`output/torneos_log.txt` — salida completa de una corrida en **DRY-RUN** con los
3 torneos del calendario y el resumen final (0 errores).

> **Parte 3 no requiere Task Scheduler** (eso es solo para las Partes 1 y 2).
