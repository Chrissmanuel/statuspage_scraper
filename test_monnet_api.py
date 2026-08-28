# test_new_monnet_api.py
from monnet_api import MonnetAPI
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def test_nueva_api():
    api = MonnetAPI()
    
    # Probar históricos
    end_date = datetime.now(ZoneInfo("UTC"))
    start_date = end_date - timedelta(days=90)
    
    print("📊 Obteniendo incidentes históricos...")
    incidentes = api.obtener_historicos(start_date, end_date)
    print(f"✅ Total: {len(incidentes)}")
    
    for inc in incidentes[:3]:
        print(f"\n--- ID: {inc.get('id')} | {inc.get('human_display_id')} ---")
        print(f"Título: {inc.get('title')}")
        print(f"Inicio: {inc.get('started_at')}")
        print(f"Fin: {inc.get('ended_at') or 'Activo'}")
        print(f"Tipo: {'Planificado' if inc.get('type') == 2 else 'Incidente'}")
        
        # Probar actualizaciones
        updates = api.obtener_actualizaciones(inc.get('id'))
        if updates:
            print(f"Actualizaciones: {len(updates)}")
            for u in updates[:2]:
                print(f"  - {u.get('message', '')[:50]}...")
    
    # Probar pendientes
    print("\n\n🔍 Buscando incidentes pendientes...")
    pendientes = api.obtener_pendientes()
    print(f"✅ Incidentes pendientes: {len(pendientes)}")
    for inc in pendientes:
        print(f"  - #{inc.get('id')}: {inc.get('title')}")

if __name__ == "__main__":
    test_nueva_api()