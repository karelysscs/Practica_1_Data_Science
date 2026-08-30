"""
SUNAT - Web Scraping del Tipo de Cambio Oficial (compra / venta)
================================================================
Extrae el tipo de cambio oficial que publica la SUNAT desde ENERO 2024 hasta el
mes actual y lo consolida en UN solo archivo (CSV y/o Excel).

Fuente oficial (pagina de consulta):
    https://e-consulta.sunat.gob.pe/cl-at-ittipcam/tcS01Alias

Requisitos:
    pip install requests pandas openpyxl
    (openpyxl solo hace falta si exportas a Excel)

--------------------------------------------------------------------------------
FLUJO GENERAL (todos los PASOS estan marcados con "# PASO N" en el codigo):

  PASO 1  Leer los parametros (rango de meses, formato de salida, etc.).
  PASO 2  Preparar el logging (consola + archivo) y la carpeta de salida.
  PASO 3  Crear una sesion HTTP con cabeceras de navegador y "calentarla"
          visitando una vez la pagina publica (para tomar cookies si hiciera falta).
  PASO 4  Recorrer MES a MES el rango pedido (enero 2024 -> mes actual):
            PASO 4.1  Pedir los datos del mes al endpoint JSON de SUNAT.
            PASO 4.2  Si el mes viene vacio (futuro / sin publicar) se anota y se sigue.
            PASO 4.3  Convertir la respuesta a {fecha: {compra, venta}}.
            PASO 4.4  Esperar un momento entre peticiones (no saturar el servidor).
  PASO 5  Construir la tabla final: UNA fila por dia calendario del rango, con
          compra, venta, si fue "publicado" ese dia y de que fecha se tomo el valor.
          Los dias sin publicacion (feriados, fines de semana, etc.) se rellenan
          con el ultimo valor disponible (regla oficial de SUNAT, nota 2).
  PASO 6  Detectar y listar los dias habiles SIN cotizacion publicada.
  PASO 7  Exportar a CSV y/o Excel.
  PASO 8  Escribir un resumen (rango, totales, dias publicados, faltantes...).

--------------------------------------------------------------------------------
NOTA SOBRE EL METODO
El calendario de la web llama por debajo a un endpoint JSON:
    POST /cl-at-ittipcam/tcS01Alias/listarTipoCambio
    body -> {"anio": 2024, "mes": 0, "token": "<texto>"}   (mes es 0-based: 0=enero)
    respuesta -> [{"fecPublica":"01/01/2024","valTipo":"3.705","codTipo":"C"}, ...]
                 codTipo "C" = compra, "V" = venta
Usar ese endpoint directamente es mas robusto y rapido que raspar el HTML del
calendario. Igual se incluye un metodo alternativo con Selenium + WebDriverWait
(--metodo selenium) por si se quiere demostrar la automatizacion del navegador.
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# ============================================================================
# PASO 0: CONFIGURACION (todo editable aqui; no hay valores "quemados" mas abajo)
# ============================================================================
BASE_URL = "https://e-consulta.sunat.gob.pe/cl-at-ittipcam/tcS01Alias"
ENDPOINT_JSON = BASE_URL + "/listarTipoCambio"

# Rango por defecto: desde enero 2024 hasta el mes actual.
INICIO_POR_DEFECTO = (2024, 1)            # (anio, mes)  -> lo pide la consigna

# Carpeta y nombres de salida.
CARPETA_SALIDA = Path(__file__).resolve().parent / "salida"
NOMBRE_BASE = "tipo_cambio_sunat"

# Cortesia con el servidor: pausa entre peticiones y reintentos.
PAUSA_ENTRE_MESES_SEG = 1.2
REINTENTOS_POR_MES = 3
BACKOFF_SEG = 2.0
TIMEOUT_HTTP_SEG = 30

# Cabeceras "de navegador" para que el servidor nos trate como al sitio real.
CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
    "Origin": "https://e-consulta.sunat.gob.pe",
    "Content-Type": "application/json; charset=utf-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

DIAS_SEMANA_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "setiembre", "octubre", "noviembre", "diciembre"]

log = logging.getLogger("sunat")


# ============================================================================
# PASO 2 (parte): utilidades de logging
# ============================================================================
def configurar_logging(carpeta_salida: Path) -> None:
    """Deja el logging escribiendo a la consola y a salida/scraping.log."""
    # La consola de Windows suele venir en cp1252; forzamos UTF-8 para los acentos.
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
    archivo = logging.FileHandler(carpeta_salida / "scraping.log", encoding="utf-8")
    archivo.setFormatter(fmt)
    log.addHandler(consola)
    log.addHandler(archivo)
    log.propagate = False


# ============================================================================
# PASO 1 (parte): parseo de "AAAA-MM" y generador de meses del rango
# ============================================================================
def parse_anio_mes(texto: str) -> tuple[int, int]:
    """Convierte '2024-01' o '2024/1' en la tupla (2024, 1). Valida el mes."""
    texto = texto.strip().replace("/", "-")
    partes = texto.split("-")
    if len(partes) != 2:
        raise argparse.ArgumentTypeError(f"Formato invalido: {texto!r} (use AAAA-MM)")
    anio, mes = int(partes[0]), int(partes[1])
    if not (1 <= mes <= 12):
        raise argparse.ArgumentTypeError(f"Mes fuera de rango en {texto!r}")
    return (anio, mes)


def iterar_meses(desde: tuple[int, int], hasta: tuple[int, int]):
    """Genera (anio, mes) desde 'desde' hasta 'hasta' inclusive, mes por mes."""
    a, m = desde
    while (a, m) <= hasta:
        yield (a, m)
        m += 1
        if m > 12:
            m = 1
            a += 1


# ============================================================================
# PASO 3: sesion HTTP
# ============================================================================
def crear_sesion() -> requests.Session:
    """Crea la sesion, pone las cabeceras y visita una vez la pagina publica."""
    s = requests.Session()
    s.headers.update(CABECERAS)
    try:
        # PASO 3.1: "calentar" la sesion (por si el servidor entrega cookies).
        s.get(BASE_URL, timeout=TIMEOUT_HTTP_SEG)
    except requests.RequestException as exc:
        log.warning("No se pudo precargar la pagina (%s). Se continua igual.", exc)
    return s


# ============================================================================
# PASO 4.1: pedir un mes al endpoint JSON de SUNAT
# ============================================================================
def descargar_mes_json(sesion: requests.Session, anio: int, mes: int) -> list[dict]:
    """Devuelve la lista cruda de registros del mes (puede venir vacia).

    'mes' aqui es 1..12; el endpoint lo quiere 0..11, por eso se envia mes-1.
    El campo 'token' solo necesita ser un texto no vacio (no valida reCAPTCHA real).
    """
    payload = json.dumps({"anio": anio, "mes": mes - 1, "token": "sunat-scraper"})

    for intento in range(1, REINTENTOS_POR_MES + 1):
        try:
            r = sesion.post(ENDPOINT_JSON, data=payload, timeout=TIMEOUT_HTTP_SEG)
            r.raise_for_status()
            datos = r.json()
            if not isinstance(datos, list):
                raise ValueError(f"respuesta inesperada: {type(datos)}")
            return datos
        except (requests.RequestException, ValueError) as exc:
            log.warning("  intento %d/%d fallo para %s-%02d: %s",
                        intento, REINTENTOS_POR_MES, anio, mes, exc)
            if intento < REINTENTOS_POR_MES:
                time.sleep(BACKOFF_SEG * intento)

    log.error("  no se pudo obtener %s-%02d tras %d intentos", anio, mes, REINTENTOS_POR_MES)
    return []


# ============================================================================
# PASO 4.3: convertir la respuesta cruda a {fecha: {"compra": x, "venta": y}}
# ============================================================================
def parsear_registros(crudo: list[dict]) -> dict[date, dict[str, float]]:
    """Agrupa por fecha y separa compra (C) de venta (V)."""
    por_fecha: dict[date, dict[str, float]] = {}
    for item in crudo:
        try:
            f = datetime.strptime(item["fecPublica"].strip(), "%d/%m/%Y").date()
            valor = float(str(item["valTipo"]).replace(",", "."))
            tipo = item["codTipo"].strip().upper()
        except (KeyError, ValueError, AttributeError):
            log.debug("  registro ignorado (formato raro): %r", item)
            continue
        celda = por_fecha.setdefault(f, {})
        if tipo == "C":
            celda["compra"] = valor
        elif tipo == "V":
            celda["venta"] = valor
    return por_fecha


# ============================================================================
# PASO 5: construir la tabla final (una fila por dia calendario del rango)
# ============================================================================
def construir_filas(publicados: dict[date, dict[str, float]],
                    desde: tuple[int, int], hasta: tuple[int, int],
                    rellenar: bool) -> list[dict]:
    """Recorre TODOS los dias del rango y arma la fila de cada uno.

    - Si el dia tiene cotizacion publicada -> se usa tal cual (publicado = True).
    - Si NO tiene y rellenar=True -> se arrastra el ultimo valor disponible
      (regla oficial de SUNAT: "se toma el del dia inmediato anterior").
    - Si NO tiene y rellenar=False -> compra/venta quedan vacios.
    """
    dia = date(desde[0], desde[1], 1)
    # ultimo dia del mes 'hasta'
    if hasta[1] == 12:
        fin = date(hasta[0], 12, 31)
    else:
        fin = date(hasta[0], hasta[1] + 1, 1) - timedelta(days=1)
    # nunca pasar de hoy
    fin = min(fin, date.today())

    filas: list[dict] = []
    ult_compra = ult_venta = None
    ult_fecha_valor: date | None = None

    while dia <= fin:
        pub = publicados.get(dia)
        if pub and "compra" in pub and "venta" in pub:
            ult_compra, ult_venta, ult_fecha_valor = pub["compra"], pub["venta"], dia
            filas.append(_fila(dia, pub["compra"], pub["venta"], True, dia))
        elif rellenar and ult_compra is not None:
            filas.append(_fila(dia, ult_compra, ult_venta, False, ult_fecha_valor))
        else:
            filas.append(_fila(dia, None, None, False, None))
        dia += timedelta(days=1)

    return filas


def _fila(f: date, compra, venta, publicado: bool, fecha_valor) -> dict:
    """Arma un diccionario-fila con todas las columnas de salida."""
    return {
        "fecha": f.isoformat(),
        "anio": f.year,
        "mes": f.month,
        "mes_nombre": MESES_ES[f.month],
        "dia": f.day,
        "dia_semana": DIAS_SEMANA_ES[f.weekday()],
        "es_fin_de_semana": f.weekday() >= 5,
        "compra": compra,
        "venta": venta,
        "publicado": publicado,
        "fecha_valor_usada": fecha_valor.isoformat() if fecha_valor else "",
    }


# ============================================================================
# PASO 6: dias habiles (lun-vie) sin cotizacion publicada -> posibles feriados
# ============================================================================
def dias_habiles_sin_publicacion(filas: list[dict]) -> list[str]:
    return [
        fila["fecha"]
        for fila in filas
        if not fila["publicado"] and not fila["es_fin_de_semana"]
    ]


# ============================================================================
# PASO 7: exportar a CSV y/o Excel
# ============================================================================
COLUMNAS = ["fecha", "anio", "mes", "mes_nombre", "dia", "dia_semana",
           "es_fin_de_semana", "compra", "venta", "publicado", "fecha_valor_usada"]


def exportar_csv(filas: list[dict], ruta: Path) -> None:
    with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(filas)
    log.info("CSV escrito: %s (%d filas)", ruta, len(filas))


def exportar_excel(filas: list[dict], ruta: Path) -> None:
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas/openpyxl no instalados: se omite el Excel. (pip install pandas openpyxl)")
        return
    df = pd.DataFrame(filas, columns=COLUMNAS)
    with pd.ExcelWriter(ruta, engine="openpyxl") as xls:
        df.to_excel(xls, index=False, sheet_name="TipoCambio")
    log.info("Excel escrito: %s (%d filas)", ruta, len(filas))


# ============================================================================
# PASO 8: resumen final
# ============================================================================
def imprimir_resumen(filas: list[dict], meses_vacios: list[str],
                     habiles_sin_pub: list[str]) -> None:
    publicados = sum(1 for f in filas if f["publicado"])
    con_valor = sum(1 for f in filas if f["compra"] is not None)
    log.info("=" * 60)
    log.info("RESUMEN - Tipo de cambio SUNAT")
    log.info("=" * 60)
    if filas:
        log.info("Rango de fechas          : %s  ->  %s", filas[0]["fecha"], filas[-1]["fecha"])
    log.info("Dias totales en el rango : %d", len(filas))
    log.info("Dias con cotizacion pub. : %d", publicados)
    log.info("Dias con valor (incl. arrastrado) : %d", con_valor)
    log.info("Dias habiles SIN publicacion (posibles feriados) : %d", len(habiles_sin_pub))
    if habiles_sin_pub:
        muestra = ", ".join(habiles_sin_pub[:15])
        log.info("   %s%s", muestra, " ..." if len(habiles_sin_pub) > 15 else "")
    if meses_vacios:
        log.info("Meses sin datos (futuros / no publicados) : %s", ", ".join(meses_vacios))
    log.info("=" * 60)


# ============================================================================
# METODO ALTERNATIVO: Selenium + WebDriverWait (opcional, --metodo selenium)
# ============================================================================
def descargar_todo_selenium(desde, hasta, pausa):
    """Recorre el calendario de la web con el navegador real.

    Se usa el mismo endpoint por debajo, pero disparado desde la pagina para
    demostrar automatizacion con WebDriverWait. Devuelve el mismo dict que
    parsear_registros: {fecha: {compra, venta}}.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.wait import WebDriverWait

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1300,900")
    opts.add_argument("--lang=es-PE")
    driver = webdriver.Chrome(options=opts)
    publicados: dict[date, dict[str, float]] = {}
    try:
        # PASO S1: abrir la pagina y esperar a que exista el buscador de mes.
        driver.get(BASE_URL)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.ID, "fecAsistenciaBusq")))

        for anio, mes in iterar_meses(desde, hasta):
            # PASO S2: ejecutar en la pagina el mismo POST que hace el calendario
            #          y esperar (WebDriverWait) a que la promesa resuelva.
            script = """
                const cb = arguments[arguments.length - 1];
                fetch(arguments[0], {method:'POST',
                    headers:{'Content-Type':'application/json; charset=utf-8'},
                    body: JSON.stringify({anio: arguments[1], mes: arguments[2], token:'x'})})
                  .then(r => r.json()).then(j => cb(j)).catch(e => cb({error:String(e)}));
            """
            driver.set_script_timeout(30)
            crudo = driver.execute_async_script(script, ENDPOINT_JSON, anio, mes - 1)
            if isinstance(crudo, dict) and crudo.get("error"):
                log.warning("  %s-%02d: %s", anio, mes, crudo["error"])
                crudo = []
            if not crudo:
                log.info("  %s %d: sin datos", MESES_ES[mes], anio)
            publicados.update(parsear_registros(crudo))
            log.info("  %s %d: %d dias", MESES_ES[mes], anio, len(parsear_registros(crudo)))
            time.sleep(pausa)
    finally:
        driver.quit()
    return publicados


