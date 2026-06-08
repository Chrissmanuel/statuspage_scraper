from datetime import datetime, timedelta
from typing import Any, Dict, List

from config import PENDIENTES_FILE
from utils import cargar_json, guardar_json, clave_incidente_dict, logger

class GestorEstado:
    @staticmethod
    def obtener_fecha_corte(proveedor: str) -> datetime:
        """Siempre retorna hace 1 hora, sin importar el proveedor"""
        hace_1_hora = datetime.now() - timedelta(hours=1)
        logger.info(f"📅 Usando fecha de corte: hace 1 hora ({hace_1_hora.strftime('%Y-%m-%d %H:%M:%S')}) para {proveedor}")
        return hace_1_hora

    @staticmethod
    def actualizar_fecha_corte(proveedor: str) -> None:
        """No hace nada - ya no necesitamos guardar el estado"""
        pass

    @staticmethod
    def gestionar_pendientes(pendientes_actuales: List[Dict[str, Any]]) -> None:
        pendientes_previos = cargar_json(PENDIENTES_FILE, [])
        pendientes_map = {clave_incidente_dict(p): p for p in pendientes_previos}
        for inc in pendientes_actuales:
            pendientes_map[clave_incidente_dict(inc)] = inc
        guardar_json(PENDIENTES_FILE, list(pendientes_map.values()))