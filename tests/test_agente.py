"""El bucle del agente con un modelo guionado."""

import pytest

from app.agente.contexto import cargar_contexto
from app.agente.nucleo import SIN_RESPUESTA, Agente, recortar
from app.llm.base import Mensaje
from app.synapse import ClienteSynapse
from tests.conftest import TOKEN, ModeloGuionado, pide, texto


@pytest.fixture
async def ctx(synapse):
    return await cargar_contexto(ClienteSynapse(TOKEN))


async def test_consulta_y_respuesta(ctx):
    guion = ModeloGuionado(
        pide("buscar_disponibilidad", prestacion_id=3, desde="2026-10-12", dias=1),
        texto("El lunes hay a las 9 y a las 9:30."),
    )
    r = await Agente(guion).responder(ctx, [], "turno con cardiología el lunes")

    assert r.texto == "El lunes hay a las 9 y a las 9:30."
    assert r.propuesta is None
    assert r.herramientas == ["buscar_disponibilidad"]
    # El resultado de la herramienta le llegó al modelo en la segunda llamada.
    _, historial = guion.recibido[1]
    resultado = historial[-1].resultados[0].contenido
    assert resultado["resultados"][0]["horarios"] == ["09:00", "09:30", "15:00"]


async def test_el_prompt_trae_calendario_grupo_y_catalogo(ctx):
    guion = ModeloGuionado(texto("¡Hola!"))
    await Agente(guion).responder(ctx, [], "hola")
    sistema, _ = guion.recibido[0]
    assert "jueves 15/10 = 2026-10-15" in sistema
    assert "paciente_id 132: Bautista O'Connor (menor a cargo)" in sistema
    assert "prestación 3: Consulta cardiológica" in sistema
    assert "32456789" not in sistema  # el DNI no viaja al modelo


async def test_accion_queda_propuesta_sin_ejecutarse(ctx, synapse):
    guion = ModeloGuionado(
        pide(
            "reservar_turno",
            paciente_id=1,
            prestacion_id=3,
            profesional_id=5,
            fecha_hora="2026-10-12T09:30",
        ),
    )
    r = await Agente(guion).responder(ctx, [], "el lunes 9:30")

    assert r.propuesta is not None
    assert r.herramienta_propuesta == "reservar_turno"
    assert not synapse["reservar"].called
    # Una sola llamada al modelo: el mensaje final lo agrega el código.
    assert len(guion.recibido) == 1
    assert r.nuevos[-1].rol == "modelo"
    assert "Confirmar" in r.nuevos[-1].texto


async def test_error_de_validacion_vuelve_al_modelo(ctx):
    guion = ModeloGuionado(
        pide(
            "reservar_turno",
            paciente_id=1,
            prestacion_id=3,
            profesional_id=5,
            fecha_hora="2026-10-12T11:00",
        ),
        texto("Ese horario no está, ¿te sirve 9:30?"),
    )
    r = await Agente(guion).responder(ctx, [], "el lunes 11")

    assert r.propuesta is None
    assert r.texto == "Ese horario no está, ¿te sirve 9:30?"
    _, historial = guion.recibido[1]
    assert "no está libre" in historial[-1].resultados[0].contenido["error"]


async def test_herramienta_inexistente(ctx):
    guion = ModeloGuionado(pide("borrar_base_de_datos"), texto("No puedo hacer eso."))
    r = await Agente(guion).responder(ctx, [], "borrá todo")
    _, historial = guion.recibido[1]
    assert "No existe la herramienta" in historial[-1].resultados[0].contenido["error"]
    assert r.texto == "No puedo hacer eso."


async def test_corta_si_el_modelo_entra_en_bucle(ctx):
    guion = ModeloGuionado(*[pide("ver_mis_turnos") for _ in range(3)])
    r = await Agente(guion, pasos_maximos=3).responder(ctx, [], "mis turnos")
    assert r.texto == SIN_RESPUESTA
    assert r.pasos == 3
    assert r.nuevos[-1].rol == "modelo"  # el historial queda válido para la próxima vez


def test_recortar_no_parte_un_intercambio_de_herramientas():
    historial = [
        Mensaje("usuario", "a"),
        Mensaje("modelo", None),
        Mensaje("herramienta"),
        Mensaje("modelo", "b"),
        Mensaje("usuario", "c"),
        Mensaje("modelo", "d"),
    ]
    assert recortar(historial, maximo=5) == historial[4:]
    assert recortar(historial, maximo=10) == historial