# ============================================================================
# PASO 1: argumentos de linea de comandos
# ============================================================================
def parse_args(argv=None) -> argparse.Namespace:
    hoy = date.today()
    p = argparse.ArgumentParser(
        description="Scraper del tipo de cambio oficial de SUNAT (enero 2024 -> mes actual)."
    )
    p.add_argument("--desde", type=parse_anio_mes, default=None,
                   metavar="AAAA-MM", help="mes inicial (por defecto 2024-01)")
    p.add_argument("--hasta", type=parse_anio_mes, default=None,
                   metavar="AAAA-MM", help=f"mes final (por defecto el mes actual: {hoy:%Y-%m})")
    p.add_argument("--salida", type=Path, default=CARPETA_SALIDA,
                   help="carpeta donde se guardan los archivos")
    p.add_argument("--formato", choices=["csv", "excel", "ambos"], default="ambos",
                   help="formato de exportacion (por defecto: ambos)")
    p.add_argument("--metodo", choices=["api", "selenium"], default="api",
                   help="'api' = requests al endpoint JSON (rapido); 'selenium' = navegador")
    p.add_argument("--sin-relleno", action="store_true",
                   help="NO arrastrar el valor del dia anterior en dias sin publicacion")
    p.add_argument("--pausa", type=float, default=PAUSA_ENTRE_MESES_SEG,
                   help="segundos de espera entre meses")
    return p.parse_args(argv)


