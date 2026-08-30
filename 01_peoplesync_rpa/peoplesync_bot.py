"""
PARTE 1 - RPA PeopleSync: registro masivo de colaboradores
=========================================================
Automatiza el alta de los 50 empleados del dataset en el formulario
"PeopleSync HRIS" usando Python + Selenium.

Flujo:
  1. Lee el dataset (CSV local o, si se activa, la hoja de Google Sheets).
  2. Abre el formulario y lee las opciones reales de cada <select>
     (Género, Área, Puesto, Contrato, Sede) para saber qué acepta.
  3. Valida cada registro en Python ANTES de tocar el navegador:
       - DNI: 8 dígitos           - Teléfono: 9 dígitos, empieza en 9
       - Correo con formato válido - Fechas convertibles a AAAA-MM-DD
       - Género/Área/Puesto/Contrato/Sede/Modalidad dentro de las opciones del form
     Los registros que no cumplen se saltan (no se detiene el proceso).
  4. Para cada registro válido: llena el formulario, envía y verifica que el
     contador "Ingresos registrados" haya subido (confirmación del alta),
     todo en una sola carga de página (sin recargar).
  5. Al final imprime el log: total procesados, cargados, fallidos y el
     detalle de cada fallo, guarda resultado_registros.csv y toma una captura
     (evidencia_registros.png) de la tabla con todos los ingresos de la sesión.

Requisitos:
    pip install selenium pandas
"""

import re
import sys
import time
import csv
from datetime import datetime
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.select import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # acentos en consola
except (AttributeError, ValueError):
    pass

# ---------------------------------------------------------------------------
# CONFIGURACIÓN  (edita estos valores)
# ---------------------------------------------------------------------------
FORM_URL = "https://the-paul2002.github.io/Proyecto-IA-/Homework1/"

CARPETA = Path(__file__).resolve().parent                             # carpeta de este script (01_peoplesync_rpa)
CSV_LOCAL = CARPETA / "Ingreso_Personal_Agosto - Hoja 1.csv"          # dataset junto al script, no depende del cwd
SHEET_ID = "1EjaoSJKdzdUBNF3XJZuTlxA21D-0vy0wkGaMR8wHVgs"
USAR_GOOGLE_SHEETS = False        # True = descarga del Sheet; False = usa CSV_LOCAL
OUTPUT_CSV = CARPETA / "resultado_registros.csv"
EVIDENCIA_PNG = CARPETA / "evidencia_registros.png"   # captura final de la tabla de ingresos

HEADLESS = False                  # True = sin ventana (para Task Scheduler)
WAIT_TIMEOUT = 15                 # segundos para WebDriverWait
PAUSA_ENTRE_REGISTROS = 0.3

GENEROS_VALIDOS = {"Masculino", "Femenino"}   # el <select> Género solo tiene estos dos


# ---------------------------------------------------------------------------
# PASO 1: leer el dataset
# ---------------------------------------------------------------------------
def leer_dataset():
    if USAR_GOOGLE_SHEETS:
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
        print(f"Leyendo dataset desde Google Sheets...")
        df = pd.read_csv(url)
    else:
        print(f"Leyendo dataset local: {CSV_LOCAL.name}")
        df = pd.read_csv(CSV_LOCAL)
    df.columns = [c.strip() for c in df.columns]
    print(f"  {len(df)} filas cargadas")
    return df


# ---------------------------------------------------------------------------
# PASO 2: abrir el formulario y leer las opciones reales de cada <select>
# ---------------------------------------------------------------------------
def crear_driver():
    op = Options()
    if HEADLESS:
        op.add_argument("--headless=new")
    op.add_argument("--window-size=1400,1000")
    op.add_argument("--lang=es-PE")
    return webdriver.Chrome(options=op)


def abrir_formulario(driver):
    driver.get(FORM_URL)
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.element_to_be_clickable((By.ID, "btn-registrar"))
    )
    # opciones aceptadas por cada desplegable (se ignoran los "— Seleccione —")
    opciones = {}
    for campo in ("genero", "area", "puesto", "contrato", "sede"):
        sel = Select(driver.find_element(By.ID, campo))
        opciones[campo] = {o.text.strip() for o in sel.options if o.get_attribute("value")}
    return opciones


