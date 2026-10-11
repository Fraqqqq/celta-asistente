"""El canal de Telegram con el Bot API simulado."""

import json

import httpx
import pytest
import respx

from app.canales import telegram
from app.config import obtener_configuracion
from tests.conftest import pide, texto

CHAT = 4242


def mensaje(texto_: str, update_id: int = 1, tipo: str = "private") -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": CHAT, "type": tipo}, "text": texto_},
    }


@pytest.fixture
def bot():
    """Cliente de Telegram con el Bot API simulado; `bot.enviados` junta los textos."""
    with respx.mock(base_url="https://api.telegram.org/bot123:abc", assert_all_called=False) as r:
        enviados: list[dict] = []

        def guardar(request: httpx.Request) -> httpx.Response:
            enviados.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        r.post("/sendMessage").mock(side_effect=guardar)
        r.post(url__regex=r"/(sendChatAction|answerCallbackQuery|editMessageReplyMarkup)").respond(
            200, json={"ok": True}
        )
        cliente = telegram.ClienteTelegram("123:abc", httpx.AsyncClient())
        cliente.enviados = enviados
        yield cliente


async def test_start_en_modo_demo_vincula_al_paciente_ficticio(bot, synapse):
    await telegram.procesar(bot, mensaje("/start"))
    assert "Modo demostración" in bot.enviados[0]["text"]


async def test_reserva_con_botones_y_confirmacion(bot, synapse, modelo):
    modelo(
        pide(
            "reservar_turno",
            paciente_id=1,
            prestacion_id=3,
            profesional_id=5,
            fecha_hora="2026-10-12T09:30",
        )
    )
    await telegram.procesar(bot, mensaje("turno cardio lunes 9:30"))
    propuesta = bot.enviados[-1]
    botones = propuesta["reply_markup"]["inline_keyboard"][0]
    assert "Consulta cardiológica" in propuesta["text"]
    assert [b["text"] for b in botones] == ["✅ Confirmar", "✖️ Descartar"]

    await telegram.procesar(
        bot,
        {
            "update_id": 2,
            "callback_query": {
                "id": "cb1",
                "data": botones[0]["callback_data"],
                "message": {"message_id": 9, "chat": {"id": CHAT}},
            },
        },
    )
    assert synapse["reservar"].called
    assert bot.enviados[-1]["text"].startswith("✅ Listo, turno reservado")


async def test_updates_repetidos_se_ignoran(bot, synapse, modelo):
    guion = modelo(texto("¡Hola!"))
    await telegram.procesar(bot, mensaje("hola", update_id=7))
    await telegram.procesar(bot, mensaje("hola", update_id=7))
    assert len(guion.recibido) == 1


async def test_grupos_se_ignoran(bot, synapse):
    await telegram.procesar(bot, mensaje("hola", tipo="group"))
    assert bot.enviados == []


async def test_mensajes_que_no_son_texto(bot):
    await telegram.procesar(
        bot, {"update_id": 3, "message": {"chat": {"id": CHAT, "type": "private"}}}
    )
    assert bot.enviados[0]["text"] == telegram.SOLO_TEXTO


async def test_nueva_conversacion(bot, synapse, modelo):
    modelo(texto("uno"), texto("dos"))
    await telegram.procesar(bot, mensaje("hola", update_id=1))
    await telegram.procesar(bot, mensaje("/nueva", update_id=2))
    await telegram.procesar(bot, mensaje("hola de nuevo", update_id=3))
    assert bot.enviados[-1]["text"] == "dos"


class TestModoIntercambio:
    """En producción, el chat se vincula con un código generado desde el portal."""

    @pytest.fixture(autouse=True)
    def modo(self, monkeypatch, synapse):
        monkeypatch.setenv("CREDENCIALES_MODO", "intercambio")
        obtener_configuracion.cache_clear()
        synapse.post("/integraciones/asistente/token", name="intercambio").respond(
            200, json={"token": "token-intercambiado", "usuario": {}}
        )

    async def test_sin_vincular_pide_vincular_desde_el_portal(self, bot):
        await telegram.procesar(bot, mensaje("quiero un turno"))
        assert bot.enviados[0]["text"] == telegram.SIN_VINCULO

    async def test_codigo_vincula_una_sola_vez(self, bot, synapse, modelo):
        codigo = telegram.crear_codigo(usuario_id=50)
        await telegram.procesar(bot, mensaje(f"/start {codigo}", update_id=1))
        assert bot.enviados[-1]["text"].startswith("✅ Cuenta vinculada")

        modelo(texto("¡Hola Camila!"))
        await telegram.procesar(bot, mensaje("hola", update_id=2))
        assert bot.enviados[-1]["text"] == "¡Hola Camila!"
        assert synapse["intercambio"].called
        pedido = synapse["intercambio"].calls[0].request
        assert json.loads(pedido.content) == {"usuario_id": 50}

        # El mismo enlace no sirve para otro chat.
        otro = {
            "update_id": 3,
            "message": {"chat": {"id": 1, "type": "private"}, "text": f"/start {codigo}"},
        }
        await telegram.procesar(bot, otro)
        assert bot.enviados[-1]["text"] == telegram.ENLACE_VENCIDO

    async def test_desvincular(self, bot, modelo):
        codigo = telegram.crear_codigo(usuario_id=50)
        await telegram.procesar(bot, mensaje(f"/start {codigo}", update_id=1))
        await telegram.procesar(bot, mensaje("/desvincular", update_id=2))
        await telegram.procesar(bot, mensaje("hola", update_id=3))
        assert bot.enviados[-1]["text"] == telegram.SIN_VINCULO
