from typing import List, Dict, Any, Tuple
from pathlib import Path
from config import RESULTADOS_FILE, WEB_APP_URL, ASIGNADOS
from http_client import HttpClient
from models import ProveedorConfig, SelectorMap
from scraper import IncidentScraper, clasificar_incidente
from utils import configurar_logging, cargar_json, guardar_json, clave_incidente_dict, distribuir_asignados, logger
from monnet_api import MonnetAPI

import os
os.environ['TZ'] = 'America/Caracas'


PROVEEDORES_LIST = [
    ProveedorConfig(
        "Monnet",
        "https://monnetpayments.status.freshservice.com/api/public/status/disruptions",
        "",  # No necesita container (API)
        SelectorMap("", "", "", "", None),  # No necesita selectores (API)
        "freshservice_api",  # 🟢 NUEVO TIPO
        False,  # navegacion_profunda
        active_url="",  # No se usa
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
    """Fusiona nuevos incidentes con el histórico usando ID como clave primaria."""
    historico = cargar_json(RESULTADOS_FILE, [])
    mapa = {
        (h.get("Proveedor"), h.get("ID")): h 
        for h in historico 
        if h.get("ID")
    }

    nuevos_unicos = []
    for inc in nuevos:
        if not inc.get("ID"):
            logger.error(f"❌ Incidente sin ID: {inc.get('Titulo', 'Unknown')[:50]} - Saltando")
            continue
        clave = (inc.get("Proveedor"), inc.get("ID"))
        if clave not in mapa:
            mapa[clave] = inc
            nuevos_unicos.append(inc)

    resultado_final = list(mapa.values())
    guardar_json(RESULTADOS_FILE, resultado_final)
    return resultado_final, nuevos_unicos


def enviar_a_google_sheets(http: HttpClient, datos: List[Dict[str, Any]], sheet: str = "History") -> bool:
    if not WEB_APP_URL:
        return False
    if not datos and sheet != "Pending":
        return False
    return http.post_json(WEB_APP_URL, {"sheet": sheet, "data": datos})


def main() -> None:
    configurar_logging()
    
    http = HttpClient()

    try:
        with IncidentScraper() as bot:
            # 1. PRIMERO: Hacer scraping de todos los proveedores
            logger.info("🆕 Iniciando scraping...")
            todos_los_incidentes: List[Dict[str, Any]] = []
            nuevos_pendientes: List[Dict[str, Any]] = []

            for prov in PROVEEDORES_LIST:
                logger.info(f"📌 Procesando: {prov.nombre}")
                
                from state_manager import GestorEstado
                fecha_corte = GestorEstado.obtener_fecha_corte(prov.nombre)
                
                incidentes = bot.ejecutar(prov, fecha_corte)
                todos_los_incidentes.extend(incidentes)
                nuevos_pendientes.extend([x for x in incidentes if x.get("Pendiente") == "SI"])

            # 2. SEGUNDO: Verificar pendientes guardados anteriormente
            pendientes_guardados = cargar_json(Path("pendientes_incidentes.json"), [])
            
            if pendientes_guardados:
                logger.info(f"🔄 Verificando {len(pendientes_guardados)} pendientes anteriores...")
                pendientes_actualizados: List[Dict[str, Any]] = []
                
                for prov in PROVEEDORES_LIST:
                    pendientes_prov = [p for p in pendientes_guardados if p.get("Proveedor") == prov.nombre]
                    if not pendientes_prov:
                        continue
                    
                    # 🔥 Monnet: verificación por API (nuevo formato)
                    if prov.nombre == "Monnet":
                        logger.info(f"🔄 Verificando {len(pendientes_prov)} pendientes de Monnet vía API...")
                        api = MonnetAPI()
                        
                        for pend in pendientes_prov:
                            pend_id = str(pend.get("ID", ""))
                            
                            if not pend_id:
                                logger.warning(f"⚠️ Pendiente sin ID: {pend.get('Titulo', '')[:50]}")
                                pend["Pendiente"] = "REVISAR"
                                pendientes_actualizados.append(pend)
                                continue
                            
                            # Consultar incidente por ID
                            inc_data = api.obtener_incidente_por_id(pend_id)
                            
                            if inc_data is None:
                                # No encontrado (404 o fuera de rango)
                                logger.info(f"✅ Monnet | Incidente {pend_id} no encontrado - marcando como resuelto: {pend.get('Titulo', '')[:50]}")
                                pend["Pendiente"] = "NO"
                                pend["Estado"] = "Resolved (incidente no encontrado)"
                                pend["Duracion_Minutos"] = "0"
                            elif inc_data.get("ended_at") is not None:
                                # Tiene fecha de fin - se resolvió
                                logger.info(f"✅ Monnet | Resuelto: {pend.get('Titulo', '')[:50]}")
                                pend["Pendiente"] = "NO"
                                # Actualizar con datos finales
                                inc_dict = api.convertir_a_dict(inc_data)
                                pend["Periodo"] = inc_dict["Periodo"]
                                pend["Duracion_Minutos"] = inc_dict["Duracion_Minutos"]
                                pend["Estado"] = inc_dict["Estado"]
                            else:
                                # Sigue activo (ended_at es null)
                                logger.info(f"🟡 Monnet | Sigue pendiente: {pend.get('Titulo', '')[:50]}")
                                pend["Pendiente"] = "SI"
                                inc_dict = api.convertir_a_dict(inc_data)
                                pend["Duracion_Minutos"] = inc_dict["Duracion_Minutos"]
                                pend["Estado"] = inc_dict["Estado"]
                                if "(Activo)" not in pend.get("Periodo", ""):
                                    pend["Periodo"] = inc_dict["Periodo"]
                            
                            pendientes_actualizados.append(pend)
                    else:
                        # Para otros proveedores (Alps, Directa24), usar Selenium
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
                encontrado = False
                for i, p in enumerate(todos_pendientes):
                    if p.get("ID") == nuevo.get("ID") and p.get("Proveedor") == nuevo.get("Proveedor"):
                        todos_pendientes[i]["Duracion_Minutos"] = nuevo.get("Duracion_Minutos", 0)
                        todos_pendientes[i]["Periodo"] = nuevo.get("Periodo", "")
                        todos_pendientes[i]["Resumen"] = nuevo.get("Resumen", "")
                        encontrado = True
                        break
                
                if not encontrado:
                    todos_pendientes.append(nuevo)
            
            # ✅ ÚNICO PUNTO DE ASIGNACIÓN
            todos_pendientes = distribuir_asignados(todos_pendientes, ASIGNADOS)
            guardar_json(Path("pendientes_incidentes.json"), todos_pendientes)

            # 4. CUARTO: Preparar y enviar resueltos a History
            ids_pendientes = {p.get("ID") for p in todos_pendientes}
            
            solo_resueltos = [x for x in todos_los_incidentes if x.get("ID") not in ids_pendientes]
            
            vistos = set()
            sin_duplicados = []
            for inc in solo_resueltos:
                if not inc.get("ID"):
                    continue
                clave = f"{inc.get('Proveedor')}_{inc.get('ID')}"
                if clave not in vistos:
                    vistos.add(clave)
                    sin_duplicados.append(inc)
            
            # ✅ HERENCIA DE ASIGNADO
            for inc in sin_duplicados:
                match_origen = next((p for p in pendientes_guardados if p.get("ID") == inc.get("ID")), None)
                if match_origen and match_origen.get("Asignado"):
                    inc["Asignado"] = match_origen["Asignado"]
                else:
                    inc["Asignado"] = ""

            # ✅ ASIGNACIÓN A HUÉRFANOS
            sin_asignar = [inc for inc in sin_duplicados if not inc.get("Asignado")]
            if sin_asignar:
                sin_asignar = distribuir_asignados(sin_asignar, ASIGNADOS)
                for huerfano in sin_asignar:
                    for inc in sin_duplicados:
                        if inc.get("ID") == huerfano.get("ID"):
                            inc["Asignado"] = huerfano.get("Asignado")
            
            for inc in sin_duplicados:
                if not inc.get("Asignado"):
                    inc["Asignado"] = "Sin Asignar"

            # 💾 GUARDAR EN HISTÓRICO
            if sin_duplicados:
                datos_para_historico = sin_duplicados.copy()
                resultado_final, nuevos_incidentes = fusionar_historico(datos_para_historico)
                logger.info(f"💾 Histórico: {len(nuevos_incidentes)} nuevos | {len(resultado_final)} totales")
                
                if nuevos_incidentes:
                    from time_parser import ParseadorTiempo
                    
                    datos_history = []
                    for inc in nuevos_incidentes:
                        fila = {k: v for k, v in inc.items() if k not in ["ID", "Periodo_Raw", "Pendiente"]}
                        
                        if "-04" in str(fila.get("Periodo", "")):
                            fila["Periodo"] = ParseadorTiempo.convertir_periodo_a_vet(str(fila["Periodo"]))
                            
                        datos_history.append(fila)
                        
                    logger.info(f"📤 Enviando {len(datos_history)} nuevos incidentes reales a History")
                    enviar_a_google_sheets(http, datos_history, "History")
                else:
                    logger.info("📭 No hay nuevos incidentes para enviar a la hoja History")
            else:
                logger.info("📭 No hay resueltos nuevos para guardar en histórico")
            
            # 5. QUINTO: Enviar pendientes a Pending
            from time_parser import ParseadorTiempo
            datos_pending = []
            for inc in todos_pendientes:
                fila = {k: v for k, v in inc.items() if k not in ["Periodo_Raw", "Pendiente"]}
                
                if "-04" in str(fila.get("Periodo", "")):
                    fila["Periodo"] = ParseadorTiempo.convertir_periodo_a_vet(str(fila["Periodo"]))
                    
                datos_pending.append(fila)
            
            logger.info(f"⚠️ Enviando {len(datos_pending)} incidentes a Pending")
            enviar_a_google_sheets(http, datos_pending, "Pending")

            # 6. RESUMEN FINAL
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