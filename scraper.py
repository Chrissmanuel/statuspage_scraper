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
    MAX_INCIDENTES_POR_PROVEEDOR, PAGE_LOAD_SLEEP, PROVEEDORES_HABILITADOS, WAIT_TIMEOUT, RESULTADOS_FILE
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


def clasificar_incidente(datos: Dict[str, Any], config: ProveedorConfig, fecha_corte=None) -> bool:
    if config.tipo == "atlassian":
        periodo = str(datos.get("Periodo_Raw", datos.get("Periodo", "")))
        tiene_fecha_final = " - " in periodo
        if not tiene_fecha_final:
            datos["Pendiente"] = "SI"

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
        #if sys.platform != "win32":
         #   opts.binary_location = "/usr/bin/chromium-browser"
        
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
    
    def procesar_respuesta_api_monnet(self, incidentes_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Transforma los datos crudos de la API de Monnet al formato estándar.
        
        Estructura esperada de API:
        {
            "id": 123,
            "title": "...",
            "description": "...",
            "status": "investigating|identified|monitoring|resolved",
            "start_time": "2026-06-08T12:00:00Z",
            "end_time": null o "2026-06-08T13:00:00Z",
            "components": [{"name": "...", "status": "..."}],
            "impact": "major|minor|critical",
            "duration_in_minutes": 60
        }
        """
        resultados = []
        
        for inc_api in incidentes_raw:
            try:
                inc_id = str(inc_api.get("id", ""))
                titulo = inc_api.get("title", "N/A").strip()
                descripcion = inc_api.get("description", "N/A").strip()
                status = inc_api.get("status", "").lower()
                start_time = inc_api.get("start_time", "")
                end_time = inc_api.get("end_time")
                components = inc_api.get("components", [])
                duracion = inc_api.get("duration_in_minutes", 0)
                
                # Validación básica
                if not titulo or titulo == "N/A":
                    logger.warning("⚠️ Monnet API | Incidente sin título, descartando")
                    continue
                
                # Convertir timestamps a VET
                periodo_vet = self._convertir_timestamps_api_a_vet(start_time, end_time)
                
                # Determinar si está pendiente
                pendiente = "SI" if not end_time else "NO"
                
                # Componentes afectados
                componentes_str = ", ".join([c.get("name", "N/A") for c in components]) if components else "N/A"
                
                # Estado legible
                estado_legible = {
                    "investigating": "Investigando",
                    "identified": "Identificado",
                    "monitoring": "Monitoreando",
                    "resolved": "Resuelto",
                }.get(status, status.upper())
                
                # Crear objeto IncidentData
                datos = IncidentData(
                    Proveedor="Monnet",
                    Titulo=titulo,
                    Periodo=periodo_vet,
                    Resumen=descripcion,
                    Estado=estado_legible,
                    Componentes=componentes_str,
                    Duracion_Minutos=int(duracion) if duracion else 0,
                    Pendiente=pendiente,
                    ID=inc_id,
                    Asignado="",
                )
                
                row = datos.to_dict()
                row['Periodo_Raw'] = f"{start_time} - {end_time}" if end_time else start_time
                
                # Aplicar filtros
                if clasificar_incidente(row, ProveedorConfig("Monnet", "", "", None, "api")):
                    resultados.append(row)
                    estado_log = "⚠️ PENDIENTE" if pendiente == "SI" else "✅"
                    logger.info(f"{estado_log} Monnet | {titulo[:60]}... (ID: {inc_id}) | {duracion}min")
                
            except Exception as e:
                logger.warning(f"❌ Error procesando incidente de Monnet API: {e}")
                continue
        
        return resultados
    
    def _convertir_timestamps_api_a_vet(self, start_iso: str, end_iso: Optional[str] = None) -> str:
        """
        Convierte timestamps ISO 8601 a formato VET legible.
        Ejemplo: "2026-06-08T12:00:00Z" → "Jun 08, 08:00 AM - 09:00 AM -04"
        """
        try:
            from datetime import datetime, timedelta
            
            if not start_iso:
                return "N/A"
            
            # Parsear fecha de inicio
            start = datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
            # Convertir a VET (UTC-4)
            start_vet = start + timedelta(hours=-4)
            
            fecha_str = start_vet.strftime("%b %d, %I:%M %p").lower()
            
            if end_iso:
                end = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
                end_vet = end + timedelta(hours=-4)
                hora_fin = end_vet.strftime("%I:%M %p").lower()
                return f"{fecha_str} - {hora_fin} -04"
            else:
                return f"{fecha_str} -04"
        except Exception as e:
            logger.warning(f"⚠️ Error convirtiendo timestamp: {e}")
            return start_iso

    def verificar_pendientes_api_monnet(self, pendientes: List[Dict[str, Any]], 
                                        incidentes_actuales_api: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Verifica estado de pendientes consultando directamente la API.
        """
        resultados = []
        
        # Crear mapa de IDs actuales
        mapa_actuales = {str(inc.get("id")): inc for inc in incidentes_actuales_api}
        
        for inc in pendientes:
            try:
                inc_id = inc.get("ID", "")
                
                if inc_id in mapa_actuales:
                    # Todavía existe en la API, actualizar datos
                    inc_actual = mapa_actuales[inc_id]
                    end_time = inc_actual.get("end_time")
                    
                    if end_time:
                        # Tiene fecha de finalización -> RESUELTO
                        inc["Pendiente"] = "NO"
                        inc["Estado"] = "Resolved"
                        inc["Periodo"] = self._convertir_timestamps_api_a_vet(
                            inc_actual.get("start_time"), 
                            end_time
                        )
                    else:
                        # Sigue sin fecha final -> PENDIENTE
                        inc["Pendiente"] = "SI"
                        inc["Estado"] = inc_actual.get("status", "investigating").capitalize()
                        inc["Duracion_Minutos"] = inc_actual.get("duration_in_minutes", 0)
                else:
                    # No aparece en la API -> Probablemente RESUELTO y archivado
                    inc["Pendiente"] = "NO"
                    inc["Estado"] = "Resolved (archived)"
                
                resultados.append(inc)
                
            except Exception as e:
                logger.warning(f"⚠️ Error verificando pendiente {inc.get('Titulo')} de Monnet: {e}")
                resultados.append(inc)
        
        return resultados

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
    def generar_id_unico(titulo: str, fecha_inicio: str) -> str:
        """
        Generar un ID único e inmutable para Monnet usando titulo + fecha_inicio.
        El hash es estable siempre que el título y fecha sean idénticos.
        """
        # Forzamos limpieza exhaustiva para asegurar que espacios fantasmas no rompan el hash
        titulo_limpio = " ".join(titulo.strip().split())  # Normaliza espacios múltiples
        fecha_limpia = " ".join(fecha_inicio.strip().split())  # Normaliza espacios múltiples
        
        base = f"{titulo_limpio.lower()}_{fecha_limpia.lower()}"
        return hashlib.md5(base.encode("utf-8")).hexdigest()

    @staticmethod
    def extraer_fecha_inicio(periodo_raw: str) -> str:
        if not periodo_raw:
            return ""
        
        # Detectar y convertir zona horaria a UTC si es necesario
        es_utc = "UTC" in periodo_raw.upper() or "GMT" in periodo_raw.upper()
        
        # Limpiar y extraer inicio eliminando los sufijos de zona horaria como -04 o -04:00
        limpio = re.sub(r'\s*[-+]\d{2}:?\d{2}\s*$', '', periodo_raw)
        limpio = re.sub(r'\s*(GMT|UTC)[-+]\d{2}:?\d{2}\s*$', '', limpio, flags=re.IGNORECASE)
        limpio = re.sub(r'\s*UTC\s*$', '', limpio, flags=re.IGNORECASE)
        
        if " - " in limpio:
            inicio = limpio.split(" - ")[0].strip()
        else:
            inicio = limpio.strip()
        
        if es_utc:
            match = re.match(r'([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{1,2}):(\d{2})\s+(am|pm)', inicio, re.IGNORECASE)
            if match:
                mes_str, dia, hora, minuto, ampm = match.groups()
                hora = int(hora)
                if ampm.lower() == "pm" and hora < 12:
                    hora += 12
                if ampm.lower() == "am" and hora == 12:
                    hora = 0
                
                hora = (hora - 4) % 24
                nuevo_ampm = "am" if hora < 12 else "pm"
                hora_12 = hora % 12
                if hora_12 == 0:
                    hora_12 = 12
                inicio = f"{mes_str} {dia}, {hora_12}:{minuto} {nuevo_ampm}"
        
        # Normalización final para garantizar paridad de IDs
        inicio = inicio.lower()
        inicio = inicio.replace('.', '')
        inicio = re.sub(r'\b0(\d):', r'\1:', inicio)
        inicio = re.sub(r'(\d+),', r'\1', inicio)
        inicio = re.sub(r'\s+', ' ', inicio).strip()
        
        return inicio

    def ejecutar(self, config: ProveedorConfig, fecha_corte=None) -> List[Dict[str, Any]]:
        if PROVEEDORES_HABILITADOS is not None and config.nombre not in PROVEEDORES_HABILITADOS:
            return []

        logger.info(f"🔍 Iniciando proveedor: {config.nombre}")
        
        # ===== MONNET: Ahora maneja por API en main.py =====
        if config.tipo == "api":
            return []
        
        resultados: List[Dict[str, Any]] = []
        
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
            return resultados

        # ✅ Fecha de corte: inicio del mes actual
        from datetime import datetime
        inicio_mes = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        for i, el in enumerate(elementos[:MAX_INCIDENTES_POR_PROVEEDOR]):
            try:
                periodo_raw = self._safe_text(el, config.selectores.periodo)
                periodo = ParseadorTiempo.convertir_periodo_a_vet(periodo_raw)

                titulo = self._safe_text(el, config.selectores.titulo)
                # ✅ Skip si no hay título en Atlassian
                if not titulo or titulo == "N/A":
                    continue
                fecha_inicio = periodo_raw.split("-")[0].strip()

                # ✅ Verificar si el ID ya existe en el histórico
                id_temp = self._obtener_id_temporal(el, config, titulo, periodo)

                if id_temp and _existe_id_en_historico(id_temp, config.nombre):
                    logger.info(f"⏪ {config.nombre} | Ya en histórico: {titulo[:60]}")
                    break

                # ✅ Verificar que sea del mes actual o posterior (usando fecha fin)
                if config.tipo == "atlassian" and " - " in periodo_raw:
                    partes = periodo_raw.split(" - ")
                    fecha_fin_str = partes[1].strip()
                    fecha_fin_str = re.sub(r'\s*(GMT|UTC)[-+]\d{2}:?\d{2}.*$', '', fecha_fin_str, flags=re.IGNORECASE).strip()
                    fecha_verificar = ParseadorTiempo.extraer_fecha(fecha_fin_str)
                    if not fecha_verificar:
                        fecha_verificar = ParseadorTiempo.extraer_fecha(partes[0].strip())
                else:
                    fecha_verificar = ParseadorTiempo.extraer_fecha(periodo_raw)

                if fecha_verificar and fecha_verificar < inicio_mes:
                    logger.info(f"⏪ {config.nombre} | Anterior a este mes: {titulo[:60]}")
                    break

                datos = IncidentData(
                    Proveedor=config.nombre,
                    Titulo=titulo,
                    Periodo=periodo,
                    Resumen=self._safe_text(el, config.selectores.resumen),
                    Estado=self._safe_text(el, config.selectores.estado) if config.selectores.estado else "N/A",
                )

                dur_txt = self._safe_text(el, config.selectores.duracion_raw) if config.tipo == "freshstatus" else datos.Periodo
                datos.Duracion_Minutos = ParseadorTiempo.calcular_duracion(dur_txt)

                # ✅ Asignación del ID final persistente
                link = None
                if config.tipo == "atlassian":
                    try:
                        link = el.find_element(By.CSS_SELECTOR, config.selectores.titulo).get_attribute("href")
                        datos.ID = self.extraer_id_incidente(link)
                    except Exception:
                        datos.ID = id_temp

                # ✅ SOLO ABRIR DETALLES si es atlassian
                if config.tipo == "atlassian" and link:
                    texto_preliminar = f"{titulo} {datos.Resumen}".lower()
                    filtros = obtener_filtros_proveedor(config.nombre)
                    if filtros["excluye"] and any(normalizar_texto(p).lower() in texto_preliminar for p in filtros["excluye"]):
                        logger.info(f"⏭️ {config.nombre} | Filtrado (sin abrir): {titulo[:60]}")
                        continue
                    try:
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

                row = datos.to_dict()
                row['Periodo_Raw'] = periodo_raw

                if not clasificar_incidente(row, config):
                    continue

                resultados.append(row)
                estado = "⚠️ PENDIENTE" if str(row.get("Pendiente", "NO")) == "SI" else "✅"
                logger.info(f"{estado} {config.nombre} | {row['Titulo'][:60]}... ({row['Duracion_Minutos']} min)")

            except (StaleElementReferenceException, NoSuchElementException):
                continue
            except Exception:
                logger.warning(f"❌ Error procesando incidente {i + 1} de {config.nombre}", exc_info=True)
                continue

        resultados.reverse()
        return resultados

    def _obtener_id_temporal(self, el, config, titulo: str, periodo: str) -> str:
        """Obtiene el ID sin abrir la página de detalles"""
        if config.tipo == "atlassian":
            try:
                link = el.find_element(By.CSS_SELECTOR, config.selectores.titulo).get_attribute("href")
                return self.extraer_id_incidente(link)
            except Exception:
                return self.generar_id_unico(titulo, periodo)
        return ""

    def verificar_pendientes(self, pendientes: List[Dict[str, Any]], config: ProveedorConfig) -> List[Dict[str, Any]]:
        resultados = []

        for inc in pendientes:
            try:
                if config.tipo == "atlassian":
                    id_val = inc.get("ID", "")
                    if id_val.startswith("/"):
                        id_val = id_val.lstrip("/")
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
                            inc["Pendiente"] = "SI" if not tiene_fecha_fin else "NO"
                    except Exception:
                        inc["Pendiente"] = "SI"

                resultados.append(inc)

            except Exception as e:
                logger.warning(f"⚠️ Error verificando pendiente {inc.get('Titulo')} de {config.nombre}: {e}")
                resultados.append(inc)

        return resultados


def _existe_id_en_historico(id_incidente: str, proveedor: str) -> bool:
    """Verifica si un ID ya existe en el histórico"""
    from utils import cargar_json
    
    historico = cargar_json(RESULTADOS_FILE, [])
    return any(
        h.get("ID") == id_incidente and h.get("Proveedor") == proveedor 
        for h in historico
    )
