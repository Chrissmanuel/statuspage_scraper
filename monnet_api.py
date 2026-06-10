import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo
from config import VET, HTTP_TIMEOUT, HTTP_RETRIES
from utils import logger




# Al inicio del archivo, definir el mapeo completo
COMPONENT_NAMES = {
    # PAYIN Components
    "157231": "API BACK OFFICE PAYIN",
    "157233": "PAYIN API",
    "157235": "PAYIN Chile",
    "157237": "PAYIN Argentina",
    "157238": "PAYIN Ecuador",
    "157240": "PAYIN Peru",
    "177836": "PAYIN Colombia",
    "177837": "PAYIN Mexico",
    "246498": "PAYIN Guatemala",
    
    # PAYOUT Components
    "187968": "PAYOUT API",
    "187969": "PAYOUT API BACK OFFICE",
    "187970": "PAYOUT Argentina",
    "187971": "PAYOUT Mexico",
    "192853": "PAYOUT Chile",
    "193601": "PAYOUT Ecuador",
    "193602": "PAYOUT Peru",
    "193603": "PAYOUT Guatemala",
    "193604": "PAYOUT Honduras",
    "193605": "PAYOUT Colombia",
}


def get_component_name(component_id: str) -> str:
    """Retorna el nombre legible de un componente o el ID si no está mapeado"""
    return COMPONENT_NAMES.get(component_id, f"Componente {component_id}")



class MonnetAPI:
    """Cliente para la API pública de Freshstatus de Monnet"""
    
    BASE_URL = "https://public-api.freshstatus.io/v1/public-incidents/"
    ACCOUNT_ID = "37683"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json"
        })
    
    def obtener_historicos(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Obtiene incidentes históricos de Monnet en un rango de fechas.
        
        Args:
            start_date: Fecha de inicio (timezone-aware)
            end_date: Fecha de fin (timezone-aware)
        
        Returns:
            Lista de incidentes con datos crudos de la API
        """
        # Convertir a UTC ISO format
        start_utc = start_date.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end_date.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        all_results = []
        url = self.BASE_URL
        params = {
            "account_id": self.ACCOUNT_ID,
            "start_time__gte": start_utc,
            "start_time__lte": end_utc
        }
        
        while url:
            try:
                logger.debug(f"📡 Monnet API | Consultando: {url[:100]}...")
                response = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                
                all_results.extend(data.get("results", []))
                
                # Paginación
                url = data.get("next")
                params = None  # Los params ya vienen en la URL next
                
            except requests.RequestException as e:
                logger.error(f"❌ Error consultando API de Monnet: {e}")
                break
        
        logger.info(f"📡 Monnet API | Obtenidos {len(all_results)} incidentes históricos")
        return all_results
    
    def obtener_pendientes(self) -> List[Dict[str, Any]]:
        """
        Obtiene incidentes activos (sin end_time) de Monnet.
        
        Returns:
            Lista de incidentes pendientes
        """
        try:
            params = {
                "account_id": self.ACCOUNT_ID,
                "end_time__isempty": "true"
            }
            
            response = self.session.get(self.BASE_URL, params=params, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            pendientes = data.get("results", [])
            logger.info(f"📡 Monnet API | Obtenidos {len(pendientes)} incidentes pendientes")
            return pendientes
            
        except requests.RequestException as e:
            logger.error(f"❌ Error consultando pendientes de Monnet: {e}")
            return []
    
    def convertir_a_dict(self, incidente_api: Dict[str, Any]) -> Dict[str, Any]:
        # Extraer y mapear componentes afectados
        componentes_ids = []
        for comp in incidente_api.get("affected_components", []):
            if "component" in comp:
                componentes_ids.append(str(comp["component"]))
        
        # Convertir IDs a nombres legibles
        componentes_nombres = []
        for comp_id in componentes_ids:
            if comp_id in COMPONENT_NAMES:
                componentes_nombres.append(COMPONENT_NAMES[comp_id])
            else:
                componentes_nombres.append(f"ID:{comp_id}")
                logger.warning(f"⚠️ Componente no mapeado: {comp_id}")
        
        componentes_str = ", ".join(componentes_nombres) if componentes_nombres else "N/A"
        
        # Determinar si es mantenimiento programado
        es_planned = incidente_api.get("is_planned", False)
        scheduled_start = incidente_api.get("scheduled_start_time")
        scheduled_end = incidente_api.get("scheduled_end_time")
        
        # Usar scheduled times si existen, sino start/end
        start_time = scheduled_start if scheduled_start else incidente_api.get("start_time")
        end_time = scheduled_end if scheduled_end else incidente_api.get("end_time")
        
        # 🔥 CORRECCIÓN: Definir periodo_vet AQUÍ (no está en tu código actual)
        periodo_vet = self._formatear_periodo(start_time, end_time)
        
        # Calcular duración
        duracion = self._calcular_duracion(start_time, end_time)
        
        # Determinar si está pendiente
        pendiente = "SI" if end_time is None else "NO"
        
        # Estado por defecto
        estado = "Planned" if es_planned else "Resolved" if not pendiente else "Investigating"
        
        return {
            "Proveedor": "Monnet",
            "Titulo": incidente_api.get("title", "N/A"),
            "Periodo": periodo_vet,  # ✅ Ahora sí está definido
            "Resumen": incidente_api.get("description", "N/A"),
            "Estado": estado,
            "Componentes": componentes_str,
            "Duracion_Minutos": str(duracion),
            "Pendiente": pendiente,
            "ID": str(incidente_api.get("id", "")),
            "Periodo_Raw": periodo_vet,
            "start_time": start_time,
            "end_time": end_time,
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
                return f"{start_vet.strftime('%b %d, %I:%M %p')} VET"
                
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
                # Incidente activo: calcular desde inicio hasta ahora
                ahora = datetime.now(VET).astimezone(ZoneInfo("UTC"))
                return int((ahora - start_dt).total_seconds() / 60)
                
        except Exception as e:
            logger.debug(f"Error calculando duración: {e}")
            return 0

    def obtener_incidente_por_id(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene un incidente específico por su ID.
        
        Args:
            incident_id: ID del incidente (ej: "1607864")
        
        Returns:
            Diccionario con los datos del incidente o None si no existe (404)
        """
        try:
            url = f"{self.BASE_URL}{incident_id}/"
            params = {"account_id": self.ACCOUNT_ID}
            
            response = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                # El incidente no existe - fue eliminado o expiró
                logger.debug(f"📭 Incidente {incident_id} no encontrado (404)")
                return None
            else:
                logger.warning(f"⚠️ Error {response.status_code} consultando incidente {incident_id}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"❌ Error consultando incidente {incident_id}: {e}")
            return None