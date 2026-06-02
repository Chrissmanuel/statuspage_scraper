import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
import unicodedata


from config import LOG_FILE, RESULTADOS_FILE

def configurar_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

logger = logging.getLogger(__name__)

def normalizar_texto(texto: str | None) -> str:
    if not texto:
        return ""
    # Eliminar acentos y caracteres especiales
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII')
    return re.sub(r"\s+", " ", texto).strip()

def cargar_json(ruta: Path, default: Any) -> Any:
    if not ruta.exists():
        return default
    try:
        contenido = ruta.read_text(encoding="utf-8").strip()
        if not contenido:
            return default
        return json.loads(contenido)
    except Exception as e:
        logger.warning(f"No se pudo leer {ruta.name}: {e}")
        return default

def guardar_json(ruta: Path, datos: Any) -> None:
    with ruta.open("w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)

def clave_incidente_dict(x: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(x.get("Proveedor", "")).strip(),
        str(x.get("Titulo", "")).strip(),
        str(x.get("Periodo", "")).strip(),
    )

def _obtener_ultimo_asignado_desde_historico() -> str | None:
    """
    Lee el archivo resultado_incidentes.json y devuelve el Asignado del último incidente.
    Si no hay histórico o no tiene asignado, retorna None.
    """
    historico = cargar_json(RESULTADOS_FILE, [])
    if not historico:
        return None
    # El último elemento de la lista (asumimos orden cronológico)
    ultimo = historico[-1]
    return ultimo.get("Asignado")

def distribuir_asignados(incidentes: List[Dict[str, Any]], asignados: List[str]) -> List[Dict[str, Any]]:
    """
    Distribuye los incidentes de forma equitativa y continua usando el último asignado
    del histórico como punto de partida. Si no hay histórico, empieza por el primero.
    
    Los incidentes que ya tienen un asignado válido se respetan, y la rotación continúa
    desde el último asignado de la lista proporcionada (o desde el histórico).
    """
    if not asignados:
        return incidentes

    # 1. Determinar el "último asignado" conocido
    ultimo_asignado = _obtener_ultimo_asignado_desde_historico()
    
    # Si no hay histórico, empezamos con el primer asignado (índice -1 para que el próximo sea 0)
    if ultimo_asignado is None or ultimo_asignado not in asignados:
        idx_ultimo = -1
    else:
        idx_ultimo = asignados.index(ultimo_asignado)
    
    # El siguiente índice a usar es el siguiente al último (circular)
    siguiente_idx = (idx_ultimo + 1) % len(asignados)
    
    # 2. Contar asignaciones existentes en el lote actual (para no romper balance)
    conteo = {nombre: 0 for nombre in asignados}
    for inc in incidentes:
        if inc.get("Asignado") in conteo:
            conteo[inc["Asignado"]] += 1
    
    # 3. Asignar a los que no tienen asignado (o tienen uno inválido)
    idx_actual = siguiente_idx
    for inc in incidentes:
        if not inc.get("Asignado") or inc["Asignado"] not in conteo:
            asignado = asignados[idx_actual % len(asignados)]
            inc["Asignado"] = asignado
            conteo[asignado] = conteo.get(asignado, 0) + 1
            idx_actual += 1
    
    return incidentes

