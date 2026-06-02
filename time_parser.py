import re
from datetime import datetime, timedelta
from typing import Optional

from config import MESES, UTC, VET
from utils import normalizar_texto, logger

class ParseadorTiempo:
    @staticmethod
    def limpiar_fecha(texto: str) -> str:
        if not texto:
            return ""
        t = normalizar_texto(texto.lower())
        t = re.sub(r"\s*(gmt|utc)\b.*?$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*[-+]\d{2}:\d{2}\s*$", "", t)
        t = re.sub(r"\s*[-+]\d{4}\s*$", "", t)
        t = re.sub(r"[\(\)]", "", t)
        return t.strip()

    @classmethod
    def extraer_fecha(cls, periodo: str) -> Optional[datetime]:
        if not periodo:
            return None
        texto = cls.limpiar_fecha(periodo)
        ahora = datetime.now()

        patrones = [
            r"([a-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})",
            r"([a-z]{3})\s+(\d{1,2})\s+(\d{1,2}):(\d{2})",
            r"(\d{1,2})\s+([a-z]{3}),\s*(\d{1,2}):(\d{2})",
            r"(\d{1,2})\s+([a-z]{3})\s+(\d{1,2}):(\d{2})",
        ]

        for pat in patrones:
            m = re.search(pat, texto)
            if not m:
                continue
            if pat.startswith(r"([a-z]{3})"):
                mes_str, dia, hora, minuto = m.groups()
            else:
                dia, mes_str, hora, minuto = m.groups()

            mes = MESES.get(mes_str[:3].lower())
            if not mes:
                continue

            try:
                return datetime(ahora.year, mes, int(dia), int(hora), int(minuto))
            except ValueError:
                continue

        return None

    @classmethod
    def calcular_duracion(cls, texto: str) -> int:
        if not texto or texto.lower() == "n/a":
            return 0

        t = cls.limpiar_fecha(texto)

        pesos = {"week": 10080, "day": 1440, "hour": 60, "minute": 1, "min": 1}
        if any(unit in t for unit in pesos):
            total = 0
            for val, unit in re.findall(r"(\d+)\s*(week|day|hour|minute|min)", t):
                total += int(val) * pesos[unit]
            return total

        m_mismo_dia = re.match(
            r"([a-z]{3})\s+(\d{1,2}),\s+"
            r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?\s*-\s*"
            r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?",
            t,
            re.IGNORECASE,
        )

        if m_mismo_dia:
            mes, dia, h1, mi1, ampm1, h2, mi2, ampm2 = m_mismo_dia.groups()
            h1, h2 = int(h1), int(h2)

            if ampm1 and ampm1.lower() == "pm" and h1 < 12:
                h1 += 12
            if ampm2 and ampm2.lower() == "pm" and h2 < 12:
                h2 += 12
            if ampm1 and ampm1.lower() == "am" and h1 == 12:
                h1 = 0
            if ampm2 and ampm2.lower() == "am" and h2 == 12:
                h2 = 0

            ini = datetime(2000, MESES.get(mes[:3].lower(), 1), int(dia), h1, int(mi1))
            fin = datetime(2000, MESES.get(mes[:3].lower(), 1), int(dia), h2, int(mi2))
            if fin < ini:
                fin += timedelta(days=1)
            return int((fin - ini).total_seconds() // 60)

        partes = [p.strip() for p in t.split("-") if p.strip()]
        fechas = []
        for p in partes:
            f = cls.extraer_fecha(p)
            if f:
                fechas.append(f)

        if len(fechas) == 2:
            if fechas[1] < fechas[0]:
                fechas[1] = fechas[1].replace(year=fechas[1].year + 1)
            return int((fechas[1] - fechas[0]).total_seconds() // 60)
        if len(fechas) == 1:
            return int((datetime.now() - fechas[0]).total_seconds() // 60)
        return 0


    @classmethod
    def convertir_periodo_a_vet(cls, periodo: str) -> str:
        if not periodo:
            return periodo
        try:
            texto = normalizar_texto(periodo)
            
            # Eliminar sufijos de zona horaria (ej: -04, UTC-4, GMT-4)
            texto = re.sub(r'\s*[-+]\d{2}:?\d{2}\s*$', '', texto)
            texto = re.sub(r'\s*(GMT|UTC)\s*[-+]\d{2}:?\d{2}\s*$', '', texto, flags=re.IGNORECASE)
            texto = re.sub(r'\s*UTC\s*$', '', texto, flags=re.IGNORECASE)
            texto = texto.strip()
            
            # Si no contiene " - ", devolver original
            if " - " not in texto:
                return periodo
            
            # Dividir en inicio y fin
            partes = texto.split(" - ")
            if len(partes) != 2:
                return periodo
            
            inicio_str, fin_str = partes[0].strip(), partes[1].strip()
            
            # Función auxiliar para parsear una fecha/hora
            def parse_fecha_hora(s: str) -> Optional[datetime]:
                # Patrones: "May 23, 09:40 pm"  o  "09:40 pm" (sin fecha)
                # Primero intentar con mes día, hora
                m = re.match(r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})\s*(am|pm)", s, re.IGNORECASE)
                if m:
                    mes_str, dia, hora, minuto, ampm = m.groups()
                    mes = MESES.get(mes_str[:3].lower())
                    if not mes:
                        return None
                    hora = int(hora)
                    if ampm.lower() == "pm" and hora < 12:
                        hora += 12
                    if ampm.lower() == "am" and hora == 12:
                        hora = 0
                    year = datetime.now().year
                    # Ajuste de año si mes es enero y hoy es diciembre (evitar año futuro)
                    if mes == 1 and datetime.now().month == 12:
                        year += 1
                    return datetime(year, mes, int(dia), hora, int(minuto))
                
                # Si no tiene fecha, solo hora (asumir mismo día que la otra parte)
                m = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)", s, re.IGNORECASE)
                if m:
                    hora, minuto, ampm = m.groups()
                    hora = int(hora)
                    if ampm.lower() == "pm" and hora < 12:
                        hora += 12
                    if ampm.lower() == "am" and hora == 12:
                        hora = 0
                    return datetime(1, 1, 1, hora, int(minuto))  # fecha dummy, se ajustará después
                return None
            
            inicio_dt = parse_fecha_hora(inicio_str)
            fin_dt = parse_fecha_hora(fin_str)
            
            if not inicio_dt or not fin_dt:
                return periodo
            
            # Determinar zona horaria origen: si el raw contenía -04 o UTC-4 asumimos UTC-4 (mismo que VET)
            # Como VET es UTC-4, podemos asumir que ambos están en VET si no se especifica otra.
            # Simplemente asignamos zona VET a los datetime sin zona.
            tz = VET
            
            # Si inicio_dt tiene año 1 (solo hora), le asignamos el mismo día que fin_dt (si fin tiene fecha)
            if inicio_dt.year == 1:
                if fin_dt.year != 1:
                    inicio_dt = inicio_dt.replace(year=fin_dt.year, month=fin_dt.month, day=fin_dt.day)
                else:
                    # ambos son solo hora, usamos hoy
                    hoy = datetime.now()
                    inicio_dt = inicio_dt.replace(year=hoy.year, month=hoy.month, day=hoy.day)
            
            if fin_dt.year == 1:
                if inicio_dt.year != 1:
                    fin_dt = fin_dt.replace(year=inicio_dt.year, month=inicio_dt.month, day=inicio_dt.day)
                    # Si fin es menor que inicio, asumimos que es al día siguiente
                    if fin_dt < inicio_dt:
                        fin_dt += timedelta(days=1)
                else:
                    hoy = datetime.now()
                    fin_dt = fin_dt.replace(year=hoy.year, month=hoy.month, day=hoy.day)
            
            # Asignar zona horaria
            inicio_vet = inicio_dt.replace(tzinfo=tz) if inicio_dt.tzinfo is None else inicio_dt.astimezone(tz)
            fin_vet = fin_dt.replace(tzinfo=tz) if fin_dt.tzinfo is None else fin_dt.astimezone(tz)
            
            # Formatear
            if inicio_vet.date() == fin_vet.date():
                return f"{inicio_vet.strftime('%b %d, %I:%M %p')} - {fin_vet.strftime('%I:%M %p')} VET"
            else:
                return f"{inicio_vet.strftime('%b %d, %I:%M %p')} - {fin_vet.strftime('%b %d, %I:%M %p')} VET"

            return periodo
        except Exception:
            logger.debug("No se pudo convertir periodo a VET", exc_info=True)
            return periodo