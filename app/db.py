"""Persistencia propia del asistente: conversaciones, acciones pendientes y vínculos.

Los datos clínicos y los turnos viven en Synapse; acá solo se guarda lo necesario para
sostener la conversación. SQLite en desarrollo, PostgreSQL en producción.
"""

from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app import reloj
from app.config import obtener_configuracion


def ahora() -> datetime:
    return reloj.ahora()


class Base(DeclarativeBase):
    pass


class Conversacion(Base):
    __tablename__ = "conversaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canal: Mapped[str] = mapped_column(String(20))  # "portal" | "telegram"
    clave: Mapped[str] = mapped_column(String(80), unique=True)  # usuario o chat del canal
    usuario_id: Mapped[int] = mapped_column(Integer)  # usuario de Synapse
    historial: Mapped[list] = mapped_column(JSON, default=list)
    creada: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    actualizada: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class AccionPendiente(Base):
    """Una operación que el modelo propuso y que solo se ejecuta si el paciente confirma."""

    __tablename__ = "acciones"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    conversacion_id: Mapped[int] = mapped_column(ForeignKey("conversaciones.id"))
    herramienta: Mapped[str] = mapped_column(String(40))
    argumentos: Mapped[dict] = mapped_column(JSON)
    resumen: Mapped[str] = mapped_column(String(500))
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    creada: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    vence: Mapped[datetime] = mapped_column(DateTime)
    resuelta: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VinculoTelegram(Base):
    __tablename__ = "vinculos_telegram"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(Integer)
    creado: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class CodigoVinculo(Base):
    """Código de un solo uso que el portal genera para vincular Telegram."""

    __tablename__ = "codigos_vinculo"

    codigo: Mapped[str] = mapped_column(String(64), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(Integer)
    vence: Mapped[datetime] = mapped_column(DateTime)
    usado: Mapped[bool] = mapped_column(default=False)


class Intercambio(Base):
    """Métricas de cada mensaje atendido (sin contenido): latencia, tokens y herramientas."""

    __tablename__ = "intercambios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversacion_id: Mapped[int] = mapped_column(ForeignKey("conversaciones.id"))
    canal: Mapped[str] = mapped_column(String(20))
    resultado: Mapped[str] = mapped_column(String(30))
    milisegundos: Mapped[int] = mapped_column(Integer, default=0)
    pasos: Mapped[int] = mapped_column(Integer, default=0)
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0)
    herramientas: Mapped[list] = mapped_column(JSON, default=list)
    modelo: Mapped[str | None] = mapped_column(String(60), nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime, default=ahora)


@lru_cache
def _fabrica() -> sessionmaker[Session]:
    url = obtener_configuracion().database_url
    if url.startswith("postgres://"):  # formato que entregan algunos proveedores
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    motor = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(motor)
    return sessionmaker(motor, expire_on_commit=False)


def nueva_sesion() -> Session:
    return _fabrica()()


def obtener_sesion() -> Iterator[Session]:
    with nueva_sesion() as sesion:
        yield sesion
