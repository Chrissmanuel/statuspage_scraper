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

def clave_incidente_dict(x: Dict[str, Any]) -> Tuple[str, str]:
    """
    Retorna una tupla con Proveedor e ID para deduplicar correctamente.
    Esto es más confiable que usar Titulo+Periodo que pueden variar.
    
    Usada en:
    - main.py: fusionar_historico() para deduplicar histórico
    - state_manager.py: gestionar_pendientes() para deduplicar pendientes
    """
    return (
        str(x.get("Proveedor", "")).strip(),
        str(x.get("ID", "")).strip(),
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


def migrar_ids_monnet():
    """
    Migra los IDs de los incidentes de Monnet (freshstatus) al nuevo formato:
    - Prioriza el ID real de Freshstatus (ej. fs_1607716) si se puede extraer del Periodo_Raw
    - Si no, usa el hash de título + fecha_inicio normalizada
    """
    from scraper import IncidentScraper
    from config import RESULTADOS_FILE, PENDIENTES_FILE
    
    historico = cargar_json(RESULTADOS_FILE, [])
    if not historico:
        logger.info("📭 No hay histórico para migrar")
        return
    
    modificado = False
    mapping_viejo_nuevo = {}
    
    for inc in historico:
        if inc.get("Proveedor") != "Monnet":
            continue
        
        viejo_id = inc.get("ID", "")
        titulo = inc.get("Titulo", "")
        periodo_raw = inc.get("Periodo_Raw", inc.get("Periodo", ""))
        
        # Intentar obtener ID real de Freshstatus desde Periodo_Raw
        # Nota: En el histórico antiguo no guardamos el href, así que no podemos obtener el ID real
        # Solo podemos usar el fallback de título+fecha_inicio
        fecha_inicio = IncidentScraper.extraer_fecha_inicio(periodo_raw)
        if not fecha_inicio:
            continue
        
        nuevo_id = IncidentScraper.generar_id_unico(titulo, fecha_inicio)
        
        if viejo_id != nuevo_id:
            inc["ID"] = nuevo_id
            modificado = True
            mapping_viejo_nuevo[viejo_id] = nuevo_id
            logger.debug(f"  {titulo[:50]}... {viejo_id[:8]} → {nuevo_id[:8]}")
    
    if modificado:
        guardar_json(RESULTADOS_FILE, historico)
        logger.info(f"✅ Migrados {len(mapping_viejo_nuevo)} incidentes de Monnet en histórico")
        
        # Migrar pendientes
        pendientes = cargar_json(PENDIENTES_FILE, [])
        pendientes_migrados = 0
        for pend in pendientes:
            if pend.get("Proveedor") == "Monnet":
                viejo = pend.get("ID", "")
                if viejo in mapping_viejo_nuevo:
                    pend["ID"] = mapping_viejo_nuevo[viejo]
                    pendientes_migrados += 1
        guardar_json(PENDIENTES_FILE, pendientes)
        logger.info(f"✅ Migrados {pendientes_migrados} pendientes de Monnet")
    else:
        logger.info("📭 No se necesitaron migraciones para Monnet")