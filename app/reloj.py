"""Hora oficial: la misma convención que Synapse (hora de Argentina, sin zona horaria).

Los tests reemplazan `ahora` para congelar el tiempo.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

ZONA = ZoneInfo("America/Argentina/Buenos_Aires")


def ahora() -> datetime:
    return datetime.now(ZONA).replace(tzinfo=None, microsecond=0)
