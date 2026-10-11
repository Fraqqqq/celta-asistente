"""Herramientas que el modelo puede pedir.

Dos tipos:
- **Consultas** (disponibilidad, turnos, costo): se ejecutan en el momento y su resultado
  vuelve al modelo, recortado a lo justo (sin DNI, emails ni teléfonos).
- **Acciones** (reservar, cancelar, reprogramar): el código valida los argumentos contra
  Synapse y arma un resumen, pero **no ejecuta nada**. La acción queda pendiente hasta
  que el paciente toca "Confirmar". El modelo propone; la persona decide.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app import reloj
from app.agente.contexto import Contexto
from app.formato import dia_semana, fecha_hora
from app.llm.base import DefinicionHerramienta

MAX_DIAS_CON_HORARIOS = 6
MAX_HORARIOS_POR_DIA = 10
HORA_CORTE_TARDE = 13
HORAS_MINIMAS_CAMBIO = 24  # RN-06 de Synapse: plazo para cancelar o cambiar online


class ErrorHerramienta(Exception):
    """Argumentos inválidos: el mensaje vuelve al modelo para que se corrija o pregunte."""


@dataclass
class Propuesta:
    """Una acción validada, lista para que el paciente la confirme."""

    resumen: str
    argumentos: dict


# ---------- Utilidades ----------


def _entero(args: dict, nombre: str) -> int:
    try:
        return int(args[nombre])
    except (KeyError, TypeError, ValueError) as e:
        raise ErrorHerramienta(f"Falta o es inválido el parámetro '{nombre}'.") from e


def _fecha(texto: str | None, por_defecto: date) -> date:
    if not texto:
        return por_defecto
    try:
        return date.fromisoformat(texto[:10])
    except ValueError as e:
        raise ErrorHerramienta("La fecha debe tener el formato AAAA-MM-DD.") from e


def _fecha_hora(texto: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(texto)).replace(tzinfo=None, second=0, microsecond=0)
    except ValueError as e:
        raise ErrorHerramienta("La fecha y hora deben tener el formato AAAA-MM-DDTHH:MM.") from e


def _resumen_turno(t: dict) -> dict:
    inicio = datetime.fromisoformat(t["inicio"])
    return {
        "turno_id": t["id"],
        "fecha": inicio.date().isoformat(),
        "dia": dia_semana(inicio),
        "hora": f"{inicio:%H:%M}",
        "paciente": t["paciente"]["nombre_completo"],
        "profesional": t["profesional"]["nombre_completo"],
        "prestacion": t["prestacion"]["nombre"],
        "estado": t["estado"],
    }


def _cobertura_vigente(paciente: dict) -> dict | None:
    return next((a for a in paciente.get("afiliaciones", []) if a["vigente"]), None)


async def _horarios_libres(
    ctx: Contexto, prestacion_id: int, profesional_id: int, dia: date
) -> list[datetime]:
    resultado = await ctx.cliente.disponibilidad(prestacion_id, dia, 1, profesional_id)
    return [datetime.fromisoformat(h) for r in resultado for h in r["horarios"]]


async def _verificar_horario(
    ctx: Contexto, prestacion_id: int, profesional_id: int, inicio: datetime
) -> None:
    """El modelo puede inventar un horario: se confirma contra la agenda real."""
    if inicio <= reloj.ahora():
        raise ErrorHerramienta("Ese horario ya pasó.")
    libres = await _horarios_libres(ctx, prestacion_id, profesional_id, inicio.date())
    if inicio not in libres:
        alternativas = ", ".join(f"{h:%H:%M}" for h in libres[:MAX_HORARIOS_POR_DIA]) or "ninguno"
        raise ErrorHerramienta(
            f"Ese horario no está libre. Horarios libres de ese profesional ese día: "
            f"{alternativas}."
        )


# ---------- Consultas ----------


async def buscar_disponibilidad(ctx: Contexto, args: dict) -> dict:
    prestacion_id = _entero(args, "prestacion_id")
    prestacion = ctx.catalogo.prestacion(prestacion_id)
    if prestacion is None:
        raise ErrorHerramienta("No existe esa prestación. Usá un id del catálogo.")
    hoy = reloj.ahora().date()
    desde = max(_fecha(args.get("desde"), hoy), hoy)
    dias = min(max(int(args.get("dias") or 7), 1), 14)
    profesional_id = args.get("profesional_id")
    franja = args.get("franja") or "cualquiera"

    crudo = await ctx.cliente.disponibilidad(
        prestacion_id, desde, dias, int(profesional_id) if profesional_id else None
    )
    ahora = reloj.ahora()
    resultados = []
    for r in crudo:
        horarios = [datetime.fromisoformat(h) for h in r["horarios"]]
        horarios = [h for h in horarios if h > ahora]
        if franja == "manana":
            horarios = [h for h in horarios if h.hour < HORA_CORTE_TARDE]
        elif franja == "tarde":
            horarios = [h for h in horarios if h.hour >= HORA_CORTE_TARDE]
        if not horarios:
            continue
        fecha = date.fromisoformat(r["fecha"])
        resultados.append(
            {
                "fecha": fecha.isoformat(),
                "dia": dia_semana(fecha),
                "profesional_id": r["profesional"]["id"],
                "profesional": r["profesional"]["nombre_completo"],
                "horarios": [f"{h:%H:%M}" for h in horarios[:MAX_HORARIOS_POR_DIA]],
                "horarios_adicionales": max(len(horarios) - MAX_HORARIOS_POR_DIA, 0),
            }
        )
    fechas = sorted({r["fecha"] for r in resultados})[:MAX_DIAS_CON_HORARIOS]
    resultados = [r for r in resultados if r["fecha"] in fechas]
    return {
        "prestacion": prestacion["nombre"],
        "desde": desde.isoformat(),
        "hasta": (desde + timedelta(days=dias - 1)).isoformat(),
        "resultados": resultados,
    }


async def ver_mis_turnos(ctx: Contexto, args: dict) -> dict:
    turnos = await ctx.mis_turnos()
    ahora = reloj.ahora()
    proximos = sorted(
        (
            t
            for t in turnos
            if t["estado"] in {"RESERVADO", "CONFIRMADO"}
            and datetime.fromisoformat(t["inicio"]) >= ahora
        ),
        key=lambda t: t["inicio"],
    )
    respuesta: dict[str, Any] = {"proximos": [_resumen_turno(t) for t in proximos]}
    if args.get("incluir_pasados"):
        pasados = [t for t in turnos if datetime.fromisoformat(t["inicio"]) < ahora]
        respuesta["ultimos_pasados"] = [_resumen_turno(t) for t in pasados[:8]]
    return respuesta


async def estimar_costo(ctx: Contexto, args: dict) -> dict:
    prestacion_id = _entero(args, "prestacion_id")
    paciente = ctx.paciente(_entero(args, "paciente_id"))
    if paciente is None:
        raise ErrorHerramienta("Ese paciente no es parte del grupo familiar.")
    if ctx.catalogo.prestacion(prestacion_id) is None:
        raise ErrorHerramienta("No existe esa prestación.")
    cobertura = _cobertura_vigente(paciente)
    estimacion = await ctx.cliente.estimacion(prestacion_id, cobertura and cobertura["id"])
    return {
        "importe_a_pagar": estimacion["importe"],
        "concepto": estimacion["concepto"],
        "cubierto_por_cobertura": estimacion["cubierto"],
        "requiere_autorizacion": estimacion["requiere_autorizacion"],
        "cobertura": f"{cobertura['obra_social']} {cobertura['plan_nombre']}"
        if cobertura
        else None,
    }


# ---------- Acciones (requieren confirmación) ----------


async def proponer_reserva(ctx: Contexto, args: dict) -> Propuesta:
    paciente = ctx.paciente(_entero(args, "paciente_id"))
    if paciente is None:
        raise ErrorHerramienta("Ese paciente no es parte del grupo familiar.")
    prestacion = ctx.catalogo.prestacion(_entero(args, "prestacion_id"))
    profesional = ctx.catalogo.profesional(_entero(args, "profesional_id"))
    if prestacion is None or profesional is None:
        raise ErrorHerramienta("Prestación o profesional inexistente. Usá ids del catálogo.")
    if not ctx.catalogo.atiende(profesional, prestacion):
        raise ErrorHerramienta(f"{profesional['nombre_completo']} no hace esa prestación.")
    inicio = _fecha_hora(args.get("fecha_hora"))
    await _verificar_horario(ctx, prestacion["id"], profesional["id"], inicio)

    cobertura = None if args.get("particular") else _cobertura_vigente(paciente)
    pago = (
        f"con {cobertura['obra_social']} {cobertura['plan_nombre']}" if cobertura else "particular"
    )
    return Propuesta(
        resumen=(
            f"Reservar {prestacion['nombre']} para {paciente['nombre_completo']} con "
            f"{profesional['nombre_completo']}, el {fecha_hora(inicio)} ({pago})."
        ),
        argumentos={
            "paciente_id": paciente["id"],
            "prestacion_id": prestacion["id"],
            "profesional_id": profesional["id"],
            "inicio": inicio.isoformat(),
            "afiliacion_id": cobertura["id"] if cobertura else None,
        },
    )


async def _turno_modificable(ctx: Contexto, args: dict) -> dict:
    turno = await ctx.turno_activo(_entero(args, "turno_id"))
    if turno is None:
        raise ErrorHerramienta("No hay un turno activo con ese id. Consultá ver_mis_turnos.")
    if datetime.fromisoformat(turno["inicio"]) - reloj.ahora() < timedelta(
        hours=HORAS_MINIMAS_CAMBIO
    ):
        raise ErrorHerramienta(
            f"Faltan menos de {HORAS_MINIMAS_CAMBIO} h para el turno: online ya no se puede "
            f"cancelar ni cambiar (RN-06). El paciente tiene que llamar a la recepción."
        )
    return turno


async def proponer_cancelacion(ctx: Contexto, args: dict) -> Propuesta:
    turno = await _turno_modificable(ctx, args)
    inicio = datetime.fromisoformat(turno["inicio"])
    motivo = (args.get("motivo") or "").strip()[:200] or None
    return Propuesta(
        resumen=(
            f"Cancelar el turno de {turno['paciente']['nombre_completo']}: "
            f"{turno['prestacion']['nombre']} con {turno['profesional']['nombre_completo']}, "
            f"el {fecha_hora(inicio)}."
        ),
        argumentos={"turno_id": turno["id"], "motivo": motivo},
    )


async def proponer_reprogramacion(ctx: Contexto, args: dict) -> Propuesta:
    turno = await _turno_modificable(ctx, args)
    nuevo = _fecha_hora(args.get("nueva_fecha_hora"))
    await _verificar_horario(ctx, turno["prestacion"]["id"], turno["profesional"]["id"], nuevo)
    anterior = datetime.fromisoformat(turno["inicio"])
    return Propuesta(
        resumen=(
            f"Cambiar el turno de {turno['paciente']['nombre_completo']} "
            f"({turno['prestacion']['nombre']} con {turno['profesional']['nombre_completo']}) "
            f"del {fecha_hora(anterior)} al {fecha_hora(nuevo)}."
        ),
        argumentos={"turno_id": turno["id"], "inicio": nuevo.isoformat()},
    )


async def ejecutar_accion(ctx: Contexto, herramienta: str, argumentos: dict) -> str:
    """Ejecuta una acción ya confirmada. Devuelve el mensaje para el paciente."""
    cliente = ctx.cliente
    if herramienta == "reservar_turno":
        turno = await cliente.reservar(
            argumentos["paciente_id"],
            argumentos["profesional_id"],
            argumentos["prestacion_id"],
            datetime.fromisoformat(argumentos["inicio"]),
            argumentos["afiliacion_id"],
        )
        texto = (
            f"✅ Listo, turno reservado: {turno['prestacion']['nombre']} con "
            f"{turno['profesional']['nombre_completo']}, el "
            f"{fecha_hora(datetime.fromisoformat(turno['inicio']))}."
        )
        if turno.get("autorizacion"):
            texto += " La obra social tiene que autorizarlo: te avisamos cuando esté."
        return texto + " Te llega la confirmación por email."
    if herramienta == "cancelar_turno":
        await cliente.cancelar(argumentos["turno_id"], argumentos.get("motivo"))
        return "✅ Turno cancelado. Gracias por avisar: ese horario queda libre para otro paciente."
    if herramienta == "reprogramar_turno":
        turno = await cliente.reprogramar(
            argumentos["turno_id"], datetime.fromisoformat(argumentos["inicio"])
        )
        return (
            f"✅ Turno cambiado: ahora es el "
            f"{fecha_hora(datetime.fromisoformat(turno['inicio']))} con "
            f"{turno['profesional']['nombre_completo']}."
        )
    raise ValueError(f"Acción desconocida: {herramienta}")


# ---------- Definiciones para el modelo ----------

_ID = {"type": "integer"}

DEFINICIONES = [
    DefinicionHerramienta(
        "buscar_disponibilidad",
        "Busca horarios libres para una prestación. Usala antes de proponer cualquier "
        "horario: nunca inventes horarios.",
        {
            "type": "object",
            "properties": {
                "prestacion_id": {**_ID, "description": "id de la prestación del catálogo"},
                "desde": {"type": "string", "description": "Fecha inicial AAAA-MM-DD"},
                "dias": {"type": "integer", "description": "Cantidad de días a revisar (1-14)"},
                "profesional_id": {**_ID, "description": "Solo si el paciente eligió profesional"},
                "franja": {"type": "string", "enum": ["manana", "tarde", "cualquiera"]},
            },
            "required": ["prestacion_id"],
        },
    ),
    DefinicionHerramienta(
        "ver_mis_turnos",
        "Lista los próximos turnos del paciente y de sus menores a cargo.",
        {
            "type": "object",
            "properties": {
                "incluir_pasados": {"type": "boolean", "description": "Incluir turnos pasados"}
            },
        },
    ),
    DefinicionHerramienta(
        "estimar_costo",
        "Calcula cuánto paga el paciente por una prestación según su cobertura.",
        {
            "type": "object",
            "properties": {"prestacion_id": _ID, "paciente_id": _ID},
            "required": ["prestacion_id", "paciente_id"],
        },
    ),
    DefinicionHerramienta(
        "reservar_turno",
        "Propone reservar un turno. No lo reserva: el paciente lo confirma con un botón.",
        {
            "type": "object",
            "properties": {
                "paciente_id": {**_ID, "description": "Titular o un menor a cargo"},
                "prestacion_id": _ID,
                "profesional_id": _ID,
                "fecha_hora": {"type": "string", "description": "AAAA-MM-DDTHH:MM"},
                "particular": {
                    "type": "boolean",
                    "description": "true solo si el paciente pide no usar su cobertura",
                },
            },
            "required": ["paciente_id", "prestacion_id", "profesional_id", "fecha_hora"],
        },
    ),
    DefinicionHerramienta(
        "cancelar_turno",
        "Propone cancelar un turno activo. El paciente lo confirma con un botón.",
        {
            "type": "object",
            "properties": {"turno_id": _ID, "motivo": {"type": "string"}},
            "required": ["turno_id"],
        },
    ),
    DefinicionHerramienta(
        "reprogramar_turno",
        "Propone cambiar el horario de un turno activo (mismo profesional y prestación). "
        "El paciente lo confirma con un botón.",
        {
            "type": "object",
            "properties": {
                "turno_id": _ID,
                "nueva_fecha_hora": {"type": "string", "description": "AAAA-MM-DDTHH:MM"},
            },
            "required": ["turno_id", "nueva_fecha_hora"],
        },
    ),
]

CONSULTAS = {
    "buscar_disponibilidad": buscar_disponibilidad,
    "ver_mis_turnos": ver_mis_turnos,
    "estimar_costo": estimar_costo,
}

ACCIONES = {
    "reservar_turno": proponer_reserva,
    "cancelar_turno": proponer_cancelacion,
    "reprogramar_turno": proponer_reprogramacion,
}
