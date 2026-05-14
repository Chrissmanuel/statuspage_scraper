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
            tz_origen = UTC if "UTC" in texto.upper() else VET

            # Primero intentar con rango entre diferentes días
            m = re.search(
                r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})(?:\s*(am|pm))?\s*-\s*"
                r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})(?:\s*(am|pm))?",
                texto,
                re.IGNORECASE,
            )
            
            if m:
                mes1, dia1, h1, mi1, ampm1, mes2, dia2, h2, mi2, ampm2 = m.groups()
                year = datetime.now().year
                month1 = MESES[mes1.lower()[:3]]
                month2 = MESES[mes2.lower()[:3]]

                h1, h2 = int(h1), int(h2)
                if ampm1 and ampm1.lower() == "pm" and h1 < 12:
                    h1 += 12
                if ampm2 and ampm2.lower() == "pm" and h2 < 12:
                    h2 += 12
                if ampm1 and ampm1.lower() == "am" and h1 == 12:
                    h1 = 0
                if ampm2 and ampm2.lower() == "am" and h2 == 12:
                    h2 = 0

                inicio = datetime(year, month1, int(dia1), h1, int(mi1), tzinfo=tz_origen)
                fin = datetime(year, month2, int(dia2), h2, int(mi2), tzinfo=tz_origen)

                inicio_vet = inicio.astimezone(VET)
                fin_vet = fin.astimezone(VET)

                # Si es el mismo día, mostrar formato corto
                if inicio_vet.date() == fin_vet.date():
                    return f"{inicio_vet.strftime('%b %d, %I:%M %p')} - {fin_vet.strftime('%I:%M %p')} VET"
                else:
                    return f"{inicio_vet.strftime('%b %d, %I:%M %p')} - {fin_vet.strftime('%b %d, %I:%M %p')} VET"

            # Si no es rango entre días, intentar con rango del mismo día
            m = re.search(
                r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})(?:\s*(am|pm))?\s*-\s*(\d{1,2}):(\d{2})(?:\s*(am|pm))?",
                texto,
                re.IGNORECASE,
            )
            if m:
                mes, dia, h1, mi1, ampm1, h2, mi2, ampm2 = m.groups()
                year = datetime.now().year
                month = MESES[mes.lower()[:3]]

                h1, h2 = int(h1), int(h2)
                if ampm1 and ampm1.lower() == "pm" and h1 < 12:
                    h1 += 12
                if ampm2 and ampm2.lower() == "pm" and h2 < 12:
                    h2 += 12
                if ampm1 and ampm1.lower() == "am" and h1 == 12:
                    h1 = 0
                if ampm2 and ampm2.lower() == "am" and h2 == 12:
                    h2 = 0

                inicio = datetime(year, month, int(dia), h1, int(mi1), tzinfo=tz_origen)
                fin = datetime(year, month, int(dia), h2, int(mi2), tzinfo=tz_origen)

                inicio_vet = inicio.astimezone(VET)
                fin_vet = fin.astimezone(VET)

                return f"{inicio_vet.strftime('%b %d, %I:%M %p')} - {fin_vet.strftime('%I:%M %p')} VET"

            return periodo
        except Exception:
            logger.debug("No se pudo convertir periodo a VET", exc_info=True)
            return periodo