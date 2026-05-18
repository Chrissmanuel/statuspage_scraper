import time
from typing import Any

import requests
from requests import Session

from config import HTTP_RETRIES, HTTP_TIMEOUT
from utils import logger

def post_json(self, url: str, payload: Any, sheet: str = "History", retries: int = HTTP_RETRIES, timeout: int = HTTP_TIMEOUT) -> bool:
        for intento in range(retries):
            try:
                body_data = {"sheet": sheet, "data": payload}
                
                # 1. Hacemos el POST inicial pero bloqueamos el redireccionamiento automático (allow_redirects=False)
                r = self.session.post(
                    url,
                    json=body_data,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                    allow_redirects=False  # <--- CRUCIAL PARA GOOGLE APPS SCRIPT
                )
                
                # 2. Si Google responde con la redirección (302), perseguimos la URL real con los datos
                if r.status_code == 302:
                    redirect_url = r.headers.get('Location')
                    logger.info(f"Redirección detectada (302). Reenviando datos a la URL final...")
                    
                    # Hacemos el POST definitivo a la URL final manteniendo el JSON
                    r = self.session.post(
                        redirect_url,
                        json=body_data,
                        headers={"Content-Type": "application/json"},
                        timeout=timeout
                    )

                # 3. Validamos el éxito del envío final
                if 200 <= r.status_code < 300:
                    logger.info(f"✨ Datos guardados con éxito en la hoja '{sheet}'. Respuesta de Google: {r.text}")
                    return True
                
                logger.warning(f"HTTP {r.status_code} al enviar datos a {sheet}. Respuesta: {r.text}")
                
            except Exception as e:
                logger.warning(f"⚠️ Intento {intento + 1}/{retries} falló: {e}")
            
            time.sleep(5)
        return False