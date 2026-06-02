from typing import List, Dict, Any, Tuple
from pathlib import Path
from config import RESULTADOS_FILE, WEB_APP_URL, ASIGNADOS
from http_client import HttpClient
from models import ProveedorConfig, SelectorMap
from scraper import IncidentScraper, clasificar_incidente
from utils import configurar_logging, cargar_json, guardar_json, clave_incidente_dict, distribuir_asignados, logger
from utils import migrar_ids_a_nuevo_formato



PROVEEDORES_LIST = [
    ProveedorConfig(
        "Monnet",
        "https://monnetpayments.freshstatus.io/incidents-history",
        "div[class*='CardWrapper']",
        SelectorMap(
            "div[class*='Title']",
            "div[class*='TimeStamp']:first-of-type",
            "div.gYoMm .style__TimeStamp-sc-19bjpya-9",
            "div[class*='DescriptionContainer']",
            'div[class*="LableTag"] span',
    ),
    "freshstatus",
    False,
    active_url="https://monnetpayments.freshstatus.io/",  # <-- NUEVO
),
    ProveedorConfig(
        "Alps",
        "https://status.alps.cl/history",
        "div.incident-container",
        SelectorMap(
            "a.incident-title",
            "div.secondary",
            "div.secondary",
            "div.incident-body",
            None,
        ),
        "atlassian",
        True,
    ),
    ProveedorConfig(
        "Directa24",
        "https://status.d24.com/history",
        "div.incident-container",
        SelectorMap(
            "a.incident-title",
            "div.secondary",
            "div.secondary",
            "div.incident-body",
            None,
        ),
        "atlassian",
        True,
    ),
]


def fusionar_historico(nuevos: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    historico = cargar_json(RESULTADOS_FILE, [])
    mapa = {clave_incidente_dict(x): x for x in historico}

    nuevos_unicos = []
    for inc in nuevos:
        clave = clave_incidente_dict(inc)
        if clave not in mapa:
            mapa[clave] = inc
            nuevos_unicos.append(inc)

    resultado_final = list(mapa.values())
    guardar_json(RESULTADOS_FILE, resultado_final)
    return resultado_final, nuevos_unicos


def enviar_a_google_sheets(http: HttpClient, datos: List[Dict[str, Any]], sheet: str = "History") -> bool:
    if not datos or not WEB_APP_URL:
        return False
    return http.post_json(WEB_APP_URL, {"sheet": sheet, "data": datos})

def main() -> None:
    configurar_logging()
    #migrar_ids_a_nuevo_formato()   # <--- EJECUTAR UNA SOLA VEZ

    http = HttpClient()

    try:
        with IncidentScraper() as bot:
            # 1. PRIMERO: Hacer scraping de todos los proveedores
            logger.info("🆕 Iniciando scraping...")
            todos_los_incidentes: List[Dict[str, Any]] = []
            nuevos_pendientes: List[Dict[str, Any]] = []

            for prov in PROVEEDORES_LIST:
                logger.info(f"📌 Procesando: {prov.nombre}")
                incidentes = bot.ejecutar(prov)
                todos_los_incidentes.extend(incidentes)
                nuevos_pendientes.extend([x for x in incidentes if x.get("Pendiente") == "SI"])

            # 2. SEGUNDO: Verificar pendientes guardados anteriormente
            pendientes_guardados = cargar_json(Path("pendientes_incidentes.json"), [])
            
            if pendientes_guardados:
                logger.info(f"🔄 Verificando {len(pendientes_guardados)} pendientes anteriores...")
                pendientes_actualizados: List[Dict[str, Any]] = []
                
                for prov in PROVEEDORES_LIST:
                    pendientes_prov = [p for p in pendientes_guardados if p.get("Proveedor") == prov.nombre]
                    if pendientes_prov:
                        verificados = bot.verificar_pendientes(pendientes_prov, prov)
                        pendientes_actualizados.extend(verificados)
                
                siguen_pendientes = [p for p in pendientes_actualizados if p.get("Pendiente") == "SI"]
                resueltos_verificacion = [p for p in pendientes_actualizados if p.get("Pendiente") != "SI"]
                
                if resueltos_verificacion:
                    todos_los_incidentes.extend(resueltos_verificacion)
                    logger.info(f"✅ {len(resueltos_verificacion)} pendientes anteriores resueltos")
            else:
                siguen_pendientes = []

            # 3. TERCERO: Unificar pendientes
            todos_pendientes = siguen_pendientes.copy()
            for nuevo in nuevos_pendientes:
                if not any(p.get("ID") == nuevo.get("ID") and p.get("Proveedor") == nuevo.get("Proveedor") 
                          for p in todos_pendientes):
                    todos_pendientes.append(nuevo)
            
            guardar_json(Path("pendientes_incidentes.json"), todos_pendientes)

            # 4. CUARTO: Enviar SOLO resueltos a History
            ids_pendientes = {p.get("ID") for p in todos_pendientes}
            
            solo_resueltos = [
                x for x in todos_los_incidentes 
                if x.get("ID") not in ids_pendientes
            ]

            vistos = set()
            sin_duplicados = []
            for inc in solo_resueltos:
                if not inc.get("ID"):
                    continue
                clave = f"{inc.get('Proveedor')}_{inc.get('ID')}"
                if clave not in vistos:
                    vistos.add(clave)
                    sin_duplicados.append(inc)

            if sin_duplicados:
                datos_history = [
                    {k: v for k, v in inc.items() if k not in ["Pendiente", "ID", "Periodo_Raw"]}
                    for inc in sin_duplicados
                ]
                datos_history = distribuir_asignados(datos_history, ASIGNADOS)
                
                datos_para_historico = [
                    {k: v for k, v in inc.items() if k not in ["Pendiente", "Periodo_Raw"]}
                    for inc in sin_duplicados
                ]
                datos_para_historico = distribuir_asignados(datos_para_historico, ASIGNADOS)
                
                resultado_final, nuevos_incidentes = fusionar_historico(datos_para_historico)
                logger.info(f"💾 Histórico: {len(nuevos_incidentes)} nuevos | {len(resultado_final)} totales")

                if nuevos_incidentes and datos_history:
                    enviar_a_google_sheets(http, datos_history, "History")
                    logger.info(f"✅ {len(nuevos_incidentes)} enviados a History")
                else:
                    logger.info("📭 No hay resueltos nuevos para enviar")
            else:
                logger.info("📭 No hay resueltos para enviar")

            # 5. QUINTO: Enviar pendientes a Pending
            if todos_pendientes:
                datos_pending = [
                    {k: v for k, v in inc.items() if k not in ["Periodo_Raw"]}
                    for inc in todos_pendientes
                ]
                datos_pending = distribuir_asignados(datos_pending, ASIGNADOS)
                
                enviar_a_google_sheets(http, datos_pending, "Pending")
                logger.info(f"⚠️ {len(todos_pendientes)} pendientes enviados a Pending")

            # 6. SEXTO: Resumen final
            total_pendientes = len(todos_pendientes)
            total_enviados = len(sin_duplicados)
            
            logger.info("=" * 50)
            logger.info(f"📊 RESUMEN: {total_enviados} resueltos | ⚠️ {total_pendientes} pendientes")
            logger.info("✨ PROCESO COMPLETADO")
            logger.info("=" * 50)

    except Exception:
        logger.exception("💥 Error crítico en el sistema")
    finally:
        http.close()


if __name__ == "__main__":
    main()
