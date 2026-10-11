"""Traducción entre el formato neutral y el de Gemini (sin red)."""

import base64

from google.genai import types

from app.llm.base import LlamadaHerramienta, Mensaje, ResultadoHerramienta
from app.llm.gemini import FIRMA_NEUTRA, _a_contenidos, _desde_respuesta

FIRMA = base64.b64encode(b"firma-real").decode()


def historial(modelo_origen: str) -> list[Mensaje]:
    return [
        Mensaje("usuario", "mis turnos"),
        Mensaje(
            "modelo",
            llamadas=[LlamadaHerramienta("a1", "ver_mis_turnos", {}, FIRMA)],
            modelo=modelo_origen,
        ),
        Mensaje("herramienta", resultados=[ResultadoHerramienta("a1", "ver_mis_turnos", {"x": 1})]),
        Mensaje("modelo", "Tenés uno."),
    ]


def test_roles_y_partes():
    contenidos = _a_contenidos(historial("gemini-a"), "gemini-a")
    assert [c.role for c in contenidos] == ["user", "model", "user", "model"]
    assert contenidos[1].parts[0].function_call.name == "ver_mis_turnos"
    assert contenidos[2].parts[0].function_response.response == {"x": 1}


def test_la_firma_se_devuelve_al_mismo_modelo():
    contenidos = _a_contenidos(historial("gemini-a"), "gemini-a")
    assert contenidos[1].parts[0].thought_signature == b"firma-real"


def test_con_otro_modelo_se_usa_la_firma_neutra():
    contenidos = _a_contenidos(historial("gemini-a"), "gemini-b")
    assert contenidos[1].parts[0].thought_signature == FIRMA_NEUTRA


def test_lectura_de_respuesta_con_llamada_y_razonamiento():
    respuesta = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="pensando...", thought=True),
                        types.Part(
                            function_call=types.FunctionCall(
                                id="z", name="buscar_disponibilidad", args={"prestacion_id": 3}
                            ),
                            thought_signature=b"sig",
                        ),
                    ],
                )
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=10
        ),
    )
    r = _desde_respuesta(respuesta, "gemini-a")
    assert r.texto is None  # el razonamiento no se muestra
    assert r.llamadas[0].nombre == "buscar_disponibilidad"
    assert base64.b64decode(r.llamadas[0].firma) == b"sig"
    assert (r.tokens_entrada, r.tokens_salida) == (100, 10)
