"""
Lichess API - Parte A: Analisis de partidas de un usuario
========================================================
Descarga las ultimas N partidas de un usuario de Lichess, las pasa a un
DataFrame de pandas, calcula estadisticas (resultados, rating, color, modo),
genera graficos con matplotlib y exporta todo a CSV.

Fuente / API oficial:
    GET https://lichess.org/api/games/user/{usuario}
    (respuesta en NDJSON: un objeto JSON por linea)

Requisitos:
    pip install requests pandas matplotlib

Token (opcional para partidas publicas, recomendado por los limites de trafico):
    Generar en https://lichess.org/account/oauth/token con el scope  game:read
    y exportarlo como variable de entorno antes de ejecutar:
        PowerShell:  $env:LICHESS_TOKEN = "tu_token_aqui"
        bash:        export LICHESS_TOKEN="tu_token_aqui"

--------------------------------------------------------------------------------
FLUJO GENERAL (cada bloque esta marcado con "# PASO N" en el codigo):

  PASO 1  Leer parametros (usuario, numero de partidas, carpeta de salida...).
  PASO 2  Preparar logging (consola + output/analysis.log) y carpeta de salida.
  PASO 3  Crear la sesion HTTP (cabeceras + token si existe).
  PASO 4  Descargar las partidas del endpoint NDJSON, con reintentos y respeto
          del limite de trafico (HTTP 429 / cabecera Retry-After).
  PASO 5  Convertir cada partida al "punto de vista" del usuario analizado y
          construir el DataFrame de pandas (una fila por partida).
  PASO 6  Calcular estadisticas: resultados, rating, color y modo de juego.
  PASO 7  Generar los graficos PNG (resultados, evolucion de rating, modos).
  PASO 8  Exportar CSV (partidas crudas + estadisticas + tabla por modo).
  PASO 9  Imprimir un resumen por consola.
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============================================================================
# PASO 0: CONFIGURACION (todo editable aqui; sin valores "quemados" mas abajo)
# ============================================================================
API_PARTIDAS = "https://lichess.org/api/games/user/{usuario}"

USUARIO_POR_DEFECTO = "DrNykterstein"     # cuenta de Magnus Carlsen (muy activa)
NUM_PARTIDAS_POR_DEFECTO = 50

CARPETA_SALIDA = Path(__file__).resolve().parent / "output"

# Cortesia con el servidor: timeout, reintentos y esperas.
TIMEOUT_HTTP_SEG = 60
REINTENTOS = 4
BACKOFF_SEG = 3.0
ESPERA_TRAS_429_SEG = 62          # Lichess pide ~1 min de pausa tras un 429

MODOS_ORDEN = ["ultraBullet", "bullet", "blitz", "rapid", "classical", "correspondence"]

log = logging.getLogger("lichess_analysis")


# ============================================================================
# PASO 2: logging (consola + archivo), con acentos en Windows
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
    archivo = logging.FileHandler(carpeta_salida / "analysis.log", mode="w", encoding="utf-8")
    archivo.setFormatter(fmt)
    log.addHandler(consola)
    log.addHandler(archivo)
    log.propagate = False


# ============================================================================
# PASO 3: sesion HTTP
# ============================================================================
def crear_sesion(token: str) -> requests.Session:
    """Sesion con cabeceras de API y (si existe) el token Bearer."""
    s = requests.Session()
    s.headers.update({
        "Accept": "application/x-ndjson",
        "User-Agent": "practica1-data-science/1.0 (analisis academico)",
    })
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
        log.info("Token de Lichess detectado (%s...). Se usara para autenticar.", token[:4])
    else:
        log.warning("Sin LICHESS_TOKEN: se descargaran solo partidas publicas y con "
                    "limites de trafico mas estrictos.")
    return s


# ============================================================================
# PASO 4: descargar el stream NDJSON de partidas
# ============================================================================
def descargar_partidas(sesion: requests.Session, usuario: str, maximo: int) -> list[dict]:
    """Devuelve una lista de dicts (una por partida), de la mas reciente a la mas
    antigua. Reintenta ante errores de red y respeta el limite de trafico (429).
    """
    url = API_PARTIDAS.format(usuario=usuario)
    params = {
        "max": maximo,
        "rated": "true",
        "perfType": "ultraBullet,bullet,blitz,rapid,classical,correspondence",
        "opening": "true",
        "moves": "true",
        "clocks": "false",
        "evals": "false",
        "pgnInJson": "false",
    }

    for intento in range(1, REINTENTOS + 1):
        try:
            log.info("Descargando hasta %d partidas de '%s' (intento %d/%d)...",
                     maximo, usuario, intento, REINTENTOS)
            resp = sesion.get(url, params=params, timeout=TIMEOUT_HTTP_SEG, stream=True)

            if resp.status_code == 429:
                log.warning("HTTP 429 (limite de trafico). Esperando %d s...", ESPERA_TRAS_429_SEG)
                time.sleep(ESPERA_TRAS_429_SEG)
                continue
            if resp.status_code == 404:
                log.error("El usuario '%s' no existe en Lichess (HTTP 404).", usuario)
                return []
            if resp.status_code == 401:
                log.error("Token invalido o expirado (HTTP 401). Revisa LICHESS_TOKEN.")
                return []
            resp.raise_for_status()

            partidas: list[dict] = []
            for linea in resp.iter_lines(decode_unicode=True):
                if not linea:
                    continue
                try:
                    partidas.append(json.loads(linea))
                except json.JSONDecodeError:
                    log.debug("Linea NDJSON ilegible, se ignora: %r", linea[:120])
            log.info("Recibidas %d partidas.", len(partidas))
            return partidas

        except requests.RequestException as exc:
            log.warning("  fallo de red: %s", exc)
            if intento < REINTENTOS:
                time.sleep(BACKOFF_SEG * intento)

    log.error("No se pudieron descargar las partidas tras %d intentos.", REINTENTOS)
    return []


# ============================================================================
# PASO 5: pasar cada partida al punto de vista del usuario -> DataFrame
# ============================================================================
def _nombre(jugador: dict) -> str:
    return (jugador.get("user") or {}).get("name", "") or ""


def partida_a_fila(p: dict, usuario: str) -> dict | None:
    """Extrae los campos utiles de UNA partida, visto desde 'usuario'.
    Devuelve None si el usuario no aparece en la partida.
    """
    blancas = p.get("players", {}).get("white", {})
    negras = p.get("players", {}).get("black", {})

    u = usuario.lower()
    if _nombre(blancas).lower() == u:
        color, mio, rival = "blancas", blancas, negras
    elif _nombre(negras).lower() == u:
        color, mio, rival = "negras", negras, blancas
    else:
        return None

    ganador = p.get("winner")               # "white" | "black" | ausente (tablas)
    color_api = "white" if color == "blancas" else "black"
    if ganador is None:
        resultado = "tablas"
    elif ganador == color_api:
        resultado = "victoria"
    else:
        resultado = "derrota"

    creada = p.get("createdAt")
    fecha = (datetime.fromtimestamp(creada / 1000, tz=timezone.utc)
             if creada else None)
    movimientos = p.get("moves", "")
    n_plies = len(movimientos.split()) if movimientos else 0

    return {
        "id": p.get("id", ""),
        "fecha": fecha.strftime("%Y-%m-%d %H:%M") if fecha else "",
        "fecha_iso": fecha.isoformat() if fecha else "",
        "rated": bool(p.get("rated", False)),
        "modo": p.get("speed", p.get("perf", "desconocido")),
        "variante": p.get("variant", "standard"),
        "color": color,
        "resultado": resultado,
        "rating": mio.get("rating"),
        "rating_rival": rival.get("rating"),
        "rating_diff": mio.get("ratingDiff"),
        "estado_final": p.get("status", ""),
        "num_jugadas": (n_plies + 1) // 2,
        "apertura": (p.get("opening") or {}).get("name", ""),
        "eco": (p.get("opening") or {}).get("eco", ""),
    }


def construir_dataframe(partidas: list[dict], usuario: str):
    import pandas as pd

    filas = [f for f in (partida_a_fila(p, usuario) for p in partidas) if f]
    df = pd.DataFrame(filas)
    if df.empty:
        return df

    # Orden cronologico ascendente (util para la evolucion del rating).
    df = df.sort_values("fecha_iso").reset_index(drop=True)
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["rating_rival"] = pd.to_numeric(df["rating_rival"], errors="coerce")
    df["rating_diff"] = pd.to_numeric(df["rating_diff"], errors="coerce")
    return df


# ============================================================================
# PASO 6: estadisticas (resultados, rating, color, modo)
# ============================================================================
def calcular_estadisticas(df, usuario: str) -> list[dict]:
    """Devuelve una lista de filas {categoria, metrica, valor} (formato largo)."""
    total = len(df)
    victorias = int((df["resultado"] == "victoria").sum())
    derrotas = int((df["resultado"] == "derrota").sum())
    tablas = int((df["resultado"] == "tablas").sum())
    pct = lambda x: round(100 * x / total, 1) if total else 0.0

    filas = [
        ("general", "usuario", usuario),
        ("general", "partidas_analizadas", total),
        ("general", "rango_fechas", f"{df['fecha'].iloc[0]}  ->  {df['fecha'].iloc[-1]}"),

        ("resultado", "victorias", victorias),
        ("resultado", "derrotas", derrotas),
        ("resultado", "tablas", tablas),
        ("resultado", "win_rate_pct", pct(victorias)),
        ("resultado", "score_pct", round(100 * (victorias + 0.5 * tablas) / total, 1) if total else 0.0),
        ("resultado", "performance_neta_rating", int(df["rating_diff"].dropna().sum())),

        ("rating", "media", round(df["rating"].mean(), 1)),
        ("rating", "mediana", round(df["rating"].median(), 1)),
        ("rating", "minimo", int(df["rating"].min()) if df["rating"].notna().any() else None),
        ("rating", "maximo", int(df["rating"].max()) if df["rating"].notna().any() else None),
        ("rating", "desviacion_std", round(df["rating"].std(), 1)),
        ("rating", "rating_medio_rival", round(df["rating_rival"].mean(), 1)),
    ]

    # --- por color ---
    for color in ["blancas", "negras"]:
        sub = df[df["color"] == color]
        n = len(sub)
        v = int((sub["resultado"] == "victoria").sum())
        filas.append(("color", f"partidas_{color}", n))
        filas.append(("color", f"win_rate_{color}_pct", round(100 * v / n, 1) if n else 0.0))

    # --- por modo de juego ---
    for modo in _modos_presentes(df):
        sub = df[df["modo"] == modo]
        n = len(sub)
        v = int((sub["resultado"] == "victoria").sum())
        filas.append(("modo", f"{modo}_partidas", n))
        filas.append(("modo", f"{modo}_win_rate_pct", round(100 * v / n, 1) if n else 0.0))
        filas.append(("modo", f"{modo}_rating_medio", round(sub["rating"].mean(), 1)))

    return [{"categoria": c, "metrica": m, "valor": val} for c, m, val in filas]


def _modos_presentes(df) -> list[str]:
    presentes = list(df["modo"].unique())
    ordenados = [m for m in MODOS_ORDEN if m in presentes]
    ordenados += [m for m in presentes if m not in ordenados]
    return ordenados


def tabla_por_modo(df):
    """Tabla resumen (una fila por modo) usando groupby de pandas."""
    import pandas as pd

    def resumen(sub):
        n = len(sub)
        return pd.Series({
            "partidas": n,
            "victorias": int((sub["resultado"] == "victoria").sum()),
            "derrotas": int((sub["resultado"] == "derrota").sum()),
            "tablas": int((sub["resultado"] == "tablas").sum()),
            "win_rate_pct": round(100 * (sub["resultado"] == "victoria").sum() / n, 1) if n else 0.0,
            "rating_medio": round(sub["rating"].mean(), 1),
        })

    tabla = df.groupby("modo", group_keys=False).apply(resumen).reset_index()
    for col in ["partidas", "victorias", "derrotas", "tablas"]:
        tabla[col] = tabla[col].astype(int)
    tabla["_orden"] = tabla["modo"].apply(
        lambda m: MODOS_ORDEN.index(m) if m in MODOS_ORDEN else 99)
    return tabla.sort_values("_orden").drop(columns="_orden").reset_index(drop=True)


# ============================================================================
# PASO 7: graficos (matplotlib, backend sin ventana)
# ============================================================================
def generar_graficos(df, carpeta: Path, usuario: str) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    generados: list[Path] = []

    # --- 7.1  Resultados: victoria / derrota / tablas ---
    conteo = df["resultado"].value_counts().reindex(
        ["victoria", "tablas", "derrota"]).fillna(0).astype(int)
    fig, ax = plt.subplots(figsize=(6, 4))
    barras = ax.bar(conteo.index, conteo.values,
                    color=["#4c9f70", "#b0b0b0", "#c1483f"])
    ax.bar_label(barras)
    ax.set_title(f"Resultados de {usuario}  (n={len(df)})")
    ax.set_ylabel("partidas")
    fig.tight_layout()
    ruta = carpeta / "grafico_resultados.png"
    fig.savefig(ruta, dpi=120)
    plt.close(fig)
    generados.append(ruta)

    # --- 7.2  Evolucion del rating en el tiempo, UNA linea por modo ---
    #   (mezclar modos en una sola linea da saltos enganosos porque cada modo
    #    tiene su propia escala de rating)
    serie = df.dropna(subset=["rating"])
    if not serie.empty:
        fig, ax = plt.subplots(figsize=(8, 4))
        for modo in _modos_presentes(serie):
            sub = serie[serie["modo"] == modo].reset_index(drop=True)
            if len(sub) < 2:
                continue
            ax.plot(range(len(sub)), sub["rating"].values,
                    marker=".", linewidth=1, label=f"{modo} (n={len(sub)})")
        ax.set_title(f"Evolucion del rating de {usuario} por modo")
        ax.set_xlabel("partida del modo (cronologico)")
        ax.set_ylabel("rating")
        ax.legend()
        fig.tight_layout()
        ruta = carpeta / "grafico_rating.png"
        fig.savefig(ruta, dpi=120)
        plt.close(fig)
        generados.append(ruta)

    # --- 7.3  Partidas por modo, apiladas por resultado ---
    import pandas as pd
    pivote = (pd.crosstab(df["modo"], df["resultado"])
                .reindex(columns=["victoria", "tablas", "derrota"]).fillna(0))
    pivote = pivote.reindex([m for m in MODOS_ORDEN if m in pivote.index])
    if not pivote.empty:
        fig, ax = plt.subplots(figsize=(7, 4))
        pivote.plot(kind="bar", stacked=True, ax=ax,
                    color={"victoria": "#4c9f70", "tablas": "#b0b0b0", "derrota": "#c1483f"})
        ax.set_title(f"Partidas por modo de juego - {usuario}")
        ax.set_xlabel("modo")
        ax.set_ylabel("partidas")
        ax.tick_params(axis="x", rotation=0)
        fig.tight_layout()
        ruta = carpeta / "grafico_modos.png"
        fig.savefig(ruta, dpi=120)
        plt.close(fig)
        generados.append(ruta)

    return generados


# ============================================================================
# PASO 8: exportar CSV
# ============================================================================
def exportar_csv(df, estadisticas: list[dict], por_modo, carpeta: Path) -> None:
    import pandas as pd

    p1 = carpeta / "partidas.csv"
    df.drop(columns=["fecha_iso"]).to_csv(p1, index=False, encoding="utf-8-sig")
    log.info("CSV escrito: %s (%d filas)", p1, len(df))

    p2 = carpeta / "estadisticas.csv"
    pd.DataFrame(estadisticas).to_csv(p2, index=False, encoding="utf-8-sig")
    log.info("CSV escrito: %s (%d metricas)", p2, len(estadisticas))

    p3 = carpeta / "estadisticas_por_modo.csv"
    por_modo.to_csv(p3, index=False, encoding="utf-8-sig")
    log.info("CSV escrito: %s (%d modos)", p3, len(por_modo))


# ============================================================================
# PASO 9: resumen por consola
# ============================================================================
def imprimir_resumen(df, estadisticas: list[dict], graficos: list[Path]) -> None:
    d = {f"{e['categoria']}.{e['metrica']}": e["valor"] for e in estadisticas}
    log.info("=" * 60)
    log.info("RESUMEN - Analisis de partidas de Lichess")
    log.info("=" * 60)
    log.info("Usuario                : %s", d.get("general.usuario"))
    log.info("Partidas analizadas    : %s", d.get("general.partidas_analizadas"))
    log.info("Rango de fechas        : %s", d.get("general.rango_fechas"))
    log.info("Victorias/Tablas/Derrotas : %s / %s / %s",
             d.get("resultado.victorias"), d.get("resultado.tablas"), d.get("resultado.derrotas"))
    log.info("Win rate / Score       : %s%% / %s%%",
             d.get("resultado.win_rate_pct"), d.get("resultado.score_pct"))
    log.info("Rating medio (min-max) : %s  (%s - %s)",
             d.get("rating.media"), d.get("rating.minimo"), d.get("rating.maximo"))
    log.info("Performance neta rating: %s", d.get("resultado.performance_neta_rating"))
    log.info("Graficos generados     : %s", ", ".join(g.name for g in graficos) or "(ninguno)")
    log.info("=" * 60)


# ============================================================================
# PASO 1: argumentos de linea de comandos
# ============================================================================
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analiza las ultimas N partidas de un usuario de Lichess."
    )
    p.add_argument("--usuario", default=USUARIO_POR_DEFECTO,
                   help=f"usuario de Lichess a analizar (por defecto: {USUARIO_POR_DEFECTO})")
    p.add_argument("--n", "--max", dest="n", type=int, default=NUM_PARTIDAS_POR_DEFECTO,
                   help=f"numero maximo de partidas (por defecto: {NUM_PARTIDAS_POR_DEFECTO})")
    p.add_argument("--salida", type=Path, default=CARPETA_SALIDA,
                   help="carpeta donde se guardan CSV y graficos")
    p.add_argument("--sin-graficos", action="store_true",
                   help="no generar los PNG (solo CSV)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    # PASO 1
    args = parse_args(argv)
    if args.n <= 0:
        print("ERROR: --n debe ser un entero positivo", file=sys.stderr)
        return 2
    carpeta = Path(args.salida)

    # PASO 2
    configurar_logging(carpeta)
    log.info("### Lichess - analisis de partidas - inicio ###")

    try:
        import pandas  # noqa: F401
    except ImportError:
        log.error("Falta pandas. Instala dependencias: pip install -r requirements.txt")
        return 1

    # PASO 3
    token = os.environ.get("LICHESS_TOKEN", "").strip()
    sesion = crear_sesion(token)

    # PASO 4
    partidas = descargar_partidas(sesion, args.usuario, args.n)
    if not partidas:
        log.error("No se obtuvieron partidas. Revisa el usuario, el token o la conexion.")
        return 1

    # PASO 5
    df = construir_dataframe(partidas, args.usuario)
    if df.empty:
        log.error("Ninguna partida descargada corresponde al usuario '%s'.", args.usuario)
        return 1
    log.info("DataFrame construido: %d partidas x %d columnas.", df.shape[0], df.shape[1])

    # PASO 6
    estadisticas = calcular_estadisticas(df, args.usuario)
    por_modo = tabla_por_modo(df)

    # PASO 7
    graficos: list[Path] = []
    if not args.sin_graficos:
        try:
            graficos = generar_graficos(df, carpeta, args.usuario)
        except ImportError:
            log.warning("matplotlib no esta instalado: se omiten los graficos.")

    # PASO 8
    exportar_csv(df, estadisticas, por_modo, carpeta)

    # PASO 9
    imprimir_resumen(df, estadisticas, graficos)
    log.info("### Lichess - analisis de partidas - fin ###")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
