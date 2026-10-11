"""Lo que el agente sabe del paciente y del centro en cada mensaje."""

import time
from dataclasses import dataclass, field

from app.synapse import ClienteSynapse

ESTADOS_ACTIVOS = {"RESERVADO", "CONFIRMADO"}


@dataclass
class Catalogo:
    especialidades: list[dict]
    prestaciones: list[dict]
    profesionales: list[dict]

    def prestacion(self, prestacion_id: int) -> dict | None:
        return next((p for p in self.prestaciones if p["id"] == prestacion_id), None)

    def profesional(self, profesional_id: int) -> dict | None:
        return next((p for p in self.profesionales if p["id"] == profesional_id), None)

    def especialidad(self, especialidad_id: int) -> dict | None:
        return next((e for e in self.especialidades if e["id"] == especialidad_id), None)

    def atiende(self, profesional: dict, prestacion: dict) -> bool:
        return any(e["id"] == prestacion["especialidad_id"] for e in profesional["especialidades"])


# El catálogo cambia poco y es igual para todos los pacientes: se cachea una hora.
_cache: dict[str, tuple[float, Catalogo]] = {}
SEGUNDOS_CACHE = 3600


async def cargar_catalogo(cliente: ClienteSynapse) -> Catalogo:
    guardado = _cache.get("catalogo")
    if guardado and time.monotonic() - guardado[0] < SEGUNDOS_CACHE:
        return guardado[1]
    catalogo = Catalogo(
        especialidades=await cliente.especialidades(),
        prestaciones=await cliente.prestaciones(),
        profesionales=await cliente.profesionales(),
    )
    _cache["catalogo"] = (time.monotonic(), catalogo)
    return catalogo


def vaciar_cache() -> None:
    _cache.clear()


@dataclass
class Contexto:
    cliente: ClienteSynapse
    catalogo: Catalogo
    grupo: list[dict]  # el paciente titular primero, después sus menores a cargo
    turnos: list[dict] | None = field(default=None)

    @property
    def titular(self) -> dict:
        return self.grupo[0]

    def paciente(self, paciente_id: int) -> dict | None:
        return next((p for p in self.grupo if p["id"] == paciente_id), None)

    async def mis_turnos(self) -> list[dict]:
        if self.turnos is None:
            self.turnos = await self.cliente.mis_turnos()
        return self.turnos

    async def turno_activo(self, turno_id: int) -> dict | None:
        return next(
            (
                t
                for t in await self.mis_turnos()
                if t["id"] == turno_id and t["estado"] in ESTADOS_ACTIVOS
            ),
            None,
        )


async def cargar_contexto(cliente: ClienteSynapse) -> Contexto:
    return Contexto(
        cliente=cliente, catalogo=await cargar_catalogo(cliente), grupo=await cliente.grupo()
    )
