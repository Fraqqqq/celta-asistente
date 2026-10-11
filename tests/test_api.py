"""El chat del portal de punta a punta: API → agente → Synapse (simulado)."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import reloj
from app.db import AccionPendiente, nueva_sesion
from app.main import app
from tests.conftest import AHORA, TOKEN, pide, texto

AUTH = {"Authorization": f"Bearer {TOKEN}"}
RESERVA = pide(
    "reservar_turno",
    paciente_id=1,
    prestacion_id=3,
    profesional_id=5,
    fecha_hora="2026-10-12T09:30",
)


@pytest.fixture
def api(synapse):
    return TestClient(app)


def test_sin_token_no_hay_chat(api):
    assert api.post("/api/chat", json={"mensaje": "hola"}).status_code == 401


def test_el_asistente_es_solo_para_pacientes(api, synapse):
    synapse.get("/auth/yo").respond(200, json={"id": 2, "rol": "RECEPCION"})
    r = api.post("/api/chat", json={"mensaje": "hola"}, headers=AUTH)
    assert r.status_code == 403


def test_reserva_completa_con_confirmacion(api, modelo, synapse):
    modelo(RESERVA)
    r = api.post("/api/chat", json={"mensaje": "lunes 9:30 cardio"}, headers=AUTH).json()
    accion = r["accion"]
    assert "Consulta cardiológica" in accion["resumen"]
    assert not synapse["reservar"].called

    r = api.post(f"/api/chat/acciones/{accion['id']}", json={"confirmar": True}, headers=AUTH)
    assert r.json()["texto"].startswith("✅ Listo, turno reservado")
    assert synapse["reservar"].called

    # Tocar dos veces no reserva dos veces.
    r = api.post(f"/api/chat/acciones/{accion['id']}", json={"confirmar": True}, headers=AUTH)
    assert "ya no está disponible" in r.json()["texto"]
    assert synapse["reservar"].call_count == 1


def test_descartar_no_toca_synapse(api, modelo, synapse):
    modelo(RESERVA)
    accion = api.post("/api/chat", json={"mensaje": "x"}, headers=AUTH).json()["accion"]
    r = api.post(f"/api/chat/acciones/{accion['id']}", json={"confirmar": False}, headers=AUTH)
    assert "no hice ningún cambio" in r.json()["texto"]
    assert not synapse["reservar"].called


def test_una_accion_vencida_no_se_ejecuta(api, modelo, synapse, monkeypatch):
    modelo(RESERVA)
    accion = api.post("/api/chat", json={"mensaje": "x"}, headers=AUTH).json()["accion"]
    monkeypatch.setattr(reloj, "ahora", lambda: AHORA + timedelta(minutes=16))
    r = api.post(f"/api/chat/acciones/{accion['id']}", json={"confirmar": True}, headers=AUTH)
    assert "venció" in r.json()["texto"]
    assert not synapse["reservar"].called


def test_no_se_puede_confirmar_la_accion_de_otro_paciente(api, modelo, synapse):
    modelo(RESERVA)
    accion = api.post("/api/chat", json={"mensaje": "x"}, headers=AUTH).json()["accion"]
    synapse.get("/auth/yo").respond(200, json={"id": 51, "rol": "PACIENTE"})
    r = api.post(f"/api/chat/acciones/{accion['id']}", json={"confirmar": True}, headers=AUTH)
    assert r.status_code == 404
    assert not synapse["reservar"].called


def test_una_propuesta_nueva_reemplaza_a_la_anterior(api, modelo):
    modelo(RESERVA, RESERVA)
    primera = api.post("/api/chat", json={"mensaje": "x"}, headers=AUTH).json()["accion"]
    api.post("/api/chat", json={"mensaje": "y"}, headers=AUTH)
    with nueva_sesion() as sesion:
        assert sesion.get(AccionPendiente, primera["id"]).estado == "descartada"


def test_si_synapse_rechaza_la_accion_se_explica(api, modelo, synapse):
    synapse.post("/turnos", name="reservar").respond(
        422,
        json={
            "codigo": "regla_incumplida",
            "mensaje": "El horario ya fue tomado.",
            "regla": "RN-01",
        },
    )
    modelo(RESERVA)
    accion = api.post("/api/chat", json={"mensaje": "x"}, headers=AUTH).json()["accion"]
    r = api.post(f"/api/chat/acciones/{accion['id']}", json={"confirmar": True}, headers=AUTH)
    assert r.json()["texto"] == "No se pudo completar: El horario ya fue tomado."


def test_urgencia_responde_sin_llamar_al_modelo(api, modelo):
    guion = modelo()  # sin respuestas: si se lo llama, el test falla
    r = api.post(
        "/api/chat", json={"mensaje": "me duele el pecho, no puedo respirar"}, headers=AUTH
    )
    assert "107" in r.json()["texto"]
    assert guion.recibido == []


def test_limite_de_mensajes(api, modelo, monkeypatch):
    monkeypatch.setenv("MENSAJES_POR_VENTANA", "2")
    from app.config import obtener_configuracion

    obtener_configuracion.cache_clear()
    modelo(texto("uno"), texto("dos"))
    for _ in range(2):
        api.post("/api/chat", json={"mensaje": "hola"}, headers=AUTH)
    r = api.post("/api/chat", json={"mensaje": "hola"}, headers=AUTH)
    assert "muchos mensajes" in r.json()["texto"]


def test_modelo_caido(api, modelo):
    from app.llm.base import ModeloNoDisponible

    class Caido:
        async def generar(self, *a):
            raise ModeloNoDisponible()

    from app import servicio

    servicio.usar_modelo(Caido())
    r = api.post("/api/chat", json={"mensaje": "hola"}, headers=AUTH)
    assert r.status_code == 200
    assert "muchas consultas" in r.json()["texto"]


def test_historial_visible_y_reinicio(api, modelo):
    modelo(pide("ver_mis_turnos"), texto("Tenés un turno el 21/10."))
    api.post("/api/chat", json={"mensaje": "qué turnos tengo"}, headers=AUTH)
    historial = api.get("/api/chat/historial", headers=AUTH).json()
    assert historial == [
        {"rol": "usuario", "texto": "qué turnos tengo"},
        {"rol": "modelo", "texto": "Tenés un turno el 21/10."},
    ]
    assert api.post("/api/chat/reiniciar", headers=AUTH).status_code == 204
    assert api.get("/api/chat/historial", headers=AUTH).json() == []


def test_enlace_de_vinculacion_de_telegram(api):
    r = api.post("/api/vinculos/telegram", headers=AUTH).json()
    assert r["enlace"].startswith("https://t.me/celta_asistente_fraqqqq_bot?start=")
    assert r["minutos_vigencia"] == 10


def test_webhook_exige_el_secreto(api):
    assert api.post("/api/telegram/webhook", json={"update_id": 1}).status_code == 403
    r = api.post(
        "/api/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "secreto"},
    )
    assert r.status_code == 200


def test_al_arrancar_registra_el_webhook_de_telegram(synapse, monkeypatch):
    import respx

    from app.config import obtener_configuracion

    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://asistente.example.com")
    obtener_configuracion.cache_clear()
    with respx.mock(base_url="https://api.telegram.org/bot123:abc") as tg:
        registro = tg.post("/setWebhook").respond(200, json={"ok": True})
        with TestClient(app):
            pass
    datos = json.loads(registro.calls[0].request.content)
    assert datos["url"] == "https://asistente.example.com/api/telegram/webhook"
    assert datos["secret_token"] == "secreto"


def test_cuota_global_protege_al_resto_de_los_pacientes(api, modelo, monkeypatch):
    from app.config import obtener_configuracion

    monkeypatch.setenv("MENSAJES_GLOBALES_POR_MINUTO", "1")
    obtener_configuracion.cache_clear()
    modelo(texto("hola"))
    api.post("/api/chat", json={"mensaje": "hola"}, headers=AUTH)
    r = api.post("/api/chat", json={"mensaje": "hola de nuevo"}, headers=AUTH)
    assert "muchas consultas" in r.json()["texto"]
    # Las urgencias nunca se racionan.
    r = api.post("/api/chat", json={"mensaje": "no puedo respirar"}, headers=AUTH)
    assert "107" in r.json()["texto"]
