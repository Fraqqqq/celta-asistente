"""Cómo obtiene el asistente un token de Synapse para un chat de Telegram.

En el portal es simple: el paciente ya inició sesión y el widget manda su token. En
Telegram no hay sesión, así que hay dos modos (ver docs/adr/0003-identidad.md):

- `demo`: todos los chats operan como el paciente de demostración (datos ficticios).
  Sirve para que cualquiera pruebe el bot sin cuenta.
- `intercambio`: el paciente vincula su cuenta desde el portal; después, el asistente
  canjea su clave de servicio por un token de corta duración de ese paciente.
"""

import time

from app import synapse
from app.config import obtener_configuracion

SEGUNDOS_CACHE = 45 * 60  # Synapse emite tokens de 60 minutos

_tokens: dict[int, tuple[float, str]] = {}
_usuario_demo: dict[str, int] = {}


async def token_para(usuario_id: int) -> str:
    guardado = _tokens.get(usuario_id)
    if guardado and time.monotonic() - guardado[0] < SEGUNDOS_CACHE:
        return guardado[1]
    config = obtener_configuracion()
    if config.credenciales_modo == "demo":
        token = await synapse.iniciar_sesion(config.demo_email, config.demo_contrasena)
    else:
        token = await synapse.token_por_intercambio(usuario_id)
    _tokens[usuario_id] = (time.monotonic(), token)
    return token


def olvidar(usuario_id: int) -> None:
    _tokens.pop(usuario_id, None)


async def usuario_demo() -> int:
    """Id de usuario del paciente de demostración (solo en modo demo)."""
    if "id" not in _usuario_demo:
        config = obtener_configuracion()
        token = await synapse.iniciar_sesion(config.demo_email, config.demo_contrasena)
        yo = await synapse.ClienteSynapse(token).yo()
        _usuario_demo["id"] = yo["id"]
        _tokens[yo["id"]] = (time.monotonic(), token)
    return _usuario_demo["id"]
