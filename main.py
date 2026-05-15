from typing import List, Dict, Any, Tuple
from pathlib import Path
from config import RESULTADOS_FILE, WEB_APP_URL, ASIGNADOS
from http_client import HttpClient
from models import ProveedorConfig, SelectorMap
from scraper import IncidentScraper, clasificar_incidente
from state_manager import GestorEstado
from utils import configurar_logging, cargar_json, guardar_json, clave_incidente_dict, distribuir_asignados, logger
from datetime import datetime, timedelta



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


def enviar_a_google_sheets(http: HttpClient, datos: List[Dict[str, Any]]) -> bool:
    if not datos or not WEB_APP_URL:
        return False
    return http.post_json(WEB_APP_URL, datos)


def main() -> None:
    configurar_logging()
    http = HttpClient()

    try:
        with IncidentScraper() as bot:
            # 1. PRIMERO: Hacer scraping normal desde fecha de corte
            logger.info("🆕 Iniciando scraping...")
            todos_los_incidentes: List[Dict[str, Any]] = []
            nuevos_pendientes: List[Dict[str, Any]] = []

            for prov in PROVEEDORES_LIST:
                # Usar últimas 24 horas como corte
                fecha_corte = datetime.now() - timedelta(hours=24)
                logger.info(f"📌 {prov.nombre} usando corte: {fecha_corte.isoformat()}")

                incidentes = bot.ejecutar(prov, fecha_corte)
                todos_los_incidentes.extend(incidentes)
                nuevos_pendientes.extend([x for x in incidentes if x.get("Pendiente") == "SI"])

                GestorEstado.actualizar_fecha_corte(prov.nombre)

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
                
                # Separar resueltos y los que siguen pendientes
                siguen_pendientes = [p for p in pendientes_actualizados if p.get("Pendiente") == "SI"]
                resueltos_verificacion = [p for p in pendientes_actualizados if p.get("Pendiente") != "SI"]
                
                # Agregar los resueltos en verificación a la lista para enviar
                if resueltos_verificacion:
                    # Quitar Pendiente e ID
                    datos_resueltos = [
                        {k: v for k, v in inc.items() if k not in ["Pendiente", "ID", "Periodo_Raw"]}
                        for inc in resueltos_verificacion
                    ]
                    # Distribuir asignados
                    datos_resueltos = distribuir_asignados(datos_resueltos, ASIGNADOS)
                    todos_los_incidentes.extend(datos_resueltos)
                    logger.info(f"✅ {len(resueltos_verificacion)} pendientes anteriores resueltos")
            else:
                siguen_pendientes = []

            # 3. TERCERO: Unificar pendientes (nuevos + los que siguen)
            # Evitar duplicados por ID
            todos_pendientes = siguen_pendientes.copy()
            for nuevo in nuevos_pendientes:
                if not any(p.get("ID") == nuevo.get("ID") and p.get("Proveedor") == nuevo.get("Proveedor") 
                          for p in todos_pendientes):
                    todos_pendientes.append(nuevo)
            
            guardar_json(Path("pendientes_incidentes.json"), todos_pendientes)

            # 4. CUARTO: Enviar todos los NO pendientes (resueltos)
            listos_para_enviar = [x for x in todos_los_incidentes if x.get("Pendiente") != "SI"]
            
            # ✅ Deducir por ID antes de enviar
            vistos = set()
            sin_duplicados = []
            for inc in listos_para_enviar:
                clave = f"{inc.get('Proveedor')}_{inc.get('ID')}"
                if clave not in vistos:
                    vistos.add(clave)
                    sin_duplicados.append(inc)
            
            if sin_duplicados:
                # Quitar columnas Pendiente e ID antes de enviar
                datos_sin_pendiente = [
                    {k: v for k, v in inc.items() if k not in ["Pendiente", "ID", "Periodo_Raw"]}
                    for inc in sin_duplicados
                ]
                
                # Distribuir asignados
                datos_sin_pendiente = distribuir_asignados(datos_sin_pendiente, ASIGNADOS)
                
                resultado_final, nuevos_incidentes = fusionar_historico(datos_sin_pendiente)
                logger.info(f"💾 Histórico: {len(nuevos_incidentes)} nuevos | {len(resultado_final)} totales")

                if nuevos_incidentes:
                    if enviar_a_google_sheets(http, nuevos_incidentes):
                        logger.info(f"✅ {len(nuevos_incidentes)} enviados a Google Sheets")
                    else:
                        logger.warning("⚠️ Error enviando a Google Sheets")
                else:
                    logger.info("📭 No hay incidentes nuevos para enviar")
            else:
                logger.info("📭 No hay incidentes para enviar")

            # 5. QUINTO: Resumen final
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