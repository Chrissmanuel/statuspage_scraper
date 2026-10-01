# time_parser.py
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from zoneinfo import ZoneInfo

from config import MESES, UTC, VET
from utils import normalizar_texto, logger


class ParseadorTiempo:

    # ✅ FIX Bug 1: alias para compatibilidad con código legacy que usa _MESES_REGEX
    _MESES_REGEX = "|".join(MESES.keys())

    # =========================================================
    # UTILIDADES INTERNAS
    # =========================================================

    @staticmethod
    def _extraer_offset_minutos(texto: str) -> int:
        if not texto:
            return 0

        m = re.search(
            r'(?:GMT|UTC)\s*([-+])\s*(\d{1,2})(?::?(\d{2}))?',
            texto,
            re.IGNORECASE,
        )
        if not m:
            return 0

        signo = -1 if m.group(1) == '-' else 1
        horas = int(m.group(2))
        minutos = int(m.group(3)) if m.group(3) else 0
        return signo * (horas * 60 + minutos)

    @staticmethod
    def limpiar_fecha(texto: str) -> str:
        if not texto:
            return ""
        t = normalizar_texto(texto.lower())

        t = re.sub(r"\s*(gmt|utc)\s*$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*(gmt|utc)(?=[-+\d])", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*[-+]\d{2}:?\d{2}\s*$", "", t)
        t = re.sub(r"\s*[-+]\d{4}\s*$", "", t)
        t = re.sub(r"[\(\)]", "", t)
        return t.strip()

    @classmethod
    def _construir_datetime_vet(
        cls,
        year: int,
        mes: int,
        dia: int,
        hora: int,
        minuto: int,
        offset_min: int,
    ) -> Optional[datetime]:
        try:
            tz_origen = timezone(timedelta(minutes=offset_min))
            dt_origen = datetime(year, mes, dia, hora, minuto, tzinfo=tz_origen)
            dt_vet = dt_origen.astimezone(VET)
            return dt_vet.replace(tzinfo=None)
        except (ValueError, OverflowError):
            return None

    # =========================================================
    # EXTRACCIÓN DE FECHAS
    # =========================================================

    @classmethod
    def extraer_fecha(cls, periodo: str) -> Optional[datetime]:
        if not periodo:
            return None

        offset_min = cls._extraer_offset_minutos(periodo)
        texto = cls.limpiar_fecha(periodo)
        # ✅ FIX: usar VET en lugar de hora local del sistema
        ahora = datetime.now(VET)

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

            return cls._construir_datetime_vet(
                year=ahora.year,
                mes=mes,
                dia=int(dia),
                hora=int(hora),
                minuto=int(minuto),
                offset_min=offset_min,
            )

        return None

    # =========================================================
    # DURACIÓN
    # =========================================================

    @classmethod
    def calcular_duracion(cls, texto: str) -> int:
        if not texto or texto.lower() == "n/a":
            return 0

        pesos = {"week": 10080, "day": 1440, "hour": 60, "minute": 1, "min": 1}
        if any(unit in texto.lower() for unit in pesos):
            total = 0
            for val, unit in re.findall(r"(\d+)\s*(week|day|hour|minute|min)", texto.lower()):
                total += int(val) * pesos[unit]
            return total

        offset_min = cls._extraer_offset_minutos(texto)
        t = cls.limpiar_fecha(texto)

        # ✅ FIX: AM/PM opcional (soporta formato 24h)
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

            mes_num = MESES.get(mes[:3].lower(), 1)
            ini = datetime(2000, mes_num, int(dia), h1, int(mi1))
            fin = datetime(2000, mes_num, int(dia), h2, int(mi2))
            if fin < ini:
                fin += timedelta(days=1)
            return int((fin - ini).total_seconds() // 60)

        partes = [p.strip() for p in t.split("-") if p.strip()]
        fechas = []
        for p in partes:
            f = cls.extraer_fecha(p + f" GMT{offset_min // 60:+03d}:00" if offset_min else p)
            if f:
                fechas.append(f)

        if len(fechas) == 2:
            if fechas[1] < fechas[0]:
                fechas[1] = fechas[1].replace(year=fechas[1].year + 1)
            return int((fechas[1] - fechas[0]).total_seconds() // 60)

        if len(fechas) == 1:
            try:
                ahora_local = datetime.now(VET).replace(tzinfo=None)
                return int((ahora_local - fechas[0]).total_seconds() // 60)
            except Exception as e:
                logger.error(f"Error calculando duración con zona horaria VET: {e}")
                return int((datetime.now() - fechas[0]).total_seconds() // 60)

        return 0

    # =========================================================
    # CONVERSIÓN DE PERÍODO A VET (para mostrar)
    # =========================================================

    @classmethod
    def convertir_periodo_a_vet(cls, periodo: str) -> str:
        """
        Formatos soportados:
          A)  "Sep 23, 2026 - 14:29 GMT-03:00"
          A2) "Oct 1, 09:57 GMT-03:00"              (sin año, sin guion)
          B)  "Aug 31, 16:30 - 18:32 GMT-04:00"     (rango mismo día)
          B2) "Sep 29, 14:27 - 16:33 UTC"           (rango mismo día, UTC solo)
          C)  "Sep 28, 13:23 - Sep 29, 00:04 GMT-03:00"
        """
        if not periodo:
            return periodo
        if "VET" in periodo.upper():
            return periodo
        try:
            texto = normalizar_texto(periodo).strip()

            offset_min = cls._extraer_offset_minutos(texto)

            # ✅ FIX Bug 3: eliminar offsets GMT/UTC con número
            texto_limpio = re.sub(
                r"\s*(GMT|UTC)\s*[-+]\d{1,2}:?\d{2}\s*", " ", texto, flags=re.IGNORECASE
            )
            # ✅ FIX Bug 3: eliminar GMT/UTC sueltos (sin offset)
            texto_limpio = re.sub(
                r"\s*(GMT|UTC)\s*$", " ", texto_limpio, flags=re.IGNORECASE
            )
            texto_limpio = re.sub(
                r"\s*(GMT|UTC)\s+", " ", texto_limpio, flags=re.IGNORECASE
            )
            texto_limpio = re.sub(r"\s*[-+]\d{2}:?\d{2}\s*", " ", texto_limpio)
            texto_limpio = re.sub(r"[\(\)]", "", texto_limpio)
            texto_limpio = re.sub(r"\s+", " ", texto_limpio).strip()

            ahora_vet = datetime.now(VET)
            year_default = ahora_vet.year

            # ---------------------------------------------------------
            # CASO A: "Sep 23, 2026 - 14:29"  (un solo datetime con año)
            # ---------------------------------------------------------
            m_unico = re.match(
                r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{4})\s*-\s*(\d{1,2}):(\d{2})$",
                texto_limpio,
                re.IGNORECASE,
            )
            if m_unico:
                mes_str, dia, year, hora, minuto = m_unico.groups()
                mes = MESES.get(mes_str[:3].lower())
                if not mes:
                    logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
                    return periodo

                dt_vet = cls._construir_datetime_vet(
                    year=int(year), mes=mes, dia=int(dia),
                    hora=int(hora), minuto=int(minuto),
                    offset_min=offset_min,
                )
                if not dt_vet:
                    logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
                    return periodo
                return dt_vet.strftime("%b %d, %I:%M %p") + " VET"

            # ---------------------------------------------------------
            # ✅ CASO A2 (NUEVO): "Oct 1, 09:57"  (un solo datetime, sin año)
            # ---------------------------------------------------------
            m_unico_sin_year = re.match(
                r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})$",
                texto_limpio,
                re.IGNORECASE,
            )
            if m_unico_sin_year:
                mes_str, dia, hora, minuto = m_unico_sin_year.groups()
                mes = MESES.get(mes_str[:3].lower())
                if not mes:
                    logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
                    return periodo

                dt_vet = cls._construir_datetime_vet(
                    year=year_default, mes=mes, dia=int(dia),
                    hora=int(hora), minuto=int(minuto),
                    offset_min=offset_min,
                )
                if not dt_vet:
                    logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
                    return periodo
                return dt_vet.strftime("%b %d, %I:%M %p") + " VET"

            # ---------------------------------------------------------
            # CASO B: "Aug 31, 16:30 - 18:32"  (rango mismo día, sin año)
            # ---------------------------------------------------------
            m_rango_dia = re.match(
                r"([A-Za-z]{3})\s+(\d{1,2}),\s*"
                r"(\d{1,2}):(\d{2})\s*-\s*"
                r"(\d{1,2}):(\d{2})$",
                texto_limpio,
                re.IGNORECASE,
            )
            if m_rango_dia:
                mes_str, dia, h1, mi1, h2, mi2 = m_rango_dia.groups()
                mes = MESES.get(mes_str[:3].lower())
                if not mes:
                    logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
                    return periodo

                ini_vet = cls._construir_datetime_vet(
                    year=year_default, mes=mes, dia=int(dia),
                    hora=int(h1), minuto=int(mi1),
                    offset_min=offset_min,
                )
                fin_vet = cls._construir_datetime_vet(
                    year=year_default, mes=mes, dia=int(dia),
                    hora=int(h2), minuto=int(mi2),
                    offset_min=offset_min,
                )
                if not ini_vet or not fin_vet:
                    logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
                    return periodo

                if fin_vet < ini_vet:
                    fin_vet = fin_vet + timedelta(days=1)

                if ini_vet.date() == fin_vet.date():
                    return (
                        f"{ini_vet.strftime('%b %d, %I:%M %p')} - "
                        f"{fin_vet.strftime('%I:%M %p')} VET"
                    )
                return (
                    f"{ini_vet.strftime('%b %d, %I:%M %p')} - "
                    f"{fin_vet.strftime('%b %d, %I:%M %p')} VET"
                )

            # ---------------------------------------------------------
            # CASO C: rango completo con fechas a ambos lados
            # ---------------------------------------------------------
            m_completo = re.match(
                r"([A-Za-z]{3})\s+(\d{1,2})(?:,\s*(\d{4}))?"
                r"(?:,|\s)\s*"
                r"(\d{1,2}):(\d{2})\s*-\s*"
                r"([A-Za-z]{3})\s+(\d{1,2})(?:,\s*(\d{4}))?"
                r"(?:,|\s)\s*"
                r"(\d{1,2}):(\d{2})$",
                texto_limpio,
                re.IGNORECASE,
            )
            if m_completo:
                (mes1_str, dia1, year1, h1, mi1,
                 mes2_str, dia2, year2, h2, mi2) = m_completo.groups()
                mes1 = MESES.get(mes1_str[:3].lower())
                mes2 = MESES.get(mes2_str[:3].lower())
                if not mes1 or not mes2:
                    logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
                    return periodo

                y1 = int(year1) if year1 else year_default
                y2 = int(year2) if year2 else year_default

                ini_vet = cls._construir_datetime_vet(
                    year=y1, mes=mes1, dia=int(dia1),
                    hora=int(h1), minuto=int(mi1),
                    offset_min=offset_min,
                )
                fin_vet = cls._construir_datetime_vet(
                    year=y2, mes=mes2, dia=int(dia2),
                    hora=int(h2), minuto=int(mi2),
                    offset_min=offset_min,
                )
                if not ini_vet or not fin_vet:
                    logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
                    return periodo

                if fin_vet < ini_vet:
                    fin_vet = fin_vet.replace(year=fin_vet.year + 1)

                if ini_vet.date() == fin_vet.date():
                    return (
                        f"{ini_vet.strftime('%b %d, %I:%M %p')} - "
                        f"{fin_vet.strftime('%I:%M %p')} VET"
                    )
                return (
                    f"{ini_vet.strftime('%b %d, %I:%M %p')} - "
                    f"{fin_vet.strftime('%b %d, %I:%M %p')} VET"
                )

            logger.warning(f"⚠️ Formato de período no reconocido: {repr(periodo)}")
            return periodo

        except Exception:
            logger.warning(f"⚠️ Error convirtiendo período a VET: {repr(periodo)}", exc_info=True)
            return periodo