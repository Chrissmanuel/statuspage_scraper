from datetime import datetime
from typing import Any, Dict, List

from config import ESTADO_FILE, PENDIENTES_FILE
from utils import cargar_json, guardar_json, clave_incidente_dict, logger

class GestorEstado:
    @staticmethod
    def obtener_fecha_corte(proveedor: str) -> datetime:
        estado = cargar_json(ESTADO_FILE, {})
        ts = estado.get(proveedor)
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except ValueError:
                logger.warning(f"Fecha de corte inválida para {proveedor}, usando inicio de mes.")
        return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def actualizar_fecha_corte(proveedor: str) -> None:
        estado = cargar_json(ESTADO_FILE, {})
        estado[proveedor] = datetime.now().isoformat()
        guardar_json(ESTADO_FILE, estado)

    @staticmethod
    def gestionar_pendientes(pendientes_actuales: List[Dict[str, Any]]) -> None:
        pendientes_previos = cargar_json(PENDIENTES_FILE, [])
        pendientes_map = {clave_incidente_dict(p): p for p in pendientes_previos}
        for inc in pendientes_actuales:
            pendientes_map[clave_incidente_dict(inc)] = inc
        guardar_json(PENDIENTES_FILE, list(pendientes_map.values()))