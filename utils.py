import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
import unicodedata


from config import LOG_FILE

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

def distribuir_asignados(incidentes: List[Dict[str, Any]], asignados: List[str]) -> List[Dict[str, Any]]:
    """
    Distribuye los incidentes equitativamente entre la lista de asignados.
    """
    if not asignados:
        return incidentes
    
    # Contar cuántos tiene cada uno actualmente (si ya vienen con asignación)
    conteo = {nombre: 0 for nombre in asignados}
    for inc in incidentes:
        if inc.get("Asignado") in conteo:
            conteo[inc["Asignado"]] += 1
    
    # Asignar a los que no tienen asignado
    for inc in incidentes:
        if not inc.get("Asignado") or inc["Asignado"] not in conteo:
            # Buscar el que menos tiene
            asignado = min(conteo, key=conteo.get)
            inc["Asignado"] = asignado
            conteo[asignado] += 1
    
    return incidentes