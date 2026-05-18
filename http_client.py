import time
from typing import Any
import requests
from requests import Session
from config import HTTP_RETRIES, HTTP_TIMEOUT
from utils import logger


class HttpClient:
    def __init__(self) -> None:
        self.session = Session()

    def post_json(self, url: str, payload: Any, retries: int = HTTP_RETRIES, timeout: int = HTTP_TIMEOUT) -> bool:
    for intento in range(retries):
        try:
            # payload YA viene con la estructura correcta: {"sheet": "...", "data": [...]}
            r = self.session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            
            if 200 <= r.status_code < 300:
                sheet_name = payload.get("sheet", "History")
                data_count = len(payload.get("data", []))
                logger.info(f"✅ Datos enviados a '{sheet_name}' ({data_count} filas)")
                return True
            
            logger.warning(f"❌ HTTP {r.status_code}: {r.text[:100]}")
            
        except Exception as e:
            logger.warning(f"⚠️ Intento {intento + 1}/{retries} falló: {e}")
        
        if intento < retries - 1:
            time.sleep(5)
    
    return False