def main(argv=None) -> int:
    # PASO 1: leer parametros y fijar el rango (por defecto 2024-01 -> mes actual).
    args = parse_args(argv)
    hoy = date.today()
    desde = args.desde or INICIO_POR_DEFECTO
    hasta = args.hasta or (hoy.year, hoy.month)
    rellenar = not args.sin_relleno

    if desde > hasta:
        print(f"ERROR: 'desde' ({desde}) es posterior a 'hasta' ({hasta})", file=sys.stderr)
        return 2

    # PASO 2: logging + carpeta de salida.
    carpeta = Path(args.salida)
    configurar_logging(carpeta)
    log.info("### SUNAT tipo de cambio - inicio ###")
    log.info("Rango pedido: %s-%02d  ->  %s-%02d | metodo=%s | relleno=%s",
             desde[0], desde[1], hasta[0], hasta[1], args.metodo, rellenar)

    # PASO 3 + PASO 4: descargar todos los meses.
    meses_vacios: list[str] = []
    publicados: dict[date, dict[str, float]] = {}

    if args.metodo == "selenium":
        # Metodo navegador (opcional / demo). Si falla por red o driver,
        # se cae automaticamente al metodo 'api' para no quedarse sin datos.
        try:
            publicados = descargar_todo_selenium(desde, hasta, args.pausa)
        except Exception as exc:  # noqa: BLE001
            log.warning("El metodo Selenium fallo (%s).", str(exc).splitlines()[0])
            log.warning("Se cambia automaticamente al metodo 'api'.")
            args.metodo = "api"

    if args.metodo == "api" and not publicados:
        sesion = crear_sesion()                              # PASO 3
        for anio, mes in iterar_meses(desde, hasta):        # PASO 4
            log.info("Descargando %s %d ...", MESES_ES[mes], anio)
            crudo = descargar_mes_json(sesion, anio, mes)     # PASO 4.1
            if not crudo:                                     # PASO 4.2
                meses_vacios.append(f"{anio}-{mes:02d}")
                log.info("  (sin datos)")
                time.sleep(args.pausa)
                continue
            parsed = parsear_registros(crudo)                 # PASO 4.3
            publicados.update(parsed)
            log.info("  %d dias con cotizacion", len(parsed))
            time.sleep(args.pausa)                            # PASO 4.4

    if not publicados:
        log.error("No se obtuvo ningun dato. Revisa la conexion o el rango.")
        return 1

    # PASO 5: construir la tabla final (una fila por dia).
    filas = construir_filas(publicados, desde, hasta, rellenar)

    # PASO 6: dias habiles sin publicacion (posibles feriados).
    habiles_sin_pub = dias_habiles_sin_publicacion(filas)

    # PASO 7: exportar.
    carpeta.mkdir(parents=True, exist_ok=True)
    etiqueta = f"{desde[0]}{desde[1]:02d}_{hasta[0]}{hasta[1]:02d}"
    if args.formato in ("csv", "ambos"):
        exportar_csv(filas, carpeta / f"{NOMBRE_BASE}_{etiqueta}.csv")
    if args.formato in ("excel", "ambos"):
        exportar_excel(filas, carpeta / f"{NOMBRE_BASE}_{etiqueta}.xlsx")

    # PASO 8: resumen.
    imprimir_resumen(filas, meses_vacios, habiles_sin_pub)
    log.info("### SUNAT tipo de cambio - fin ###")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
