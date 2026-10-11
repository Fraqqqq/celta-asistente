"""Canal de Telegram: traduce updates del Bot API a casos de uso del asistente."""

import logging
import secrets
from collections import OrderedDict
from datetime import timedelta

import httpx

from app import credenciales, reloj, servicio
from app.config import obtener_configuracion
from app.db import CodigoVinculo, VinculoTelegram, nueva_sesion
from app.synapse import SesionVencida

log = logging.getLogger(__name__)

BIENVENIDA = (
    "¡Hola! Soy el asistente del Centro Médico Celta 🩺\n\n"
    "Puedo sacarte un turno, mostrarte los que tenés, cambiarlos o cancelarlos, y "
    "responder dudas del centro. Escribime como le escribirías a la recepción, por "
    "ejemplo: «quiero un turno con dermatología la semana que viene a la mañana».\n\n"
    "Antes de reservar, cambiar o cancelar algo, siempre te pido que lo confirmes."
)
BIENVENIDA_DEMO = (
    "\n\n🧪 Modo demostración: estás usando la cuenta de Camila O'Connor, una paciente "
    "ficticia. Todos los datos son inventados."
)
SIN_VINCULO = (
    "Para usar el asistente, primero vinculá tu cuenta: entrá al portal del paciente, "
    "abrí el asistente y tocá «Seguir en Telegram»."
)
AYUDA = (
    "Comandos:\n"
    "/nueva: empezar una conversación nueva\n"
    "/desvincular: desconectar tu cuenta de este chat\n"
    "/ayuda: ver este mensaje\n\n"
    "Para todo lo demás, escribime como en cualquier chat."
)
ENLACE_VENCIDO = "El enlace venció o ya se usó. Generá uno nuevo desde el portal."
SOLO_TEXTO = "Por ahora solo leo mensajes de texto. ¿Me lo escribís?"


class ClienteTelegram:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None):
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = http or httpx.AsyncClient(timeout=40.0)

    async def llamar(self, metodo: str, **datos) -> dict:
        respuesta = await self._http.post(f"{self._base}/{metodo}", json=datos)
        cuerpo = respuesta.json()
        if not cuerpo.get("ok"):
            log.warning("Telegram %s falló: %s", metodo, cuerpo.get("description"))
        return cuerpo

    async def enviar(self, chat_id: int, texto: str, botones: list[list[dict]] | None = None):
        datos: dict = {"chat_id": chat_id, "text": texto[:4000]}
        if botones:
            datos["reply_markup"] = {"inline_keyboard": botones}
        return await self.llamar("sendMessage", **datos)


# Telegram reintenta si el webhook tarda: se descartan updates repetidos.
_procesados: OrderedDict[int, None] = OrderedDict()


def _ya_procesado(update_id: int) -> bool:
    if update_id in _procesados:
        return True
    _procesados[update_id] = None
    while len(_procesados) > 1000:
        _procesados.popitem(last=False)
    return False


def _botones(accion_id: str) -> list[list[dict]]:
    return [
        [
            {"text": "✅ Confirmar", "callback_data": f"c:{accion_id}"},
            {"text": "✖️ Descartar", "callback_data": f"d:{accion_id}"},
        ]
    ]


def crear_codigo(usuario_id: int) -> str:
    """Código de un solo uso para el enlace t.me/<bot>?start=<código>."""
    codigo = secrets.token_urlsafe(24)
    with nueva_sesion() as sesion:
        sesion.add(
            CodigoVinculo(
                codigo=codigo,
                usuario_id=usuario_id,
                vence=reloj.ahora()
                + timedelta(minutes=obtener_configuracion().minutos_vigencia_codigo),
            )
        )
        sesion.commit()
    return codigo


async def _usuario_del_chat(chat_id: int) -> int | None:
    with nueva_sesion() as sesion:
        vinculo = sesion.get(VinculoTelegram, chat_id)
        if vinculo:
            return vinculo.usuario_id
    if obtener_configuracion().credenciales_modo == "demo":
        usuario_id = await credenciales.usuario_demo()
        _vincular(chat_id, usuario_id)
        return usuario_id
    return None


def _vincular(chat_id: int, usuario_id: int) -> None:
    with nueva_sesion() as sesion:
        vinculo = sesion.get(VinculoTelegram, chat_id)
        if vinculo:
            vinculo.usuario_id = usuario_id
        else:
            sesion.add(VinculoTelegram(chat_id=chat_id, usuario_id=usuario_id))
        sesion.commit()


def _canjear_codigo(codigo: str, chat_id: int) -> bool:
    with nueva_sesion() as sesion:
        registro = sesion.get(CodigoVinculo, codigo)
        if registro is None or registro.usado or registro.vence < reloj.ahora():
            return False
        registro.usado = True
        sesion.commit()
        _vincular(chat_id, registro.usuario_id)
        return True


