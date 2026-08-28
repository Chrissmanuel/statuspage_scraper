# monnet_api.py
import requests
import json
import re
import gzip
import io
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo
from config import VET, HTTP_TIMEOUT
from utils import logger


class MonnetAPI:
    """Cliente para la NUEVA API pública de Freshservice de Monnet"""
    
    BASE_URL = "https://monnetpayments.status.freshservice.com/api/public/status/disruptions"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Accept-Encoding": "gzip, deflate",  # ← Sin br, solo gzip y deflate
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Referer": "https://monnetpayments.status.freshservice.com/",
            "Origin": "https://monnetpayments.status.freshservice.com",
        })
        self._service_names_cache = {}
    
    def _decodificar_respuesta(self, response: requests.Response) -> dict:
        """
        Decodifica la respuesta manejando compresión manualmente si es necesario.
        Usa gzip como fallback si requests no descomprimió automáticamente.
        """
        content_encoding = response.headers.get('Content-Encoding', '')
        
        # Si la respuesta está comprimida con gzip y requests no la descomprimió
        if 'gzip' in content_encoding or 'deflate' in content_encoding:
            try:
                # Intentar descomprimir con gzip
                if 'gzip' in content_encoding:
                    with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as gz:
                        data = gz.read()
                    return json.loads(data.decode('utf-8'))
                else:
                    # deflate
                    import zlib
                    data = zlib.decompress(response.content, -zlib.MAX_WBITS)
                    return json.loads(data.decode('utf-8'))
            except Exception as e:
                logger.debug(f"Error descomprimiendo manualmente: {e}")
                # Fallback: intentar con response.json() normal
                return response.json()
        else:
            # Respuesta sin comprimir
            return response.json()
    
    def _extraer_nombre_servicio_desde_titulo(self, title: str, service_id: str) -> str:
        """Extrae el nombre del servicio desde el título del incidente."""
        if service_id in self._service_names_cache:
            return self._service_names_cache[service_id]
        
        pattern = r'\[([^\]]+)\]'
        matches = re.findall(pattern, title)
        
        if matches:
            for match in matches:
                if len(match.strip()) > 2:
                    nombre = match.strip()
                    self._service_names_cache[service_id] = nombre
                    return nombre
        
        logger.warning(f"⚠️ No se pudo extraer nombre para servicio {service_id} desde título: {title}")
        return f"Servicio {service_id}"
    
    def obtener_historicos(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Obtiene incidentes históricos de Monnet en un rango de fechas."""
        all_results = []
        page = 1
        per_page = 100
        
        start_utc = start_date.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end_date.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        filtros = [
            {
                "condition": "time_window",
                "operator": "is_between",
                "value": [start_utc, end_utc]
            }
        ]
        filter_str = json.dumps(filtros, separators=(',', ':'))
        
        while True:
            try:
                import urllib.parse
                encoded_filter = urllib.parse.quote(filter_str, safe='')
                
                url = f"{self.BASE_URL}?filter={encoded_filter}&order_by=started_at&order_type=desc&page={page}&per_page={per_page}"
                
                logger.debug(f"📡 Monnet API | Consultando página {page}...")
                
                response = self.session.get(url, timeout=HTTP_TIMEOUT)
                
                if response.status_code != 200:
                    logger.error(f"❌ Error consultando API de Monnet: {response.status_code}")
                    break
                
                if not response.content:
                    logger.warning("⚠️ Respuesta vacía de la API de Monnet")
                    break
                
                # Intentar decodificar
                try:
                    data = self._decodificar_respuesta(response)
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Error decodificando JSON: {e}")
                    # Último intento: usar response.json() directamente
                    try:
                        data = response.json()
                    except:
                        break
                except Exception as e:
                    logger.error(f"❌ Error procesando respuesta: {e}")
                    break
                
                disruptions = data.get("disruptions", [])
                all_results.extend(disruptions)
                
                meta = data.get("meta", {})
                if page >= meta.get("last", 1) or not disruptions:
                    break
                
                page += 1
                
            except requests.RequestException as e:
                logger.error(f"❌ Error consultando API de Monnet: {e}")
                break
        
        logger.info(f"📡 Monnet API | Obtenidos {len(all_results)} incidentes históricos")
        return all_results
    
    def obtener_pendientes(self) -> List[Dict[str, Any]]:
        """Obtiene incidentes activos (pendientes) de Monnet."""
        try:
            end_date = datetime.now(ZoneInfo("UTC"))
            start_date = end_date - timedelta(days=90)
            
            todos = self.obtener_historicos(start_date, end_date)
            
            pendientes = [inc for inc in todos if inc.get("ended_at") is None]
            
            logger.info(f"📡 Monnet API | Obtenidos {len(pendientes)} incidentes pendientes")
            return pendientes
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo pendientes de Monnet: {e}")
            return []
    
    def obtener_actualizaciones(self, incident_id: int) -> List[Dict[str, Any]]:
        """Obtiene las actualizaciones de un incidente específico."""
        try:
            url = f"{self.BASE_URL}/{incident_id}/updates"
            
            response = self.session.get(url, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            
            data = self._decodificar_respuesta(response)
            
            updates = data.get("disruption_updates", [])
            logger.debug(f"📡 Monnet API | Obtenidas {len(updates)} actualizaciones para incidente {incident_id}")
            return updates
            
        except requests.RequestException as e:
            logger.error(f"❌ Error obteniendo actualizaciones del incidente {incident_id}: {e}")
            return []
    
    def obtener_incidente_por_id(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene un incidente específico por su ID."""
        try:
            end_date = datetime.now(ZoneInfo("UTC"))
            start_date = end_date - timedelta(days=90)
            
            todos = self.obtener_historicos(start_date, end_date)
            
            for inc in todos:
                if str(inc.get("id")) == str(incident_id):
                    return inc
            
            logger.debug(f"📭 Incidente {incident_id} no encontrado en el rango de 90 días")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error consultando incidente {incident_id}: {e}")
            return None
    
    def convertir_a_dict(self, incidente_api: Dict[str, Any]) -> Dict[str, Any]:
        """Convierte un incidente de la NUEVA API al formato estándar."""
        componentes_nombres = []
        title = incidente_api.get("title", "")
        
        for service in incidente_api.get("impacted_services", []):
            service_id = str(service.get("id"))
            if service_id:
                nombre = self._extraer_nombre_servicio_desde_titulo(title, service_id)
                componentes_nombres.append(nombre)
        
        componentes_str = ", ".join(componentes_nombres) if componentes_nombres else "N/A"
        
        es_planned = incidente_api.get("type") == 2
        
        start_time = incidente_api.get("started_at")
        end_time = incidente_api.get("ended_at")
        
        periodo_vet = self._formatear_periodo(start_time, end_time)
        duracion = self._calcular_duracion(start_time, end_time)
        pendiente = "SI" if end_time is None else "NO"
        
        status_map = {
            1: "Investigating",
            2: "Resolved",
            3: "Monitoring",
            4: "Planned",
            5: "Post-Mortem"
        }
        status_code = incidente_api.get("status", 1)
        estado = status_map.get(status_code, "Unknown")
        
        if es_planned:
            estado = "Planned"
        
        return {
            "Proveedor": "Monnet",
            "Titulo": incidente_api.get("title", "N/A"),
            "Periodo": periodo_vet,
            "Resumen": incidente_api.get("description", "N/A"),
            "Estado": estado,
            "Componentes": componentes_str,
            "Duracion_Minutos": str(duracion),
            "Pendiente": pendiente,
            "ID": str(incidente_api.get("id", "")),
            "Human_ID": incidente_api.get("human_display_id", ""),
            "Periodo_Raw": periodo_vet,
            "start_time": start_time,
            "end_time": end_time,
            "tipo_incidente": incidente_api.get("type"),
            "status_code": incidente_api.get("status"),
            "created_at": incidente_api.get("created_at"),
            "updated_at": incidente_api.get("updated_at"),
        }
    
    def _formatear_periodo(self, start_time: Optional[str], end_time: Optional[str]) -> str:
        """Formatea el período en formato legible VET"""
        if not start_time:
            return "N/A"
        
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            start_vet = start_dt.astimezone(VET)
            
            if end_time:
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                end_vet = end_dt.astimezone(VET)
                
                if start_vet.date() == end_vet.date():
                    return f"{start_vet.strftime('%b %d, %I:%M %p')} - {end_vet.strftime('%I:%M %p')} VET"
                else:
                    return f"{start_vet.strftime('%b %d, %I:%M %p')} - {end_vet.strftime('%b %d, %I:%M %p')} VET"
            else:
                return f"{start_vet.strftime('%b %d, %I:%M %p')} VET (Activo)"
                
        except Exception as e:
            logger.debug(f"Error formateando período: {e}")
            return start_time
    
    def _calcular_duracion(self, start_time: Optional[str], end_time: Optional[str]) -> int:
        """Calcula duración en minutos"""
        if not start_time:
            return 0
        
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            
            if end_time:
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                return int((end_dt - start_dt).total_seconds() / 60)
            else:
                ahora = datetime.now(VET).astimezone(ZoneInfo("UTC"))
                return int((ahora - start_dt).total_seconds() / 60)
                
        except Exception as e:
            logger.debug(f"Error calculando duración: {e}")
            return 0