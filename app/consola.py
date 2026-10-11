"""Chat por consola contra Synapse y Gemini reales, para desarrollo.

python -m app.consola                  # interactivo
python -m app.consola "hola" "..."     # mensajes en secuencia ("si" / "no" resuelven la acción)
"""

import asyncio
import sys

from app import servicio, synapse
from app.config import obtener_configuracion
from app.db import nueva_sesion


async def principal(mensajes: list[str]) -> None:
    config = obtener_configuracion()
    token = await synapse.iniciar_sesion(config.demo_email, config.demo_contrasena)
    yo = await synapse.ClienteSynapse(token).yo()
    pendiente: str | None = None
    with nueva_sesion() as sesion:
        conversacion = servicio.obtener_conversacion(sesion, "consola", "consola", yo["id"])
        servicio.reiniciar(sesion, conversacion)
        entradas = iter(mensajes) if mensajes else None
        while True:
            try:
                texto = next(entradas) if entradas else input("\nvos> ")
            except (StopIteration, EOFError):
                break
            if entradas:
                print(f"\nvos> {texto}")
            if pendiente and texto.lower() in {"si", "sí", "no"}:
                respuesta = await servicio.resolver_accion(
                    sesion, conversacion, pendiente, texto.lower() != "no", token
                )
                pendiente = None
                print(f"asistente> {respuesta}")
                continue
            respuesta = await servicio.atender(sesion, conversacion, token, texto)
            print(f"asistente> {respuesta.texto}")
            if respuesta.accion:
                pendiente = respuesta.accion.id
                print(f"   [{respuesta.accion.resumen}]  → respondé si / no")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="   · %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    asyncio.run(principal(sys.argv[1:]))
