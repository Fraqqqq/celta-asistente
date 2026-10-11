"""Condiciones de carrera: doble confirmación y mensajes simultáneos."""

import asyncio
import json

import httpx

from app import servicio
from app.db import nueva_sesion
from tests.conftest import TOKEN, pide, texto, turno

RESERVA = pide(
    "reservar_turno",
    paciente_id=1,
    prestacion_id=3,
    profesional_id=5,
    fecha_hora="2026-10-12T09:30",
)


async def _proponer(modelo) -> str:
    modelo(RESERVA)
    with nueva_sesion() as sesion:
        conversacion = servicio.obtener_conversacion(sesion, "portal", "portal:50", 50)
        respuesta = await servicio.atender(sesion, conversacion, TOKEN, "lunes 9:30")
    return respuesta.accion.id


async def _confirmar(accion_id: str) -> str:
    with nueva_sesion() as sesion:
        conversacion = servicio.obtener_conversacion(sesion, "portal", "portal:50", 50)
        return await servicio.resolver_accion(sesion, conversacion, accion_id, True, TOKEN)


async def test_doble_confirmacion_simultanea_ejecuta_una_sola_vez(synapse, modelo):
    async def reserva_lenta(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)  # latencia real de red: la otra confirmación entra en el medio
        inicio = json.loads(request.content)["inicio"]
        return httpx.Response(201, json={**turno(99, inicio)})

    synapse.post("/turnos", name="reservar").mock(side_effect=reserva_lenta)
    accion_id = await _proponer(modelo)
    textos = await asyncio.gather(_confirmar(accion_id), _confirmar(accion_id))
    assert synapse["reservar"].call_count == 1
    assert sum(t.startswith("✅") for t in textos) == 1


class ModeloLento:
    """Tarda en responder, como Gemini real: deja lugar a que entre otro mensaje."""

    async def generar(self, sistema, historial, herramientas):
        await asyncio.sleep(0.05)
        ultimo = historial[-1].texto
        return texto(f"respuesta a {ultimo}")


async def _mensaje(texto_: str) -> None:
    async with servicio.candado("portal:50"):
        with nueva_sesion() as sesion:
            conversacion = servicio.obtener_conversacion(sesion, "portal", "portal:50", 50)
            await servicio.atender(sesion, conversacion, TOKEN, texto_)


async def test_mensajes_simultaneos_no_pisan_el_historial(synapse):
    servicio.usar_modelo(ModeloLento())
    await asyncio.gather(_mensaje("uno"), _mensaje("dos"))
    with nueva_sesion() as sesion:
        conversacion = servicio.obtener_conversacion(sesion, "portal", "portal:50", 50)
        textos = [m.get("texto") for m in conversacion.historial]
    assert textos == ["uno", "respuesta a uno", "dos", "respuesta a dos"]
