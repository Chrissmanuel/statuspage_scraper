# reset_monnet.py
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from monnet_api import MonnetAPI
from utils import guardar_json, logger, cargar_json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reset_y_reconstruir_monnet():
    """
    Borra todos los incidentes de Monnet y los vuelve a extraer desde el 1 de mayo 2026
    ORDENADOS CRONOLÓGICAMENTE (viejos primero, nuevos al final)
    """
    
    # Archivos a modificar
    resultado_file = Path("resultado_incidentes.json")
    pendientes_file = Path("pendientes_incidentes.json")
    
    # 1. Crear backups
    logger.info("📦 Creando backups...")
    
    if resultado_file.exists():
        backup_resultado = resultado_file.with_suffix(".json.bak")
        import shutil
        shutil.copy(resultado_file, backup_resultado)
        logger.info(f"   Backup de histórico: {backup_resultado}")
    
    if pendientes_file.exists():
        backup_pendientes = pendientes_file.with_suffix(".json.bak")
        shutil.copy(pendientes_file, backup_pendientes)
        logger.info(f"   Backup de pendientes: {backup_pendientes}")
    
    # 2. Cargar datos actuales
    historico = cargar_json(resultado_file, [])
    pendientes = cargar_json(pendientes_file, [])
    
    # 3. Filtrar: eliminar todos los Monnet del histórico
    original_count = len(historico)
    otros_proveedores = [inc for inc in historico if inc.get("Proveedor") != "Monnet"]
    monnet_eliminados = original_count - len(otros_proveedores)
    
    logger.info(f"🗑️ Eliminando {monnet_eliminados} incidentes de Monnet del histórico")
    
    # 4. Eliminar Monnet de pendientes
    pendientes_otros = [pend for pend in pendientes if pend.get("Proveedor") != "Monnet"]
    pendientes_eliminados = len(pendientes) - len(pendientes_otros)
    
    logger.info(f"🗑️ Eliminando {pendientes_eliminados} pendientes de Monnet")
    
    # 5. Extraer nuevos datos de Monnet desde el 1 de mayo 2026
    logger.info("📡 Extrayendo nuevos datos de Monnet desde el 1 de mayo 2026...")
    
    api = MonnetAPI()
    
    # Fecha de inicio: 1 de mayo 2026 a las 00:00 UTC
    fecha_inicio = datetime(2026, 5, 1, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
    fecha_fin = datetime.now(ZoneInfo("UTC"))
    
    logger.info(f"   Desde: {fecha_inicio.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   Hasta: {fecha_fin.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Obtener históricos
        incidentes_api = api.obtener_historicos(fecha_inicio, fecha_fin)
        logger.info(f"   📊 Obtenidos {len(incidentes_api)} incidentes históricos")
        
        # Obtener pendientes
        pendientes_api = api.obtener_pendientes()
        logger.info(f"   ⚠️ Obtenidos {len(pendientes_api)} incidentes pendientes")
        
        # Convertir al formato interno
        nuevos_incidentes = []
        for inc_api in incidentes_api:
            inc_dict = api.convertir_a_dict(inc_api)
            nuevos_incidentes.append(inc_dict)
        
        # 🔥 ORDENAR CRONOLÓGICAMENTE (viejos primero, nuevos al final)
        def obtener_fecha_orden(incidente):
            """Extrae la fecha de inicio para ordenar"""
            start_time = incidente.get("start_time", "")
            if start_time:
                try:
                    if start_time.endswith('Z'):
                        start_time = start_time.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(start_time)
                    return dt
                except:
                    pass
            
            # Fallback: usar Periodo_Raw
            periodo_raw = incidente.get("Periodo_Raw", incidente.get("Periodo", ""))
            # Intentar extraer fecha del texto
            import re
            match = re.search(r'([A-Za-z]{3})\s+(\d{1,2})', periodo_raw)
            if match:
                # Orden aproximado por mes y día
                meses = {"jan":1, "feb":2, "mar":3, "apr":4, "may":5, "jun":6,
                        "jul":7, "aug":8, "sep":9, "oct":10, "nov":11, "dec":12}
                mes = meses.get(match.group(1).lower(), 1)
                dia = int(match.group(2))
                return datetime(2026, mes, dia)
            
            return datetime.min
        
        # Ordenar de más viejo a más nuevo
        nuevos_incidentes.sort(key=obtener_fecha_orden)
        
        logger.info(f"✅ Convertidos y ordenados {len(nuevos_incidentes)} incidentes (viejos → nuevos)")
        
        # Mostrar primeros y últimos para verificar
        if nuevos_incidentes:
            primero = nuevos_incidentes[0]
            ultimo = nuevos_incidentes[-1]
            logger.info(f"   📅 Primer incidente: {primero.get('Periodo', 'N/A')[:30]} - {primero.get('Titulo', '')[:40]}")
            logger.info(f"   📅 Último incidente: {ultimo.get('Periodo', 'N/A')[:30]} - {ultimo.get('Titulo', '')[:40]}")
        
        # Convertir pendientes (no se ordenan, son activos)
        nuevos_pendientes = []
        for pend_api in pendientes_api:
            pend_dict = api.convertir_a_dict(pend_api)
            pend_dict["Pendiente"] = "SI"
            nuevos_pendientes.append(pend_dict)
        
        logger.info(f"✅ Convertidos {len(nuevos_pendientes)} pendientes")
        
    except Exception as e:
        logger.error(f"❌ Error extrayendo datos de Monnet: {e}")
        logger.info("🔄 Restaurando backups...")
        
        # Restaurar backups
        if backup_resultado.exists():
            shutil.copy(backup_resultado, resultado_file)
        if backup_pendientes.exists():
            shutil.copy(backup_pendientes, pendientes_file)
        
        return
    
    # 6. Unir con otros proveedores
    # Para otros proveedores, también debemos asegurar el orden cronológico
    # Extraer fechas de otros proveedores para ordenarlos también
    def obtener_fecha_otros(incidente):
        """Extrae fecha de incidentes de otros proveedores"""
        periodo = incidente.get("Periodo", "")
        try:
            # Intentar extraer fecha del período
            import re
            match = re.search(r'([A-Za-z]{3})\s+(\d{1,2})', periodo)
            if match:
                meses = {"jan":1, "feb":2, "mar":3, "apr":4, "may":5, "jun":6,
                        "jul":7, "aug":8, "sep":9, "oct":10, "nov":11, "dec":12}
                mes = meses.get(match.group(1).lower(), 1)
                dia = int(match.group(2))
                return datetime(2026, mes, dia)
        except:
            pass
        return datetime.min
    
    # Ordenar otros proveedores también
    otros_proveedores.sort(key=obtener_fecha_otros)
    
    # Combinar todo
    historico_final = otros_proveedores + nuevos_incidentes
    pendientes_final = pendientes_otros + nuevos_pendientes
    
    # 7. Guardar archivos
    guardar_json(resultado_file, historico_final)
    guardar_json(pendientes_file, pendientes_final)
    
    # 8. Reporte final
    logger.info("=" * 60)
    logger.info("📊 RESET DE MONNET COMPLETADO:")
    logger.info(f"   🗑️ Eliminados: {monnet_eliminados} históricos + {pendientes_eliminados} pendientes")
    logger.info(f"   ✅ Nuevos Monnet: {len(nuevos_incidentes)} históricos + {len(nuevos_pendientes)} pendientes")
    logger.info(f"   📜 Total histórico: {len(historico_final)} incidentes")
    logger.info(f"   ⚠️ Total pendientes: {len(pendientes_final)} incidentes")
    logger.info("=" * 60)
    
    # 9. Mostrar distribución temporal
    logger.info("\n📅 DISTRIBUCIÓN TEMPORAL (primeros y últimos 5):")
    logger.info("\n🔹 PRIMEROS 5 (más viejos):")
    for inc in historico_final[:5]:
        logger.info(f"   - {inc.get('Periodo', 'N/A')[:35]} | {inc.get('Proveedor')} | {inc.get('Titulo', '')[:40]}")
    
    logger.info("\n🔸 ÚLTIMOS 5 (más nuevos):")
    for inc in historico_final[-5:]:
        logger.info(f"   - {inc.get('Periodo', 'N/A')[:35]} | {inc.get('Proveedor')} | {inc.get('Titulo', '')[:40]}")


def verificar_orden_cronologico():
    """Verifica que el histórico esté correctamente ordenado"""
    resultado_file = Path("resultado_incidentes.json")
    
    with open(resultado_file, "r", encoding="utf-8") as f:
        incidentes = json.load(f)
    
    import re
    from datetime import datetime
    
    meses = {"jan":1, "feb":2, "mar":3, "apr":4, "may":5, "jun":6,
             "jul":7, "aug":8, "sep":9, "oct":10, "nov":11, "dec":12}
    
    def extraer_fecha(periodo):
        match = re.search(r'([A-Za-z]{3})\s+(\d{1,2})', periodo)
        if match:
            mes = meses.get(match.group(1).lower(), 1)
            dia = int(match.group(2))
            return (mes, dia)
        return (99, 99)
    
    # Verificar orden
    orden_correcto = True
    for i in range(1, len(incidentes)):
        fecha_prev = extraer_fecha(incidentes[i-1].get("Periodo", ""))
        fecha_curr = extraer_fecha(incidentes[i].get("Periodo", ""))
        
        if fecha_prev > fecha_curr:
            orden_correcto = False
            print(f"\n⚠️ Problema de orden en índice {i}:")
            print(f"   Anterior: {incidentes[i-1].get('Periodo', 'N/A')} - {incidentes[i-1].get('Proveedor')}")
            print(f"   Actual:   {incidentes[i].get('Periodo', 'N/A')} - {incidentes[i].get('Proveedor')}")
            break
    
    if orden_correcto:
        print("\n✅ El histórico está correctamente ordenado (viejos → nuevos)")


if __name__ == "__main__":
    # Primero, hacer el reset
    reset_y_reconstruir_monnet()
    
    # Luego, verificar el orden
    verificar_orden_cronologico()
    
    print("\n💡 Para usar el nuevo histórico, ejecuta main.py normalmente")