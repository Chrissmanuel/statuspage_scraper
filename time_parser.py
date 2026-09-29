# time_parser.py
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from zoneinfo import ZoneInfo

from config import MESES, UTC, VET
from utils import normalizar_texto, logger


class ParseadorTiempo:

    # =========================================================
    # UTILIDADES INTERNAS
    # =========================================================

    @staticmethod
    def _extraer_offset_minutos(texto: str) -> int:
        """
        Extrae el offset en minutos desde textos tipo:
          - 'GMT-03:00'
          - 'GMT-3'
          - 'UTC-04:00'
          - 'UTC+2'
        Retorna 0 si no encuentra offset (asume hora local/VET).
        """
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

        # Quitamos 'gmt'/'utc' pero preservamos el offset numérico para _extraer_offset_minutos
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
        """
        Construye un datetime en la zona indicada por offset_min
        y lo convierte a VET. Devuelve un datetime naive en VET.
        """
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
        """
        Extrae una fecha/hora de un texto y la devuelve en VET (naive).
        Respeta el offset GMT/UTC indicado en el texto.
        """
        if not periodo:
            return None

        offset_min = cls._extraer_offset_minutos(periodo)
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

        # 1. Duración explícita ("1 hour, 23 minutes")
        pesos = {"week": 10080, "day": 1440, "hour": 60, "minute": 1, "min": 1}
        if any(unit in texto.lower() for unit in pesos):
            total = 0
            for val, unit in re.findall(r"(\d+)\s*(week|day|hour|minute|min)", texto.lower()):
                total += int(val) * pesos[unit]
            return total

        # 2. Rango del mismo día ("Sep 29, 10:30 AM - 11:45 AM")
        offset_min = cls._extraer_offset_minutos(texto)
        t = cls.limpiar_fecha(texto)

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

        # 3. Rangos con fechas completas o incidentes activos
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
            # Incidente activo: comparar contra ahora en VET
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
        Convierte un período tipo 'Sep 29, 09:30 GMT-03:00 - Sep 29, 10:45 GMT-03:00'
        a un string en VET listo para mostrar.
        Respeta el offset del texto original.
        """
        if not periodo:
            return periodo

        try:
            texto = normalizar_texto(periodo)

            # Offsets por cada extremo (pueden ser distintos si cambia el sufijo)
            partes_originales = re.split(r"\s+-\s+", texto, maxsplit=1)

            offset_global = cls._extraer_offset_minutos(texto)

            # Limpiar sufijos para parsear las horas
            texto_limpio = re.sub(
                r"\s*(GMT|UTC)\s*[-+]\d{2}:?\d{2}\s*", " ", texto, flags=re.IGNORECASE
            )
            texto_limpio = re.sub(
                r"\s*[-+]\d{2}:?\d{2}\s*", " ", texto_limpio
            )
            texto_limpio = texto_limpio.strip()

            if " - " not in texto_limpio:
                return periodo

            inicio_str, fin_str = [p.strip() for p in texto_limpio.split(" - ")]

            def parse_fecha_hora(s: str) -> Optional[datetime]:
                # Formato: "Sep 29, 09:30 AM"
                m = re.match(
                    r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})\s*(am|pm)",
                    s,
                    re.IGNORECASE,
                )
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
                    if mes == 1 and datetime.now().month == 12:
                        year += 1
                    return cls._construir_datetime_vet(
                        year=year, mes=mes, dia=int(dia),
                        hora=hora, minuto=int(minuto),
                        offset_min=offset_global,
                    )

                # Formato: "10:45 AM" (solo hora)
                m = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)", s, re.IGNORECASE)
                if m:
                    hora, minuto, ampm = m.groups()
                    hora = int(hora)
                    if ampm.lower() == "pm" and hora < 12:
                        hora += 12
                    if ampm.lower() == "am" and hora == 12:
                        hora = 0
                    # Marcamos con year=1 para resolver después
                    return datetime(1, 1, 1, hora, int(minuto))

                return None

            inicio_dt = parse_fecha_hora(inicio_str)
            fin_dt = parse_fecha_hora(fin_str)

            if not inicio_dt or not fin_dt:
                return periodo

            # Si algún extremo vino sin fecha, heredar del otro
            if inicio_dt.year == 1 and fin_dt.year != 1:
                inicio_dt = inicio_dt.replace(
                    year=fin_dt.year, month=fin_dt.month, day=fin_dt.day
                )
                # Reaplicar conversión a VET (porque lo construimos sin tz)
                tz_origen = timezone(timedelta(minutes=offset_global))
                inicio_dt = (
                    inicio_dt.replace(tzinfo=tz_origen)
                    .astimezone(VET)
                    .replace(tzinfo=None)
                )

            if fin_dt.year == 1 and inicio_dt.year != 1:
                fin_dt = fin_dt.replace(
                    year=inicio_dt.year, month=inicio_dt.month, day=inicio_dt.day
                )
                if fin_dt < inicio_dt:
                    fin_dt += timedelta(days=1)
                tz_origen = timezone(timedelta(minutes=offset_global))
                fin_dt = (
                    fin_dt.replace(tzinfo=tz_origen)
                    .astimezone(VET)
                    .replace(tzinfo=None)
                )

            if inicio_dt.date() == fin_dt.date():
                return (
                    f"{inicio_dt.strftime('%b %d, %I:%M %p')} - "
                    f"{fin_dt.strftime('%I:%M %p')} VET"
                )
            else:
                return (
                    f"{inicio_dt.strftime('%b %d, %I:%M %p')} - "
                    f"{fin_dt.strftime('%b %d, %I:%M %p')} VET"
                )

        except Exception:
            logger.debug("No se pudo convertir periodo a VET", exc_info=True)
            return periodo