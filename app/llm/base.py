"""Formato neutral de la conversación y contrato que cumple cualquier modelo de lenguaje.

El agente no conoce a Gemini: habla con `ModeloLenguaje`. Cambiar de proveedor es
escribir otro adaptador (ver docs/adr/0001-modelo.md), y los tests usan uno falso.
"""

from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

Rol = Literal["usuario", "modelo", "herramienta"]


@dataclass
class LlamadaHerramienta:
    id: str
    nombre: str
    argumentos: dict
    # Firma opaca del razonamiento del modelo (Gemini 3 exige devolverla tal cual).
    firma: str | None = None


@dataclass
class ResultadoHerramienta:
    id: str
    nombre: str
    contenido: dict


@dataclass
class Mensaje:
    rol: Rol
    texto: str | None = None
    llamadas: list[LlamadaHerramienta] = field(default_factory=list)
    resultados: list[ResultadoHerramienta] = field(default_factory=list)
    modelo: str | None = None  # qué modelo generó el mensaje (solo rol "modelo")

    def a_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [])}

    @classmethod
    def desde_dict(cls, datos: dict) -> "Mensaje":
        return cls(
            rol=datos["rol"],
            texto=datos.get("texto"),
            llamadas=[LlamadaHerramienta(**x) for x in datos.get("llamadas", [])],
            resultados=[ResultadoHerramienta(**x) for x in datos.get("resultados", [])],
            modelo=datos.get("modelo"),
        )


@dataclass
class DefinicionHerramienta:
    nombre: str
    descripcion: str
    parametros: dict  # JSON Schema


@dataclass
class RespuestaModelo:
    texto: str | None
    llamadas: list[LlamadaHerramienta]
    modelo: str
    tokens_entrada: int = 0
    tokens_salida: int = 0


class ModeloNoDisponible(Exception):
    """Ningún modelo pudo responder (cuota agotada, saturación o caída del proveedor)."""


class ModeloLenguaje(Protocol):
    async def generar(
        self,
        sistema: str,
        historial: list[Mensaje],
        herramientas: list[DefinicionHerramienta],
    ) -> RespuestaModelo: ...
