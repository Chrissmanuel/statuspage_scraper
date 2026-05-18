import time
from typing import Any
import requests
from requests import Session
from config import HTTP_RETRIES, HTTP_TIMEOUT
from utils import logger


class HttpClient:
    def __init__(self) -> None:
        self.session = Session()

    def post_json(self, url: str, payload: Any, sheet: str = "History", retries: int = HTTP_RETRIES, timeout: int = HTTP_TIMEOUT) -> bool:
        for intento in range(retries):
            try:
                body_data = {"sheet": sheet, "data": payload}
                
                # Primer intento con allow_redirects=False para detectar 302
                r = self.session.post(
                    url,
                    json=body_data,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                    allow_redirects=False
                )
                
                # Si Google redirige, seguir la URL manualmente
                if r.status_code in (301, 302, 303, 307, 308):
                    redirect_url = r.headers.get("Location", "")
                    if redirect_url:
                        logger.info(f"🔄 Redirección ({r.status_code}) → reenviando datos...")
                        r = self.session.post(
                            redirect_url,
                            json=body_data,
                            headers={"Content-Type": "application/json"},
                            timeout=timeout
                        )
                
                if 200 <= r.status_code < 300:
                    logger.info(f"✅ Datos enviados a '{sheet}' ({len(payload)} filas)")
                    return True
                
                logger.warning(f"❌ HTTP {r.status_code} al enviar a '{sheet}': {r.text[:100]}")
                
            except Exception as e:
                logger.warning(f"⚠️ Intento {intento + 1}/{retries} falló: {e}")
            
            if intento < retries - 1:
                time.sleep(5)
        
        return False

    def close(self) -> None:
        self.session.close()