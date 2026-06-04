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
    
    def _extraer_id_freshstatus_real(self, elemento) -> Optional[str]:
        """Intenta obtener el ID numérico real del incidente desde la URL en página activa."""
        try:
            enlace = elemento.find_element(By.CSS_SELECTOR, "a.incident-title, a.PO")
            href = enlace.get_attribute("href")
            match = re.search(r'/incident/(\d+)', href)
            if match:
                return f"fs_{match.group(1)}"
        except:
            pass
        return None
    
    def _scrapear_activos_freshstatus(self, config: ProveedorConfig) -> List[Dict[str, Any]]:
        """
        Scrapea la página activa (raíz) de un proveedor freshstatus para obtener
        incidentes en curso (pendientes).
        """
        if not config.active_url:
            return []
        
        logger.info(f"🔍 {config.nombre} | Scrapeando activos (pendientes) desde {config.active_url}")
        self.driver.get(config.active_url)
        time.sleep(PAGE_LOAD_SLEEP)
        
        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, config.container)))
            elementos = self.driver.find_elements(By.CSS_SELECTOR, f"{config.container}.liveincident")
        except (TimeoutException, WebDriverException):
            logger.warning(f"⚠️ {config.nombre} | No se encontraron incidentes activos")
            return []
        
        resultados = []
        for el in elementos:
            try:
                # 🔥 RESTRUCTURACIÓN DE BÚSQUEDA DEL TÍTULO:
                titulo = "N/A"
                try:
                    # Buscamos primero la clase nativa y exacta de Freshstatus para el título aislado
                    elemento_titulo_limpio = el.find_element(By.CSS_SELECTOR, "div.incidentTitle")
                    titulo = elemento_titulo_limpio.text.strip()
                except:
                    # Fallback si cambia la estructura de la página
                    titulo_sucio = self._safe_text(el, config.selectores.titulo)
                    if titulo_sucio and titulo_sucio != "N/A":
                        titulo = titulo_sucio.split("\n")[0].strip()

                if not titulo or titulo == "N/A" or titulo == "":
                    continue

                # Extraer período (raw)
                periodo_raw = self._safe_text(el, config.selectores.periodo)
                if not periodo_raw or periodo_raw == "N/A":
                    try:
                        started = el.find_element(By.CSS_SELECTOR, "span.style__LabelInfo-sc-19bjpya-14 .title + span")
                        periodo_raw = started.text
                    except:
                        periodo_raw = ""
                
                # Extraer fecha de inicio limpia
                fecha_inicio = self.extraer_fecha_inicio(periodo_raw)
                if not fecha_inicio:
                    fecha_inicio = periodo_raw  # fallback
                
                # Convertir período a VET para mostrar bonito en la celda
                periodo_vet = ParseadorTiempo.convertir_periodo_a_vet(periodo_raw)
                
                # Intentar capturar el resumen de forma limpia mediante su contenedor de estilo
                resumen = "N/A"
                try:
                    elemento_resumen = el.find_element(By.CSS_SELECTOR, "div[class*='DescriptionContainer']")
                    resumen = elemento_resumen.text.strip()
                except:
                    resumen = self._safe_text(el, config.selectores.resumen)

                estado = self._safe_text(el, config.selectores.estado) if config.selectores.estado else "Active"
                
                # Crear IncidentData
                datos = IncidentData(
                    Proveedor=config.nombre,
                    Titulo=titulo,
                    Periodo=periodo_vet,
                    Resumen=resumen,
                    Estado=estado,
                    Pendiente="SI",
                )
                
                # Extraer duración real desde el span.duration de la página
                dur_txt = "N/A"
                try:
                    elemento_duracion = el.find_element(By.CSS_SELECTOR, "span.duration")
                    dur_txt = elemento_duracion.get_attribute("title") or elemento_duracion.text
                except:
                    dur_txt = self._safe_text(el, config.selectores.duracion_raw)

                if dur_txt and dur_txt != "N/A" and dur_txt.strip() != "":
                    datos.Duracion_Minutos = ParseadorTiempo.calcular_duracion(dur_txt)
                else:
                    datos.Duracion_Minutos = 0
                
                # Generar el ID mediante el Hash basado en el nuevo título extraído quirúrgicamente
                datos.ID = self.generar_id_unico(titulo, fecha_inicio)
                
                try:
                    componentes = el.find_element(By.CSS_SELECTOR, "div.components-affected").text
                    datos.Componentes = normalizar_texto(
                        componentes.replace("Affected services", "").replace("This incident affected:", "").strip()
                    ) or "N/A"
                except:
                    datos.Componentes = "N/A"
                
                row = datos.to_dict()
                row['Periodo_Raw'] = periodo_raw 
                
                if clasificar_incidente(row, config):
                    resultados.append(row)
                    logger.info(f"⚠️ PENDIENTE (activo) {config.nombre} | {row['Titulo'][:60]}... | ID: {datos.ID} ({datos.Duracion_Minutos} min)")
            except Exception as e:
                logger.warning(f"❌ Error procesando incidente activo: {e}")
                continue
        
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
        Genera un ID único basado en título + fecha de inicio.
        """
        base = f"{titulo}-{fecha_inicio}"
        return hashlib.md5(base.encode()).hexdigest()

    @staticmethod
    def extraer_fecha_inicio(periodo_raw: str) -> str:
        if not periodo_raw:
            return ""
        
        # Detectar y convertir zona horaria a UTC
        es_utc = "UTC" in periodo_raw.upper() or "GMT" in periodo_raw.upper()
        
        # Limpiar y extraer inicio
        limpio = re.sub(r'\s*[-+]\d{2}:?\d{2}\s*$', '', periodo_raw)
        limpio = re.sub(r'\s*(GMT|UTC)[-+]\d{2}:?\d{2}\s*$', '', limpio, flags=re.IGNORECASE)
        limpio = re.sub(r'\s*UTC\s*$', '', limpio, flags=re.IGNORECASE)
        
        if " - " in limpio:
            inicio = limpio.split(" - ")[0].strip()
        else:
            inicio = limpio.strip()
        
        # Si es UTC, convertir la hora restando 4 horas (UTC-4 = VET)
        if es_utc:
            # Extraer hora y convertir
            match = re.match(r'([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{1,2}):(\d{2})\s+(am|pm)', inicio, re.IGNORECASE)
            if match:
                mes_str, dia, hora, minuto, ampm = match.groups()
                hora = int(hora)
                if ampm.lower() == "pm" and hora < 12:
                    hora += 12
                if ampm.lower() == "am" and hora == 12:
                    hora = 0
                # Restar 4 horas (UTC-4 a VET es lo mismo, pero si es UTC, ajustamos)
                hora = (hora - 4) % 24
                # Convertir de vuelta a 12h
                nuevo_ampm = "am" if hora < 12 else "pm"
                hora_12 = hora % 12
                if hora_12 == 0:
                    hora_12 = 12
                inicio = f"{mes_str} {dia}, {hora_12}:{minuto} {nuevo_ampm}"
        
        # Normalización final
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
        
        resultados: List[Dict[str, Any]] = []
        
        # --- Scrapear incidentes activos (pendientes) si existe active_url ---
        if config.tipo == "freshstatus" and config.active_url:
            activos = self._scrapear_activos_freshstatus(config)
            resultados.extend(activos)

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

                # =========================================================================
                # 🟢 AQUÍ VA EL CAMBIO EXACTO: LIMPIEZA ABSOLUTA DE FECHA DE INICIO CON REGEX
                # =========================================================================
                # Buscamos el formato "Mes Día, Hora:Minuto AM/PM" (ej: Jun 04, 08:20 AM)
                match_fecha_inicio = re.search(r"([a-zA-Z]{3}\s+\d{1,2},\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))", periodo_raw)
                
                if match_fecha_inicio:
                    # Forzamos a usar estrictamente esta base limpia (ej: "Jun 04, 08:20 AM")
                    fecha_inicio = match_fecha_inicio.group(1).strip()
                else:
                    # Fallback por si la regex no encuentra el patrón común
                    fecha_inicio = periodo_raw.split("-")[0].strip()
                # =========================================================================

                # ✅ Verificar si el ID ya existe en el histórico (usamos el ID que se generará)
                if config.tipo == "freshstatus":
                    id_temp = self.generar_id_unico(titulo, fecha_inicio)
                    
                else:  # atlassian
                    id_temp = self._obtener_id_temporal(el, config, titulo, periodo)
                if id_temp and _existe_id_en_historico(id_temp, config.nombre):
                    logger.info(f"⏪ {config.nombre} | Ya en histórico: {titulo[:60]}")
                    break

                # ✅ Verificar que sea del mes actual o posterior (usando fecha fin)
                if config.tipo == "atlassian" and " - " in periodo_raw:
                    partes = periodo_raw.split(" - ")
                    fecha_fin_str = partes[1].strip()
                    fecha_fin_str = re.sub(r'\s*(GMT|UTC)[-+]\d{2}:?\\d{2}.*$', '', fecha_fin_str, flags=re.IGNORECASE).strip()
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

                # ✅ Asignación del ID usando fecha_inicio para freshstatus
                link = None
                if config.tipo == "atlassian":
                    try:
                        link = el.find_element(By.CSS_SELECTOR, config.selectores.titulo).get_attribute("href")
                        datos.ID = self.extraer_id_incidente(link)
                    except Exception:
                        datos.ID = self.generar_id_unico(titulo, fecha_inicio)
                elif config.tipo == "freshstatus":
                    datos.ID = self.generar_id_unico(titulo, fecha_inicio)

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
        elif config.tipo == "freshstatus":
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

                elif config.tipo == "freshstatus":
                    actuales = self.ejecutar(config, None)  # None porque no usamos fecha_corte
                    match = next((a for a in actuales if a["ID"] == inc["ID"]), None)
                    if match:
                        periodo = match["Periodo"]
                        # Detectamos si de verdad tiene una fecha de cierre (ej. Jun 04, 08:20 AM - 01:15 PM -04)
                        tiene_fecha_fin = "-" in periodo and len(periodo.split("-")) >= 2
                        
                        if not tiene_fecha_fin:
                            inc["Pendiente"] = "SI"
                            inc["Estado"] = match.get("Estado", "N/A")
                        else:
                            # 🟢 SI TIENE FECHA FIN -> ESTÁ RESUELTO, pase lo que pase con el texto del estado
                            inc["Estado"] = match.get("Estado") if match.get("Estado") != "N/A" else "Resolved"
                            inc["Periodo"] = match["Periodo"] 
                            inc["Pendiente"] = "NO"  # Rompemos el bucle mandándolo a History
                    else:
                        # Si ya ni siquiera aparece en la web principal, asumimos que se cerró y archivó
                        inc["Pendiente"] = "NO"
                        inc["Estado"] = "Resolved"

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
