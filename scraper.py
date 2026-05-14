import re
import time
import sys
import hashlib
from contextlib import AbstractContextManager
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


from config import (
    CHROMEDRIVER_PATH, DETAIL_LOAD_SLEEP, FILTROS_GLOBALES, FILTROS_POR_PROVEEDOR,
    MAX_INCIDENTES_POR_PROVEEDOR, PAGE_LOAD_SLEEP, PROVEEDORES_HABILITADOS, WAIT_TIMEOUT
)
from models import IncidentData, ProveedorConfig
from time_parser import ParseadorTiempo
from utils import normalizar_texto, logger


def obtener_filtros_proveedor(nombre: str) -> Dict[str, Any]:
    filtros = FILTROS_POR_PROVEEDOR.get(nombre, {})
    return {
        "incluye": filtros.get("incluye", FILTROS_GLOBALES["incluye"]),
        "excluye": filtros.get("excluye", FILTROS_GLOBALES["excluye"]),
        "monedas": filtros.get("monedas", FILTROS_GLOBALES["monedas"]),
        "duracion_minima": filtros.get("duracion_minima", FILTROS_GLOBALES["duracion_minima"]),
    }


def clasificar_incidente(datos: Dict[str, Any], config: ProveedorConfig, fecha_corte) -> bool:
    fecha_inc = None

    if config.tipo == "atlassian":
        periodo = str(datos.get("Periodo_Raw", datos.get("Periodo", "")))
        if " - " in periodo:
            partes = [p.strip() for p in periodo.split(" - ")]
            if len(partes) >= 2:
                fecha_fin_str = partes[1]
                fecha_fin = ParseadorTiempo.extraer_fecha(fecha_fin_str)
                if fecha_fin:
                    fecha_inc = fecha_fin
        else:
            fecha_inc = ParseadorTiempo.extraer_fecha(periodo)

        if fecha_inc and fecha_inc < fecha_corte:
            logger.info(f"⏪ {config.nombre} | Anterior al corte (fecha fin): {datos.get('Titulo', '')[:60]}")
            return False
    else:
        fecha_inc = ParseadorTiempo.extraer_fecha(datos.get("Periodo", ""))
        if fecha_inc and fecha_inc < fecha_corte:
            logger.info(f"⏪ {config.nombre} | Anterior al corte: {datos.get('Titulo', '')[:60]}")
            return False

    # filtros de palabras y duración (igual que ahora)
    filtros = obtener_filtros_proveedor(config.nombre)
    texto_completo = " ".join(str(v) for v in datos.values() if v).lower()

    if filtros["incluye"] and not any(normalizar_texto(p).lower() in texto_completo for p in filtros["incluye"]):
        return False
    if filtros["excluye"] and any(normalizar_texto(p).lower() in texto_completo for p in filtros["excluye"]):
        logger.info(f"⏭️ {config.nombre} | Filtrado: {datos.get('Titulo', '')[:60]}")
        return False
    if filtros["monedas"] and not any(normalizar_texto(m).lower() in texto_completo for m in filtros["monedas"]):
        return False

    try:
        duracion = int(datos.get("Duracion_Minutos", 0))
    except (ValueError, TypeError):
        duracion = 0
    if duracion < int(filtros["duracion_minima"]):
        logger.info(f"⏭️ {config.nombre} | Duración {duracion}min < {filtros['duracion_minima']}min: {datos.get('Titulo', '')[:60]}")
        return False

    return True


