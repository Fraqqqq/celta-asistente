"""Cliente de la API de Synapse.

El asistente nunca accede a la base de Synapse: opera la API con el token del propio
paciente. Así, las reglas de negocio y la autorización (un paciente solo ve y gestiona
lo suyo y lo de sus menores a cargo, RN-22) las sigue aplicando Synapse, aunque el
modelo de lenguaje intente otra cosa.
"""

import asyncio
from datetime import date, datetime
from typing import Any

import httpx

from app.config import obtener_configuracion


class ErrorSynapse(Exception):
    """Error devuelto por Synapse. `mensaje` ya viene redactado para el paciente."""

    def __init__(self, mensaje: str, estado: int, regla: str | None = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.estado = estado
        self.regla = regla


class SesionVencida(ErrorSynapse):
    pass


# Un cliente HTTP compartido (reutiliza conexiones y el contexto TLS) por event loop.
_clientes: dict[int, httpx.AsyncClient] = {}


def _cliente_http() -> httpx.AsyncClient:
    clave = id(asyncio.get_running_loop())
    if clave not in _clientes:
        _clientes[clave] = httpx.AsyncClient(
            base_url=obtener_configuracion().synapse_url, timeout=15.0
        )
    return _clientes[clave]


async def _verificar(respuesta: httpx.Response) -> Any:
    if respuesta.is_success:
        return respuesta.json() if respuesta.content else None
    try:
        cuerpo = respuesta.json()
        mensaje = cuerpo.get("mensaje") or "Synapse rechazó la operación."
        regla = cuerpo.get("regla")
    except ValueError:
        mensaje, regla = "Synapse no está disponible en este momento.", None
    if respuesta.status_code == 401:
        raise SesionVencida(mensaje, 401)
    raise ErrorSynapse(mensaje, respuesta.status_code, regla)


async def iniciar_sesion(email: str, contrasena: str) -> str:
    http = _cliente_http()
    datos = await _verificar(
        await http.post("/auth/login", json={"email": email, "contrasena": contrasena})
    )
    return datos["token"]


async def token_por_intercambio(usuario_id: int) -> str:
    """Pide a Synapse un token de corta duración para el paciente vinculado.

    Lo autoriza la clave de servicio del asistente; ver docs/adr/0003-identidad.md.
    """
    config = obtener_configuracion()
    datos = await _verificar(
        await _cliente_http().post(
            "/integraciones/asistente/token",
            json={"usuario_id": usuario_id},
            headers={"X-Clave-Servicio": config.synapse_clave_servicio},
        )
    )
    return datos["token"]


class ClienteSynapse:
    """Operaciones de Synapse en nombre de un paciente."""

    def __init__(self, token: str):
        self._token = token

    async def _pedir(self, metodo: str, ruta: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self._token}"}
        respuesta = await _cliente_http().request(metodo, ruta, headers=headers, **kwargs)
        return await _verificar(respuesta)

    async def yo(self) -> dict:
        return await self._pedir("GET", "/auth/yo")

    async def grupo(self) -> list[dict]:
        return await self._pedir("GET", "/portal/grupo")

    async def mis_turnos(self) -> list[dict]:
        return await self._pedir("GET", "/portal/turnos")

    async def turno(self, turno_id: int) -> dict:
        return await self._pedir("GET", f"/turnos/{turno_id}")

    async def especialidades(self) -> list[dict]:
        return await self._pedir("GET", "/especialidades")

    async def prestaciones(self) -> list[dict]:
        return await self._pedir("GET", "/prestaciones")

    async def profesionales(self) -> list[dict]:
        return await self._pedir("GET", "/profesionales")

    async def disponibilidad(
        self, prestacion_id: int, desde: date, dias: int, profesional_id: int | None = None
    ) -> list[dict]:
        params: dict[str, Any] = {
            "prestacion_id": prestacion_id,
            "fecha": desde.isoformat(),
            "dias": dias,
        }
        if profesional_id:
            params["profesional_id"] = profesional_id
        return await self._pedir("GET", "/disponibilidad", params=params)

    async def estimacion(self, prestacion_id: int, afiliacion_id: int | None) -> dict:
        params: dict[str, Any] = {"prestacion_id": prestacion_id}
        if afiliacion_id:
            params["afiliacion_id"] = afiliacion_id
        return await self._pedir("GET", "/estimacion", params=params)

    async def reservar(
        self,
        paciente_id: int,
        profesional_id: int,
        prestacion_id: int,
        inicio: datetime,
        afiliacion_id: int | None,
    ) -> dict:
        return await self._pedir(
            "POST",
            "/turnos",
            json={
                "paciente_id": paciente_id,
                "profesional_id": profesional_id,
                "prestacion_id": prestacion_id,
                "inicio": inicio.isoformat(),
                "afiliacion_id": afiliacion_id,
            },
        )

    async def cancelar(self, turno_id: int, motivo: str | None) -> dict:
        return await self._pedir("POST", f"/turnos/{turno_id}/cancelar", json={"motivo": motivo})

    async def reprogramar(self, turno_id: int, inicio: datetime) -> dict:
        return await self._pedir(
            "POST", f"/turnos/{turno_id}/reprogramar", json={"inicio": inicio.isoformat()}
        )
