"""
Cliente HTTP especializado para consumir la API pública de Monnet.
API: https://public-api.freshstatus.io/v1/public-incidents/
"""

import requests
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class MonnetApiClient:
    """Cliente para la API pública de Freshstatus de Monnet"""
    
    BASE_URL = "https://public-api.freshstatus.io/v1/public-incidents/"
    ACCOUNT_ID = "37683"  # ID de cuenta de Monnet
    TIMEOUT = 30
    RETRIES = 3
    
    def __init__(self, timeout: int = TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def close(self):
        """Cierra la sesión"""
        if self.session:
            self.session.close()
    
    def obtener_historico(self, fecha_inicio: Optional[datetime] = None, 
                         fecha_fin: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Obtiene el histórico de incidentes dentro de un rango de fechas.
        
        Args:
            fecha_inicio: Fecha de inicio (por defecto: inicio del mes actual)
            fecha_fin: Fecha de fin (por defecto: fin del mes actual)
        
        Returns:
            Lista de incidentes del histórico
        """
        if not fecha_inicio:
            ahora = datetime.utcnow()
            fecha_inicio = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        if not fecha_fin:
            ahora = datetime.utcnow()
            if ahora.month == 12:
                fecha_fin = ahora.replace(year=ahora.year + 1, month=1, day=1, 
                                         hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
            else:
                fecha_fin = ahora.replace(month=ahora.month + 1, day=1, 
                                         hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
        
        # Formatear fechas en ISO 8601
        start_iso = fecha_inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = fecha_fin.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        params = {
            "account_id": self.ACCOUNT_ID,
            "start_time__gte": start_iso,
            "start_time__lte": end_iso,
        }
        
        logger.info(f"🔍 Monnet API | Obteniendo histórico desde {start_iso} hasta {end_iso}")
        
        try:
            respuesta = self._hacer_request(params)
            if respuesta and "incidents" in respuesta:
                incidentes = respuesta["incidents"]
                logger.info(f"✅ Monnet API | Obtenidos {len(incidentes)} incidentes del histórico")
                return incidentes
            return []
        except Exception as e:
            logger.error(f"❌ Error obteniendo histórico de Monnet: {e}")
            return []
    
    def obtener_pendientes(self) -> List[Dict[str, Any]]:
        """
        Obtiene los incidentes activos/pendientes (sin fecha de finalización).
        
        Returns:
            Lista de incidentes pendientes
        """
        params = {
            "account_id": self.ACCOUNT_ID,
            "end_time__isempty": "true",
        }
        
        logger.info("🔍 Monnet API | Obteniendo incidentes pendientes")
        
        try:
            respuesta = self._hacer_request(params)
            if respuesta and "incidents" in respuesta:
                incidentes = respuesta["incidents"]
                logger.info(f"⚠️ Monnet API | Obtenidos {len(incidentes)} incidentes pendientes")
                return incidentes
            return []
        except Exception as e:
            logger.error(f"❌ Error obteniendo pendientes de Monnet: {e}")
            return []
    
    def _hacer_request(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Realiza una solicitud GET a la API con reintentos.
        
        Args:
            params: Parámetros de query
        
        Returns:
            Respuesta JSON o None si falla
        """
        url = f"{self.BASE_URL}?{urlencode(params)}"
        
        for intento in range(self.RETRIES):
            try:
                logger.debug(f"📡 Intento {intento + 1}/{self.RETRIES}: {url}")
                
                respuesta = self.session.get(url, timeout=self.timeout)
                respuesta.raise_for_status()
                
                return respuesta.json()
            
            except requests.exceptions.Timeout:
                logger.warning(f"⏱️ Timeout en intento {intento + 1}/{self.RETRIES}")
                if intento == self.RETRIES - 1:
                    raise
            
            except requests.exceptions.HTTPError as e:
                logger.warning(f"⚠️ HTTP Error {e.response.status_code} en intento {intento + 1}/{self.RETRIES}")
                if e.response.status_code == 429:  # Rate limit
                    logger.info("📊 Rate limit detectado, aguardando...")
                    if intento < self.RETRIES - 1:
                        import time
                        time.sleep(2 ** intento)
                elif intento == self.RETRIES - 1:
                    raise
            
            except requests.exceptions.RequestException as e:
                logger.warning(f"🌐 Error de conexión en intento {intento + 1}/{self.RETRIES}: {e}")
                if intento == self.RETRIES - 1:
                    raise
        
        return None