class IncidentScraper(AbstractContextManager):
    def __init__(self) -> None:
        self.driver = self._crear_driver()
        self.wait = WebDriverWait(self.driver, WAIT_TIMEOUT)
        self.main_window: Optional[str] = None

    def _crear_driver(self) -> WebDriver:
        opts = Options()
        
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        
        # En Linux, usar Chromium en lugar de Chrome
        if sys.platform != "win32":
            opts.binary_location = "/usr/bin/chromium-browser"
        
        if sys.platform == "win32":
            service = Service(str(CHROMEDRIVER_PATH))
        else:
            service = Service()
        
        return webdriver.Chrome(service=service, options=opts)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def _safe_text(self, el, selector: str, default: str = "N/A") -> str:
        if not selector:
            return default
        try:
            # si 'el' es WebDriver (usado para páginas internas), soportar ese caso
            if hasattr(el, "find_element"):
                return normalizar_texto(el.find_element(By.CSS_SELECTOR, selector).text)
            else:
                return default
        except Exception:
            return default

    def _safe_click(self, element) -> None:
        try:
            self.driver.execute_script("arguments[0].click();", element)
        except Exception:
            try:
                element.click()
            except Exception:
                pass

    @staticmethod
    def extraer_id_incidente(href: str) -> str:
        """
        Recibe el href completo y devuelve solo el ID del incidente.
        Ejemplo:
        - https://monnetpayments.freshstatus.io/incident/1607365 -> incident/1607365
        - https://status.alps.cl/incidents/jybtdf112n0v -> incidents/jybtdf112n0v
        """
        if not href:
            return ""
        path = urlparse(href).path.strip("/")
        return path

    @staticmethod
    def generar_id_unico(titulo: str, periodo: str) -> str:
        """
        Genera un ID único basado en título + periodo.
        """
        base = f"{titulo}-{periodo}"
        return "monnet-" + hashlib.md5(base.encode()).hexdigest()

    def ejecutar(self, config: ProveedorConfig, fecha_corte) -> List[Dict[str, Any]]:
        if PROVEEDORES_HABILITADOS is not None and config.nombre not in PROVEEDORES_HABILITADOS:
            return []

        logger.info(f"🔍 Iniciando proveedor: {config.nombre}")
        self.driver.get(config.url)
        self.main_window = self.driver.current_window_handle
        time.sleep(PAGE_LOAD_SLEEP)

        if config.tipo == "atlassian":
            for b in self.driver.find_elements(By.CSS_SELECTOR, "div.expand-incidents"):
                self._safe_click(b)
            time.sleep(1)

        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, config.container)))
            elementos = self.driver.find_elements(By.CSS_SELECTOR, config.container)
        except (TimeoutException, WebDriverException):
            logger.warning(f"⚠️ No se encontraron incidentes para {config.nombre}")
            return []

        resultados: List[Dict[str, Any]] = []

        for i, el in enumerate(elementos[:MAX_INCIDENTES_POR_PROVEEDOR]):
            try:
                periodo_raw = self._safe_text(el, config.selectores.periodo)
                periodo = ParseadorTiempo.convertir_periodo_a_vet(periodo_raw)
                titulo = self._safe_text(el, config.selectores.titulo)

                # --- Calcular fecha de corte usando fecha_fin cuando es Atlassian ---
                fecha_inc = None
                if config.tipo == "atlassian":
                    if " - " in periodo_raw:
                        partes = [p.strip() for p in periodo_raw.split(" - ")]
                        if len(partes) >= 2:
                            fecha_fin_str = partes[1]
                            fecha_fin = ParseadorTiempo.extraer_fecha(fecha_fin_str)
                            if fecha_fin:
                                fecha_inc = fecha_fin
                    else:
                        fecha_inc = ParseadorTiempo.extraer_fecha(periodo_raw)
                else:
                    fecha_inc = ParseadorTiempo.extraer_fecha(periodo)

                if fecha_inc and fecha_inc < fecha_corte:
                    logger.info(f"⏪ {config.nombre} | Fecha fin anterior al corte: {titulo[:60]}")
                    if config.tipo == "atlassian":
                        # en Atlassian rompemos el bucle porque el resto será más antiguo
                        break
                    else:
                        break  # en otros proveedores sólo omitimos este incidente

                datos = IncidentData(
                    Proveedor=config.nombre,
                    Titulo=titulo,
                    Periodo=periodo,
                    Resumen=self._safe_text(el, config.selectores.resumen),
                    Estado=self._safe_text(el, config.selectores.estado) if config.selectores.estado else "N/A",
                )

                dur_txt = self._safe_text(el, config.selectores.duracion_raw) if config.tipo == "freshstatus" else datos.Periodo
                datos.Duracion_Minutos = ParseadorTiempo.calcular_duracion(dur_txt)

                # Nuevo campo ID
                if config.tipo == "atlassian":
                    try:
                        link = el.find_element(By.CSS_SELECTOR, config.selectores.titulo).get_attribute("href")
                        datos.ID = self.extraer_id_incidente(link)
                    except Exception:
                        link = None
                        datos.ID = self.generar_id_unico(titulo, periodo)
                elif config.tipo == "freshstatus":
                    link = None
                    datos.ID = self.generar_id_unico(titulo, periodo)

                # Abrir detalles solo si es atlassian, tiene link y pasa el filtro de fecha en clasificar_incidente
                if config.tipo == "atlassian" and link:
                    try:
                        # abrir en nueva pestaña y extraer updates
                        self.driver.execute_script("window.open(arguments[0]);", link)
                        self.driver.switch_to.window(self.driver.window_handles[-1])
                        time.sleep(DETAIL_LOAD_SLEEP)

                        try:
                            updates = self.driver.find_elements(By.CSS_SELECTOR, "div.update-row")
                            if updates:
                                latest = updates[0]
                                estado = normalizar_texto(latest.find_element(By.CSS_SELECTOR, "h2.update-title").text)
                                cuerpo = normalizar_texto(latest.find_element(By.CSS_SELECTOR, "div.update-body").text)
                                datos.Estado = f"{estado}: {cuerpo}"
                                if any(w in estado.lower() for w in ("resolved", "completed")):
                                    datos.Pendiente = "NO"
                                else:
                                    datos.Pendiente = "SI"
                            else:
                                datos.Estado = datos.Estado or "N/A"
                        except Exception:
                            datos.Estado = datos.Estado or "N/A"

                        # Historial
                        try:
                            historial = "\n".join([
                                f"{normalizar_texto(u.find_element(By.CSS_SELECTOR, 'h2.update-title').text)}: "
                                f"{normalizar_texto(u.find_element(By.CSS_SELECTOR, 'div.update-body').text)}"
                                for u in updates
                            ])
                            if historial:
                                datos.Resumen = f"{datos.Resumen}\n\nHistorial:\n{historial}"
                        except Exception:
                            pass

                        # Componentes
                        try:
                            datos.Componentes = normalizar_texto(
                                self.driver.find_element(By.CSS_SELECTOR, "div.components-affected").text
                            )
                        except Exception:
                            datos.Componentes = "N/A"

                        self.driver.close()
                        self.driver.switch_to.window(self.main_window)

                    except Exception:
                        logger.warning(f"⚠️ No se pudo extraer detalle de {config.nombre}")

                # Extraer componentes en freshstatus
                if config.tipo == "freshstatus":
                    try:
                        componentes = el.find_element(By.CSS_SELECTOR, "div.components-affected").text
                        datos.Componentes = normalizar_texto(
                            componentes.replace("Affected services", "").replace("This incident affected:", "").strip()
                        ) or "N/A"
                    except Exception:
                        pass

                row = datos.to_dict()
                row["Periodo_Raw"] = periodo_raw

                if not clasificar_incidente(row, config, fecha_corte):
                    continue

                resultados.append(row)
                estado = "⚠️ PENDIENTE" if str(row.get("Pendiente", "NO")) == "SI" else "✅"
                logger.info(f"{estado} {config.nombre} | {row['Titulo'][:60]}... ({row['Duracion_Minutos']} min)")

            except (StaleElementReferenceException, NoSuchElementException):
                continue
            except Exception:
                logger.warning(f"❌ Error procesando incidente {i + 1} de {config.nombre}", exc_info=True)
                continue

        return resultados

    def verificar_pendientes(self, pendientes: List[Dict[str, Any]], config: ProveedorConfig) -> List[Dict[str, Any]]:
        resultados = []

        for inc in pendientes:
            try:
                if config.tipo == "atlassian":
                    # construir URL robusta a partir del ID; ID puede venir como "incidents/xxxx", "incident/xxxx", o solo "xxxx"
                    id_val = inc.get("ID", "")
                    if id_val.startswith("/"):
                        id_val = id_val.lstrip("/")
                    # si el ID ya es una URL absoluta
                    if id_val.startswith("http"):
                        url = id_val
                    else:
                        if not id_val:
                            url = config.url
                        else:
                            parsed = urlparse(config.url)
                            base = f"{parsed.scheme}://{parsed.netloc}"
                            if id_val.startswith("incidents") or id_val.startswith("incident"):
                                url = f"{base}/{id_val}"
                            else:
                                url = f"{base}/incidents/{id_val}"

                    self.driver.get(url)
                    time.sleep(DETAIL_LOAD_SLEEP)
                    try:
                        # intentar extraer periodo interno (si existe) y updates
                        periodo = ""
                        try:
                            periodo = self.driver.find_element(By.CSS_SELECTOR, "div.incident-data div.secondary").text
                        except Exception:
                            periodo = ""

                        tiene_fecha_fin = "-" in (periodo or "")

                        try:
                            latest_title = self.driver.find_element(By.CSS_SELECTOR, "div.update-row h2.update-title").text
                            latest_body = self.driver.find_element(By.CSS_SELECTOR, "div.update-row div.update-body").text
                            inc["Estado"] = f"{normalizar_texto(latest_title)}: {normalizar_texto(latest_body)}"
                            if any(w in latest_title.lower() for w in ("resolved", "completed")):
                                inc["Pendiente"] = "NO"
                            else:
                                inc["Pendiente"] = "SI" if not tiene_fecha_fin else inc.get("Pendiente", "SI")
                        except Exception:
                            # si no encuentra updates, fallback al periodo externo
                            inc["Pendiente"] = "SI" if not tiene_fecha_fin else "NO"
                    except Exception:
                        inc["Pendiente"] = "SI"

                elif config.tipo == "freshstatus":
                    actuales = self.ejecutar(config, ParseadorTiempo.hoy_inicio())
                    match = next((a for a in actuales if a["ID"] == inc["ID"]), None)
                    if match:
                        periodo = match["Periodo"]
                        tiene_fecha_fin = "-" in periodo
                        if not tiene_fecha_fin:
                            inc["Pendiente"] = "SI"
                        else:
                            inc["Estado"] = match["Estado"]
                            inc["Pendiente"] = "NO" if "Resolved" in match["Estado"] or "Completed" in match["Estado"] else "SI"
                    else:
                        inc["Pendiente"] = "NO"
                        inc["Estado"] = "Resolved"

                resultados.append(inc)

            except Exception as e:
                logger.warning(f"⚠️ Error verificando pendiente {inc.get('Titulo')} de {config.nombre}: {e}")
                resultados.append(inc)

        return resultados