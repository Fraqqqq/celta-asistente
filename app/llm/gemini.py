"""Adaptador de Gemini (Google AI Studio, free tier) con modelos de respaldo."""

import asyncio
import base64
import logging
import time
import uuid

from google import genai
from google.genai import errors, types

from app.llm.base import (
    DefinicionHerramienta,
    LlamadaHerramienta,
    Mensaje,
    ModeloNoDisponible,
    RespuestaModelo,
)

log = logging.getLogger(__name__)

# Firma que Gemini acepta para llamadas que no generó el modelo actual (por ejemplo, si
# la conversación empezó con otro modelo de la lista de respaldo).
FIRMA_NEUTRA = b"skip_thought_signature_validator"

# Errores que justifican probar con el siguiente modelo de la lista.
ESTADOS_REINTENTABLES = {404, 429, 500, 503, 504}
# Tras un 429 (cuota por minuto agotada) el modelo se saltea un rato para no perder tiempo.
SEGUNDOS_PAUSA_CUOTA = 60


def _a_contenidos(historial: list[Mensaje], modelo: str) -> list[types.Content]:
    contenidos: list[types.Content] = []
    for m in historial:
        if m.rol == "usuario":
            contenidos.append(types.Content(role="user", parts=[types.Part(text=m.texto or "")]))
        elif m.rol == "modelo":
            partes: list[types.Part] = []
            if m.texto:
                partes.append(types.Part(text=m.texto))
            for i, ll in enumerate(m.llamadas):
                firma = None
                if i == 0:  # Gemini valida la firma de la primera llamada de cada paso
                    mismo_modelo = m.modelo == modelo and ll.firma
                    firma = base64.b64decode(ll.firma) if mismo_modelo else FIRMA_NEUTRA
                partes.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            id=ll.id, name=ll.nombre, args=ll.argumentos
                        ),
                        thought_signature=firma,
                    )
                )
            contenidos.append(types.Content(role="model", parts=partes))
        else:
            contenidos.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=r.id, name=r.nombre, response=r.contenido
                            )
                        )
                        for r in m.resultados
                    ],
                )
            )
    return contenidos


def _desde_respuesta(respuesta: types.GenerateContentResponse, modelo: str) -> RespuestaModelo:
    textos: list[str] = []
    llamadas: list[LlamadaHerramienta] = []
    candidato = respuesta.candidates[0] if respuesta.candidates else None
    partes = candidato.content.parts if candidato and candidato.content else None
    for parte in partes or []:
        if parte.thought:
            continue
        if parte.function_call:
            fc = parte.function_call
            llamadas.append(
                LlamadaHerramienta(
                    id=fc.id or uuid.uuid4().hex[:12],
                    nombre=fc.name or "",
                    argumentos=dict(fc.args or {}),
                    firma=(
                        base64.b64encode(parte.thought_signature).decode()
                        if parte.thought_signature
                        else None
                    ),
                )
            )
        elif parte.text:
            textos.append(parte.text)
    uso = respuesta.usage_metadata
    return RespuestaModelo(
        texto="".join(textos).strip() or None,
        llamadas=llamadas,
        modelo=modelo,
        tokens_entrada=(uso.prompt_token_count or 0) if uso else 0,
        tokens_salida=(uso.candidates_token_count or 0) if uso else 0,
    )


class Gemini:
    def __init__(self, api_key: str, modelos: list[str], segundos_espera: float = 25.0):
        self._cliente = genai.Client(api_key=api_key)
        self._modelos = modelos
        self._espera = segundos_espera
        self._sin_razonamiento: set[str] = set()  # modelos que no aceptan thinking_level
        self._pausados: dict[str, float] = {}  # modelo -> momento en que vuelve a probarse

    def _configuracion(
        self, modelo: str, sistema: str, herramientas: list[DefinicionHerramienta]
    ) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=sistema,
            temperature=0.3,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=h.nombre,
                            description=h.descripcion,
                            parameters_json_schema=h.parametros,
                        )
                        for h in herramientas
                    ]
                )
            ],
            # El agente ejecuta las herramientas: el SDK no debe hacerlo por su cuenta.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=(
                None
                if modelo in self._sin_razonamiento
                else types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
            ),
        )

    async def _intentar(
        self, modelo: str, sistema: str, historial: list[Mensaje], herramientas
    ) -> RespuestaModelo:
        respuesta = await asyncio.wait_for(
            self._cliente.aio.models.generate_content(
                model=modelo,
                contents=_a_contenidos(historial, modelo),
                config=self._configuracion(modelo, sistema, herramientas),
            ),
            timeout=self._espera,
        )
        return _desde_respuesta(respuesta, modelo)

    async def generar(
        self,
        sistema: str,
        historial: list[Mensaje],
        herramientas: list[DefinicionHerramienta],
    ) -> RespuestaModelo:
        disponibles = [m for m in self._modelos if self._pausados.get(m, 0) <= time.monotonic()]
        for modelo in disponibles or self._modelos:
            inicio = time.perf_counter()
            try:
                try:
                    resultado = await self._intentar(modelo, sistema, historial, herramientas)
                except errors.ClientError as e:
                    if e.code == 400 and "thinking" in str(e).lower():
                        self._sin_razonamiento.add(modelo)
                        resultado = await self._intentar(modelo, sistema, historial, herramientas)
                    else:
                        raise
            except TimeoutError:
                log.warning("%s no respondió en %.0f s; pruebo el siguiente", modelo, self._espera)
                continue
            except errors.APIError as e:
                if e.code == 429:
                    self._pausados[modelo] = time.monotonic() + SEGUNDOS_PAUSA_CUOTA
                if e.code in ESTADOS_REINTENTABLES:
                    log.warning("%s devolvió %s; pruebo el siguiente", modelo, e.code)
                    continue
                raise
            log.info(
                "modelo=%s ms=%d tokens_entrada=%d tokens_salida=%d llamadas=%s",
                modelo,
                (time.perf_counter() - inicio) * 1000,
                resultado.tokens_entrada,
                resultado.tokens_salida,
                [ll.nombre for ll in resultado.llamadas],
            )
            return resultado
        raise ModeloNoDisponible("Ningún modelo respondió.")
