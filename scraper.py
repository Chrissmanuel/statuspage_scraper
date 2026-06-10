import re
import time
import sys
import hashlib
from contextlib import AbstractContextManager
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from datetime import datetime, timedelta  # ← AÑADIR timedelta
from zoneinfo import ZoneInfo

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
    MAX_INCIDENTES_POR_PROVEEDOR, PAGE_LOAD_SLEEP, PROVEEDORES_HABILITADOS, 
    WAIT_TIMEOUT, RESULTADOS_FILE
)
from models import IncidentData, ProveedorConfig
from time_parser import ParseadorTiempo
from utils import normalizar_texto, logger

# Intentar importar MonnetAPI, si falla mostrar warning
try:
    from monnet_api import MonnetAPI
    MONNET_API_DISPONIBLE = True
except ImportError:
    MONNET_API_DISPONIBLE = False
    logger.warning("⚠️ MonnetAPI no disponible, se usará método legacy para Monnet")


def obtener_filtros_proveedor(nombre: str) -> Dict[str, Any]:
    """Obtiene los filtros configurados para un proveedor específico"""
    filtros = FILTROS_POR_PROVEEDOR.get(nombre, {})
    return {
        "incluye": filtros.get("incluye", FILTROS_GLOBALES["incluye"]),
        "excluye": filtros.get("excluye", FILTROS_GLOBALES["excluye"]),
        "monedas": filtros.get("monedas", FILTROS_GLOBALES["monedas"]),
        "duracion_minima": filtros.get("duracion_minima", FILTROS_GLOBALES["duracion_minima"]),
    }


def clasificar_incidente(datos: Dict[str, Any], config: ProveedorConfig, fecha_corte=None) -> bool:
    """Clasifica un incidente según los filtros configurados"""
    # Para Atlassian, detectar pendientes por falta de fecha final
    if config.tipo == "atlassian":
        periodo = str(datos.get("Periodo_Raw", datos.get("Periodo", "")))
        tiene_fecha_final = " - " in periodo
        if not tiene_fecha_final:
            datos["Pendiente"] = "SI"

    filtros = obtener_filtros_proveedor(config.nombre)
    texto_completo = " ".join(str(v) for v in datos.values() if v).lower()

    # Verificar inclusiones
    if filtros["incluye"] and not any(normalizar_texto(p).lower() in texto_completo for p in filtros["incluye"]):
        return False
    
    # Verificar exclusiones
    if filtros["excluye"] and any(normalizar_texto(p).lower() in texto_completo for p in filtros["excluye"]):
        logger.info(f"⏭️ {config.nombre} | Filtrado: {datos.get('Titulo', '')[:60]}")
        return False
    
    # Verificar monedas
    if filtros["monedas"] and not any(normalizar_texto(m).lower() in texto_completo for m in filtros["monedas"]):
        return False

    # Verificar duración mínima
    try:
        duracion = int(datos.get("Duracion_Minutos", 0))
    except (ValueError, TypeError):
        duracion = 0
    
    if duracion < int(filtros["duracion_minima"]):
        logger.info(f"⏭️ {config.nombre} | Duración {duracion}min < {filtros['duracion_minima']}min: {datos.get('Titulo', '')[:60]}")
        return False

    return True


