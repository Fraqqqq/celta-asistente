"""Detección de urgencias antes de llamar al modelo.

Es una regla fija y no depende del modelo: si un mensaje describe una posible urgencia,
la respuesta con los teléfonos de emergencia sale siempre, aunque la API de Gemini esté
caída o el modelo lo interprete distinto. Es preferible pasarse a no avisar.
"""

import re
import unicodedata

MENSAJE_URGENCIA = (
    "⚠️ Lo que contás puede ser una urgencia. No esperes un turno:\n"
    "• Llamá al 107 (SAME) o al 911.\n"
    "• O andá a la guardia más cercana.\n\n"
    "Si no es una urgencia y querés sacar un turno, contame y te ayudo."
)

MENSAJE_CRISIS = (
    "Lamento mucho que estés pasando por esto. No estás solo/a: hay gente para escucharte "
    "ahora mismo.\n"
    "• Centro de Asistencia al Suicida: 135 (CABA y GBA) o 0800 345 1435, gratis y las 24 h.\n"
    "• Si estás en peligro inmediato, llamá al 107 o al 911.\n\n"
    "Si querés, también te ayudo a sacar un turno."
)

_URGENCIAS = [
    r"dolor (fuerte |muy fuerte )?(en el|de) pecho",
    r"me duele (mucho )?el pecho",
    r"no (puedo|puede) respirar",
    r"(me )?falta (el )?aire",
    r"me ahogo",
    r"desmay",
    r"inconsciente",
    r"convulsi",
    r"no (reacciona|responde)",
    r"sangra(do)? (mucho|abundante|sin parar)",
    r"hemorragia",
    r"(se me|tengo la) (cara|boca) (torcida|dormida)",
    r"no (puedo|puede) mover (el|la|un|una|medio)",
    r"intoxica",
    r"envenen",
    r"sobredosis",
    r"(lo|la|me) (atropell|apuñal|baleo|balear)",
    r"(accidente|choque) (grave|de auto|de moto)",
    r"reaccion alergica (grave|fuerte)",
    r"se me cierra la garganta",
]

_CRISIS = [
    r"\b(me quiero|quiero) (matar|morir)",
    r"\bsuicid",
    r"no quiero (vivir|seguir viviendo)",
    r"quitarme la vida",
    r"hacerme dano",
]


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sin_tildes if not unicodedata.combining(c))


def detectar(texto: str) -> str | None:
    """Devuelve el mensaje fijo a enviar, o None si no hay señales de urgencia."""
    normalizado = _normalizar(texto)
    if any(re.search(p, normalizado) for p in _CRISIS):
        return MENSAJE_CRISIS
    if any(re.search(p, normalizado) for p in _URGENCIAS):
        return MENSAJE_URGENCIA
    return None
