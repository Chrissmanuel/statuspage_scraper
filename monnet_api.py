# monnet_api.py - VERSIÓN ACTUALIZADA

import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo
from config import VET, HTTP_TIMEOUT
from utils import logger


class MonnetAPI:
    """Cliente para la API pública de Freshservice de Monnet (NUEVA VERSIÓN)"""
    
    BASE_URL = "https://monnetpayments.status.freshservice.com/api/public/status/disruptions"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://monnetpayments.status.freshservice.com/",
            "Origin": "https://monnetpayments.status.freshservice.com",
        })
        self._service_names_cache = {}
    
    def _construir_url_filtro(self, filtros: List[Dict[str, Any]], page: int = 1, per_page: int = 100) -> str:
        """Construye la URL con filtros correctamente formateados"""
        import urllib.parse
        
        filter_str = json.dumps(filtros, separators=(',', ':'))
        encoded_filter = urllib.parse.quote(filter_str, safe='')
        
        return f"{self.BASE_URL}?filter={encoded_filter}&order_by=started_at&order_type=desc&page={page}&per_page={per_page}"
    
    def obtener_historicos(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Obtiene incidentes históricos en un rango de fechas.
        Usa SOLO el filtro time_window (compatible con la API).
        """
        all_results = []
        page = 1
        per_page = 100
        
        start_utc = start_date.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end_date.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # ✅ Solo un filtro: time_window (compatible)
        filtros = [
            {
                "condition": "time_window",
                "operator": "is_between",
                "value": [start_utc, end_utc]
            }
        ]
        
        while True:
            try:
                url = self._construir_url_filtro(filtros, page, per_page)
                logger.debug(f"📡 Monnet API | Consultando página {page}...")
                
                response = self.session.get(url, timeout=HTTP_TIMEOUT)
                
                if response.status_code != 200:
                    logger.error(f"❌ Error consultando API de Monnet: {response.status_code}")
                    if response.text:
                        logger.error(f"   Detalles: {response.text[:200]}")
                    break
                
                data = response.json()
                disruptions = data.get("disruptions", [])
                all_results.extend(disruptions)
                
                meta = data.get("meta", {})
                if page >= meta.get("last", 1) or not disruptions:
                    break
                
                page += 1
                
            except requests.RequestException as e:
                logger.error(f"❌ Error consultando API de Monnet: {e}")
                break
            except json.JSONDecodeError as e:
                logger.error(f"❌ Error decodificando JSON: {e}")
                break
        
        logger.info(f"📡 Monnet API | Obtenidos {len(all_results)} incidentes históricos")
        return all_results
    
    def obtener_pendientes(self) -> List[Dict[str, Any]]:
        """
        Obtiene incidentes activos (pendientes) de Monnet.
        ✅ Método actualizado: usa time_window + filtro local por ended_at
        """
        try:
            # Buscar en los últimos 90 días
            end_date = datetime.now(ZoneInfo("UTC"))
            start_date = end_date - timedelta(days=90)
            
            todos = self.obtener_historicos(start_date, end_date)
            
            # ✅ Filtrar localmente por ended_at (más confiable)
            pendientes = [inc for inc in todos if inc.get("ended_at") is None]
            
            logger.info(f"📡 Monnet API | Obtenidos {len(pendientes)} incidentes pendientes")
            return pendientes
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo pendientes de Monnet: {e}")
            return []
    
    def obtener_incidente_por_id(self, incident_id: int) -> Optional[Dict[str, Any]]:
        """Obtiene un incidente específico por su ID numérico"""
        try:
            url = f"{self.BASE_URL}/{incident_id}"
            response = self.session.get(url, timeout=HTTP_TIMEOUT)
            
            if response.status_code == 404:
                logger.debug(f"📭 Incidente {incident_id} no encontrado (404)")
                return None
            
            if response.status_code != 200:
                logger.error(f"❌ Error consultando incidente {incident_id}: {response.status_code}")
                return None
            
            data = response.json()
            return data.get("disruption")
            
        except requests.RequestException as e:
            logger.error(f"❌ Error consultando incidente {incident_id}: {e}")
            return None
    
    def obtener_actualizaciones(self, incident_id: int) -> List[Dict[str, Any]]:
        """Obtiene las actualizaciones de un incidente específico"""
        try:
            url = f"{self.BASE_URL}/{incident_id}/updates"
            response = self.session.get(url, timeout=HTTP_TIMEOUT)
            
            if response.status_code != 200:
                logger.error(f"❌ Error obteniendo actualizaciones del incidente {incident_id}: {response.status_code}")
                return []
            
            data = response.json()
            return data.get("disruption_updates", [])
            
        except requests.RequestException as e:
            logger.error(f"❌ Error obteniendo actualizaciones del incidente {incident_id}: {e}")
            return []
    
    def convertir_a_dict(self, incidente_api: Dict[str, Any]) -> Dict[str, Any]:
        """Convierte un incidente de la NUEVA API al formato estándar"""
        # Extraer nombres de servicios afectados
        componentes_nombres = []
        title = incidente_api.get("title", "")
        
        for service in incidente_api.get("impacted_services", []):
            service_id = str(service.get("id"))
            if service_id:
                # Intentar extraer nombre del servicio desde el título
                nombre = self._extraer_nombre_servicio_desde_titulo(title, service_id)
                componentes_nombres.append(nombre)
        
        componentes_str = ", ".join(componentes_nombres) if componentes_nombres else "N/A"
        
        # Determinar si es planificado
        es_planned = incidente_api.get("type") == 2
        
        # Fechas
        start_time = incidente_api.get("started_at")
        end_time = incidente_api.get("ended_at")
        
        # Formatear período
        periodo_vet = self._formatear_periodo(start_time, end_time)
        
        # Calcular duración
        duracion = self._calcular_duracion(start_time, end_time)
        
        # Estado pendiente
        pendiente = "SI" if end_time is None else "NO"
        
        # Mapeo de status
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
        
        # Obtener el ID como string (para compatibilidad)
        incident_id = str(incidente_api.get("id", ""))
        
        return {
            "Proveedor": "Monnet",
            "Titulo": title,
            "Periodo": periodo_vet,
            "Resumen": incidente_api.get("description", "N/A"),
            "Estado": estado,
            "Componentes": componentes_str,
            "Duracion_Minutos": str(duracion),
            "Pendiente": pendiente,
            "ID": incident_id,
            "Human_ID": incidente_api.get("human_display_id", ""),
            "Periodo_Raw": periodo_vet,
            "start_time": start_time,
            "end_time": end_time,
            "tipo_incidente": incidente_api.get("type"),
            "status_code": incidente_api.get("status"),
            "created_at": incidente_api.get("created_at"),
            "updated_at": incidente_api.get("updated_at"),
        }
    
    def _extraer_nombre_servicio_desde_titulo(self, title: str, service_id: str) -> str:
        """Extrae el nombre del servicio desde el título del incidente."""
        if service_id in self._service_names_cache:
            return self._service_names_cache[service_id]
        
        # Intentar extraer entre corchetes
        import re
        pattern = r'\[([^\]]+)\]'
        matches = re.findall(pattern, title)
        
        if matches:
            for match in matches:
                if len(match.strip()) > 2:
                    nombre = match.strip()
                    self._service_names_cache[service_id] = nombre
                    return nombre
        
        return f"Servicio {service_id}"
    
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