# ---------------------------------------------------------------------------
# PASO 3: validar un registro contra las reglas del formulario
# ---------------------------------------------------------------------------
def a_fecha_iso(texto):
    """Convierte '14/01/1998' (o variantes) a '1998-01-14'. Devuelve None si no se puede."""
    texto = str(texto).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def validar(reg, opciones):
    """Devuelve una lista de errores (vacía = registro válido)."""
    errores = []

    if not str(reg["apellidos_nombres"]).strip():
        errores.append("apellidos_nombres vacío")

    if not re.fullmatch(r"\d{8}", str(reg["dni"]).strip()):
        errores.append(f"DNI inválido ({reg['dni']})")

    if not re.fullmatch(r"9\d{8}", str(reg["telefono"]).strip()):
        errores.append(f"teléfono inválido ({reg['telefono']})")

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(reg["correo"]).strip()):
        errores.append(f"correo inválido ({reg['correo']})")

    if a_fecha_iso(reg["fecha_nacimiento"]) is None:
        errores.append(f"fecha_nacimiento inválida ({reg['fecha_nacimiento']})")
    if a_fecha_iso(reg["fecha_ingreso"]) is None:
        errores.append(f"fecha_ingreso inválida ({reg['fecha_ingreso']})")

    if str(reg["genero"]).strip() not in GENEROS_VALIDOS:
        errores.append(f"género '{reg['genero']}' no está en el formulario (Masculino/Femenino)")

    for campo in ("area", "puesto", "contrato", "sede"):
        if str(reg[campo]).strip() not in opciones[campo]:
            errores.append(f"{campo} '{reg[campo]}' fuera de las opciones del formulario")

    if str(reg["modalidad"]).strip() not in {"Presencial", "Remoto", "Híbrido"}:
        errores.append(f"modalidad '{reg['modalidad']}' inválida")

    return errores


# ---------------------------------------------------------------------------
# PASO 4: llenar el formulario y registrar un colaborador
# ---------------------------------------------------------------------------
def set_fecha(driver, campo_id, valor_iso):
    """Los <input type=date> se llenan por JS para evitar problemas de idioma."""
    el = driver.find_element(By.ID, campo_id)
    driver.execute_script(
        "arguments[0].value = arguments[1];"
        "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
        "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
        el, valor_iso,
    )


def registrar_colaborador(driver, reg):
    """Llena y envía un registro. Lanza excepción si el alta no se confirma."""
    n_antes = int(driver.find_element(By.ID, "counter").text.strip() or "0")

    driver.find_element(By.ID, "nombres").clear()
    driver.find_element(By.ID, "nombres").send_keys(str(reg["apellidos_nombres"]).strip())
    driver.find_element(By.ID, "dni").clear()
    driver.find_element(By.ID, "dni").send_keys(str(reg["dni"]).strip())
    set_fecha(driver, "fecha_nacimiento", a_fecha_iso(reg["fecha_nacimiento"]))
    Select(driver.find_element(By.ID, "genero")).select_by_visible_text(str(reg["genero"]).strip())
    driver.find_element(By.ID, "telefono").clear()
    driver.find_element(By.ID, "telefono").send_keys(str(reg["telefono"]).strip())
    driver.find_element(By.ID, "correo").clear()
    driver.find_element(By.ID, "correo").send_keys(str(reg["correo"]).strip())

    Select(driver.find_element(By.ID, "area")).select_by_visible_text(str(reg["area"]).strip())
    Select(driver.find_element(By.ID, "puesto")).select_by_visible_text(str(reg["puesto"]).strip())
    Select(driver.find_element(By.ID, "contrato")).select_by_visible_text(str(reg["contrato"]).strip())
    Select(driver.find_element(By.ID, "sede")).select_by_visible_text(str(reg["sede"]).strip())
    set_fecha(driver, "fecha_ingreso", a_fecha_iso(reg["fecha_ingreso"]))
    # El radio de modalidad suele estar oculto por CSS (label estilizado encima),
    # por eso .click() de Selenium falla con ElementNotInteractableException.
    # Se hace click por JS, que igual dispara los eventos change del formulario.
    modalidad_el = driver.find_element(
        By.CSS_SELECTOR, f'input[name="modalidad"][value="{str(reg["modalidad"]).strip()}"]'
    )
    driver.execute_script(
        "arguments[0].click();"
        "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
        modalidad_el,
    )

    # La barra de estado (div.status-bar, fija al pie) se superpone al botón y
    # Selenium falla con ElementClickInterceptedException. Se centra el botón en
    # el viewport y se hace click por JS, que dispara igual el onclick registrar().
    btn = driver.find_element(By.ID, "btn-registrar")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    driver.execute_script("arguments[0].click();", btn)

    # PASO 4.1: verificar que el contador subió (confirmación del alta, sin recargar).
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        lambda d: int(d.find_element(By.ID, "counter").text.strip() or "0") == n_antes + 1
    )


