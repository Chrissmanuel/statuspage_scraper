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
    🟢 MIGRACIÓN SEGURA: Convierte IDs viejos de Monnet al nuevo formato.
    
    ESTRATEGIA DE DEDUPLICACIÓN:
    - Viejos IDs: Basados en (Proveedor, Titulo, Periodo_Raw) - PUEDE VARIAR
    - Nuevos IDs: Hash MD5 de (titulo + fecha_inicio) - ESTABLE E INMUTABLE
    
    Esta función preserva los datos históricos sin perder información.
    
    ⚠️ EJECUTAR UNA SOLA VEZ al cambiar la estrategia de IDs.
    """
    from scraper import IncidentScraper
    from config import PENDIENTES_FILE, RESULTADOS_FILE
    import hashlib
    
    logger.info("🔄 INICIANDO MIGRACIÓN DE IDs MONNET...")
    
    # 1. CARGAR DATOS HISTÓRICOS
    historico = cargar_json(RESULTADOS_FILE, [])
    if not historico:
        logger.info("📭 No hay histórico para migrar")
        return
    
    # Contar cuántos incidentes de Monnet hay
    monnet_count = sum(1 for inc in historico if inc.get("Proveedor") == "Monnet")
    if monnet_count == 0:
        logger.info("📭 No hay incidentes de Monnet en el histórico")
        return
    
    logger.info(f"📊 Encontrados {monnet_count} incidentes de Monnet para procesar")
    
    modificado = False
    mapping_viejo_nuevo = {}
    fallos = []
    
    # 2. PROCESAR CADA INCIDENTE DE MONNET
    for idx, inc in enumerate(historico):
        if inc.get("Proveedor") != "Monnet":
            continue
        
        viejo_id = inc.get("ID", "")
        titulo = inc.get("Titulo", "").strip()
        periodo_raw = inc.get("Periodo_Raw", inc.get("Periodo", "")).strip()
        
        # ✅ VALIDACIÓN: Asegurarse que tenemos datos básicos
        if not viejo_id or not titulo:
            logger.warning(f"⚠️ Incidente sin ID o Título válido: {inc}")
            fallos.append(("sin_datos", inc))
            continue
        
        try:
            # 🔴 EXTRAER FECHA DE INICIO (es la clave para generar el nuevo ID)
            fecha_inicio = IncidentScraper.extraer_fecha_inicio(periodo_raw)
            
            if not fecha_inicio or fecha_inicio == "":
                logger.warning(f"⚠️ No se pudo extraer fecha_inicio de: {periodo_raw[:50]}")
                fallos.append(("sin_fecha", inc))
                continue
            
            # 🟢 GENERAR NUEVO ID CON LA MISMA LÓGICA QUE scraper.py
            # IMPORTANTE: Debe coincidir EXACTAMENTE con scraper.py línea 268
            titulo_limpio = " ".join(titulo.strip().split())  # Normalizar espacios
            fecha_limpia = " ".join(fecha_inicio.strip().split())  # Normalizar espacios
            base = f"{titulo_limpio.lower()}_{fecha_limpia.lower()}"
            nuevo_id = hashlib.md5(base.encode("utf-8")).hexdigest()
            
            # ✅ COMPARAR Y ACTUALIZAR SI ES DIFERENTE
            if viejo_id != nuevo_id:
                inc["ID"] = nuevo_id
                modificado = True
                mapping_viejo_nuevo[viejo_id] = nuevo_id
                logger.info(f"✅ {idx+1}/{monnet_count} | {titulo[:40]}... | {viejo_id[:8]} → {nuevo_id[:8]}")
            else:
                logger.info(f"ℹ️ {idx+1}/{monnet_count} | {titulo[:40]}... | ID ya es correcto ({nuevo_id[:8]})")
        
        except Exception as e:
            logger.error(f"❌ Error procesando {titulo[:40]}: {e}")
            fallos.append(("error", inc, str(e)))
            continue
    
    # 3. GUARDAR CAMBIOS EN HISTÓRICO
    if modificado:
        try:
            guardar_json(RESULTADOS_FILE, historico)
            logger.info(f"✅ Histórico guardado: {len(mapping_viejo_nuevo)} IDs migrados")
        except Exception as e:
            logger.error(f"❌ Error guardando histórico: {e}")
            return
    else:
        logger.info("ℹ️ Todos los IDs de Monnet ya están en el nuevo formato")
    
    # 4. MIGRAR PENDIENTES
    try:
        pendientes = cargar_json(PENDIENTES_FILE, [])
        pendientes_migrados = 0
        pendientes_sin_cambio = 0
        
        for pend in pendientes:
            if pend.get("Proveedor") != "Monnet":
                continue
            
            viejo_pend_id = pend.get("ID", "")
            
            # Si el viejo ID está en el mapping, actualizar
            if viejo_pend_id in mapping_viejo_nuevo:
                nuevo_pend_id = mapping_viejo_nuevo[viejo_pend_id]
                pend["ID"] = nuevo_pend_id
                pendientes_migrados += 1
                logger.info(f"📌 Pendiente migrado: {viejo_pend_id[:8]} → {nuevo_pend_id[:8]}")
            else:
                # Si NO está en el mapping, probablemente ya tiene el nuevo ID
                pendientes_sin_cambio += 1
        
        if pendientes:
            guardar_json(PENDIENTES_FILE, pendientes)
            logger.info(f"✅ Pendientes: {pendientes_migrados} migrados, {pendientes_sin_cambio} ya actualizados")
        else:
            logger.info("ℹ️ No hay pendientes para migrar")
    
    except Exception as e:
        logger.error(f"❌ Error migrando pendientes: {e}")
        return
    
    # 5. REPORTE FINAL
    logger.info("=" * 60)
    if fallos:
        logger.warning(f"⚠️ MIGRACIONES CON FALLOS:")
        for tipo_fallo, *datos in fallos:
            if tipo_fallo == "sin_datos":
                logger.warning(f"   - Sin datos básicos: {datos[0]}")
            elif tipo_fallo == "sin_fecha":
                logger.warning(f"   - Sin fecha extraída: {datos[0].get('Titulo', 'N/A')[:40]}")
            elif tipo_fallo == "error":
                logger.warning(f"   - Error: {datos[1]}")
    
    logger.info(f"📊 RESUMEN FINAL:")
    logger.info(f"   ✅ IDs migrados: {len(mapping_viejo_nuevo)}")
    logger.info(f"   ⚠️ Fallos/omisiones: {len(fallos)}")
    logger.info(f"   📌 Pendientes migrados: {pendientes_migrados}")
    logger.info("=" * 60)
    logger.info("✨ MIGRACIÓN COMPLETADA")