async def _comando(tg: ClienteTelegram, chat_id: int, texto: str) -> bool:
    """Atiende los comandos. Devuelve False si el texto no era un comando."""
    comando, _, argumento = texto.partition(" ")
    comando = comando.split("@")[0].lower()
    demo = obtener_configuracion().credenciales_modo == "demo"

    if comando == "/start":
        if argumento and not demo:
            if _canjear_codigo(argumento.strip(), chat_id):
                await tg.enviar(chat_id, "✅ Cuenta vinculada.\n\n" + BIENVENIDA)
            else:
                await tg.enviar(chat_id, ENLACE_VENCIDO)
            return True
        vinculado = await _usuario_del_chat(chat_id) is not None
        await tg.enviar(
            chat_id,
            BIENVENIDA + (BIENVENIDA_DEMO if demo else "" if vinculado else "\n\n" + SIN_VINCULO),
        )
        return True
    if comando == "/ayuda" or comando == "/help":
        await tg.enviar(chat_id, AYUDA)
        return True
    if comando == "/nueva":
        usuario_id = await _usuario_del_chat(chat_id)
        if usuario_id is not None:
            with nueva_sesion() as sesion:
                conversacion = servicio.obtener_conversacion(
                    sesion, "telegram", f"telegram:{chat_id}", usuario_id
                )
                servicio.reiniciar(sesion, conversacion)
        await tg.enviar(chat_id, "Listo, empezamos de cero. ¿En qué te ayudo?")
        return True
    if comando == "/desvincular":
        with nueva_sesion() as sesion:
            vinculo = sesion.get(VinculoTelegram, chat_id)
            if vinculo:
                sesion.delete(vinculo)
                sesion.commit()
        await tg.enviar(chat_id, "Desvinculé tu cuenta de este chat. ¡Hasta pronto!")
        return True
    return False


async def _mensaje(tg: ClienteTelegram, chat_id: int, texto: str) -> None:
    if texto.startswith("/") and await _comando(tg, chat_id, texto):
        return
    usuario_id = await _usuario_del_chat(chat_id)
    if usuario_id is None:
        await tg.enviar(chat_id, SIN_VINCULO)
        return
    await tg.llamar("sendChatAction", chat_id=chat_id, action="typing")
    token = await credenciales.token_para(usuario_id)
    with nueva_sesion() as sesion:
        conversacion = servicio.obtener_conversacion(
            sesion, "telegram", f"telegram:{chat_id}", usuario_id
        )
        respuesta = await servicio.atender(sesion, conversacion, token, texto)
    if respuesta.accion:
        await tg.enviar(
            chat_id,
            f"{respuesta.texto}\n\n📋 {respuesta.accion.resumen}",
            _botones(respuesta.accion.id),
        )
    else:
        await tg.enviar(chat_id, respuesta.texto)


async def _boton(tg: ClienteTelegram, consulta: dict) -> None:
    mensaje = consulta.get("message") or {}
    chat_id = mensaje.get("chat", {}).get("id")
    tipo, _, accion_id = (consulta.get("data") or "").partition(":")
    await tg.llamar("answerCallbackQuery", callback_query_id=consulta["id"])
    if chat_id is None or tipo not in {"c", "d"}:
        return
    usuario_id = await _usuario_del_chat(chat_id)
    if usuario_id is None:
        await tg.enviar(chat_id, SIN_VINCULO)
        return
    confirmar = tipo == "c"
    # Se sacan los botones para que no se pueda tocar dos veces.
    await tg.llamar(
        "editMessageReplyMarkup",
        chat_id=chat_id,
        message_id=mensaje.get("message_id"),
        reply_markup={"inline_keyboard": []},
    )
    token = await credenciales.token_para(usuario_id)
    with nueva_sesion() as sesion:
        conversacion = servicio.obtener_conversacion(
            sesion, "telegram", f"telegram:{chat_id}", usuario_id
        )
        try:
            texto = await servicio.resolver_accion(
                sesion, conversacion, accion_id, confirmar, token
            )
        except servicio.AccionInvalida:
            texto = "Esa acción ya no está disponible."
    await tg.enviar(chat_id, texto)


async def procesar(tg: ClienteTelegram, update: dict) -> None:
    if _ya_procesado(update.get("update_id", -1)):
        return
    if "callback_query" in update:
        chat_id = update["callback_query"].get("message", {}).get("chat", {}).get("id")
        manejador = _boton(tg, update["callback_query"])
    elif "message" in update:
        mensaje = update["message"]
        chat_id = mensaje["chat"]["id"]
        if mensaje["chat"].get("type") != "private":
            return  # el asistente solo atiende chats privados
        texto = mensaje.get("text")
        manejador = _mensaje(tg, chat_id, texto) if texto else tg.enviar(chat_id, SOLO_TEXTO)
    else:
        return

    # Un mensaje por chat a la vez, para que dos mensajes seguidos no pisen el historial.
    async with servicio.candado(f"telegram:{chat_id}"):
        try:
            await manejador
        except SesionVencida:
            log.warning("Token rechazado por Synapse para el chat %s", chat_id)
            if chat_id:
                usuario_id = await _usuario_del_chat(chat_id)
                if usuario_id is not None:
                    credenciales.olvidar(usuario_id)
                await tg.enviar(chat_id, "Se cortó la conexión con el sistema. Probá de nuevo.")
        except Exception:
            log.exception("Error procesando el update %s", update.get("update_id"))
            if chat_id:
                await tg.enviar(chat_id, "Uy, algo falló de mi lado. Probá de nuevo en un rato.")
