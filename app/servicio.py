"""Casos de uso del asistente, comunes a todos los canales (portal y Telegram)."""

import asyncio
import logging
import secrets
import time
import weakref
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import reloj
from app.agente import emergencias
from app.agente.contexto import cargar_contexto
from app.agente.herramientas import ejecutar_accion
from app.agente.nucleo import Agente
from app.config import obtener_configuracion
from app.db import AccionPendiente, Conversacion, Intercambio
from app.llm.base import Mensaje, ModeloLenguaje, ModeloNoDisponible
from app.synapse import ClienteSynapse, ErrorSynapse, SesionVencida

log = logging.getLogger(__name__)

MENSAJE_SATURADO = (
    "Estoy recibiendo muchas consultas y no puedo responderte ahora. Probá en unos minutos "
    "o gestioná tu turno desde el portal."
)
MENSAJE_LIMITE = "Recibí muchos mensajes seguidos. Esperá unos minutos y seguimos. 🙏"
MENSAJE_SYNAPSE_CAIDO = (
    "No puedo acceder al sistema de turnos en este momento. Probá más tarde o llamá a la "
    "recepción al (011) 5555-0100."
)


@dataclass
class AccionVisible:
    id: str
    resumen: str


@dataclass
class Respuesta:
    texto: str
    accion: AccionVisible | None = None


@lru_cache
def _modelo_por_defecto() -> ModeloLenguaje:
    from app.llm.gemini import Gemini

    config = obtener_configuracion()
    return Gemini(config.gemini_api_key, config.lista_modelos, config.segundos_espera_modelo)


_modelo_actual: ModeloLenguaje | None = None


def obtener_modelo() -> ModeloLenguaje:
    return _modelo_actual or _modelo_por_defecto()


def usar_modelo(modelo: ModeloLenguaje | None) -> None:
    """Reemplaza el modelo (lo usan los tests y las evaluaciones)."""
    global _modelo_actual
    _modelo_actual = modelo


# ---------- Conversaciones ----------

# Un mensaje por conversación a la vez: si llegan dos juntos (doble envío, dos pestañas),
# el segundo espera y lee el historial ya actualizado en lugar de pisarlo. Los candados
# se liberan solos cuando nadie los usa. Vale para una sola instancia del servicio; con
# varias habría que bloquear la fila (SELECT ... FOR UPDATE).
_candados: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def candado(clave: str) -> asyncio.Lock:
    lock = _candados.get(clave)
    if lock is None:
        lock = asyncio.Lock()
        _candados[clave] = lock
    return lock


def obtener_conversacion(sesion: Session, canal: str, clave: str, usuario_id: int) -> Conversacion:
    conversacion = sesion.scalar(select(Conversacion).where(Conversacion.clave == clave))
    if conversacion is None:
        conversacion = Conversacion(canal=canal, clave=clave, usuario_id=usuario_id, historial=[])
        sesion.add(conversacion)
        sesion.flush()
    elif conversacion.usuario_id != usuario_id:
        # Otro paciente se vinculó a este chat: no hereda la conversación anterior.
        conversacion.usuario_id = usuario_id
        conversacion.historial = []
    return conversacion


def reiniciar(sesion: Session, conversacion: Conversacion) -> None:
    conversacion.historial = []
    _descartar_pendientes(sesion, conversacion.id)
    sesion.commit()


def _historial(conversacion: Conversacion) -> list[Mensaje]:
    vencida = reloj.ahora() - conversacion.actualizada > timedelta(
        hours=obtener_configuracion().horas_contexto
    )
    if vencida:
        return []
    return [Mensaje.desde_dict(m) for m in conversacion.historial]


def _guardar(conversacion: Conversacion, historial: list[Mensaje]) -> None:
    conversacion.historial = [m.a_dict() for m in historial[-60:]]
    conversacion.actualizada = reloj.ahora()


def _excede_limite(sesion: Session, conversacion: Conversacion) -> bool:
    config = obtener_configuracion()
    desde = reloj.ahora() - timedelta(minutes=config.minutos_ventana)
    cantidad = sesion.scalar(
        select(func.count(Intercambio.id)).where(
            Intercambio.conversacion_id == conversacion.id, Intercambio.creado >= desde
        )
    )
    return (cantidad or 0) >= config.mensajes_por_ventana


def _excede_cuota_global(sesion: Session) -> bool:
    """Tope para todo el servicio: muchos chats distintos no pueden agotar la cuota del
    modelo y dejar sin asistente a los demás."""
    config = obtener_configuracion()
    ahora = reloj.ahora()
    por_minuto = sesion.scalar(
        select(func.count(Intercambio.id)).where(
            Intercambio.creado >= ahora - timedelta(minutes=1), Intercambio.pasos > 0
        )
    )
    por_dia = sesion.scalar(
        select(func.count(Intercambio.id)).where(
            Intercambio.creado >= ahora - timedelta(days=1), Intercambio.pasos > 0
        )
    )
    return (por_minuto or 0) >= config.mensajes_globales_por_minuto or (
        por_dia or 0
    ) >= config.mensajes_globales_por_dia


def _descartar_pendientes(sesion: Session, conversacion_id: int) -> None:
    for accion in sesion.scalars(
        select(AccionPendiente).where(
            AccionPendiente.conversacion_id == conversacion_id,
            AccionPendiente.estado == "pendiente",
        )
    ):
        accion.estado = "descartada"
        accion.resuelta = reloj.ahora()


