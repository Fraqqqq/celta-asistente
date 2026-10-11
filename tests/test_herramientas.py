"""Las herramientas validan contra Synapse y nunca confían en lo que inventa el modelo."""

import pytest

from app.agente import herramientas as h
from app.agente.contexto import cargar_contexto
from app.synapse import ClienteSynapse
from tests.conftest import TOKEN


@pytest.fixture
async def ctx(synapse):
    return await cargar_contexto(ClienteSynapse(TOKEN))


async def test_disponibilidad_filtra_franja_y_horarios_pasados(ctx):
    # Hoy es domingo 11/10 a las 10:00: el 09:00 y el 09:30 de hoy ya pasaron.
    r = await h.buscar_disponibilidad(ctx, {"prestacion_id": 3, "dias": 2, "franja": "manana"})
    assert len(r["resultados"]) == 1  # hoy no queda nada a la mañana
    lunes = r["resultados"][0]
    assert (lunes["fecha"], lunes["dia"]) == ("2026-10-12", "lunes")
    assert lunes["horarios"] == ["09:00", "09:30"]
    assert lunes["profesional"] == "Martín Ríos"


async def test_disponibilidad_de_tarde(ctx):
    r = await h.buscar_disponibilidad(ctx, {"prestacion_id": 3, "dias": 1, "franja": "tarde"})
    assert r["resultados"][0]["horarios"] == ["15:00"]


async def test_disponibilidad_rechaza_prestacion_inventada(ctx):
    with pytest.raises(h.ErrorHerramienta):
        await h.buscar_disponibilidad(ctx, {"prestacion_id": 999})


async def test_mis_turnos_solo_muestra_los_proximos_activos(ctx):
    r = await h.ver_mis_turnos(ctx, {})
    assert [t["turno_id"] for t in r["proximos"]] == [11, 10]
    assert "ultimos_pasados" not in r


async def test_propuesta_de_reserva_con_resumen_y_cobertura(ctx):
    propuesta = await h.proponer_reserva(
        ctx,
        {
            "paciente_id": 132,
            "prestacion_id": 2,
            "profesional_id": 7,
            "fecha_hora": "2026-10-13T09:30",
        },
    )
    assert propuesta.resumen == (
        "Reservar Consulta pediátrica para Bautista O'Connor con Valeria Sosa, "
        "el martes 13/10 a las 09:30 (con OSDE 310)."
    )
    assert propuesta.argumentos["afiliacion_id"] == 2


async def test_reserva_particular_no_usa_la_cobertura(ctx):
    propuesta = await h.proponer_reserva(
        ctx,
        {
            "paciente_id": 1,
            "prestacion_id": 3,
            "profesional_id": 5,
            "fecha_hora": "2026-10-13T15:00",
            "particular": True,
        },
    )
    assert propuesta.argumentos["afiliacion_id"] is None
    assert "(particular)" in propuesta.resumen


@pytest.mark.parametrize(
    "args, error",
    [
        (
            {
                "paciente_id": 777,
                "prestacion_id": 3,
                "profesional_id": 5,
                "fecha_hora": "2026-10-13T09:00",
            },
            "grupo familiar",
        ),
        (
            {
                "paciente_id": 1,
                "prestacion_id": 3,
                "profesional_id": 7,
                "fecha_hora": "2026-10-13T09:00",
            },
            "no hace esa prestación",
        ),
        (
            {
                "paciente_id": 1,
                "prestacion_id": 3,
                "profesional_id": 5,
                "fecha_hora": "2026-10-13T11:00",
            },
            "no está libre",
        ),
        (
            {
                "paciente_id": 1,
                "prestacion_id": 3,
                "profesional_id": 5,
                "fecha_hora": "2026-10-11T09:00",
            },
            "ya pasó",
        ),
        (
            {"paciente_id": 1, "prestacion_id": 3, "profesional_id": 5, "fecha_hora": "el martes"},
            "formato",
        ),
    ],
)
async def test_reserva_invalida_vuelve_al_modelo_como_error(ctx, args, error):
    with pytest.raises(h.ErrorHerramienta, match=error):
        await h.proponer_reserva(ctx, args)


async def test_horario_ocupado_ofrece_alternativas(ctx):
    with pytest.raises(h.ErrorHerramienta, match="09:00, 09:30, 15:00"):
        await h.proponer_reserva(
            ctx,
            {
                "paciente_id": 1,
                "prestacion_id": 3,
                "profesional_id": 5,
                "fecha_hora": "2026-10-13T10:00",
            },
        )


async def test_no_se_cancela_online_con_menos_de_24_horas(ctx):
    with pytest.raises(h.ErrorHerramienta, match="RN-06"):
        await h.proponer_cancelacion(ctx, {"turno_id": 11})


async def test_no_se_cancela_un_turno_ajeno_o_pasado(ctx):
    for turno_id in (12, 4242):
        with pytest.raises(h.ErrorHerramienta, match="turno activo"):
            await h.proponer_cancelacion(ctx, {"turno_id": turno_id})


async def test_reprogramacion_valida_el_nuevo_horario(ctx):
    propuesta = await h.proponer_reprogramacion(
        ctx, {"turno_id": 10, "nueva_fecha_hora": "2026-10-22T15:00"}
    )
    assert "del miércoles 21/10 a las 09:00 al jueves 22/10 a las 15:00" in propuesta.resumen


async def test_ejecutar_reserva_llama_a_synapse(ctx, synapse):
    texto = await h.ejecutar_accion(
        ctx,
        "reservar_turno",
        {
            "paciente_id": 1,
            "prestacion_id": 3,
            "profesional_id": 5,
            "inicio": "2026-10-13T09:00:00",
            "afiliacion_id": 1,
        },
    )
    assert synapse["reservar"].called
    assert texto.startswith("✅ Listo, turno reservado")
    assert "martes 13/10 a las 09:00" in texto
