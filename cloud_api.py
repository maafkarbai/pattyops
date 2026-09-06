from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    first_seen_at: Mapped[str] = mapped_column(String(40), nullable=False)
    last_seen_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    software_version: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class CloudEvent(Base):
    __tablename__ = "patty_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), index=True
    )
    edge_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    track_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    received_at: Mapped[str] = mapped_column(String(40), nullable=False)
    video_seconds: Mapped[float | None] = mapped_column(Float)
    previous_state: Mapped[str | None] = mapped_column(String(16))
    new_state: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source: Mapped[str | None] = mapped_column(Text)
    model_path: Mapped[str | None] = mapped_column(Text)


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sent_at: datetime
    software_version: str = Field(min_length=1, max_length=64)
    database_path: str | None = Field(default=None, max_length=255)


class EventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=3, max_length=255)
    edge_event_id: int = Field(ge=1)
    session_id: int = Field(ge=1)
    track_id: int = Field(ge=0)
    event_type: Literal["DETECTED", "STATE_CHANGED", "FLIPPED", "REMOVED"]
    occurred_at: datetime
    video_seconds: float | None = Field(default=None, ge=0)
    previous_state: Literal["RAW", "COOKED"] | None = None
    new_state: Literal["RAW", "COOKED"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str | None = Field(default=None, max_length=2048)
    model_path: str | None = Field(default=None, max_length=2048)


class EventBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[EventRequest] = Field(min_length=1, max_length=1000)


class EventResponse(BaseModel):
    id: int
    device_id: str
    edge_event_id: int
    session_id: int
    track_id: int
    event_type: str
    occurred_at: str
    received_at: str
    previous_state: str | None
    new_state: str | None
    confidence: float | None


def parse_device_tokens(raw_value: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise ValueError("PATTYOPS_DEVICE_TOKENS_JSON must be valid JSON") from error
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("PATTYOPS_DEVICE_TOKENS_JSON must be a non-empty object")
    if not all(
        isinstance(device_id, str)
        and device_id.strip()
        and isinstance(token, str)
        and len(token) >= 16
        for device_id, token in parsed.items()
    ):
        raise ValueError(
            "Device token configuration must map device IDs to tokens of at least 16 characters"
        )
    return parsed


def create_app(
    database_url: str | None = None,
    device_tokens: dict[str, str] | None = None,
) -> FastAPI:
    resolved_database_url = database_url or os.getenv(
        "PATTYOPS_CLOUD_DATABASE_URL", "sqlite:///./pattyops_cloud.db"
    )
    if resolved_database_url.startswith("postgres://"):
        resolved_database_url = resolved_database_url.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif resolved_database_url.startswith("postgresql://"):
        resolved_database_url = resolved_database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )

    resolved_tokens = device_tokens
    if resolved_tokens is None:
        raw_tokens = os.getenv("PATTYOPS_DEVICE_TOKENS_JSON", "")
        resolved_tokens = parse_device_tokens(raw_tokens) if raw_tokens else {}

    connect_args = (
        {"check_same_thread": False}
        if resolved_database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(
        resolved_database_url, pool_pre_ping=True, connect_args=connect_args
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not resolved_tokens:
            raise RuntimeError("PATTYOPS_DEVICE_TOKENS_JSON is required")
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    application = FastAPI(
        title="PattyOps Cloud API",
        version="1.0.0",
        lifespan=lifespan,
    )

    def database_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def authenticated_device(
        authorization: str = Header(alias="Authorization"),
        device_id: str = Header(alias="X-Device-ID"),
    ) -> str:
        prefix = "Bearer "
        expected_token = resolved_tokens.get(device_id)
        supplied_token = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        if not expected_token or not secrets.compare_digest(supplied_token, expected_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid device credentials",
            )
        return device_id

    def touch_device(
        session: Session,
        device_id: str,
        *,
        software_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Device:
        timestamp = utc_iso()
        device = session.get(Device, device_id)
        if device is None:
            device = Device(
                device_id=device_id,
                first_seen_at=timestamp,
                last_seen_at=timestamp,
                software_version=software_version,
                metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
            )
            session.add(device)
        else:
            device.last_seen_at = timestamp
            if software_version:
                device.software_version = software_version
            if metadata is not None:
                device.metadata_json = json.dumps(metadata, separators=(",", ":"))
        return device

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/devices/heartbeat")
    def heartbeat(
        heartbeat_request: HeartbeatRequest,
        device_id: str = Depends(authenticated_device),
        session: Session = Depends(database_session),
    ) -> dict[str, str]:
        touch_device(
            session,
            device_id,
            software_version=heartbeat_request.software_version,
            metadata={
                "edge_sent_at": heartbeat_request.sent_at.isoformat(),
                "database_path": heartbeat_request.database_path,
            },
        )
        session.commit()
        return {"status": "ok", "server_time": utc_iso()}

    @application.post("/v1/events/batch")
    def ingest_events(
        batch: EventBatchRequest,
        device_id: str = Depends(authenticated_device),
        session: Session = Depends(database_session),
    ) -> dict[str, int]:
        accepted = 0
        duplicates = 0
        touch_device(session, device_id)

        for event in batch.events:
            if not event.idempotency_key.startswith(f"{device_id}:"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Event idempotency key does not match the authenticated device",
                )
            existing = session.scalar(
                select(CloudEvent.id).where(
                    CloudEvent.idempotency_key == event.idempotency_key
                )
            )
            if existing is not None:
                duplicates += 1
                continue

            record = CloudEvent(
                idempotency_key=event.idempotency_key,
                device_id=device_id,
                edge_event_id=event.edge_event_id,
                session_id=event.session_id,
                track_id=event.track_id,
                event_type=event.event_type,
                occurred_at=event.occurred_at.isoformat(),
                received_at=utc_iso(),
                video_seconds=event.video_seconds,
                previous_state=event.previous_state,
                new_state=event.new_state,
                confidence=event.confidence,
                metadata_json=json.dumps(
                    event.metadata, separators=(",", ":"), sort_keys=True
                ),
                source=event.source,
                model_path=event.model_path,
            )
            try:
                with session.begin_nested():
                    session.add(record)
                    session.flush()
                accepted += 1
            except IntegrityError:
                duplicates += 1

        session.commit()
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "acknowledged": accepted + duplicates,
        }

    @application.get("/v1/events", response_model=list[EventResponse])
    def list_events(
        limit: int = Query(default=100, ge=1, le=1000),
        device_id: str = Depends(authenticated_device),
        session: Session = Depends(database_session),
    ) -> list[EventResponse]:
        rows = session.scalars(
            select(CloudEvent)
            .where(CloudEvent.device_id == device_id)
            .order_by(CloudEvent.id.desc())
            .limit(limit)
        ).all()
        return [
            EventResponse(
                id=row.id,
                device_id=row.device_id,
                edge_event_id=row.edge_event_id,
                session_id=row.session_id,
                track_id=row.track_id,
                event_type=row.event_type,
                occurred_at=row.occurred_at,
                received_at=row.received_at,
                previous_state=row.previous_state,
                new_state=row.new_state,
                confidence=row.confidence,
            )
            for row in rows
        ]

    return application


app = create_app()
