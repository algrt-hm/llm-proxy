import logging
import re
from datetime import datetime, timezone
from os import getenv

from sqlalchemy import DateTime, Integer, String, Text, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .constants import PACKAGE_NAME

DB_URL = getenv("LLM_PROXY_DB_URL", "sqlite+aiosqlite:///./llmproxy.db")
LOGGER = logging.getLogger(PACKAGE_NAME)


def redact_db_url(url: str) -> str:
    """Redact password from a database URL for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


class Base(DeclarativeBase):
    pass


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(256))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class CacheHit(Base):
    __tablename__ = "cache_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(256))
    cache_type: Mapped[str] = mapped_column(String(16))  # "response" or "idempotency"
    cached_trace_id: Mapped[int] = mapped_column(Integer)
    request_json: Mapped[str | None] = mapped_column(Text, nullable=True)


engine = create_async_engine(DB_URL, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def _get_trace_columns(sync_conn) -> set[str]:
    inspector = inspect(sync_conn)
    return {column["name"] for column in inspector.get_columns("traces")}


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrate existing databases: add columns if missing.
        trace_columns = await conn.run_sync(_get_trace_columns)

        if "idempotency_key" not in trace_columns:
            await conn.execute(text("ALTER TABLE traces ADD COLUMN idempotency_key VARCHAR(256)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_traces_idempotency_key ON traces (idempotency_key)"))
        if "cache_key" not in trace_columns:
            await conn.execute(text("ALTER TABLE traces ADD COLUMN cache_key VARCHAR(64)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_traces_cache_key ON traces (cache_key)"))


async def test_db_connectivity() -> None:
    async with engine.connect() as conn:
        result = await conn.scalar(text("SELECT 1"))
    if result != 1:
        raise RuntimeError(f"Database connectivity check failed for {redact_db_url(DB_URL)!r}: expected 1, got {result!r}")
    LOGGER.info("Database connectivity check passed.")
