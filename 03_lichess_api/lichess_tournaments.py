"""
Lichess API - Parte B: Automatizacion de torneos (Arena)
=======================================================
Define un calendario SEMANAL de torneos y los crea automaticamente en Lichess
mediante la API. Calcula para cada torneo su proxima fecha/hora, se salta los
que ya pasaron esta semana, tiene modo simulacion (DRY-RUN) y no se detiene si
un torneo concreto falla.

API oficial:
    POST https://lichess.org/api/tournament        (crea un torneo Arena)
    campos: name, clockTime, clockIncrement, minutes, startDate (ms), variant,
            rated, description ...

Requisitos:
    pip install requests

Token OBLIGATORIO (scope  tournament:write):
    Generar en https://lichess.org/account/oauth/token y exportarlo:
        PowerShell:  $env:LICHESS_TOKEN = "tu_token_aqui"
        bash:        export LICHESS_TOKEN="tu_token_aqui"

--------------------------------------------------------------------------------
FLUJO GENERAL (bloques marcados con "# PASO N"):

  PASO 1  Leer parametros (--real / --dry-run, --reprogramar, carpeta salida).
  PASO 2  Preparar logging (consola + output/torneos_log.txt) y leer el token.
  PASO 3  Para cada entrada de CALENDARIO_SEMANAL:
            PASO 3.1  Calcular la proxima ocurrencia (dia de la semana + hora).
            PASO 3.2  Si ya paso esta semana -> saltar (o mover a la siguiente
                      semana si se paso --reprogramar).
            PASO 3.3  Construir el cuerpo de la peticion (clock, minutos, ...).
            PASO 3.4  DRY-RUN: solo mostrar lo que se enviaria.
                      REAL: POST a la API, con manejo de errores que NO corta
                      el resto del calendario.
  PASO 4  Imprimir resumen: procesados / creados / saltados / con error.
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ============================================================================
# PASO 0: CONFIGURACION
# ============================================================================
API_TORNEO = "https://lichess.org/api/tournament"

# --- Modo simulacion: True = NO crea nada real; solo muestra que se enviaria. ---
# Dejar en True por defecto para no crear torneos duplicados por accidente.
# Cambiar a False (o pasar --real) solo para la demo / evidencia.
DRY_RUN = True

CARPETA_SALIDA = Path(__file__).resolve().parent / "output"

# Reloj base por modo de juego:  (minutos iniciales, incremento en segundos)
CONFIG_MODO = {
    "bullet":    {"clockTime": 1,  "clockIncrement": 0},
    "blitz":     {"clockTime": 3,  "clockIncrement": 2},
    "rapid":     {"clockTime": 10, "clockIncrement": 0},
    "classical": {"clockTime": 30, "clockIncrement": 0},
}

# dia de la semana (texto -> numero;  lunes = 0 ... domingo = 6)
DIAS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}

# --------------------------------------------------------------------------
# CALENDARIO SEMANAL  ->  editar aqui los torneos propios.
#   nombre       : 2-30 caracteres
#   dia          : lunes..domingo (sin tildes)
#   hora         : "HH:MM" hora local de la maquina que ejecuta el script
#   modo         : bullet | blitz | rapid | classical
#   duracion_min : cuanto dura el torneo (20-720)
#   variante     : standard | chess960 | crazyhouse | atomic | ...
#   rated        : True (cuenta para el rating) | False (casual)
# --------------------------------------------------------------------------
CALENDARIO_SEMANAL = [
    {"nombre": "Blitz de los Lunes", "dia": "lunes",     "hora": "20:00",
     "modo": "blitz",  "duracion_min": 60, "variante": "standard", "rated": True},
    {"nombre": "Bullet Miercoles",   "dia": "miercoles", "hora": "21:00",
     "modo": "bullet", "duracion_min": 45, "variante": "standard", "rated": True},
    {"nombre": "Rapid del Sabado",   "dia": "sabado",    "hora": "11:00",
     "modo": "rapid",  "duracion_min": 90, "variante": "standard", "rated": False},
]

MARGEN_MINIMO_MIN = 6        # Lichess exige que startDate sea >= 5 min en el futuro
TIMEOUT_HTTP_SEG = 30
ESPERA_TRAS_429_SEG = 62
PAUSA_ENTRE_TORNEOS_SEG = 1.5

log = logging.getLogger("lichess_tournaments")


# ============================================================================
# PASO 2: logging (consola + archivo)
# ============================================================================
def configurar_logging(carpeta_salida: Path) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    carpeta_salida.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    log.handlers.clear()

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(fmt)
    archivo = logging.FileHandler(carpeta_salida / "torneos_log.txt", mode="w", encoding="utf-8")
    archivo.setFormatter(fmt)
    log.addHandler(consola)
    log.addHandler(archivo)
    log.propagate = False


# ============================================================================
# PASO 3.1: proxima ocurrencia de (dia de la semana + hora)
# ============================================================================
def proxima_ocurrencia(dia: str, hora: str, ahora: datetime) -> datetime:
    """Devuelve el datetime de ESTA semana para ese dia+hora.
    Puede quedar en el pasado (si ya paso): el PASO 3.2 decide que hacer.
    """
    objetivo_dow = DIAS[dia.strip().lower()]
    hh, mm = (int(x) for x in hora.strip().split(":"))

    delta_dias = (objetivo_dow - ahora.weekday()) % 7
    fecha = (ahora + timedelta(days=delta_dias)).replace(
        hour=hh, minute=mm, second=0, microsecond=0)
    return fecha


# ============================================================================
# PASO 3.3: cuerpo de la peticion para crear el torneo
# ============================================================================
def construir_payload(torneo: dict, inicio: datetime) -> dict:
    modo = torneo["modo"].strip().lower()
    if modo not in CONFIG_MODO:
        raise ValueError(f"modo '{modo}' no configurado (usa {list(CONFIG_MODO)})")
    reloj = CONFIG_MODO[modo]

    return {
        "name": torneo["nombre"][:30],
        "clockTime": reloj["clockTime"],
        "clockIncrement": reloj["clockIncrement"],
        "minutes": int(torneo["duracion_min"]),
        "startDate": int(inicio.timestamp() * 1000),   # epoch en milisegundos
        "variant": torneo.get("variante", "standard"),
        "rated": bool(torneo.get("rated", True)),
        "description": (
            f"Torneo semanal automatico ({modo}). "
            f"Creado por script de la Practica 1 - Data Science."
        ),
    }


# ============================================================================
# PASO 3.4: crear el torneo (o simularlo)
# ============================================================================
def crear_torneo(sesion: requests.Session, payload: dict, dry_run: bool) -> tuple[str, str]:
    """Devuelve (estado, detalle). estado in {'creado', 'simulado', 'error'}.
    NUNCA lanza: los errores se devuelven para no cortar el calendario.
    """
    if dry_run:
        log.info("   [DRY-RUN] se enviaria POST %s", API_TORNEO)
        for k, v in payload.items():
            log.info("      %-15s = %s", k, v)
        return "simulado", "no se llamo a la API"

    for intento in (1, 2):
        try:
            r = sesion.post(API_TORNEO, data=payload, timeout=TIMEOUT_HTTP_SEG)
            if r.status_code == 429:
                log.warning("   HTTP 429 (limite de trafico). Esperando %d s...", ESPERA_TRAS_429_SEG)
                time.sleep(ESPERA_TRAS_429_SEG)
                continue
            if r.status_code == 401:
                return "error", "HTTP 401: token invalido o sin scope tournament:write"
            if r.status_code == 400:
                return "error", f"HTTP 400: parametros rechazados -> {r.text[:300]}"
            r.raise_for_status()

            data = r.json()
            tid = data.get("id", "?")
            return "creado", f"https://lichess.org/tournament/{tid}"

        except requests.RequestException as exc:
            if intento == 1:
                log.warning("   fallo de red (%s). Reintentando una vez...", exc)
                time.sleep(3)
                continue
            return "error", f"fallo de red: {exc}"

    return "error", "no se pudo crear tras los reintentos"


# ============================================================================
# PASO 1: argumentos
# ============================================================================
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Crea automaticamente un calendario semanal de torneos en Lichess."
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--real", action="store_true",
                   help="crear los torneos DE VERDAD (ignora DRY_RUN=True del archivo)")
    g.add_argument("--dry-run", action="store_true",
                   help="forzar modo simulacion (no crea nada)")
    p.add_argument("--reprogramar", action="store_true",
                   help="si un torneo ya paso esta semana, moverlo a la semana siguiente "
                        "en vez de saltarlo")
    p.add_argument("--salida", type=Path, default=CARPETA_SALIDA,
                   help="carpeta del log de salida")
    return p.parse_args(argv)


def main(argv=None) -> int:
    # PASO 1
    args = parse_args(argv)
    dry_run = True if args.dry_run else (False if args.real else DRY_RUN)

    # PASO 2
    carpeta = Path(args.salida)
    configurar_logging(carpeta)
    log.info("### Lichess - automatizacion de torneos - inicio ###")
    log.info("Modo: %s", "SIMULACION (DRY-RUN)" if dry_run else "REAL (se crearan torneos)")

    token = os.environ.get("LICHESS_TOKEN", "").strip()
    if not token and not dry_run:
        log.error("Falta LICHESS_TOKEN y estas en modo REAL. Exporta el token "
                  "(scope tournament:write) y vuelve a ejecutar.")
        return 1
    if token:
        log.info("Token detectado (%s...).", token[:4])

    sesion = requests.Session()
    sesion.headers.update({"User-Agent": "practica1-data-science/1.0 (torneos)"})
    if token:
        sesion.headers["Authorization"] = f"Bearer {token}"

    ahora = datetime.now()
    log.info("Fecha/hora local de referencia: %s", ahora.strftime("%Y-%m-%d %H:%M (%A)"))

    # PASO 3
    creados = simulados = saltados = errores = 0
    for i, torneo in enumerate(CALENDARIO_SEMANAL, 1):
        log.info("-" * 60)
        log.info("[%d/%d] %s  (%s %s, %s, %s min)", i, len(CALENDARIO_SEMANAL),
                 torneo["nombre"], torneo["dia"], torneo["hora"],
                 torneo["modo"], torneo["duracion_min"])
        try:
            # PASO 3.1
            inicio = proxima_ocurrencia(torneo["dia"], torneo["hora"], ahora)

            # PASO 3.2
            limite = ahora + timedelta(minutes=MARGEN_MINIMO_MIN)
            if inicio < limite:
                if args.reprogramar:
                    inicio += timedelta(days=7)
                    log.info("   ya paso esta semana -> reprogramado al %s",
                             inicio.strftime("%Y-%m-%d %H:%M"))
                else:
                    log.info("   SALTADO: su hora de esta semana (%s) ya paso.",
                             inicio.strftime("%Y-%m-%d %H:%M"))
                    saltados += 1
                    continue

            log.info("   inicio programado: %s", inicio.strftime("%Y-%m-%d %H:%M (%A)"))

            # PASO 3.3
            payload = construir_payload(torneo, inicio)

            # PASO 3.4
            estado, detalle = crear_torneo(sesion, payload, dry_run)
            if estado == "creado":
                creados += 1
                log.info("   CREADO -> %s", detalle)
            elif estado == "simulado":
                simulados += 1
            else:
                errores += 1
                log.error("   ERROR: %s", detalle)

        except Exception as exc:  # noqa: BLE001  -> un torneo no debe cortar el resto
            errores += 1
            log.error("   ERROR inesperado en '%s': %s", torneo.get("nombre", "?"), exc)

        time.sleep(PAUSA_ENTRE_TORNEOS_SEG)

    # PASO 4
    log.info("=" * 60)
    log.info("RESUMEN - Automatizacion de torneos")
    log.info("=" * 60)
    log.info("Torneos en el calendario : %d", len(CALENDARIO_SEMANAL))
    log.info("Creados (API real)       : %d", creados)
    log.info("Simulados (DRY-RUN)      : %d", simulados)
    log.info("Saltados (ya pasaron)    : %d", saltados)
    log.info("Con error                : %d", errores)
    log.info("=" * 60)
    log.info("### Lichess - automatizacion de torneos - fin ###")
    return 0 if errores == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