class IncidentScraper(AbstractContextManager):
    """Scraper principal para obtener incidentes de múltiples proveedores"""
    
    def __init__(self) -> None:
        self.driver = None
        self.wait = None
        self.main_window: Optional[str] = None
        self._inicializar_driver()
    
    def _inicializar_driver(self) -> None:
        """Inicializa el driver de Selenium solo si es necesario"""
        try:
            self.driver = self._crear_driver()
            self.wait = WebDriverWait(self.driver, WAIT_TIMEOUT)
        except Exception as e:
            logger.warning(f"⚠️ No se pudo inicializar WebDriver: {e}")
            self.driver = None
            self.wait = None

    def _crear_driver(self) -> WebDriver:
        """Crea y configura el driver de Chrome"""
        opts = Options()
        
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        
        if sys.platform == "win32":
            service = Service(str(CHROMEDRIVER_PATH))
        else:
            service = Service()
        
        return webdriver.Chrome(service=service, options=opts)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Cierra el driver al salir del contexto"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass

    def _safe_text(self, el, selector: str, default: str = "N/A") -> str:
        """Extrae texto de forma segura usando CSS selector"""
        if not selector or not el:
            return default
        try:
            if hasattr(el, "find_element"):
                return normalizar_texto(el.find_element(By.CSS_SELECTOR, selector).text)
            return default
        except Exception:
            return default

    def _safe_click(self, element) -> None:
        """Hace click de forma segura usando JavaScript como fallback"""
        if not element:
            return
        try:
            self.driver.execute_script("arguments[0].click();", element)
        except Exception:
            try:
                element.click()
            except Exception:
                pass

    @staticmethod
    def extraer_id_incidente(href: str) -> str:
        """Extrae el ID del incidente desde una URL"""
        if not href:
            return ""
        path = urlparse(href).path.strip("/")
        return path

    @staticmethod
    def extraer_fecha_inicio(periodo_raw: str) -> str:
        """Extrae la fecha de inicio de un período formateado"""
        if not periodo_raw:
            return ""
        
        # Limpiar y extraer inicio eliminando sufijos de zona horaria
        limpio = re.sub(r'\s*[-+]\d{2}:?\d{2}\s*$', '', periodo_raw)
        limpio = re.sub(r'\s*(GMT|UTC)[-+]\d{2}:?\d{2}\s*$', '', limpio, flags=re.IGNORECASE)
        limpio = re.sub(r'\s*UTC\s*$', '', limpio, flags=re.IGNORECASE)
        
        if " - " in limpio:
            inicio = limpio.split(" - ")[0].strip()
        else:
            inicio = limpio.strip()
        
        # Normalización final
        inicio = inicio.lower()
        inicio = inicio.replace('.', '')
        inicio = re.sub(r'\b0(\d):', r'\1:', inicio)
        inicio = re.sub(r'(\d+),', r'\1', inicio)
        inicio = re.sub(r'\s+', ' ', inicio).strip()
        
        return inicio

    def _ejecutar_monnet_api(self, config: ProveedorConfig, fecha_corte=None) -> tuple[List[Dict[str, Any]], set[str]]:
        """
        Ejecuta scraping de Monnet usando la API pública.
        Retorna: (lista_de_incidentes_nuevos, set_de_ids_activos_pendientes)
        """
        from utils import cargar_json
        from config import RESULTADOS_FILE
        
        if not MONNET_API_DISPONIBLE:
            logger.error("❌ MonnetAPI no disponible, no se puede procesar Monnet")
            return [], set()
        
        resultados = []
        ids_activos = set()
        
        try:
            api = MonnetAPI()
            hoy = datetime.now(ZoneInfo("UTC"))
            
            # Cargar histórico existente para verificar duplicados
            historico_existente = cargar_json(RESULTADOS_FILE, [])
            ids_existentes = {
                inc.get("ID") 
                for inc in historico_existente 
                if inc.get("Proveedor") == "Monnet" and inc.get("ID")
            }
            
            # 🔥 CORREGIDO: Siempre buscar desde fecha_corte o desde inicio del mes
            if fecha_corte:
                # Buscar desde la última ejecución hasta hoy
                inicio_busqueda = fecha_corte
                logger.info(f"📡 Monnet API | Buscando incidentes desde {inicio_busqueda.strftime('%Y-%m-%d %H:%M:%S')} UTC hasta hoy")
                
                # Obtener incidentes desde fecha_corte hasta hoy
                historicos_api = api.obtener_historicos(inicio_busqueda, hoy)
                logger.info(f"📡 Monnet API | Obtenidos {len(historicos_api)} incidentes desde última ejecución")
                
                # Procesar SOLO los que NO existen en histórico
                for inc_api in historicos_api:
                    try:
                        inc_dict = api.convertir_a_dict(inc_api)
                        id_actual = inc_dict["ID"]
                        
                        # 🔥 VERIFICAR si ya existe en histórico
                        if id_actual in ids_existentes:
                            logger.info(f"⏪ Monnet | ID {id_actual} ya existe, saltando")
                            continue
                        
                        if clasificar_incidente(inc_dict, config):
                            resultados.append(inc_dict)
                            logger.info(f"✅ Monnet (nuevo) | {inc_dict['Titulo'][:60]}... | ID: {id_actual}")
                        else:
                            logger.info(f"⏭️ Monnet | Filtrado: {inc_dict['Titulo'][:60]}...")
                    except Exception as e:
                        logger.warning(f"❌ Error procesando incidente: {e}")
                        continue
            else:
                # Primera ejecución: buscar desde inicio del mes
                inicio_busqueda = hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                logger.info(f"📡 Monnet API | Primera ejecución - Buscando desde {inicio_busqueda.date()} hasta hoy")
                
                historicos_api = api.obtener_historicos(inicio_busqueda, hoy)
                logger.info(f"📡 Monnet API | Obtenidos {len(historicos_api)} incidentes en el rango")
                
                # Recorrer de más NUEVO a más VIEJO y cortar al encontrar el primer duplicado
                encontrado_duplicado = False
                for inc_api in historicos_api:
                    if encontrado_duplicado:
                        break
                        
                    try:
                        inc_dict = api.convertir_a_dict(inc_api)
                        id_actual = inc_dict["ID"]
                        
                        if id_actual in ids_existentes:
                            logger.info(f"⏪ Monnet | ID {id_actual} ya existe, cortando búsqueda")
                            encontrado_duplicado = True
                            break
                        
                        if clasificar_incidente(inc_dict, config):
                            resultados.append(inc_dict)
                            logger.info(f"✅ Monnet (nuevo) | {inc_dict['Titulo'][:60]}... | ID: {id_actual}")
                        else:
                            logger.info(f"⏭️ Monnet | Filtrado: {inc_dict['Titulo'][:60]}...")
                    except Exception as e:
                        logger.warning(f"❌ Error procesando incidente: {e}")
                        continue
            
            # Obtener incidentes pendientes
            pendientes_api = api.obtener_pendientes()
            for inc_api in pendientes_api:
                try:
                    inc_dict = api.convertir_a_dict(inc_api)
                    id_actual = inc_dict["ID"]
                    ids_activos.add(id_actual)
                    
                    # Verificar si ya está en resultados
                    if any(r.get("ID") == id_actual for r in resultados):
                        continue
                    
                    if clasificar_incidente(inc_dict, config):
                        resultados.append(inc_dict)
                        logger.info(f"⚠️ PENDIENTE (nuevo) Monnet | {inc_dict['Titulo'][:60]}... | ID: {id_actual}")
                    else:
                        logger.info(f"⏭️ Monnet | Pendiente filtrado: {inc_dict['Titulo'][:60]}...")
                except Exception as e:
                    logger.warning(f"❌ Error procesando pendiente: {e}")
                    continue
            
            logger.info(f"📡 Monnet API | Total nuevos incidentes: {len(resultados)}")
            
        except Exception as e:
            logger.error(f"💥 Error en _ejecutar_monnet_api: {e}", exc_info=True)
            return [], set()
        
        return resultados, ids_activos

    def _scrapear_atlassian(self, config: ProveedorConfig) -> List[Dict[str, Any]]:
        """Scrapea proveedores tipo Atlassian usando Selenium"""
        if not self.driver:
            logger.error("❌ WebDriver no disponible para Atlassian")
            return []
        
        resultados = []
        
        try:
            self.driver.get(config.url)
            self.main_window = self.driver.current_window_handle
            time.sleep(PAGE_LOAD_SLEEP)

            # Expandir incidentes si es necesario
            for b in self.driver.find_elements(By.CSS_SELECTOR, "div.expand-incidents"):
                self._safe_click(b)
            time.sleep(1)

            # Esperar y obtener contenedores
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, config.container)))
            elementos = self.driver.find_elements(By.CSS_SELECTOR, config.container)
            
        except (TimeoutException, WebDriverException) as e:
            logger.warning(f"⚠️ No se encontraron incidentes para {config.nombre}: {e}")
            return []
        
        # Fecha de corte: inicio del mes actual
        inicio_mes = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        for i, el in enumerate(elementos[:MAX_INCIDENTES_POR_PROVEEDOR]):
            try:
                periodo_raw = self._safe_text(el, config.selectores.periodo)
                periodo = ParseadorTiempo.convertir_periodo_a_vet(periodo_raw)
                
                titulo = self._safe_text(el, config.selectores.titulo)
                if not titulo or titulo == "N/A":
                    continue
                
                # Verificar si ya existe en histórico
                id_temp = self._obtener_id_temporal(el, config, titulo, periodo)
                if id_temp and _existe_id_en_historico(id_temp, config.nombre):
                    logger.info(f"⏪ {config.nombre} | Ya en histórico: {titulo[:60]}")
                    break
                
                # Verificar que sea del mes actual
                if " - " in periodo_raw:
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
                
                # Crear datos base
                datos = IncidentData(
                    Proveedor=config.nombre,
                    Titulo=titulo,
                    Periodo=periodo,
                    Resumen=self._safe_text(el, config.selectores.resumen),
                    Estado=self._safe_text(el, config.selectores.estado) if config.selectores.estado else "N/A",
                )
                
                datos.Duracion_Minutos = ParseadorTiempo.calcular_duracion(datos.Periodo)
                
                # Obtener ID y link
                link = None
                try:
                    link = el.find_element(By.CSS_SELECTOR, config.selectores.titulo).get_attribute("href")
                    datos.ID = self.extraer_id_incidente(link)
                except Exception:
                    datos.ID = id_temp
                
                # Abrir detalles si es necesario
                if link:
                    texto_preliminar = f"{titulo} {datos.Resumen}".lower()
                    filtros = obtener_filtros_proveedor(config.nombre)
                    
                    if filtros["excluye"] and any(normalizar_texto(p).lower() in texto_preliminar for p in filtros["excluye"]):
                        logger.info(f"⏭️ {config.nombre} | Filtrado (sin abrir): {titulo[:60]}")
                        continue
                    
                    try:
                        self.driver.execute_script("window.open(arguments[0]);", link)
                        self.driver.switch_to.window(self.driver.window_handles[-1])
                        time.sleep(DETAIL_LOAD_SLEEP)

                        # Obtener último update
                        updates = self.driver.find_elements(By.CSS_SELECTOR, "div.update-row")
                        if updates:
                            latest = updates[0]
                            estado = normalizar_texto(latest.find_element(By.CSS_SELECTOR, "h2.update-title").text)
                            cuerpo = normalizar_texto(latest.find_element(By.CSS_SELECTOR, "div.update-body").text)
                            datos.Estado = f"{estado}: {cuerpo}"
                            datos.Pendiente = "NO" if any(w in estado.lower() for w in ("resolved", "completed")) else "SI"
                        
                        # Obtener historial completo
                        if len(updates) > 1:
                            historial = "\n".join([
                                f"{normalizar_texto(u.find_element(By.CSS_SELECTOR, 'h2.update-title').text)}: "
                                f"{normalizar_texto(u.find_element(By.CSS_SELECTOR, 'div.update-body').text)}"
                                for u in updates
                            ])
                            if historial:
                                datos.Resumen = f"{datos.Resumen}\n\nHistorial:\n{historial}"
                        
                        # Obtener componentes afectados
                        try:
                            datos.Componentes = normalizar_texto(
                                self.driver.find_element(By.CSS_SELECTOR, "div.components-affected").text
                            )
                        except Exception:
                            pass
                        
                        self.driver.close()
                        self.driver.switch_to.window(self.main_window)
                        
                    except Exception as e:
                        logger.warning(f"⚠️ Error en detalle de {config.nombre}: {e}")
                        if self.driver:
                            try:
                                self.driver.close()
                                self.driver.switch_to.window(self.main_window)
                            except:
                                pass
                
                row = datos.to_dict()
                row['Periodo_Raw'] = periodo_raw
                
                if not clasificar_incidente(row, config):
                    continue
                
                resultados.append(row)
                estado_icono = "⚠️ PENDIENTE" if row.get("Pendiente") == "SI" else "✅"
                logger.info(f"{estado_icono} {config.nombre} | {row['Titulo'][:60]}... ({row['Duracion_Minutos']} min)")
                
            except (StaleElementReferenceException, NoSuchElementException):
                continue
            except Exception as e:
                logger.warning(f"❌ Error procesando incidente {i + 1} de {config.nombre}: {e}")
                continue
        
        return resultados

    def _obtener_id_temporal(self, el, config, titulo: str, periodo: str) -> str:
        """Obtiene ID temporal sin abrir página de detalles"""
        try:
            if config.tipo == "atlassian":
                link = el.find_element(By.CSS_SELECTOR, config.selectores.titulo).get_attribute("href")
                if link:
                    return self.extraer_id_incidente(link)
        except Exception:
            pass
        return ""

    def ejecutar(self, config: ProveedorConfig, fecha_corte=None) -> List[Dict[str, Any]]:
        """Ejecuta el scraping para un proveedor específico"""
        if PROVEEDORES_HABILITADOS is not None and config.nombre not in PROVEEDORES_HABILITADOS:
            return []
        
        logger.info(f"🔍 Iniciando proveedor: {config.nombre}")
        
        # Monnet usa API
        if config.nombre == "Monnet" and config.tipo == "freshstatus":
            nuevos_incidentes, ids_activos = self._ejecutar_monnet_api(config, fecha_corte)
            # Almacenar los IDs activos para usarlos en verificar_pendientes
            self._monnet_ids_activos = ids_activos
            return nuevos_incidentes
        
        # Atlassian y otros usan Selenium
        return self._scrapear_atlassian(config)
    
    def verificar_pendientes(self, pendientes: List[Dict[str, Any]], config: ProveedorConfig) -> List[Dict[str, Any]]:
        """Verifica el estado actual de incidentes pendientes"""
        if not pendientes:
            return []
        
        resultados = []
        
        # Monnet usa los IDs activos ya obtenidos en la ejecución
        if config.nombre == "Monnet" and config.tipo == "freshstatus" and MONNET_API_DISPONIBLE:
            if hasattr(self, '_monnet_ids_activos'):
                ids_activos = self._monnet_ids_activos
                logger.info(f"🔄 Verificando {len(pendientes)} pendientes de Monnet usando IDs almacenados")
                
                for pend in pendientes:
                    pend_id = str(pend.get("ID", ""))
                    
                    if not pend_id:
                        logger.warning(f"⚠️ Pendiente sin ID: {pend.get('Titulo', '')[:50]}")
                        pend["Pendiente"] = "REVISAR"
                        resultados.append(pend)
                        continue
                    
                    if pend_id in ids_activos:
                        # Sigue activo
                        pend["Pendiente"] = "SI"
                        logger.info(f"🟡 Monnet | Sigue pendiente: {pend.get('Titulo', '')[:60]}...")
                    else:
                        # El incidente ya no está en API (resuelto o eliminado)
                        logger.warning(f"⚠️ Incidente {pend_id} no encontrado en API (resuelto o eliminado): {pend.get('Titulo', '')[:50]}")
                        pend["Pendiente"] = "NO"
                        pend["Estado"] = "Resolved (not found in API)"
                    
                    resultados.append(pend)
                
                return resultados
            else:
                logger.warning("⚠️ No hay IDs activos almacenados para Monnet, usando API directamente")
                return self._verificar_pendientes_monnet_api(pendientes, config)
        
        # Atlassian usa Selenium
        for inc in pendientes:
            try:
                if config.tipo == "atlassian":
                    logger.info(f"🔍 Verificando pendiente Atlassian: {inc.get('Titulo', '')[:50]}...")
                    inc = self._verificar_pendiente_atlassian(inc, config)
                resultados.append(inc)
            except Exception as e:
                logger.warning(f"⚠️ Error verificando pendiente {inc.get('Titulo', '')[:50]}: {e}")
                resultados.append(inc)
        
        return resultados
    
    def _verificar_pendiente_atlassian(self, inc: Dict[str, Any], config: ProveedorConfig) -> Dict[str, Any]:
        """Verifica un pendiente de Atlassian usando Selenium"""
        if not self.driver:
            return inc
        
        try:
            id_val = inc.get("ID", "")
            
            # Construir URL
            if id_val.startswith("http"):
                url = id_val
            else:
                parsed = urlparse(config.url)
                base = f"{parsed.scheme}://{parsed.netloc}"
                id_clean = id_val.lstrip("/")
                if id_clean.startswith("incidents") or id_clean.startswith("incident"):
                    url = f"{base}/{id_clean}"
                else:
                    url = f"{base}/incidents/{id_clean}"
            
            logger.info(f"🔍 Verificando {config.nombre}: {url}")
            self.driver.get(url)
            time.sleep(DETAIL_LOAD_SLEEP)
            
            # 🔥 VALIDACIÓN SIMPLE: Buscar el ID del incidente en la URL actual y en el HTML
            current_url = self.driver.current_url
            
            # Extraer el ID de la URL actual para comparar
            import re
            url_match = re.search(r'/incidents/([a-z0-9]+)', current_url)
            current_id = url_match.group(1) if url_match else None
            
            # Si el ID no está en la URL, fue redirigido a página principal
            if current_id != id_val and id_val not in current_url:
                logger.warning(f"⚠️ {config.nombre} | Incidente redirigido (ID no encontrado en URL): {inc.get('Titulo', '')[:50]}")
                inc["Pendiente"] = "NO"
                inc["Estado"] = "Resolved (Incidente no disponible)"
                return inc
            
            # Verificar que la página contiene un contenedor de incidente válido
            try:
                # Buscar cualquier elemento que indique que es una página de incidente válida
                incident_container = self.driver.find_element(By.CSS_SELECTOR, 
                    "div.incident-updates-container, div.components-affected, h1.incident-name")
                
                # Si llegamos aquí, el incidente existe y tiene contenido
                logger.info(f"✅ {config.nombre} | Incidente válido encontrado: {inc.get('Titulo', '')[:50]}")
                
            except NoSuchElementException:
                logger.warning(f"⚠️ {config.nombre} | Incidente sin contenedor válido: {inc.get('Titulo', '')[:50]}")
                inc["Pendiente"] = "NO"
                inc["Estado"] = "Resolved"
                return inc
            
            # Obtener el estado del incidente
            try:
                updates = self.driver.find_elements(By.CSS_SELECTOR, "div.update-row")
                if updates:
                    latest = updates[0]
                    estado = normalizar_texto(latest.find_element(By.CSS_SELECTOR, "h2.update-title").text)
                    cuerpo = normalizar_texto(latest.find_element(By.CSS_SELECTOR, "div.update-body").text)
                    inc["Estado"] = f"{estado}: {cuerpo}"
                    
                    if any(w in estado.lower() for w in ("resolved", "completed")):
                        inc["Pendiente"] = "NO"
                        logger.info(f"✅ {config.nombre} | Resuelto: {inc.get('Titulo', '')[:50]}...")
                    else:
                        inc["Pendiente"] = "SI"
                        logger.info(f"🟡 {config.nombre} | Sigue pendiente: {inc.get('Titulo', '')[:50]}...")
                else:
                    # No hay updates pero el incidente existe
                    inc["Pendiente"] = "SI"
                    logger.info(f"🟡 {config.nombre} | Sin updates, asumiendo pendiente: {inc.get('Titulo', '')[:50]}...")
                
                # Extraer componentes
                try:
                    componentes = self.driver.find_element(By.CSS_SELECTOR, "div.components-affected").text
                    inc["Componentes"] = normalizar_texto(componentes)
                except:
                    pass
                    
            except Exception as e:
                logger.warning(f"⚠️ Error extrayendo estado: {e}")
                inc["Pendiente"] = "SI"
            
        except WebDriverException as e:
            logger.warning(f"⚠️ Error de conexión verificando {inc.get('Titulo', '')[:50]}: {e}")
            inc["Pendiente"] = "SI"
        except Exception as e:
            logger.warning(f"⚠️ Error verificando {inc.get('Titulo', '')[:50]}: {e}")
            inc["Pendiente"] = "SI"
        
        return inc
    
    def _verificar_pendientes_monnet_api(self, pendientes: List[Dict[str, Any]], config: ProveedorConfig) -> List[Dict[str, Any]]:
        """Verifica pendientes de Monnet usando la API"""
        if not MONNET_API_DISPONIBLE:
            logger.error("❌ MonnetAPI no disponible")
            return pendientes
        
        resultados = []
        
        try:
            api = MonnetAPI()
            pendientes_actuales_api = api.obtener_pendientes()
            ids_activos = {str(inc["id"]) for inc in pendientes_actuales_api}
            
            for pend in pendientes:
                pend_id = str(pend.get("ID", ""))
                
                if pend_id in ids_activos:
                    # Sigue pendiente - buscar datos actualizados
                    inc_actual = next((inc for inc in pendientes_actuales_api if str(inc["id"]) == pend_id), None)
                    if inc_actual:
                        inc_dict = api.convertir_a_dict(inc_actual)
                        pend["Periodo"] = inc_dict["Periodo"]
                        pend["Duracion_Minutos"] = inc_dict["Duracion_Minutos"]
                        pend["Estado"] = inc_dict["Estado"]
                    
                    pend["Pendiente"] = "SI"
                    logger.info(f"🟡 Monnet | Sigue pendiente: {pend.get('Titulo', '')[:60]}...")
                else:
                    # Se resolvió
                    pend["Pendiente"] = "NO"
                    pend["Estado"] = "Resolved"
                    logger.info(f"✅ Monnet | Resuelto: {pend.get('Titulo', '')[:60]}...")
                
                resultados.append(pend)
                
        except Exception as e:
            logger.error(f"❌ Error verificando pendientes Monnet: {e}")
            return pendientes
        
        return resultados


def _existe_id_en_historico(id_incidente: str, proveedor: str) -> bool:
    """Verifica si un ID ya existe en el histórico"""
    if not id_incidente:
        return False
    
    from utils import cargar_json
    
    try:
        historico = cargar_json(RESULTADOS_FILE, [])
        return any(
            str(h.get("ID", "")) == str(id_incidente) and h.get("Proveedor") == proveedor 
            for h in historico
        )
    except Exception:
        return False