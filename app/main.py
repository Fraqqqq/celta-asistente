"""API del asistente: chat del portal, vinculación de Telegram y webhook del bot."""

import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import servicio
from app.canales import telegram
from app.config import obtener_configuracion
from app.db import obtener_sesion
from app.synapse import ClienteSynapse, ErrorSynapse, SesionVencida

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# Secreto que Telegram manda en cada llamada al webhook. Si no se configura uno, se genera
# al arrancar: el webhook se vuelve a registrar en cada inicio con el secreto vigente.
_secreto_webhook: list[str] = []


def secreto_webhook() -> str:
    if not _secreto_webhook:
        configurado = obtener_configuracion().telegram_secreto_webhook
        _secreto_webhook.append(configurado or secrets.token_urlsafe(32))
    return _secreto_webhook[0]


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    config = obtener_configuracion()
    if config.url_webhook and config.telegram_bot_token:
        respuesta = await _cliente_telegram().llamar(
            "setWebhook",
            url=config.url_webhook,
            secret_token=secreto_webhook(),
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
        )
        log.info("Webhook de Telegram en %s: %s", config.url_webhook, respuesta.get("ok"))
    yield


app = FastAPI(
    title="Asistente del Centro Médico Celta",
    version="1.0.0",
    description="Agente con IA que gestiona turnos sobre la API de Synapse. Datos ficticios.",
    lifespan=ciclo_de_vida,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=obtener_configuracion().origenes_permitidos,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(SesionVencida)
async def sesion_vencida(_: Request, error: SesionVencida):
    return JSONResponse(status_code=401, content={"mensaje": error.mensaje})


# ---------- Identidad del paciente (portal) ----------

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Paciente:
    usuario_id: int
    token: str


async def paciente_actual(
    credenciales: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Paciente:
    """El widget manda el token de Synapse del paciente; Synapse dice quién es."""
    if credenciales is None:
        raise HTTPException(401, "Iniciá sesión en el portal para usar el asistente.")
    try:
        yo = await ClienteSynapse(credenciales.credentials).yo()
    except ErrorSynapse as e:
        raise HTTPException(401, e.mensaje) from e
    if yo["rol"] != "PACIENTE":
        raise HTTPException(403, "El asistente es solo para pacientes.")
    return Paciente(yo["id"], credenciales.credentials)


def _conversacion(sesion: Session, paciente: Paciente):
    return servicio.obtener_conversacion(
        sesion, "portal", f"portal:{paciente.usuario_id}", paciente.usuario_id
    )


# ---------- Chat del portal ----------


class EntradaChat(BaseModel):
    mensaje: str = Field(min_length=1, max_length=1000)


class AccionSalida(BaseModel):
    id: str
    resumen: str


class SalidaChat(BaseModel):
    texto: str
    accion: AccionSalida | None = None


class EntradaAccion(BaseModel):
    confirmar: bool


class MensajeVisible(BaseModel):
    rol: str
    texto: str


@app.post("/api/chat", response_model=SalidaChat, tags=["Portal"])
async def chat(
    datos: EntradaChat,
    paciente: Paciente = Depends(paciente_actual),
    sesion: Session = Depends(obtener_sesion),
):
    respuesta = await servicio.atender(
        sesion, _conversacion(sesion, paciente), paciente.token, datos.mensaje
    )
    accion = respuesta.accion
    return SalidaChat(
        texto=respuesta.texto,
        accion=AccionSalida(id=accion.id, resumen=accion.resumen) if accion else None,
    )


@app.post("/api/chat/acciones/{accion_id}", response_model=SalidaChat, tags=["Portal"])
async def resolver(
    accion_id: str,
    datos: EntradaAccion,
    paciente: Paciente = Depends(paciente_actual),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        texto = await servicio.resolver_accion(
            sesion, _conversacion(sesion, paciente), accion_id, datos.confirmar, paciente.token
        )
    except servicio.AccionInvalida as e:
        raise HTTPException(404, str(e)) from e
    return SalidaChat(texto=texto)


@app.get("/api/chat/historial", response_model=list[MensajeVisible], tags=["Portal"])
async def historial(
    paciente: Paciente = Depends(paciente_actual), sesion: Session = Depends(obtener_sesion)
):
    """Lo que el widget muestra al abrirse: solo los textos, sin llamadas a herramientas."""
    conversacion = _conversacion(sesion, paciente)
    sesion.commit()
    return [
        MensajeVisible(rol=m["rol"], texto=m["texto"])
        for m in conversacion.historial
        if m["rol"] in {"usuario", "modelo"} and m.get("texto") and not m.get("llamadas")
    ][-40:]


@app.post("/api/chat/reiniciar", status_code=204, tags=["Portal"])
async def reiniciar(
    paciente: Paciente = Depends(paciente_actual), sesion: Session = Depends(obtener_sesion)
):
    servicio.reiniciar(sesion, _conversacion(sesion, paciente))


class SalidaVinculo(BaseModel):
    enlace: str
    minutos_vigencia: int


@app.post("/api/vinculos/telegram", response_model=SalidaVinculo, tags=["Portal"])
async def vincular_telegram(paciente: Paciente = Depends(paciente_actual)):
    """Enlace de un solo uso para abrir el bot ya vinculado a la cuenta del paciente."""
    config = obtener_configuracion()
    codigo = telegram.crear_codigo(paciente.usuario_id)
    return SalidaVinculo(
        enlace=f"https://t.me/{config.telegram_bot_usuario}?start={codigo}",
        minutos_vigencia=config.minutos_vigencia_codigo,
    )


# ---------- Telegram ----------


@app.post("/api/telegram/webhook", include_in_schema=False)
async def webhook_telegram(
    update: dict,
    tareas: BackgroundTasks,
    secreto: str = Header(default="", alias="X-Telegram-Bot-Api-Secret-Token"),
):
    if not hmac.compare_digest(secreto.encode(), secreto_webhook().encode()):
        raise HTTPException(403, "Secreto inválido.")
    # Se responde enseguida y se procesa aparte: si el webhook tarda, Telegram reintenta.
    tareas.add_task(telegram.procesar, _cliente_telegram(), update)
    return {"ok": True}


_tg: list[telegram.ClienteTelegram] = []


def _cliente_telegram() -> telegram.ClienteTelegram:
    if not _tg:
        _tg.append(telegram.ClienteTelegram(obtener_configuracion().telegram_bot_token))
    return _tg[0]


@app.get("/api/salud", tags=["Sistema"])
async def salud():
    return {"estado": "ok"}
