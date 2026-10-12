# Auditoría técnica 360°

**Fecha:** 11/10/2026 · **Alcance:** el asistente (`app/`, 2.400 líneas), su integración con Synapse (endpoint de canje de token y widget del portal) y el despliegue (Docker, Render, GitHub Actions).

**Contexto.** Agente con IA que gestiona turnos médicos por Telegram y desde el portal de Synapse. Python 3.12 · FastAPI · google-genai (Gemini, *function calling*) · SQLAlchemy 2 · httpx · Telegram Bot API · React 19 (widget). Corre en Render (plan gratuito, Docker), con CI en GitHub Actions.

**Método.** Lectura completa del código y revisión contra OWASP Top 10 (2021) y [OWASP Top 10 para aplicaciones con LLM (2025)](https://genai.owasp.org/llm-top-10/). Cada hallazgo de concurrencia se **reprodujo con un test antes de corregirlo**.

---

## 1. Resumen de la auditoría

**Estado general: bueno, con tres defectos de concurrencia y uno de disponibilidad, ya corregidos.**

El diseño resuelve bien los riesgos propios de un agente con LLM:

| Riesgo OWASP LLM | Cómo lo mitiga el proyecto |
|---|---|
| LLM01 *Prompt injection* | El modelo no tiene más permisos que el paciente: usa el token del paciente y Synapse aplica la autorización (RN-22). Las evaluaciones incluyen dos ataques, y los dos fallan. |
| LLM06 *Excessive agency* | Las acciones no se ejecutan: el código las valida, arma el resumen y espera la confirmación humana ([ADR 0002](adr/0002-confirmacion-humana.md)). |
| LLM05 *Improper output handling* | Lo que confirma el paciente lo arma el código, no el modelo. Los textos se renderizan como texto plano: React escapa, y Telegram los recibe sin `parse_mode`. |
| LLM02 *Sensitive information disclosure* | Al modelo no le llegan DNI, email ni teléfono (hay un test que lo verifica). Los logs registran métricas, nunca el contenido. |
| LLM10 *Unbounded consumption* | Había límite por conversación, pero **no uno global** → corregido (H2). |

No se encontraron inyecciones SQL (todo pasa por el ORM, con parámetros), XSS, secretos versionados ni endpoints sin autenticación (el webhook exige un secreto comparado en tiempo constante).

| Severidad | Encontrados | Corregidos en esta auditoría |
|---|---|---|
| Alta | 2 | 2 |
| Media | 11 | 2 |
| Baja | 6 | 1 |

---

## 2. Vulnerabilidades críticas (corregidas)

### H1 · Alta · Una acción confirmada dos veces se ejecutaba dos veces
**Dónde:** `servicio.resolver_accion`. El código leía `estado == "pendiente"`, esperaba la respuesta de Synapse y recién después marcaba la acción como `confirmada`. Dos confirmaciones simultáneas (doble toque, o un reintento de Telegram) pasaban las dos el control. Synapse frenaba la segunda **reserva** porque el horario ya estaba ocupado, pero con otras acciones el resultado dependía del caso: era un problema de idempotencia (OWASP A04, diseño inseguro).

**Reproducción:** `tests/test_concurrencia.py::test_doble_confirmacion_simultanea_ejecuta_una_sola_vez` (agrega 50 ms de latencia a Synapse y lanza dos confirmaciones con `asyncio.gather`). Antes del arreglo, Synapse recibía 2 llamadas.

**Arreglo:** transición de estado atómica en la base, que sirve también con varias instancias:

```python
reclamada = sesion.execute(
    update(AccionPendiente)
    .where(AccionPendiente.id == accion.id, AccionPendiente.estado == "pendiente")
    .values(estado="en_curso")
).rowcount
sesion.commit()
if not reclamada:
    return "Esa acción ya no está disponible."
```

Si Synapse rechaza el token antes de ejecutar, la acción vuelve a `pendiente` para que se pueda reintentar. El mismo patrón (`UPDATE … RETURNING`) se aplicó al **canje de códigos de vinculación** de Telegram.

### H2 · Alta · Cualquiera podía agotar la cuota del modelo para todos
**Dónde:** `servicio._excede_limite` limitaba **por conversación**, y en Telegram cada chat es una conversación. Con 30 cuentas de Telegram se puede agotar la cuota diaria de Gemini y dejar el servicio sin respuestas (OWASP LLM10, consumo sin límites; denegación de servicio económica).

**Arreglo:** un tope global por minuto y por día, configurable y por debajo de la cuota gratuita, que se aplica **después** del detector de urgencias (una urgencia nunca se raciona):

```python
if _excede_cuota_global(sesion):        # 10 por minuto, 600 por día
    registro.resultado = "cuota_global"
    return Respuesta(MENSAJE_SATURADO)
```

Lo cubre el test `test_cuota_global_protege_al_resto_de_los_pacientes`, que además verifica que "no puedo respirar" sigue respondiendo con el 107.

### H3 · Media · Mensajes simultáneos del portal pisaban el historial
Telegram serializaba los mensajes por chat, pero el portal no. Dos envíos casi juntos leían el mismo historial y el último en guardar borraba el intercambio del otro.

**Arreglo:** el candado por conversación pasó a `servicio.candado(clave)` y lo usan los dos canales. Es un `WeakValueDictionary`, así que también se corrige una **fuga de memoria**: el diccionario de candados de Telegram crecía con cada chat nuevo y nunca se vaciaba. Lo cubre el test `test_mensajes_simultaneos_no_pisan_el_historial`.

### H4 · Media · Un error de red en Gemini no pasaba al modelo de respaldo
Solo se capturaban `APIError` y `TimeoutError`. Un `httpx.ConnectError` (DNS o TLS) cortaba el mensaje con un error 500 en lugar de probar el siguiente modelo. **Arreglo:** se capturan `httpx.TransportError` y `UnknownApiResponseError`, con su test.

---

## 3. Mejoras estructurales y de rendimiento (pendientes)

### Seguridad

**M1 · Media · Retención de datos de salud sin límite.** Los historiales de conversación pueden contener síntomas ("me duele…") y no tienen vencimiento. La Ley 25.326 exige no conservar datos más tiempo del necesario. Propuesta: una purga diaria.

```python
def purgar(sesion: Session, dias: int = 30) -> int:
    limite = reloj.ahora() - timedelta(days=dias)
    viejas = select(Conversacion.id).where(Conversacion.actualizada < limite)
    sesion.execute(delete(AccionPendiente).where(AccionPendiente.conversacion_id.in_(viejas)))
    sesion.execute(update(Conversacion).where(Conversacion.actualizada < limite).values(historial=[]))
    sesion.commit()
```

**M2 · Media · El canje de token no queda auditado ni limitado en Synapse.** `POST /integraciones/asistente/token` emite tokens de cualquier paciente a quien presente la clave. Hay que registrar cada canje en la auditoría de Synapse (quién, cuándo, para qué usuario) y aplicarle el limitador por IP que la otra rama de Synapse ya está incorporando. También conviene exigir que la clave tenga al menos 32 bytes cuando el entorno es producción.

**M3 · Media · La key de Gemini se compartió en texto plano** en el chat de desarrollo. Es del free tier y los datos son ficticios, pero la buena práctica es **rotarla** en AI Studio antes de cargarla en Render.

**B1 · Baja · El JWT del portal vive en `localStorage`** (es una decisión previa de Synapse). Un XSS en el portal podría robarlo, y con él usar el asistente. La mitigación de fondo es una cookie `HttpOnly` con `SameSite=Strict` en Synapse; el widget no agrega superficie nueva porque no inserta HTML.

**B2 · Baja · En modo demo cualquier usuario de Telegram opera sobre la misma paciente ficticia.** Es un riesgo aceptado y documentado ([ADR 0003](adr/0003-identidad.md)): la demo de Synapse se reinicia cada madrugada.

### Estabilidad

**M4 · Media · Estado en memoria que supone una sola instancia:** los updates ya procesados, los candados, las pausas de modelos y la caché de tokens. En Render gratis hay una sola instancia, así que hoy es correcto. Para escalar hay que pasarlo a Redis, o a la base con `SELECT … FOR UPDATE` en el caso de los candados.

**M5 · Media · Sin migraciones.** La base se crea con `create_all`, que no altera tablas existentes: el primer cambio de esquema en producción va a fallar sin aviso. Propuesta: Alembic, igual que Synapse.

**B3 · Baja · Los updates del webhook se procesan en `BackgroundTasks`.** Si el proceso se reinicia en el medio, el update se pierde, porque Telegram ya recibió un 200. Es aceptable para un chat: el paciente vuelve a escribir. La alternativa robusta es una cola persistente.

### Rendimiento

**M6 · Media · Llamadas síncronas a la base dentro de handlers `async`.** Con SQLite local no se nota. Con PostgreSQL remoto (Neon), cada consulta bloquea el event loop entre 5 y 50 ms y frena al resto de las conversaciones. Propuesta: motor asíncrono.

```python
# db.py
motor = create_async_engine(url.replace("+psycopg", "+psycopg_async"))
SesionAsync = async_sessionmaker(motor, expire_on_commit=False)

# servicio.py
conversacion = await sesion.scalar(select(Conversacion).where(Conversacion.clave == clave))
```

**M7 · Media · Cada pedido del portal valida el token con `GET /auth/yo`.** Es un viaje de red extra por mensaje, y en Render gratis puede despertar a Synapse. Propuesta: una caché corta, indexada por el hash del token.

```python
_identidades: dict[str, tuple[float, dict]] = {}

async def identidad(token: str) -> dict:
    clave = hashlib.sha256(token.encode()).hexdigest()
    guardada = _identidades.get(clave)
    if guardada and time.monotonic() - guardada[0] < 60:
        return guardada[1]
    yo = await ClienteSynapse(token).yo()
    _identidades[clave] = (time.monotonic(), yo)
    return yo
```

**B4 · Baja (corregida) · Faltaban índices** para las consultas de límites (`intercambios(conversacion_id, creado)` y `intercambios(creado)`).

### UI/UX y accesibilidad (widget)

- ✅ Navegación por teclado completa, `Escape` para cerrar, el foco vuelve al botón al cerrar, `role="log"` para los mensajes, `role="alert"` para los errores, y en mobile un panel inferior que respeta la barra de navegación.
- **Corregido:** el indicador de "escribiendo" usaba `aria-label` en un `div` sin rol, y los lectores de pantalla no lo anunciaban. Ahora tiene `role="status"` y texto oculto.
- **Corregido:** al confirmar una acción se invalidaban **todas** las consultas de React Query; ahora solo las del portal.
- **B5 · Baja · En celulares el panel ocupa el 88 % de la pantalla, pero se declara `aria-modal="false"`.** En ese tamaño se comporta como un modal, así que debería atrapar el foco. Propuesta: `aria-modal={esMovil}`, más un *focus trap* (o el `<dialog>` nativo que ya usa Synapse).

### Calidad de código y arquitectura

**M8 · Media · `herramientas.py` concentra definiciones, validación y ejecución** (400 líneas, tres diccionarios paralelos que hay que mantener sincronizados). Propuesta: patrón **Command**, con una clase por herramienta y un registro.

```python
class Herramienta(Protocol):
    definicion: DefinicionHerramienta
    requiere_confirmacion: bool
    async def preparar(self, ctx: Contexto, args: dict) -> dict | Propuesta: ...
    async def ejecutar(self, ctx: Contexto, args: dict) -> str: ...

REGISTRO: dict[str, Herramienta] = {h.definicion.nombre: h for h in (
    BuscarDisponibilidad(), VerMisTurnos(), EstimarCosto(),
    ReservarTurno(), CancelarTurno(), ReprogramarTurno(),
)}
```

Así, agregar una herramienta es agregar una clase (principio abierto/cerrado), y `nucleo.py` deja de distinguir entre consultas y acciones con `if`.

**M9 · Media · Singletons globales mutables** (`servicio._modelo_actual`, cachés de `credenciales`, `main._tg`). Funcionan, pero acoplan los módulos y los tests tienen que limpiarlos uno por uno en `conftest.py`. Propuesta: una clase `Dependencias` en `app.state`, inyectada con `Depends`.

**B6 · Baja · Sin trazabilidad entre servicios.** Los logs no llevan un id de pedido, así que no se puede seguir un mensaje desde Telegram hasta Synapse. Propuesta: un middleware que genere `X-Request-ID`, lo propague en las llamadas a Synapse y lo incluya en cada línea de log (JSON).

---

## 4. Plan de acción

**Hecho en esta auditoría** (68 tests, 4 nuevos):
1. Confirmación atómica de acciones y de códigos de vinculación (H1).
2. Cuota global por minuto y por día (H2).
3. Candado por conversación en los dos canales, sin fuga de memoria (H3).
4. Respaldo ante errores de red de Gemini (H4).
5. Índices, accesibilidad del indicador de escritura e invalidación acotada de la caché.

**Corto plazo (antes de publicar en LinkedIn):**
1. Rotar la key de Gemini y cargarla en Render (M3).
2. Purga de historiales a los 30 días (M1).
3. Auditar los canjes de token en Synapse, al aplicar el parche sobre la rama de seguridad (M2).
4. *Focus trap* del widget en mobile (B5).

**Mediano plazo (si el proyecto sigue creciendo):**
1. Alembic (M5) y motor asíncrono de base de datos (M6).
2. Refactor de herramientas al patrón Command (M8) y dependencias inyectadas (M9).
3. Caché de identidad (M7) y trazabilidad con `X-Request-ID` (B6).
4. Estado compartido en Redis antes de tener más de una instancia (M4).
