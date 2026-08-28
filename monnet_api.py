import requests
import json
import re
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
        # ✅ Cabeceras IDÉNTICAS a las que usa el navegador
        # En __init__ de MonnetAPI
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Referer": "https://monnetpayments.status.freshservice.com/",
            "Origin": "https://monnetpayments.status.freshservice.com",
        })
        self._service_names_cache = {}
    
    def _extraer_nombre_servicio_desde_titulo(self, title: str, service_id: str) -> str:
        """
        Extrae el nombre del servicio desde el título del incidente.
        Ej: "Bank Intermittence – [BANK ITAU I CL I BT] - [PAYINS]" -> "BANK ITAU I CL I BT"
        """
        if service_id in self._service_names_cache:
            return self._service_names_cache[service_id]
        
        # Buscar patrones como [NOMBRE_SERVICIO]
        pattern = r'\[([^\]]+)\]'
        matches = re.findall(pattern, title)
        
        if matches:
            # Tomar el primer match que no sea demasiado corto
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
        
        # Construir filtro SIN espacios
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
                logger.debug(f"URL: {url[:150]}...")
                
                response = self.session.get(url, timeout=HTTP_TIMEOUT)
                
                # ✅ LOG DETALLADO para ver qué está pasando en Git
                logger.info(f"📡 Monnet API | Status: {response.status_code}")
                logger.info(f"📡 Monnet API | Headers: {dict(response.headers)}")
                logger.info(f"📡 Monnet API | Body preview: {response.text[:500]}")
                
                # ✅ Si no es 200, mostrar el error real
                if response.status_code != 200:
                    logger.error(f"❌ Error consultando API de Monnet: {response.status_code}")
                    logger.error(f"   Respuesta: {response.text[:500]}")
                    break
                
                # ✅ Verificar que la respuesta no esté vacía
                if not response.text or not response.text.strip():
                    logger.warning("⚠️ Respuesta vacía de la API de Monnet")
                    break
                
                # ✅ Intentar parsear JSON
                try:
                    data = response.json()
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Error decodificando JSON: {e}")
                    logger.error(f"   Respuesta recibida: {response.text[:200]}")
                    break
                
                disruptions = data.get("disruptions", [])
                all_results.extend(disruptions)
                
                meta = data.get("meta", {})
                if page >= meta.get("last", 1):
                    break
                
                page += 1
                
            except requests.RequestException as e:
                logger.error(f"❌ Error consultando API de Monnet: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"   Status: {e.response.status_code}")
                    logger.error(f"   Texto: {e.response.text[:500]}")
                break
        
        logger.info(f"📡 Monnet API | Obtenidos {len(all_results)} incidentes históricos")
        return all_results
    
    def obtener_pendientes(self) -> List[Dict[str, Any]]:
        """
        Obtiene incidentes activos (pendientes) de Monnet.
        """
        try:
            end_date = datetime.now(ZoneInfo("UTC"))
            start_date = end_date - timedelta(days=90)
            
            todos = self.obtener_historicos(start_date, end_date)
            
            # Filtrar los que no tienen ended_at (pendientes)
            pendientes = [inc for inc in todos if inc.get("ended_at") is None]
            
            logger.info(f"📡 Monnet API | Obtenidos {len(pendientes)} incidentes pendientes")
            return pendientes
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo pendientes de Monnet: {e}")
            return []
    
    def obtener_actualizaciones(self, incident_id: int) -> List[Dict[str, Any]]:
        """
        Obtiene las actualizaciones de un incidente específico.
        Endpoint: /disruptions/{id}/updates
        """
        try:
            url = f"{self.BASE_URL}/{incident_id}/updates"
            
            response = self.session.get(url, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            updates = data.get("disruption_updates", [])
            logger.debug(f"📡 Monnet API | Obtenidas {len(updates)} actualizaciones para incidente {incident_id}")
            return updates
            
        except requests.RequestException as e:
            logger.error(f"❌ Error obteniendo actualizaciones del incidente {incident_id}: {e}")
            return []
    
    def obtener_incidente_por_id(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene un incidente específico por su ID.
        Como la API no tiene endpoint directo por ID, buscamos en el rango de 90 días.
        """
        try:
            end_date = datetime.now(ZoneInfo("UTC"))
            start_date = end_date - timedelta(days=90)
            
            todos = self.obtener_historicos(start_date, end_date)
            
            # Buscar el incidente por ID
            for inc in todos:
                if str(inc.get("id")) == str(incident_id):
                    return inc
            
            logger.debug(f"📭 Incidente {incident_id} no encontrado en el rango de 90 días")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error consultando incidente {incident_id}: {e}")
            return None
    
    def convertir_a_dict(self, incidente_api: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convierte un incidente de la NUEVA API al formato estándar.
        """
        # Extraer y mapear componentes afectados
        componentes_nombres = []
        title = incidente_api.get("title", "")
        
        for service in incidente_api.get("impacted_services", []):
            service_id = str(service.get("id"))
            if service_id:
                nombre = self._extraer_nombre_servicio_desde_titulo(title, service_id)
                componentes_nombres.append(nombre)
        
        componentes_str = ", ".join(componentes_nombres) if componentes_nombres else "N/A"
        
        # Determinar si es mantenimiento programado
        # type: 1 = Incident, 2 = Planned Maintenance
        es_planned = incidente_api.get("type") == 2
        
        # Obtener fechas
        start_time = incidente_api.get("started_at")
        end_time = incidente_api.get("ended_at")
        
        # Formatear período
        periodo_vet = self._formatear_periodo(start_time, end_time)
        
        # Calcular duración
        duracion = self._calcular_duracion(start_time, end_time)
        
        # Determinar si está pendiente
        pendiente = "SI" if end_time is None else "NO"
        
        # Mapear estado de Freshservice a nuestro formato
        # status: 1 = Investigating, 2 = Resolved, 3 = Monitoring, 4 = Scheduled, 5 = Post-Mortem
        status_map = {
            1: "Investigating",
            2: "Resolved",
            3: "Monitoring",
            4: "Planned",
            5: "Post-Mortem"
        }
        status_code = incidente_api.get("status", 1)
        estado = status_map.get(status_code, "Unknown")
        
        # Si es planificado, forzar estado "Planned"
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