"""Fechas en el formato en que habla un paciente argentino."""

from datetime import date, datetime

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def dia_semana(d: date) -> str:
    return DIAS[d.weekday()]


def fecha_corta(d: date) -> str:
    return f"{dia_semana(d)} {d.day:02d}/{d.month:02d}"


def fecha_hora(dt: datetime) -> str:
    return f"{fecha_corta(dt)} a las {dt:%H:%M}"
