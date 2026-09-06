from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


SYNC_SCHEMA = """
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS cloud_sync_state (
    device_id TEXT PRIMARY KEY,
    last_event_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edge_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    installation_id TEXT NOT NULL UNIQUE
);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SyncConfig:
    database_path: Path
    cloud_url: str
    device_id: str
    device_token: str
    batch_size: int = 100
    interval_seconds: float = 5.0
    request_timeout_seconds: float = 15.0
    allow_insecure_http: bool = False

    @classmethod
    def from_environment(cls) -> "SyncConfig":
        return cls(
            database_path=Path(
                os.getenv("PATTYOPS_DATABASE_PATH", "Database/pattyops.db")
            ).expanduser().resolve(),
            cloud_url=os.getenv("PATTYOPS_CLOUD_URL", "").strip().rstrip("/"),
            device_id=os.getenv("PATTYOPS_DEVICE_ID", "").strip(),
            device_token=os.getenv("PATTYOPS_DEVICE_TOKEN", "").strip(),
            batch_size=int(os.getenv("PATTYOPS_SYNC_BATCH_SIZE", "100")),
            interval_seconds=float(
                os.getenv("PATTYOPS_SYNC_INTERVAL_SECONDS", "5")
            ),
            request_timeout_seconds=float(
                os.getenv("PATTYOPS_SYNC_TIMEOUT_SECONDS", "15")
            ),
            allow_insecure_http=env_bool("PATTYOPS_ALLOW_INSECURE_HTTP"),
        )

    def validate(self) -> None:
        if not self.cloud_url:
            raise ValueError("PATTYOPS_CLOUD_URL is required")
        if not self.device_id:
            raise ValueError("PATTYOPS_DEVICE_ID is required")
        if not self.device_token:
            raise ValueError("PATTYOPS_DEVICE_TOKEN is required")
        if self.batch_size < 1 or self.batch_size > 1000:
            raise ValueError("PATTYOPS_SYNC_BATCH_SIZE must be between 1 and 1000")
        if self.interval_seconds < 1:
            raise ValueError("PATTYOPS_SYNC_INTERVAL_SECONDS must be at least 1")

        parsed = urlparse(self.cloud_url)
        is_local = parsed.hostname in {"localhost", "127.0.0.1", "cloud-api"}
        if parsed.scheme != "https" and not (self.allow_insecure_http or is_local):
            raise ValueError(
                "PATTYOPS_CLOUD_URL must use HTTPS; set "
                "PATTYOPS_ALLOW_INSECURE_HTTP=true only for a trusted test network"
            )


Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class EdgeSynchronizer:
    def __init__(self, config: SyncConfig, transport: Transport | None = None):
        config.validate()
        self.config = config
        self.transport = transport or self._post_json

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.cloud_url}{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.device_token}",
                "Content-Type": "application/json",
                "User-Agent": "PattyOps-Edge/1.0",
                "X-Device-ID": self.config.device_id,
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.request_timeout_seconds
            ) as response:
                contents = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            details = error.read(500).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"cloud returned HTTP {error.code}: {details}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"cloud connection failed: {error.reason}") from error
        return json.loads(contents) if contents else {}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.config.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.executescript(SYNC_SCHEMA)
        connection.commit()
        return connection

    def _last_event_id(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT last_event_id FROM cloud_sync_state WHERE device_id = ?",
            (self.config.device_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    def _installation_id(self, connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT installation_id FROM edge_identity WHERE singleton = 1"
        ).fetchone()
        if row:
            return str(row[0])
        installation_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO edge_identity(singleton, installation_id) VALUES (1, ?)",
            (installation_id,),
        )
        connection.commit()
        return installation_id

    def _pending_events(
        self,
        connection: sqlite3.Connection,
        last_event_id: int,
        installation_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT event.id AS edge_event_id, event.session_id, event.track_id,
                   event.event_type, event.occurred_at, event.video_seconds,
                   event.previous_state, event.new_state, event.confidence,
                   event.metadata_json, session.source, session.model_path
            FROM patty_events AS event
            JOIN cooking_sessions AS session ON session.id = event.session_id
            WHERE event.id > ?
            ORDER BY event.id
            LIMIT ?
            """,
            (last_event_id, self.config.batch_size),
        ).fetchall()

        events: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {"invalid_edge_metadata": True}
            events.append(
                {
                    "idempotency_key": (
                        f"{self.config.device_id}:{installation_id}:"
                        f"{row['edge_event_id']}"
                    ),
                    "edge_event_id": row["edge_event_id"],
                    "session_id": row["session_id"],
                    "track_id": row["track_id"],
                    "event_type": row["event_type"],
                    "occurred_at": row["occurred_at"],
                    "video_seconds": row["video_seconds"],
                    "previous_state": row["previous_state"],
                    "new_state": row["new_state"],
                    "confidence": row["confidence"],
                    "metadata": metadata,
                    "source": row["source"],
                    "model_path": row["model_path"],
                }
            )
        return events

    def _advance_checkpoint(
        self, connection: sqlite3.Connection, event_id: int
    ) -> None:
        connection.execute(
            """
            INSERT INTO cloud_sync_state(device_id, last_event_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at
            """,
            (self.config.device_id, event_id, now_iso()),
        )
        connection.commit()

    def heartbeat(self) -> None:
        self.transport(
            "/v1/devices/heartbeat",
            {
                "sent_at": now_iso(),
                "software_version": "1.0",
                "database_path": self.config.database_path.name,
            },
        )

    def sync_once(self) -> int:
        if not self.config.database_path.is_file():
            return 0

        self.heartbeat()
        with closing(self._connect()) as connection:
            installation_id = self._installation_id(connection)
            last_event_id = self._last_event_id(connection)
            events = self._pending_events(
                connection, last_event_id, installation_id
            )
            if not events:
                return 0

            response = self.transport("/v1/events/batch", {"events": events})
            acknowledged = int(response.get("acknowledged", -1))
            if acknowledged != len(events):
                raise RuntimeError(
                    f"cloud acknowledged {acknowledged} of {len(events)} events"
                )
            self._advance_checkpoint(connection, int(events[-1]["edge_event_id"]))
            return len(events)

    def run_forever(self) -> None:
        backoff = self.config.interval_seconds
        while True:
            try:
                count = self.sync_once()
                if count:
                    print(f"Synced {count} PattyOps event(s).", flush=True)
                backoff = self.config.interval_seconds
            except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
                print(f"Sync deferred: {error}", file=sys.stderr, flush=True)
                backoff = min(max(backoff * 2, 2), 60)
            time.sleep(backoff)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize the PattyOps edge SQLite event log to the cloud."
    )
    parser.add_argument(
        "--once", action="store_true", help="Perform one heartbeat/sync cycle and exit"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        synchronizer = EdgeSynchronizer(SyncConfig.from_environment())
        if args.once:
            count = synchronizer.sync_once()
            print(f"Synced {count} PattyOps event(s).")
        else:
            synchronizer.run_forever()
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"Edge sync error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
