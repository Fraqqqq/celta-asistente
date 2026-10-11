# ADR 0004 · Urgencias detectadas por reglas, antes del modelo

**Estado:** aceptada · 11/10/2026

## Contexto
Un paciente puede escribirle al asistente algo como "me duele el pecho y no puedo respirar". La respuesta correcta no es ofrecerle un turno para el jueves. Si eso dependiera del modelo, quedaría expuesto a sus errores, a la latencia y a que la API de Gemini esté caída.

## Decisión
Cada mensaje pasa primero por un detector de reglas ([`app/agente/emergencias.py`](../../app/agente/emergencias.py)): expresiones regulares sobre el texto normalizado, sin tildes ni mayúsculas. Hay dos listas:

- **Urgencia médica** (dolor de pecho, falta de aire, desmayo, convulsiones, hemorragia, signos de ACV, etc.). Responde con el **107 (SAME)**, el 911 y la recomendación de ir a la guardia.
- **Crisis de salud mental** (ideas de suicidio). Responde con el **Centro de Asistencia al Suicida (135 / 0800 345 1435)**.

Si hay coincidencia, la respuesta es fija y **el modelo no se llama**. El paciente igual puede seguir: el mensaje le ofrece ayuda con un turno si no era una urgencia. Además, las instrucciones del modelo repiten la regla para los casos que las expresiones no cubran.

## Consecuencias
- ✅ La respuesta a una urgencia es inmediata (4 ms), siempre la misma, y no depende de un proveedor externo.
- ✅ Se testea como cualquier otro código.
- ⚠️ Las reglas dan falsos positivos: "turno con cardiología porque a veces me duele el pecho" dispara el aviso. Se eligió pasarse antes que quedarse corto, y el mensaje no bloquea la conversación.
- ⚠️ No reemplaza un sistema de triage. El asistente no evalúa síntomas.
