import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
import unicodedata


from config import LOG_FILE, RESULTADOS_FILE, PENDIENTES_FILE

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
    
    # Función para extraer fecha de inicio del período_raw
    def extraer_fecha_inicio_desde_periodo(periodo_raw: str) -> str:
        """Extrae solo la fecha de inicio de un período raw"""
        if not periodo_raw:
            return ""
        
        # Limpiar zona horaria
        limpio = re.sub(r'\s*[-+]\d{2}:?\d{2}\s*$', '', periodo_raw)
        limpio = re.sub(r'\s*(GMT|UTC)[-+]\d{2}:?\d{2}\s*$', '', limpio, flags=re.IGNORECASE)
        
        # Si tiene rango (ej: "Jun 06, 02:00 AM - 07:00 AM")
        if " - " in limpio:
            inicio = limpio.split(" - ")[0].strip()
        else:
            inicio = limpio.strip()
        
        # Normalizar formato
        inicio = inicio.lower()
        inicio = re.sub(r'\s+', ' ', inicio)
        
        # Convertir a formato estándar: "mmm dd, hh:mm am/pm"
        # Ejemplo: "jun 06, 02:00 am"
        match = re.match(r'([a-z]{3})\s+(\d{1,2}),?\s+(\d{1,2}):(\d{2})\s*(am|pm)', inicio, re.IGNORECASE)
        if match:
            mes, dia, hora, minuto, ampm = match.groups()
            # Normalizar: primera letra mayúscula, resto minúscula
            mes = mes.capitalize()
            return f"{mes} {int(dia):02d}, {int(hora):02d}:{minuto} {ampm.upper()}"
        
        return inicio
    
    # Migrar histórico
    historico_migrados = 0
    historico_ya_nativos = 0
    historico_no_encontrados = []
    
    for inc in historico:
        if inc.get("Proveedor") != "Monnet":
            continue
        
        viejo_id = inc.get("ID", "")
        
        # Si ya es ID nativo (solo números), saltar
        if viejo_id.isdigit():
            historico_ya_nativos += 1
            continue
        
        # Extraer fecha de inicio del período_raw
        periodo_raw = inc.get("Periodo_Raw", inc.get("Periodo", ""))
        fecha_key = extraer_fecha_inicio_desde_periodo(periodo_raw)
        
        if fecha_key and fecha_key in mapping_fecha:
            nuevo_id = mapping_fecha[fecha_key]["id"]
            inc["ID"] = nuevo_id
            historico_migrados += 1
            logger.info(f"✅ Histórico | {fecha_key} | {viejo_id[:8]} → {nuevo_id} | {inc.get('Titulo', '')[:40]}")
        else:
            historico_no_encontrados.append({
                "titulo": inc.get("Titulo", ""),
                "fecha": fecha_key,
                "periodo_raw": periodo_raw
            })
    
    # Migrar pendientes
    pendientes_migrados = 0
    pendientes_ya_nativos = 0
    pendientes_no_encontrados = []
    
    for pend in pendientes:
        if pend.get("Proveedor") != "Monnet":
            continue
        
        viejo_id = pend.get("ID", "")
        
        if viejo_id.isdigit():
            pendientes_ya_nativos += 1
            continue
        
        periodo_raw = pend.get("Periodo_Raw", pend.get("Periodo", ""))
        fecha_key = extraer_fecha_inicio_desde_periodo(periodo_raw)
        
        if fecha_key and fecha_key in mapping_fecha:
            nuevo_id = mapping_fecha[fecha_key]["id"]
            pend["ID"] = nuevo_id
            pendientes_migrados += 1
            logger.info(f"📌 Pendiente | {fecha_key} | {viejo_id[:8]} → {nuevo_id} | {pend.get('Titulo', '')[:40]}")
        else:
            pendientes_no_encontrados.append({
                "titulo": pend.get("Titulo", ""),
                "fecha": fecha_key,
                "periodo_raw": periodo_raw
            })
    
    # Guardar cambios
    if historico_migrados > 0:
        guardar_json(RESULTADOS_FILE, historico)
        logger.info(f"💾 Histórico guardado: {historico_migrados} IDs migrados")
    
    if pendientes_migrados > 0:
        guardar_json(PENDIENTES_FILE, pendientes)
        logger.info(f"💾 Pendientes guardados: {pendientes_migrados} IDs migrados")
    
    # Reporte de no encontrados
    if historico_no_encontrados:
        logger.warning(f"⚠️ {len(historico_no_encontrados)} incidentes NO encontrados en API:")
        for item in historico_no_encontrados[:5]:  # Mostrar solo primeros 5
            logger.warning(f"   - {item['titulo'][:50]} | fecha: '{item['fecha']}'")
    
    if pendientes_no_encontrados:
        logger.warning(f"⚠️ {len(pendientes_no_encontrados)} pendientes NO encontrados en API:")
        for item in pendientes_no_encontrados[:5]:
            logger.warning(f"   - {item['titulo'][:50]} | fecha: '{item['fecha']}'")
    
    # Resumen final
    logger.info("=" * 60)
    logger.info(f"📊 MIGRACIÓN MONNET POR FECHA COMPLETADA:")
    logger.info(f"   📜 Histórico: {historico_migrados} migrados | {historico_ya_nativos} ya nativos | {len(historico_no_encontrados)} no encontrados")
    logger.info(f"   ⚠️ Pendientes: {pendientes_migrados} migrados | {pendientes_ya_nativos} ya nativos | {len(pendientes_no_encontrados)} no encontrados")
    logger.info("=" * 60)


