# ADR 0001 · Gemini free tier, detrás de una interfaz propia y con modelos de respaldo

**Estado:** aceptada · 11/10/2026

## Contexto
El proyecto tenía que costar cero. Gemini, desde Google AI Studio, ofrece una cuota gratuita con *function calling*. Al medirla aparecieron tres problemas:

- **La cuota depende mucho del modelo.** Los modelos "flash" dan **20 pedidos por día** en el free tier, y cada mensaje del paciente usa entre 1 y 3. Con dos conversaciones se agotan.
- **La latencia también varía.** En las mismas tres conversaciones, `gemini-flash-lite-latest` respondió con una mediana de 2,5 s y `gemini-3.1-flash-lite`, de 22 s.
- **El servicio se satura:** devuelve 503 en horas pico.

## Decisión
1. El agente habla con una interfaz propia (`ModeloLenguaje`). Gemini es un adaptador más ([`app/llm/gemini.py`](../../app/llm/gemini.py)), y los tests usan un modelo guionado.
2. **Lista de modelos de respaldo**, configurable con `MODELOS`. El principal es `gemini-flash-lite-latest`, con la cuota más amplia y la menor latencia.
   - Ante un 503 se reintenta una vez el mismo modelo.
   - Ante un 429 por cuota por minuto, el modelo se saltea 60 s. Si la cuota agotada es la diaria, se saltea una hora.
3. Razonamiento (`thinking_level`) en `LOW`. Si un modelo no lo admite, se lo recuerda y se reintenta sin razonamiento.
4. Para que todo esto alcance, el agente gasta poco: el catálogo, el grupo familiar y el calendario van en las instrucciones de sistema, en lugar de pedirse con herramientas. Además, cuando el modelo propone una acción, el mensaje final lo escribe el código, sin otra llamada al modelo.

## Consecuencias
- ✅ Costo cero, con **16/16 casos** de evaluación aprobados y una mediana de 2,4 s por caso ([resultados](../../evals/RESULTADOS.md)).
- ✅ Cambiar de proveedor es escribir un adaptador.
- ⚠️ `gemini-flash-lite-latest` es un alias: Google puede cambiar el modelo que hay detrás. Por eso existen las [evaluaciones](../../evals/), que se vuelven a correr antes de cada cambio.
- ⚠️ En el free tier, **Google puede usar los datos para mejorar sus productos**. Acá los datos son ficticios. Con pacientes reales habría que usar el plan pago (o Vertex AI), que no los usa, y un acuerdo de tratamiento de datos de salud (Ley 25.326 y Ley 26.529).
