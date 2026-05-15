import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent

# Detectar sistema operativo
if sys.platform == "win32":
    CHROMEDRIVER_PATH = BASE_DIR / "chromedriver.exe"
else:
    CHROMEDRIVER_PATH = "chromedriver"  # En Linux se usa el del PATH

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://script.google.com/macros/s/AKfycbyyuJ4rtYMj4dwHYTx6PvT37fNG-QGbkclS1L18Jc3F1GyEc_63fQurtMXwZ-Q2v4enzw/exec")

PENDIENTES_FILE = BASE_DIR / "pendientes_incidentes.json"
ESTADO_FILE = BASE_DIR / "scrape_estado.json"
RESULTADOS_FILE = BASE_DIR / "resultado_incidentes.json"
LOG_FILE = BASE_DIR / "scraper.log"

UTC = ZoneInfo("UTC")
VET = ZoneInfo("America/Caracas")

MESES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}

ASIGNADOS = ["Airam S", "Luis G", "Merlis M", "Greider G"]

DEFAULT_FILTROS = {
    "incluye": [],
    "excluye": ["test", "sandbox"],
    "monedas": [],
    "duracion_minima": 30,
}

PROVEEDORES_HABILITADOS: Optional[set[str]] = {"Monnet", "Alps", "Directa24"}

FILTROS_GLOBALES = DEFAULT_FILTROS.copy()

FILTROS_POR_PROVEEDOR: Dict[str, Dict[str, Any]] = {
    "Monnet": {
        "incluye": ["Peru", "Mexico", "Guatemala","Ecuador", "Honduras"],
        "excluye": ["Brasil","Colombia","Argentina","Mexico", "Chile"],
        "monedas": [],
        "duracion_minima":30,
    },
    "Alps": {
        "incluye": ["Peru", "Mexico", "Guatemala","Ecuador", "Chile", "Honduras"],
        "excluye": ["Brasil","Colombia","Argentina","khipu", "Cobre"],
        "monedas": [],
        "duracion_minima": 30,
    },
    "Directa24": {
        "incluye": ["Peru", "Mexico", "Guatemala","Ecuador", "Chile", "Honduras","API", "Cashin", "Cashout","Maintenance"],
        "excluye": ["Brasil","Colombia","Argentina", "Conversion Rates"],
        "monedas": [],
        "duracion_minima": 30,
    },
}

MAX_INCIDENTES_POR_PROVEEDOR = 500
WAIT_TIMEOUT = 12
PAGE_LOAD_SLEEP = 2
DETAIL_LOAD_SLEEP = 1
HTTP_TIMEOUT = 90
HTTP_RETRIES = 3