def migrar_ids_monnet_por_fecha():
    """
    MIGRACIÓN POR FECHA DE INICIO NORMALIZADA A UTC
    """
    from monnet_api import MonnetAPI
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from config import PENDIENTES_FILE, RESULTADOS_FILE
    import re
    from typing import Optional
    
    logger.info("🔄 INICIANDO MIGRACIÓN DE MONNET POR FECHA (normalizada a UTC)...")
    
    # ==================== FUNCIONES AUXILIARES ====================
    
    def normalizar_fecha_utc(fecha_str: str) -> Optional[datetime]:
        """Convierte string ISO a datetime UTC"""
        if not fecha_str:
            return None
        try:
            if fecha_str.endswith('Z'):
                fecha_str = fecha_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(fecha_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(ZoneInfo("UTC"))
        except Exception as e:
            logger.debug(f"Error parseando fecha: {fecha_str} - {e}")
            return None
    
    def extraer_fecha_utc_desde_periodo(periodo_raw: str) -> Optional[datetime]:
        """Extrae fecha de inicio de periodo_raw y la convierte a UTC"""
        if not periodo_raw:
            return None
        
        # Detectar zona horaria
        tiene_offset_vet = '-04' in periodo_raw or '-04:00' in periodo_raw
        tiene_gmt = 'GMT' in periodo_raw.upper() or 'UTC' in periodo_raw.upper()
        
        # Limpiar y extraer inicio
        limpio = re.sub(r'\s*[-+]\d{2}:?\d{2}\s*$', '', periodo_raw)
        limpio = re.sub(r'\s*(GMT|UTC)[-+]\d{2}:?\d{2}\s*$', '', limpio, flags=re.IGNORECASE)
        
        if " - " in limpio:
            inicio_str = limpio.split(" - ")[0].strip()
        else:
            inicio_str = limpio.strip()
        
        # Parsear fecha/hora local
        match = re.match(r'([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{1,2}):(\d{2})\s*(am|pm)', inicio_str, re.IGNORECASE)
        if not match:
            # Intentar sin AM/PM
            match = re.match(r'([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{1,2}):(\d{2})', inicio_str, re.IGNORECASE)
            if not match:
                return None
        
        mes_str, dia, hora, minuto = match.groups()[:4]
        ampm = match.groups()[4] if len(match.groups()) > 4 else None
        
        # Convertir hora a 24h
        hora = int(hora)
        if ampm:
            if ampm.lower() == 'pm' and hora < 12:
                hora += 12
            if ampm.lower() == 'am' and hora == 12:
                hora = 0
        
        # Obtener año actual
        año = datetime.now().year
        meses = {"jan":1, "feb":2, "mar":3, "apr":4, "may":5, "jun":6,
                 "jul":7, "aug":8, "sep":9, "oct":10, "nov":11, "dec":12}
        mes = meses.get(mes_str.lower(), 1)
        
        # Ajustar año si es diciembre y estamos en enero
        if mes == 12 and datetime.now().month == 1:
            año -= 1
        
        # Crear datetime local
        dt_local = datetime(año, mes, int(dia), hora, int(minuto))
        
        # Determinar zona horaria
        if tiene_offset_vet:
            dt_local = dt_local.replace(tzinfo=ZoneInfo("America/Caracas"))
        elif tiene_gmt:
            dt_local = dt_local.replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt_local = dt_local.replace(tzinfo=ZoneInfo("America/Caracas"))
        
        # Convertir a UTC
        return dt_local.astimezone(ZoneInfo("UTC"))
    
    # ==================== FIN FUNCIONES AUXILIARES ====================
    
    # Cargar datos existentes
    historico = cargar_json(RESULTADOS_FILE, [])
    pendientes = cargar_json(PENDIENTES_FILE, [])
    
    if not historico and not pendientes:
        logger.info("📭 No hay datos para migrar")
        return
    
    # Obtener datos de API
    api = MonnetAPI()
    hoy = datetime.now(ZoneInfo("UTC"))
    fecha_inicio = hoy - timedelta(days=90)
    
    logger.info(f"📡 Obteniendo datos de API desde {fecha_inicio.date()}...")
    
    try:
        incidentes_api = api.obtener_historicos(fecha_inicio, hoy)
        pendientes_api = api.obtener_pendientes()
        todos_api = incidentes_api + pendientes_api
        logger.info(f"📊 API devolvió {len(todos_api)} incidentes")
    except Exception as e:
        logger.error(f"❌ Error obteniendo datos de API: {e}")
        return
    
    # Crear mapping con fecha UTC normalizada
    mapping_fecha_utc = {}
    
    for inc_api in todos_api:
        start_time = inc_api.get("start_time", "")
        if not start_time:
            continue
        
        fecha_utc = normalizar_fecha_utc(start_time)
        
        if fecha_utc:
            fecha_key = fecha_utc.strftime("%Y-%m-%d %H:%M:%S")
            mapping_fecha_utc[fecha_key] = {
                "id": str(inc_api["id"]),
                "titulo": inc_api.get("title", ""),
                "fecha_utc": fecha_utc
            }
    
    logger.info(f"📋 Mapping creado con {len(mapping_fecha_utc)} fechas UTC")
    
    # Migrar histórico
    historico_migrados = 0
    historico_ya_nativos = 0
    historico_no_encontrados = []
    
    for inc in historico:
        if inc.get("Proveedor") != "Monnet":
            continue
        
        viejo_id = inc.get("ID", "")
        
        if viejo_id.isdigit():
            historico_ya_nativos += 1
            continue
        
        periodo_raw = inc.get("Periodo_Raw", inc.get("Periodo", ""))
        fecha_utc = extraer_fecha_utc_desde_periodo(periodo_raw)
        
        if fecha_utc:
            fecha_key = fecha_utc.strftime("%Y-%m-%d %H:%M:%S")
            
            if fecha_key in mapping_fecha_utc:
                nuevo_id = mapping_fecha_utc[fecha_key]["id"]
                inc["ID"] = nuevo_id
                historico_migrados += 1
                logger.info(f"✅ Histórico | {fecha_utc.strftime('%b %d, %H:%M UTC')} | {viejo_id[:8]} → {nuevo_id}")
            else:
                # Buscar por margen de ±2 minutos
                encontrado = False
                for api_key, api_data in mapping_fecha_utc.items():
                    api_dt = api_data["fecha_utc"]
                    diff = abs((fecha_utc - api_dt).total_seconds())
                    if diff <= 120:
                        inc["ID"] = api_data["id"]
                        historico_migrados += 1
                        encontrado = True
                        logger.info(f"✅ Histórico (tolerancia) | {fecha_utc.strftime('%H:%M UTC')} ~ {api_dt.strftime('%H:%M UTC')} | {viejo_id[:8]} → {api_data['id']}")
                        break
                
                if not encontrado:
                    historico_no_encontrados.append({
                        "titulo": inc.get("Titulo", ""),
                        "fecha_local": periodo_raw,
                        "fecha_utc": fecha_utc.strftime("%Y-%m-%d %H:%M:%S")
                    })
        else:
            historico_no_encontrados.append({
                "titulo": inc.get("Titulo", ""),
                "fecha_local": periodo_raw,
                "fecha_utc": "No se pudo parsear"
            })
    
    # Migrar pendientes
    pendientes_migrados = 0
    pendientes_ya_nativos = 0
    pendientes_no_encontrados = []
    
    for pend in pendientes:
        if pend.get("Proveedor") != "Monnet":
            continue
        
        viejo_id = pend.get("ID", "")
        
        if viejo_id.isdigit():
            pendientes_ya_nativos += 1
            continue
        
        periodo_raw = pend.get("Periodo_Raw", pend.get("Periodo", ""))
        fecha_utc = extraer_fecha_utc_desde_periodo(periodo_raw)
        
        if fecha_utc:
            fecha_key = fecha_utc.strftime("%Y-%m-%d %H:%M:%S")
            
            if fecha_key in mapping_fecha_utc:
                nuevo_id = mapping_fecha_utc[fecha_key]["id"]
                pend["ID"] = nuevo_id
                pendientes_migrados += 1
                logger.info(f"📌 Pendiente | {fecha_utc.strftime('%b %d, %H:%M UTC')} | {viejo_id[:8]} → {nuevo_id}")
            else:
                # Buscar por tolerancia
                encontrado = False
                for api_key, api_data in mapping_fecha_utc.items():
                    api_dt = api_data["fecha_utc"]
                    diff = abs((fecha_utc - api_dt).total_seconds())
                    if diff <= 120:
                        pend["ID"] = api_data["id"]
                        pendientes_migrados += 1
                        encontrado = True
                        logger.info(f"📌 Pendiente (tolerancia) | {viejo_id[:8]} → {api_data['id']}")
                        break
                
                if not encontrado:
                    pendientes_no_encontrados.append({
                        "titulo": pend.get("Titulo", ""),
                        "fecha_local": periodo_raw,
                        "fecha_utc": fecha_utc.strftime("%Y-%m-%d %H:%M:%S")
                    })
        else:
            pendientes_no_encontrados.append({
                "titulo": pend.get("Titulo", ""),
                "fecha_local": periodo_raw,
                "fecha_utc": "No se pudo parsear"
            })
    
    # Guardar cambios
    if historico_migrados > 0:
        guardar_json(RESULTADOS_FILE, historico)
        logger.info(f"💾 Histórico guardado: {historico_migrados} IDs migrados")
    
    if pendientes_migrados > 0:
        guardar_json(PENDIENTES_FILE, pendientes)
        logger.info(f"💾 Pendientes guardados: {pendientes_migrados} IDs migrados")
    
    # Reporte de no encontrados
    if historico_no_encontrados:
        logger.warning(f"⚠️ {len(historico_no_encontrados)} incidentes NO encontrados en API:")
        for item in historico_no_encontrados[:10]:
            logger.warning(f"   - {item['titulo'][:50]} | fecha: '{item['fecha_local']}' -> UTC: {item['fecha_utc']}")
    
    if pendientes_no_encontrados:
        logger.warning(f"⚠️ {len(pendientes_no_encontrados)} pendientes NO encontrados en API:")
        for item in pendientes_no_encontrados[:5]:
            logger.warning(f"   - {item['titulo'][:50]} | fecha: '{item['fecha_local']}'")
    
    # Resumen
    logger.info("=" * 60)
    logger.info(f"📊 MIGRACIÓN MONNET POR FECHA COMPLETADA:")
    logger.info(f"   📜 Histórico: {historico_migrados} migrados | {historico_ya_nativos} ya nativos | {len(historico_no_encontrados)} no encontrados")
    logger.info(f"   ⚠️ Pendientes: {pendientes_migrados} migrados | {pendientes_ya_nativos} ya nativos | {len(pendientes_no_encontrados)} no encontrados")
    logger.info("=" * 60)