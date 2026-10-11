"""Configuración leída de variables de entorno (o de un archivo .env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracion(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Modelo de lenguaje ---
    gemini_api_key: str = ""
    # Se prueban en orden: si uno está saturado (429/503) o tarda demasiado, se pasa al siguiente.
    # Los "flash-lite" tienen la cuota gratuita más amplia; ver docs/adr/0001-modelo.md.
    modelos: str = "gemini-flash-lite-latest,gemini-3.1-flash-lite,gemini-3.8-flash"
    segundos_espera_modelo: float = 25.0
    pasos_maximos: int = 6  # llamadas al modelo por mensaje del paciente

    # --- Synapse ---
    synapse_url: str = "http://localhost:8000/api"
    # "intercambio": el asistente pide a Synapse un token del paciente vinculado (producción).
    # "demo": todos los chats de Telegram operan como el paciente de demostración.
    credenciales_modo: str = "demo"
    synapse_clave_servicio: str = ""
    demo_email: str = "paciente@celta.example"
    demo_contrasena: str = "Synapse2026"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_bot_usuario: str = "celta_asistente_fraqqqq_bot"
    telegram_secreto_webhook: str = ""  # vacío: se genera uno al arrancar
    # URL pública del servicio. Si está, al arrancar se registra el webhook de Telegram.
    # Render la expone sola como RENDER_EXTERNAL_URL.
    url_publica: str = ""
    render_external_url: str = ""

    # --- Asistente ---
    database_url: str = "sqlite:///./asistente.db"
    cors_origenes: str = "http://localhost:5173"
    minutos_vigencia_accion: int = 15
    minutos_vigencia_codigo: int = 10
    horas_contexto: int = 6  # pasado este tiempo sin mensajes, la conversación arranca de cero
    mensajes_por_ventana: int = 20  # límite por conversación...
    minutos_ventana: int = 10  # ...en esta ventana de tiempo
    # Topes para todo el servicio, por debajo de la cuota gratuita de Gemini.
    mensajes_globales_por_minuto: int = 10
    mensajes_globales_por_dia: int = 600

    @property
    def url_webhook(self) -> str | None:
        base = self.url_publica or self.render_external_url
        return f"{base.rstrip('/')}/api/telegram/webhook" if base else None

    @property
    def lista_modelos(self) -> list[str]:
        return [m.strip() for m in self.modelos.split(",") if m.strip()]

    @property
    def origenes_permitidos(self) -> list[str]:
        return [o.strip() for o in self.cors_origenes.split(",") if o.strip()]


@lru_cache
def obtener_configuracion() -> Configuracion:
    return Configuracion()
