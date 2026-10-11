"""Fixtures: Synapse y Telegram simulados con respx, un modelo de lenguaje con guion y
el reloj congelado el domingo 11/10/2026 a las 10:00."""

import json
from datetime import datetime, timedelta

import httpx
import pytest
import respx

from app import config, credenciales, db, reloj, servicio
from app import synapse as synapse_mod
from app.agente import contexto
from app.canales import telegram
from app.llm.base import LlamadaHerramienta, RespuestaModelo

SYNAPSE = "http://synapse.test/api"
AHORA = datetime(2026, 10, 11, 10, 0)
TOKEN = "token-camila"

ESPECIALIDADES = [{"id": 2, "nombre": "Pediatría"}, {"id": 3, "nombre": "Cardiología"}]
PRESTACIONES = [
    {
        "id": 2,
        "codigo": "P1",
        "nombre": "Consulta pediátrica",
        "tipo": "CONSULTA",
        "especialidad_id": 2,
        "duracion_minutos": 20,
        "precio_particular": "30000",
    },
    {
        "id": 3,
        "codigo": "C1",
        "nombre": "Consulta cardiológica",
        "tipo": "CONSULTA",
        "especialidad_id": 3,
        "duracion_minutos": 30,
        "precio_particular": "40000",
    },
]
PROFESIONALES = [
    {
        "id": 5,
        "nombre": "Martín",
        "apellido": "Ríos",
        "nombre_completo": "Martín Ríos",
        "matricula": "MN 1",
        "especialidades": [ESPECIALIDADES[1]],
    },
    {
        "id": 7,
        "nombre": "Valeria",
        "apellido": "Sosa",
        "nombre_completo": "Valeria Sosa",
        "matricula": "MN 2",
        "especialidades": [ESPECIALIDADES[0]],
    },
]
OSDE = {
    "id": 1,
    "plan_id": 2,
    "numero_afiliado": "61",
    "vigente": True,
    "plan_nombre": "310",
    "obra_social": "OSDE",
}
GRUPO = [
    {
        "id": 1,
        "dni": "32456789",
        "nombre": "Camila",
        "apellido": "O'Connor",
        "nombre_completo": "Camila O'Connor",
        "fecha_nacimiento": "1988-03-17",
        "sexo": "F",
        "afiliaciones": [OSDE],
    },
    {
        "id": 132,
        "dni": "56123456",
        "nombre": "Bautista",
        "apellido": "O'Connor",
        "nombre_completo": "Bautista O'Connor",
        "fecha_nacimiento": "2020-09-01",
        "sexo": "M",
        "afiliaciones": [{**OSDE, "id": 2}],
    },
]


def turno(id_: int, inicio: str, estado: str = "RESERVADO", paciente: int = 0) -> dict:
    return {
        "id": id_,
        "inicio": inicio,
        "estado": estado,
        "paciente": GRUPO[paciente],
        "profesional": PROFESIONALES[0],
        "prestacion": PRESTACIONES[1],
        "autorizacion": None,
    }


TURNOS = [
    turno(10, "2026-10-21T09:00:00"),  # cardiología, en 10 días
    turno(11, "2026-10-11T18:00:00"),  # hoy: menos de 24 h
    turno(12, "2026-10-01T09:00:00", "ATENDIDO"),
]


def disponibilidad(request: httpx.Request) -> httpx.Response:
    """Horarios libres: cada día, 09:00, 09:30 y 15:00 con el profesional de la prestación."""
    params = request.url.params
    prestacion = int(params["prestacion_id"])
    profesional = PROFESIONALES[0] if prestacion == 3 else PROFESIONALES[1]
    desde = datetime.fromisoformat(params["fecha"])
    dias = int(params.get("dias", 1))
    resultado = []
    for i in range(dias):
        dia = desde + timedelta(days=i)
        resultado.append(
            {
                "profesional": profesional,
                "fecha": dia.date().isoformat(),
                "horarios": [
                    f"{dia.date().isoformat()}T{h}:00" for h in ("09:00", "09:30", "15:00")
                ],
            }
        )
    return httpx.Response(200, json=resultado)


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SYNAPSE_URL", SYNAPSE)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_SECRETO_WEBHOOK", "secreto")
    monkeypatch.setenv("CREDENCIALES_MODO", "demo")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(reloj, "ahora", lambda: AHORA)
    config.obtener_configuracion.cache_clear()
    db._fabrica.cache_clear()
    contexto.vaciar_cache()
    credenciales._tokens.clear()
    synapse_mod._clientes.clear()
    credenciales._usuario_demo.clear()
    telegram._procesados.clear()
    yield
    servicio.usar_modelo(None)


@pytest.fixture
def synapse():
    with respx.mock(base_url=SYNAPSE, assert_all_called=False) as router:
        router.post("/auth/login").respond(200, json={"token": TOKEN, "usuario": {}})
        router.get("/auth/yo").respond(
            200,
            json={"id": 50, "email": "c@x", "rol": "PACIENTE", "activo": True, "paciente_id": 1},
        )
        router.get("/especialidades").respond(200, json=ESPECIALIDADES)
        router.get("/prestaciones").respond(200, json=PRESTACIONES)
        router.get("/profesionales").respond(200, json=PROFESIONALES)
        router.get("/portal/grupo").respond(200, json=GRUPO)
        router.get("/portal/turnos").respond(200, json=TURNOS)
        router.get("/disponibilidad").mock(side_effect=disponibilidad)
        router.get("/estimacion").respond(
            200,
            json={
                "importe": "2000",
                "concepto": "COSEGURO",
                "cubierto": True,
                "requiere_autorizacion": False,
            },
        )
        router.post("/turnos", name="reservar").mock(
            side_effect=lambda req: httpx.Response(
                201,
                json={
                    **turno(99, json.loads(req.content)["inicio"]),
                    "prestacion": PRESTACIONES[1],
                },
            )
        )
        router.post(url__regex=r"/turnos/\d+/cancelar", name="cancelar").respond(
            200, json=turno(10, "2026-10-21T09:00:00", "CANCELADO")
        )
        router.post(url__regex=r"/turnos/\d+/reprogramar", name="reprogramar").mock(
            side_effect=lambda req: httpx.Response(
                200, json=turno(10, json.loads(req.content)["inicio"])
            )
        )
        yield router


class ModeloGuionado:
    """Modelo falso: devuelve las respuestas en orden y guarda lo que recibió."""

    def __init__(self, *respuestas: RespuestaModelo):
        self.respuestas = list(respuestas)
        self.recibido: list[tuple[str, list]] = []

    async def generar(self, sistema, historial, herramientas):
        self.recibido.append((sistema, list(historial)))
        if not self.respuestas:
            raise AssertionError("El modelo recibió más llamadas de las previstas")
        return self.respuestas.pop(0)


def texto(t: str) -> RespuestaModelo:
    return RespuestaModelo(texto=t, llamadas=[], modelo="falso")


def pide(nombre: str, **argumentos) -> RespuestaModelo:
    return RespuestaModelo(
        texto=None,
        llamadas=[LlamadaHerramienta(id=f"id-{nombre}", nombre=nombre, argumentos=argumentos)],
        modelo="falso",
    )


@pytest.fixture
def modelo():
    def instalar(*respuestas: RespuestaModelo) -> ModeloGuionado:
        guion = ModeloGuionado(*respuestas)
        servicio.usar_modelo(guion)
        return guion

    return instalar
