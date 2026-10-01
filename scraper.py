import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import MESES, VET
from utils import normalizar_texto, logger


class ParseadorTiempo:
    """Parsea los períodos de incidentes de los proveedores Statuspage."""

    _MESES_REGEX = r"[A-Za-z]{3,9}"

    @staticmethod
    def _offset_timezone(texto: str):
        """Devuelve el timezone indicado por GMT/UTC±HH:MM, si existe."""
        if not texto:
            return None

        match = re.search(
            r"\b(?:GMT|UTC)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?\b",
            texto,
            re.IGNORECASE,
        )
        if not match:
            return None

        sign = 1 if match.group(1) == "+" else -1
        hours = int(match.group(2))
        minutes = int(match.group(3) or 0)
        return timezone(sign * timedelta(hours=hours, minutes=minutes))

    @staticmethod
    def limpiar_fecha(texto: str) -> str:
        if not texto:
            return ""

        t = normalizar_texto(texto.lower())
        # Se elimina la zona, pero se conserva la fecha y la hora. Esto permite
        # procesar tanto GMT-03:00 como UTC-0400 y los formatos antiguos.
        t = re.sub(
            r"\s*(?:gmt|utc)\s*[+-]\s*\d{1,2}:?\d{2}\s*$",
            "",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(r"\s*(?:gmt|utc)\s*$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*[+-]\d{2}:?\d{2}\s*$", "", t)
        t = re.sub(r"[()]", "", t)
        return re.sub(r"\s+", " ", t).strip()

    @classmethod
    def _parse_datetime(cls, texto: str, default_year: Optional[int] = None) -> Optional[datetime]:
        """Parsea fechas antiguas y el nuevo formato Alps con año y 24 horas."""
        if not texto:
            return None

        texto = normalizar_texto(texto).strip()
        year = default_year or datetime.now().year

        patrones = [
            # Alps: Sep 30, 2026 - 14:05 (el guion puede venir como en-dash)
            (
                rf"^({cls._MESES_REGEX})\s+(\d{{1,2}}),\s*(\d{{4}})\s*[-–—]\s*"
                r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?$"
            ),
            # Con año, sin separador entre fecha y hora.
            (
                rf"^({cls._MESES_REGEX})\s+(\d{{1,2}}),\s*(\d{{4}})\s+"
                r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?$"
            ),
            # Formato antiguo: Sep 30, 14:05 AM/PM o Sep 30, 14:05.
            (
                rf"^({cls._MESES_REGEX})\s+(\d{{1,2}}),?\s*"
                r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?$"
            ),
            # Variantes día-mes usadas por algunos proveedores.
            (
                rf"^(\d{{1,2}})\s+({cls._MESES_REGEX}),?\s*"
                r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?$"
            ),
        ]

        for index, patron in enumerate(patrones):
            match = re.match(patron, texto, re.IGNORECASE)
            if not match:
                continue

            values = match.groups()
            try:
                if index == 3:
                    dia, mes_str, hora, minuto, ampm = values
                elif index < 2:
                    mes_str, dia, parsed_year, hora, minuto, ampm = values
                    year = int(parsed_year)
                else:
                    mes_str, dia, hora, minuto, ampm = values

                mes = MESES.get(mes_str[:3].lower())
                if not mes:
                    return None

                hora = int(hora)
                if ampm:
                    if hora < 1 or hora > 12:
                        return None
                    if ampm.lower() == "pm" and hora < 12:
                        hora += 12
                    elif ampm.lower() == "am" and hora == 12:
                        hora = 0
                elif hora > 23:
                    return None

                return datetime(year, mes, int(dia), hora, int(minuto))
            except (TypeError, ValueError):
                return None

        return None

    @classmethod
    def _parse_period_start(cls, periodo: str):
        """Devuelve el inicio como datetime aware cuando el texto tiene zona."""
        if not periodo:
            return None

        zona = cls._offset_timezone(periodo)
        limpio = cls.limpiar_fecha(periodo)

        # Nuevo Alps: la fecha está antes del guion y la hora después.
        match = re.match(
            rf"^({cls._MESES_REGEX})\s+(\d{{1,2}}),\s*(\d{{4}})\s*[-–—]\s*"
            r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?$",
            limpio,
            re.IGNORECASE,
        )
        if match:
            fecha = cls._parse_datetime(limpio)
        else:
            fecha = cls._parse_datetime(limpio.split(" - ")[0].strip())

        return fecha.replace(tzinfo=zona) if fecha and zona else fecha

    @classmethod
    def extraer_fecha(cls, periodo: str) -> Optional[datetime]:
        """Extrae una fecha y la normaliza a VET cuando no trae zona explícita."""
        fecha = cls._parse_period_start(periodo)
        if not fecha:
            return None
        return fecha if fecha.tzinfo else fecha.replace(tzinfo=VET)

    @classmethod
    def calcular_duracion(cls, texto: str) -> int:
        if not texto or texto.lower() == "n/a":
            return 0

        pesos = {"week": 10080, "day": 1440, "hour": 60, "minute": 1, "min": 1}
        if any(unit in texto.lower() for unit in pesos):
            return sum(
                int(valor) * pesos[unidad]
                for valor, unidad in re.findall(
                    r"(\d+)\s*(week|day|hour|minute|min)", texto.lower()
                )
            )

        t = cls.limpiar_fecha(texto)
        match = re.match(
            rf"^({cls._MESES_REGEX})\s+(\d{{1,2}}),?\s*"
            r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?\s*[-–—]\s*"
            r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?$",
            t,
            re.IGNORECASE,
        )
        if match:
            mes, dia, h1, mi1, ampm1, h2, mi2, ampm2 = match.groups()
            inicio = cls._parse_datetime(f"{mes} {dia}, {h1}:{mi1} {ampm1 or ''}")
            fin = cls._parse_datetime(f"{mes} {dia}, {h2}:{mi2} {ampm2 or ''}")
            if inicio and fin:
                if fin < inicio:
                    fin += timedelta(days=1)
                return max(0, int((fin - inicio).total_seconds() // 60))

        partes = [parte.strip() for parte in t.split(" - ") if parte.strip()]
        fechas = [cls._parse_datetime(parte) for parte in partes]
        fechas = [fecha for fecha in fechas if fecha]

        if len(fechas) == 2:
            if fechas[1] < fechas[0]:
                fechas[1] += timedelta(days=1)
            return max(0, int((fechas[1] - fechas[0]).total_seconds() // 60))

        inicio = cls._parse_period_start(texto)
        if inicio:
            try:
                ahora = datetime.now(VET)
                if inicio.tzinfo is None:
                    inicio = inicio.replace(tzinfo=VET)
                return max(0, int((ahora - inicio.astimezone(VET)).total_seconds() // 60))
            except Exception as exc:
                logger.error(f"Error calculando duración: {exc}")

        return 0

    @classmethod
    def convertir_periodo_a_vet(cls, periodo: str) -> str:
        """Convierte períodos, incluido el timestamp nuevo de Atlassian Alps, a VET."""
        if not periodo:
            return periodo

        try:
            texto = normalizar_texto(periodo).strip()
            zona = cls._offset_timezone(texto)
            limpio = cls.limpiar_fecha(texto)

            # Alps nuevo: "Sep 30, 2026 - 14:05 GMT-03:00".
            inicio = cls._parse_period_start(texto)
            if inicio and re.match(
                rf"^{cls._MESES_REGEX}\s+\d{{1,2}},\s*\d{{4}}\s*[-–—]\s*\d{{1,2}}:\d{{2}}",
                limpio,
                re.IGNORECASE,
            ):
                inicio = inicio.astimezone(VET) if inicio.tzinfo else inicio.replace(tzinfo=VET)
                return f"{inicio.strftime('%b %d, %I:%M %p')} VET"

            if " - " not in limpio:
                return periodo

            partes = limpio.split(" - ", 1)
            inicio = cls._parse_datetime(partes[0].strip())
            fin = cls._parse_datetime(partes[1].strip())
            if not inicio or not fin:
                return periodo

            if fin.year == datetime.now().year and fin < inicio:
                fin += timedelta(days=1)

            origen = zona or VET
            inicio_vet = inicio.replace(tzinfo=origen).astimezone(VET)
            fin_vet = fin.replace(tzinfo=origen).astimezone(VET)
            if inicio_vet.date() == fin_vet.date():
                return f"{inicio_vet.strftime('%b %d, %I:%M %p')} - {fin_vet.strftime('%I:%M %p')} VET"
            return (
                f"{inicio_vet.strftime('%b %d, %I:%M %p')} - "
                f"{fin_vet.strftime('%b %d, %I:%M %p')} VET"
            )
        except Exception:
            logger.debug("No se pudo convertir periodo a VET", exc_info=True)
            return periodo



















































































































