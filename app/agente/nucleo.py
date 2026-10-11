"""El bucle del agente: el modelo pide herramientas, el código las ejecuta, y se repite
hasta que el modelo responde con texto o propone una acción para confirmar."""

import logging
from dataclasses import dataclass, field

from app.agente import prompt
from app.agente.contexto import Contexto
from app.agente.herramientas import (
    ACCIONES,
    CONSULTAS,
    DEFINICIONES,
    ErrorHerramienta,
    Propuesta,
)
from app.llm.base import Mensaje, ModeloLenguaje, ResultadoHerramienta
from app.synapse import ErrorSynapse, SesionVencida

log = logging.getLogger(__name__)

SIN_RESPUESTA = "Perdón, no pude resolverlo. ¿Me lo contás de otra forma?"
TEXTO_PROPUESTA = "Te dejo el resumen para que lo confirmes:"
MENSAJES_HISTORIAL = 30


@dataclass
class Resultado:
    texto: str
    nuevos: list[Mensaje]
    propuesta: Propuesta | None = None
    herramienta_propuesta: str | None = None
    pasos: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    herramientas: list[str] = field(default_factory=list)
    modelo: str | None = None


def recortar(historial: list[Mensaje], maximo: int = MENSAJES_HISTORIAL) -> list[Mensaje]:
    """Conserva los últimos mensajes sin cortar un intercambio de herramientas por la mitad:
    el recorte siempre empieza en un mensaje escrito por el paciente."""
    if len(historial) <= maximo:
        return historial
    recorte = historial[-maximo:]
    for i, m in enumerate(recorte):
        if m.rol == "usuario":
            return recorte[i:]
    return []


class Agente:
    def __init__(self, modelo: ModeloLenguaje, pasos_maximos: int = 6):
        self._modelo = modelo
        self._pasos_maximos = pasos_maximos

    async def _ejecutar_consulta(self, ctx: Contexto, nombre: str, args: dict) -> dict:
        try:
            return await CONSULTAS[nombre](ctx, args)
        except ErrorHerramienta as e:
            return {"error": str(e)}
        except SesionVencida:
            raise
        except ErrorSynapse as e:
            return {"error": e.mensaje}

    async def responder(self, ctx: Contexto, historial: list[Mensaje], texto: str) -> Resultado:
        sistema = prompt.construir(ctx)
        nuevos = [Mensaje("usuario", texto=texto)]
        resultado = Resultado(texto=SIN_RESPUESTA, nuevos=nuevos)

        for _ in range(self._pasos_maximos):
            respuesta = await self._modelo.generar(
                sistema, recortar(historial) + nuevos, DEFINICIONES
            )
            resultado.pasos += 1
            resultado.tokens_entrada += respuesta.tokens_entrada
            resultado.tokens_salida += respuesta.tokens_salida
            resultado.modelo = respuesta.modelo
            nuevos.append(
                Mensaje("modelo", respuesta.texto, respuesta.llamadas, modelo=respuesta.modelo)
            )
            if not respuesta.llamadas:
                resultado.texto = respuesta.texto or SIN_RESPUESTA
                return resultado

            salidas: list[ResultadoHerramienta] = []
            for llamada in respuesta.llamadas:
                resultado.herramientas.append(llamada.nombre)
                if llamada.nombre in CONSULTAS:
                    contenido = await self._ejecutar_consulta(
                        ctx, llamada.nombre, llamada.argumentos
                    )
                elif llamada.nombre in ACCIONES:
                    contenido = await self._proponer(
                        ctx, llamada.nombre, llamada.argumentos, resultado
                    )
                else:
                    contenido = {"error": f"No existe la herramienta {llamada.nombre}."}
                salidas.append(ResultadoHerramienta(llamada.id, llamada.nombre, contenido))
            nuevos.append(Mensaje("herramienta", resultados=salidas))

            if resultado.propuesta:
                # El turno del modelo termina acá: el resto lo decide el paciente con los
                # botones. Se agrega la respuesta al historial sin gastar otra llamada.
                resultado.texto = respuesta.texto or TEXTO_PROPUESTA
                nuevos.append(
                    Mensaje(
                        "modelo",
                        f"{resultado.texto} {resultado.propuesta.resumen} "
                        "(se muestran los botones Confirmar y Descartar)",
                    )
                )
                return resultado

        nuevos.append(Mensaje("modelo", SIN_RESPUESTA))
        return resultado

    async def _proponer(self, ctx: Contexto, nombre: str, args: dict, resultado: Resultado) -> dict:
        if resultado.propuesta is not None:
            return {"error": "Ya hay una acción esperando confirmación. Proponé una por vez."}
        try:
            propuesta = await ACCIONES[nombre](ctx, args)
        except ErrorHerramienta as e:
            return {"error": str(e)}
        except SesionVencida:
            raise
        except ErrorSynapse as e:
            return {"error": e.mensaje}
        resultado.propuesta = propuesta
        resultado.herramienta_propuesta = nombre
        return {"estado": "esperando_confirmacion_del_paciente", "resumen": propuesta.resumen}