# ---------- Atender un mensaje ----------


async def atender(sesion: Session, conversacion: Conversacion, token: str, texto: str) -> Respuesta:
    inicio = time.perf_counter()
    registro = Intercambio(
        conversacion_id=conversacion.id, canal=conversacion.canal, resultado="ok"
    )
    historial = _historial(conversacion)
    texto = texto.strip()[:1000]

    try:
        if _excede_limite(sesion, conversacion):
            registro.resultado = "limite"
            return Respuesta(MENSAJE_LIMITE)

        urgencia = emergencias.detectar(texto)
        if urgencia:
            registro.resultado = "urgencia"
            historial += [Mensaje("usuario", texto=texto), Mensaje("modelo", urgencia)]
            _guardar(conversacion, historial)
            return Respuesta(urgencia)

        # Después de la detección de urgencias: esa respuesta nunca se raciona.
        if _excede_cuota_global(sesion):
            registro.resultado = "cuota_global"
            return Respuesta(MENSAJE_SATURADO)

        try:
            ctx = await cargar_contexto(ClienteSynapse(token))
            agente = Agente(obtener_modelo(), obtener_configuracion().pasos_maximos)
            resultado = await agente.responder(ctx, historial, texto)
        except ModeloNoDisponible:
            registro.resultado = "modelo_no_disponible"
            return Respuesta(MENSAJE_SATURADO)
        except SesionVencida:
            raise
        except ErrorSynapse as e:
            log.warning("Synapse respondió %s: %s", e.estado, e.mensaje)
            registro.resultado = "synapse_error"
            return Respuesta(MENSAJE_SYNAPSE_CAIDO)

        registro.pasos = resultado.pasos
        registro.tokens_entrada = resultado.tokens_entrada
        registro.tokens_salida = resultado.tokens_salida
        registro.herramientas = resultado.herramientas
        registro.modelo = resultado.modelo
        _guardar(conversacion, historial + resultado.nuevos)

        if resultado.propuesta is None:
            return Respuesta(resultado.texto)

        _descartar_pendientes(sesion, conversacion.id)
        accion = AccionPendiente(
            id=secrets.token_urlsafe(9),
            conversacion_id=conversacion.id,
            herramienta=resultado.herramienta_propuesta,
            argumentos=resultado.propuesta.argumentos,
            resumen=resultado.propuesta.resumen,
            vence=reloj.ahora()
            + timedelta(minutes=obtener_configuracion().minutos_vigencia_accion),
        )
        sesion.add(accion)
        registro.resultado = "propuesta"
        return Respuesta(resultado.texto, AccionVisible(accion.id, accion.resumen))
    finally:
        registro.milisegundos = int((time.perf_counter() - inicio) * 1000)
        sesion.add(registro)
        sesion.commit()


# ---------- Confirmar o descartar una acción ----------


class AccionInvalida(Exception):
    pass


async def resolver_accion(
    sesion: Session, conversacion: Conversacion, accion_id: str, confirmar: bool, token: str
) -> str:
    accion = sesion.get(AccionPendiente, accion_id)
    if accion is None or accion.conversacion_id != conversacion.id:
        raise AccionInvalida("Esa acción no existe.")
    if accion.estado != "pendiente":
        return "Esa acción ya no está disponible. Si querés, empezamos de nuevo."
    if reloj.ahora() > accion.vence:
        accion.estado = "vencida"
        sesion.commit()
        return "Pasó mucho tiempo y la propuesta venció. Pedímelo de nuevo y lo reviso."

    # Transición atómica pendiente → en_curso: si llegan dos confirmaciones a la vez
    # (doble toque, reintento de Telegram), solo una gana y la acción se ejecuta una vez.
    reclamada = sesion.execute(
        update(AccionPendiente)
        .where(AccionPendiente.id == accion.id, AccionPendiente.estado == "pendiente")
        .values(estado="en_curso")
    ).rowcount
    sesion.commit()
    if not reclamada:
        return "Esa acción ya no está disponible. Si querés, empezamos de nuevo."
    sesion.refresh(accion)

    historial = _historial(conversacion)
    accion.resuelta = reloj.ahora()
    if not confirmar:
        accion.estado = "descartada"
        texto = "Listo, no hice ningún cambio. ¿Te ayudo con algo más?"
    else:
        try:
            ctx = await cargar_contexto(ClienteSynapse(token))
            texto = await ejecutar_accion(ctx, accion.herramienta, accion.argumentos)
            accion.estado = "confirmada"
        except SesionVencida:
            # No se llegó a ejecutar: la acción vuelve a quedar disponible para reintentar.
            accion.estado = "pendiente"
            accion.resuelta = None
            sesion.commit()
            raise
        except ErrorSynapse as e:
            accion.estado = "fallida"
            texto = f"No se pudo completar: {e.mensaje}"

    eleccion = "Confirmar" if confirmar else "Descartar"
    historial += [
        Mensaje("usuario", texto=f"[Toqué «{eleccion}» en: {accion.resumen}]"),
        Mensaje("modelo", texto),
    ]
    _guardar(conversacion, historial)
    sesion.commit()
    return texto
