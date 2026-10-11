import pytest

from app.agente.emergencias import MENSAJE_CRISIS, MENSAJE_URGENCIA, detectar


@pytest.mark.parametrize(
    "mensaje",
    [
        "Me duele mucho el pecho y el brazo izquierdo",
        "mi papá no puede respirar",
        "Mi hijo se desmayó en el colegio",
        "tiene una hemorragia que no para",
        "se me cierra la garganta, creo que es alergia",
        "mi abuela tiene la cara torcida y no puede mover el brazo",
    ],
)
def test_detecta_urgencias(mensaje):
    assert detectar(mensaje) == MENSAJE_URGENCIA


@pytest.mark.parametrize(
    "mensaje", ["ya no quiero vivir", "pienso en el suicidio", "me quiero matar"]
)
def test_detecta_crisis_con_linea_de_asistencia(mensaje):
    respuesta = detectar(mensaje)
    assert respuesta == MENSAJE_CRISIS
    assert "135" in respuesta


@pytest.mark.parametrize(
    "mensaje",
    [
        "quiero un turno con cardiología",
        "necesito cambiar el turno del jueves",
        "cuánto sale la consulta pediátrica?",
        "hola!",
    ],
)
def test_mensajes_comunes_no_disparan_alerta(mensaje):
    assert detectar(mensaje) is None
