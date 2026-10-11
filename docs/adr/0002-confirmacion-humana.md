# ADR 0002 · El modelo propone, la persona confirma

**Estado:** aceptada · 11/10/2026

## Contexto
Un modelo de lenguaje se puede equivocar: elegir otro horario, entender mal para quién era el turno o ser manipulado con un mensaje (*prompt injection*). Si ejecutara las reservas directamente, cada error sería un turno real mal dado.

## Decisión
Las herramientas se dividen en dos grupos:

- **Consultas** (`buscar_disponibilidad`, `ver_mis_turnos`, `estimar_costo`): se ejecutan enseguida.
- **Acciones** (`reservar_turno`, `cancelar_turno`, `reprogramar_turno`): **no ejecutan nada**. El código:
  1. valida los argumentos contra Synapse (el paciente es del grupo familiar, el profesional hace esa prestación y **el horario está realmente libre**);
  2. arma el resumen **en el código, no con el modelo**, a partir de los datos de Synapse;
  3. guarda una `AccionPendiente`, que vence a los 15 minutos;
  4. le muestra al paciente el resumen con los botones **Confirmar** y **Descartar**.

Recién al confirmar se llama a Synapse. Cada acción se puede resolver una sola vez, solo desde la conversación que la creó, y una propuesta nueva descarta la anterior.

## Consecuencias
- ✅ El paciente lee un resumen generado por el código, no por el modelo: aunque el modelo "alucine", lo que se confirma es exactamente lo que se va a ejecutar.
- ✅ Si el modelo inventa un horario, la validación lo rechaza y le devuelve los horarios libres reales, y el modelo se corrige solo.
- ✅ Una sola llamada al modelo menos por acción, porque el mensaje con el resumen no lo escribe el modelo.
- ⚠️ Un paso más para el paciente. Es aceptable: en el resto de la industria, reservar o pagar también lleva una confirmación explícita.
