# reset_monnet.py - ACTUALIZADO para nueva API (VERSIÓN COMPLETA)

import shutil
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from utils import cargar_json, guardar_json, logger
from monnet_api import MonnetAPI


def reset_y_reconstruir_monnet():
    """
    Limpia el histórico de Monnet y lo reconstruye con la nueva API
    Preserva las asignaciones cuando el título coincide
    """
    
    resultado_file = Path("resultado_incidentes.json")
    pendientes_file = Path("pendientes_incidentes.json")
    
    # 1. Crear backups
    logger.info("📦 Creando backups...")
    
    if resultado_file.exists():
        shutil.copy(resultado_file, resultado_file.with_suffix(".json.bak"))
        logger.info(f"   Backup de histórico: {resultado_file.with_suffix('.json.bak')}")
    
    if pendientes_file.exists():
        shutil.copy(pendientes_file, pendientes_file.with_suffix(".json.bak"))
        logger.info(f"   Backup de pendientes: {pendientes_file.with_suffix('.json.bak')}")
    
    # 2. Cargar datos actuales
    logger.info("📂 Cargando datos actuales...")
    historico = cargar_json(resultado_file, [])
    pendientes = cargar_json(pendientes_file, [])
    
    # 3. Crear mapa de asignaciones de Monnet antiguos por título
    monnet_antiguos = [inc for inc in historico if inc.get("Proveedor") == "Monnet"]
    mapa_asignaciones = {}
    for inc in monnet_antiguos:
        titulo = inc.get("Titulo", "").strip()
        if titulo:
            mapa_asignaciones[titulo] = inc.get("Asignado", "")
    
    logger.info(f"📋 {len(mapa_asignaciones)} títulos de Monnet antiguos mapeados")
    
    # 4. Eliminar TODOS los Monnet del histórico
    otros_proveedores = [inc for inc in historico if inc.get("Proveedor") != "Monnet"]
    monnet_eliminados = len(historico) - len(otros_proveedores)
    
    logger.info(f"🗑️ Eliminando {monnet_eliminados} incidentes de Monnet del histórico")
    
    # 5. Eliminar Monnet de pendientes
    pendientes_otros = [pend for pend in pendientes if pend.get("Proveedor") != "Monnet"]
    pendientes_eliminados = len(pendientes) - len(pendientes_otros)
    
    logger.info(f"🗑️ Eliminando {pendientes_eliminados} pendientes de Monnet")
    
    # 6. Extraer nuevos datos de Monnet con la nueva API
    logger.info("📡 Extrayendo nuevos datos de Monnet desde el 1 de enero 2026...")
    
    api = MonnetAPI()
    fecha_inicio = datetime(2026, 1, 1, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
    fecha_fin = datetime.now(ZoneInfo("UTC"))
    
    incidentes_api = api.obtener_historicos(fecha_inicio, fecha_fin)
    logger.info(f"   📊 Obtenidos {len(incidentes_api)} incidentes históricos")
    
    # 7. Convertir y preservar asignaciones por título
    nuevos_incidentes = []
    asignaciones_preservadas = 0
    
    for inc_api in incidentes_api:
        inc_dict = api.convertir_a_dict(inc_api)
        
        # Intentar preservar asignación por título
        titulo_nuevo = inc_dict.get("Titulo", "").strip()
        if titulo_nuevo in mapa_asignaciones and mapa_asignaciones[titulo_nuevo]:
            inc_dict["Asignado"] = mapa_asignaciones[titulo_nuevo]
            asignaciones_preservadas += 1
            logger.info(f"   🔄 Asignación preservada: '{titulo_nuevo[:40]}...' → {inc_dict['Asignado']}")
        
        nuevos_incidentes.append(inc_dict)
    
    logger.info(f"   ✅ Convertidos {len(nuevos_incidentes)} incidentes")
    logger.info(f"   🔄 Asignaciones preservadas: {asignaciones_preservadas}")
    
    # 8. Guardar archivos
    logger.info("💾 Guardando archivos...")
    historico_final = otros_proveedores + nuevos_incidentes
    guardar_json(resultado_file, historico_final)
    guardar_json(pendientes_file, pendientes_otros)
    
    # 9. Reporte final
    logger.info("=" * 60)
    logger.info("📊 RESET DE MONNET COMPLETADO:")
    logger.info(f"   🗑️ Eliminados: {monnet_eliminados} históricos + {pendientes_eliminados} pendientes")
    logger.info(f"   ✅ Nuevos Monnet: {len(nuevos_incidentes)} históricos")
    logger.info(f"   📜 Total histórico: {len(historico_final)} incidentes")
    logger.info("=" * 60)
    
    # 10. Mostrar primeros y últimos incidentes
    if nuevos_incidentes:
        logger.info("\n📅 NUEVOS INCIDENTES DE MONNET:")
        logger.info("\n🔹 PRIMEROS 3 (más viejos):")
        for inc in nuevos_incidentes[:3]:
            logger.info(f"   - ID: {inc.get('ID')} | {inc.get('Periodo', 'N/A')[:35]} | {inc.get('Titulo', '')[:50]}")
        
        logger.info("\n🔸 ÚLTIMOS 3 (más nuevos):")
        for inc in nuevos_incidentes[-3:]:
            logger.info(f"   - ID: {inc.get('ID')} | {inc.get('Periodo', 'N/A')[:35]} | {inc.get('Titulo', '')[:50]}")


def verificar_ids():
    """Verifica que los IDs de Monnet sean los nuevos (1, 2, 3...)"""
    resultado_file = Path("resultado_incidentes.json")
    historico = cargar_json(resultado_file, [])
    
    monnet_ids = [inc.get("ID") for inc in historico if inc.get("Proveedor") == "Monnet"]
    
    print(f"\n🔍 IDs de Monnet en el histórico: {len(monnet_ids)}")
    if monnet_ids:
        print(f"   IDs: {monnet_ids[:10]}...")
        print(f"   ¿Todos son numéricos? {all(str(id).isdigit() for id in monnet_ids)}")


if __name__ == "__main__":
    # Configurar logging
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    print("=" * 60)
    print("🔄 RESET DE MONNET - MIGRACIÓN A NUEVA API")
    print("=" * 60)
    print("\n⚠️  Esto ELIMINARÁ todos los incidentes de Monnet del histórico")
    print("   y los reemplazará con los datos de la NUEVA API.")
    print("\n   Los backups se crearán automáticamente.")
    print("   Archivos de backup: resultado_incidentes.json.bak y pendientes_incidentes.json.bak")
    print("\n" + "=" * 60)
    
    respuesta = input("\n¿Continuar? (sí/no): ").strip().lower()
    
    if respuesta in ["si", "sí", "s", "yes", "y"]:
        reset_y_reconstruir_monnet()
        verificar_ids()
        print("\n✅ Migración completada. Revisa los archivos para verificar.")
    else:
        print("❌ Operación cancelada.")