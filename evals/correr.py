"""Corre las evaluaciones contra Gemini y una instancia de Synapse con la demo cargada.

    python -m evals.correr                 # todos los casos
    python -m evals.correr urgencia costo  # solo los que contienen esas palabras

Las acciones propuestas nunca se confirman: la evaluación no modifica datos.
Escribe un resumen en evals/RESULTADOS.md y el detalle en evals/resultados/.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from app import servicio, synapse
from app.config import obtener_configuracion
from app.db import Intercambio, nueva_sesion
from evals.casos import CASOS, Caso

CARPETA = Path(__file__).parent
PAUSA_ENTRE_CASOS = 6  # segundos: el free tier limita las llamadas por minuto


async def correr_caso(caso: Caso, token: str, usuario_id: int) -> dict:
    llamadas: list[dict] = []
    propuesta = None
    texto = ""
    milisegundos = 0
    with nueva_sesion() as sesion:
        clave = f"eval:{caso.nombre}:{time.time_ns()}"  # conversación nueva en cada corrida
        conversacion = servicio.obtener_conversacion(sesion, "eval", clave, usuario_id)
        servicio.reiniciar(sesion, conversacion)
        for mensaje in caso.mensajes:
            antes = len(conversacion.historial)
            inicio = time.perf_counter()
            respuesta = await servicio.atender(sesion, conversacion, token, mensaje)
            milisegundos += int((time.perf_counter() - inicio) * 1000)
            for m in conversacion.historial[antes:]:
                llamadas += [
                    {"nombre": ll["nombre"], "argumentos": ll["argumentos"]}
                    for ll in m.get("llamadas", [])
                ]
            texto = respuesta.texto
            if respuesta.accion:
                from app.db import AccionPendiente

                propuesta = sesion.get(AccionPendiente, respuesta.accion.id).herramienta
        modelos = {
            i.modelo
            for i in sesion.query(Intercambio).filter_by(conversacion_id=conversacion.id)
            if i.modelo
        }
    resultado = {"llamadas": llamadas, "propuesta": propuesta, "texto": texto}
    chequeos = [{"chequeo": c.descripcion, "ok": bool(c.funcion(resultado))} for c in caso.chequeos]
    return {
        "caso": caso.nombre,
        "categoria": caso.categoria,
        "mensajes": caso.mensajes,
        **resultado,
        "chequeos": chequeos,
        "ok": all(c["ok"] for c in chequeos),
        "milisegundos": milisegundos,
        "modelos": sorted(modelos),
    }


def informe(resultados: list[dict]) -> str:
    total = len(resultados)
    aprobados = sum(r["ok"] for r in resultados)
    mediana = sorted(r["milisegundos"] for r in resultados)[total // 2] / 1000
    modelos = ", ".join(sorted({m for r in resultados for m in r["modelos"]}))
    lineas = [
        "# Resultados de la evaluación",
        "",
        f"**{aprobados} de {total} casos aprobados** · modelo: {modelos} · "
        f"latencia mediana por caso: {mediana:.1f} s",
        "",
        "Generado con `python -m evals.correr` contra la demo de Synapse. Los casos están en "
        "[casos.py](casos.py).",
        "",
        "| Categoría | Caso | Resultado | Chequeos |",
        "|---|---|---|---|",
    ]
    for r in resultados:
        detalle = "<br>".join(f"{'✅' if c['ok'] else '❌'} {c['chequeo']}" for c in r["chequeos"])
        lineas.append(
            f"| {r['categoria']} | {r['caso']} | {'✅' if r['ok'] else '❌'} | {detalle} |"
        )
    return "\n".join(lineas) + "\n"


async def principal(filtros: list[str]) -> None:
    config = obtener_configuracion()
    token = await synapse.iniciar_sesion(config.demo_email, config.demo_contrasena)
    usuario_id = (await synapse.ClienteSynapse(token).yo())["id"]
    casos = [c for c in CASOS if not filtros or any(f in c.nombre for f in filtros)]

    resultados = []
    for i, caso in enumerate(casos):
        if i:
            await asyncio.sleep(PAUSA_ENTRE_CASOS)
        r = await correr_caso(caso, token, usuario_id)
        resultados.append(r)
        print(
            f"{'✅' if r['ok'] else '❌'} {caso.nombre}  ({r['milisegundos']} ms, {r['modelos']})"
        )
        for c in r["chequeos"]:
            if not c["ok"]:
                print(f"     ❌ {c['chequeo']}")
                print(f"     texto: {r['texto'][:200]!r}")
                print(f"     llamadas: {r['llamadas']}")

    (CARPETA / "resultados").mkdir(exist_ok=True)
    marca = time.strftime("%Y%m%d-%H%M%S")
    detalle = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in resultados)
    (CARPETA / "resultados" / f"{marca}.jsonl").write_text(detalle, encoding="utf-8")
    if not filtros:
        (CARPETA / "RESULTADOS.md").write_text(informe(resultados), encoding="utf-8")
    print(f"\n{sum(r['ok'] for r in resultados)}/{len(resultados)} casos aprobados")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(principal(sys.argv[1:]))