# ---------------------------------------------------------------------------
# PASO 4.2: captura de evidencia con todos los ingresos al final de la página
# ---------------------------------------------------------------------------
def capturar_evidencia(driver, ruta):
    """Desplaza a la tabla 'Ingresos Registrados en esta Sesión' y guarda un PNG
    de la página completa (se agranda la ventana al alto real para que salgan
    todas las filas, no solo lo visible)."""
    try:
        driver.execute_script(
            "document.getElementById('records-section').scrollIntoView({block:'start'});"
        )
        alto = driver.execute_script("return document.documentElement.scrollHeight")
        ancho = driver.execute_script("return document.documentElement.scrollWidth")
        tam_previo = driver.get_window_size()
        driver.set_window_size(max(ancho, 1400), alto + 120)
        time.sleep(0.5)
        driver.save_screenshot(str(ruta))
        driver.set_window_size(tam_previo["width"], tam_previo["height"])
        print(f"Evidencia guardada en: {ruta}")
    except Exception as e:
        print(f"No se pudo guardar la evidencia: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    df = leer_dataset()                                  # PASO 1

    driver = crear_driver()
    resultados = []
    cargados = fallidos = 0

    try:
        opciones = abrir_formulario(driver)              # PASO 2

        for i, fila in enumerate(df.to_dict("records"), start=1):
            ident = f"fila {i} | DNI {fila['dni']} | {fila['apellidos_nombres']}"

            # PASO 3: validar
            errores = validar(fila, opciones)
            if errores:
                fallidos += 1
                motivo = "; ".join(errores)
                print(f"[{i:>2}/{len(df)}] SALTADO  {ident} -> {motivo}")
                resultados.append({**_campos(i, fila), "estado": "FALLIDO", "motivo": motivo})
                continue

            # PASO 4: registrar
            try:
                registrar_colaborador(driver, fila)
                cargados += 1
                print(f"[{i:>2}/{len(df)}] OK       {ident}")
                resultados.append({**_campos(i, fila), "estado": "OK", "motivo": ""})
            except (TimeoutException, NoSuchElementException) as e:
                fallidos += 1
                print(f"[{i:>2}/{len(df)}] ERROR    {ident} -> {type(e).__name__}")
                resultados.append({**_campos(i, fila), "estado": "FALLIDO",
                                   "motivo": f"no se confirmó el alta ({type(e).__name__})"})
                driver.execute_script("limpiarFormulario();")   # dejar el form limpio

            time.sleep(PAUSA_ENTRE_REGISTROS)

        capturar_evidencia(driver, EVIDENCIA_PNG)        # PASO 4.2
    finally:
        driver.quit()

    # PASO 5: guardar CSV y mostrar el resumen
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(resultados[0].keys()))
        w.writeheader()
        w.writerows(resultados)

    print("\n" + "=" * 60)
    print("RESUMEN - PeopleSync RPA")
    print("=" * 60)
    print(f"Total procesados : {len(df)}")
    print(f"Cargados con éxito: {cargados}")
    print(f"No cargados       : {fallidos}")
    print("-" * 60)
    for r in resultados:
        if r["estado"] == "FALLIDO":
            print(f"  fila {r['fila']:>2} | DNI {r['dni']} | {r['nombre'][:28]:<28} | {r['motivo']}")
    print("-" * 60)
    print(f"Detalle completo en: {OUTPUT_CSV}")
    print(f"Evidencia (captura) : {EVIDENCIA_PNG}")


def _campos(i, fila):
    """Columnas que se guardan en el CSV de resultados."""
    return {
        "fila": i,
        "dni": fila["dni"],
        "nombre": fila["apellidos_nombres"],
        "genero": fila["genero"],
        "area": fila["area"],
        "fecha_ingreso": fila["fecha_ingreso"],
    }


if __name__ == "__main__":
    main()
