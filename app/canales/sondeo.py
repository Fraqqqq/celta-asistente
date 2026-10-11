"""Bot de Telegram por long polling, para desarrollo local (sin URL pública).

    python -m app.canales.sondeo

En producción se usa el webhook (`/api/telegram/webhook`), que no necesita un proceso
corriendo todo el tiempo.
"""

import asyncio
import logging

from app.canales.telegram import ClienteTelegram, procesar
from app.config import obtener_configuracion

log = logging.getLogger(__name__)
_en_curso: set[asyncio.Task] = set()  # referencia para que el recolector no las corte


async def principal() -> None:
    tg = ClienteTelegram(obtener_configuracion().telegram_bot_token)
    await tg.llamar("deleteWebhook")
    yo = await tg.llamar("getMe")
    log.info("Escuchando como @%s (Ctrl+C para salir)", yo["result"]["username"])
    desplazamiento = 0
    while True:
        try:
            respuesta = await tg.llamar(
                "getUpdates",
                offset=desplazamiento,
                timeout=30,
                allowed_updates=["message", "callback_query"],
            )
        except Exception:
            log.exception("Fallo al pedir updates; reintento en 5 s")
            await asyncio.sleep(5)
            continue
        for update in respuesta.get("result", []):
            desplazamiento = update["update_id"] + 1
            tarea = asyncio.create_task(procesar(tg, update))
            _en_curso.add(tarea)
            tarea.add_done_callback(_en_curso.discard)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(principal())
