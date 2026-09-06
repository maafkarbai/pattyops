from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


RAW = "RAW"
COOKED = "COOKED"


def now_iso() -> str:
    """Return a timezone-aware timestamp suitable for SQLite text storage."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _class_key(class_name: str) -> str:
    """Normalize a detector label for case-insensitive alias matching."""
    return "".join(character for character in class_name.casefold() if character.isalnum())


def normalize_patty_state(
    class_name: str, class_map: dict[str, str] | None = None
) -> str | None:
    """Map the dataset's detector classes to lifecycle states.

    SaltPepper is an action/ingredient class rather than a patty, so it is
    intentionally excluded from patty lifecycle tracking.
    """
    if class_map:
        mapped_state = class_map.get(_class_key(class_name))
        if mapped_state is not None:
            return mapped_state

    normalized = class_name.strip().lower().replace("-", "_")
    if normalized == "raw_patty" or ("raw" in normalized and "patty" in normalized):
        return RAW
    if "cookedpatty" in normalized.replace("_", ""):
        return COOKED
    return None


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS cooking_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    model_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED'))
);

CREATE TABLE IF NOT EXISTS patties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES cooking_sessions(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    current_state TEXT CHECK (current_state IN ('RAW', 'COOKED')),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'REMOVED')),
    flip_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (session_id, track_id)
);

CREATE TABLE IF NOT EXISTS patty_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patty_id INTEGER NOT NULL REFERENCES patties(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES cooking_sessions(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('DETECTED', 'STATE_CHANGED', 'FLIPPED', 'REMOVED')
    ),
    occurred_at TEXT NOT NULL,
    video_seconds REAL,
    previous_state TEXT,
    new_state TEXT,
    confidence REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_session_time
    ON patty_events(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_track
    ON patty_events(session_id, track_id, id);
"""


class PattyOpsDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Some inference SDKs invoke callbacks on a worker thread even though
        # the logger is initialized by the main thread.
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def start_session(self, source: str, model_path: str) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO cooking_sessions(source, model_path, started_at, status)
            VALUES (?, ?, ?, 'RUNNING')
            """,
            (source, model_path, now_iso()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_session(self, session_id: int, status: str = "COMPLETED") -> None:
        if status not in {"COMPLETED", "FAILED"}:
            raise ValueError("Session status must be COMPLETED or FAILED")
        self.connection.execute(
            "UPDATE cooking_sessions SET ended_at = ?, status = ? WHERE id = ?",
            (now_iso(), status, session_id),
        )
        self.connection.commit()

    def create_patty(
        self,
        session_id: int,
        track_id: int,
        state: str,
        confidence: float,
        video_seconds: float | None,
        metadata: dict | None = None,
        occurred_at: str | None = None,
    ) -> int:
        timestamp = occurred_at or now_iso()
        cursor = self.connection.execute(
            """
            INSERT INTO patties(
                session_id, track_id, first_seen, last_seen, current_state, status
            ) VALUES (?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (session_id, track_id, timestamp, timestamp, state),
        )
        patty_id = int(cursor.lastrowid)
        self._insert_event(
            patty_id=patty_id,
            session_id=session_id,
            track_id=track_id,
            event_type="DETECTED",
            video_seconds=video_seconds,
            previous_state=None,
            new_state=state,
            confidence=confidence,
            metadata=metadata,
            occurred_at=timestamp,
        )
        self.connection.commit()
        return patty_id

    def reactivate_patty(
        self,
        patty_id: int,
        session_id: int,
        track_id: int,
        state: str,
        confidence: float,
        video_seconds: float | None,
        occurred_at: str | None = None,
    ) -> None:
        timestamp = occurred_at or now_iso()
        self.connection.execute(
            """
            UPDATE patties
            SET last_seen = ?, current_state = ?, status = 'ACTIVE'
            WHERE id = ?
            """,
            (timestamp, state, patty_id),
        )
        self._insert_event(
            patty_id=patty_id,
            session_id=session_id,
            track_id=track_id,
            event_type="DETECTED",
            video_seconds=video_seconds,
            previous_state=None,
            new_state=state,
            confidence=confidence,
            metadata={"reason": "track_reappeared"},
            occurred_at=timestamp,
        )
        self.connection.commit()

    def touch_patty(self, patty_id: int) -> None:
        self.connection.execute(
            "UPDATE patties SET last_seen = ? WHERE id = ?",
            (now_iso(), patty_id),
        )

    def change_state(
        self,
        patty_id: int,
        session_id: int,
        track_id: int,
        previous_state: str,
        new_state: str,
        confidence: float,
        video_seconds: float | None,
        occurred_at: str | None = None,
    ) -> str:
        event_type = "FLIPPED" if previous_state == COOKED and new_state == RAW else "STATE_CHANGED"
        flip_increment = 1 if event_type == "FLIPPED" else 0
        timestamp = occurred_at or now_iso()
        self.connection.execute(
            """
            UPDATE patties
            SET last_seen = ?, current_state = ?, status = 'ACTIVE',
                flip_count = flip_count + ?
            WHERE id = ?
            """,
            (timestamp, new_state, flip_increment, patty_id),
        )
        self._insert_event(
            patty_id=patty_id,
            session_id=session_id,
            track_id=track_id,
            event_type=event_type,
            video_seconds=video_seconds,
            previous_state=previous_state,
            new_state=new_state,
            confidence=confidence,
            metadata=None,
            occurred_at=timestamp,
        )
        self.connection.commit()
        return event_type

    def remove_patty(
        self,
        patty_id: int,
        session_id: int,
        track_id: int,
        previous_state: str,
        video_seconds: float | None,
        reason: str,
        occurred_at: str | None = None,
    ) -> None:
        timestamp = occurred_at or now_iso()
        self.connection.execute(
            "UPDATE patties SET last_seen = ?, status = 'REMOVED' WHERE id = ?",
            (timestamp, patty_id),
        )
        self._insert_event(
            patty_id=patty_id,
            session_id=session_id,
            track_id=track_id,
            event_type="REMOVED",
            video_seconds=video_seconds,
            previous_state=previous_state,
            new_state=None,
            confidence=None,
            metadata={"reason": reason},
            occurred_at=timestamp,
        )
        self.connection.commit()

    def _insert_event(
        self,
        *,
        patty_id: int,
        session_id: int,
        track_id: int,
        event_type: str,
        video_seconds: float | None,
        previous_state: str | None,
        new_state: str | None,
        confidence: float | None,
        metadata: dict | None,
        occurred_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO patty_events(
                patty_id, session_id, track_id, event_type, occurred_at,
                video_seconds, previous_state, new_state, confidence, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patty_id,
                session_id,
                track_id,
                event_type,
                occurred_at,
                video_seconds,
                previous_state,
                new_state,
                confidence,
                json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
            ),
        )

    def list_events(self, limit: int = 100) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT id, session_id, track_id, event_type, occurred_at,
                       video_seconds, previous_state, new_state, confidence,
                       metadata_json
                FROM patty_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()

    def __enter__(self) -> "PattyOpsDatabase":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


@dataclass(frozen=True)
class Observation:
    track_id: int
    class_name: str
    confidence: float
    video_seconds: float | None = None


@dataclass
class TrackState:
    history: deque[tuple[str, float]]
    last_seen_frame: int
    last_video_seconds: float | None
    stable_state: str | None = None
    patty_id: int | None = None
    removed: bool = False


@dataclass(frozen=True)
class EmittedEvent:
    track_id: int
    event_type: str
    previous_state: str | None
    new_state: str | None
    confidence: float | None
    occurred_at: str | None = None


@dataclass
class PattyStateManager:
    database: PattyOpsDatabase
    session_id: int
    history_size: int = 12
    min_stable_observations: int = 5
    stable_ratio: float = 0.65
    removal_grace_frames: int = 45
    class_map: dict[str, str] = field(default_factory=dict)
    tracks: dict[int, TrackState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.history_size < 1:
            raise ValueError("history_size must be at least 1")
        if not 1 <= self.min_stable_observations <= self.history_size:
            raise ValueError("min_stable_observations must be between 1 and history_size")
        if not 0 < self.stable_ratio <= 1:
            raise ValueError("stable_ratio must be in (0, 1]")
        if self.removal_grace_frames < 1:
            raise ValueError("removal_grace_frames must be at least 1")
        normalized_map: dict[str, str] = {}
        for class_name, state_name in self.class_map.items():
            normalized_state = state_name.strip().upper()
            if normalized_state not in {RAW, COOKED}:
                raise ValueError(
                    f"Invalid state {state_name!r} for class {class_name!r}; "
                    "expected RAW or COOKED"
                )
            normalized_map[_class_key(class_name)] = normalized_state
        self.class_map = normalized_map

    def update(
        self,
        frame_index: int,
        observations: Iterable[Observation],
        frame_video_seconds: float | None = None,
    ) -> list[EmittedEvent]:
        emitted: list[EmittedEvent] = []
        seen_track_ids: set[int] = set()

        for observation in observations:
            state_name = normalize_patty_state(observation.class_name, self.class_map)
            if state_name is None:
                continue

            track_id = int(observation.track_id)
            seen_track_ids.add(track_id)
            track = self.tracks.get(track_id)
            if track is None:
                track = TrackState(
                    history=deque(maxlen=self.history_size),
                    last_seen_frame=frame_index,
                    last_video_seconds=observation.video_seconds,
                )
                self.tracks[track_id] = track

            track.last_seen_frame = frame_index
            track.last_video_seconds = observation.video_seconds
            track.history.append((state_name, float(observation.confidence)))

            candidate, candidate_confidence = self._stable_candidate(track.history)
            if candidate is None:
                continue

            if track.patty_id is None:
                occurred_at = now_iso()
                track.patty_id = self.database.create_patty(
                    session_id=self.session_id,
                    track_id=track_id,
                    state=candidate,
                    confidence=candidate_confidence,
                    video_seconds=observation.video_seconds,
                    metadata={"detector_class": observation.class_name},
                    occurred_at=occurred_at,
                )
                track.stable_state = candidate
                track.removed = False
                emitted.append(
                    EmittedEvent(
                        track_id,
                        "DETECTED",
                        None,
                        candidate,
                        candidate_confidence,
                        occurred_at,
                    )
                )
                continue

            if track.removed:
                occurred_at = now_iso()
                self.database.reactivate_patty(
                    patty_id=track.patty_id,
                    session_id=self.session_id,
                    track_id=track_id,
                    state=candidate,
                    confidence=candidate_confidence,
                    video_seconds=observation.video_seconds,
                    occurred_at=occurred_at,
                )
                track.removed = False
                track.stable_state = candidate
                emitted.append(
                    EmittedEvent(
                        track_id,
                        "DETECTED",
                        None,
                        candidate,
                        candidate_confidence,
                        occurred_at,
                    )
                )
                continue

            self.database.touch_patty(track.patty_id)
            if candidate == track.stable_state:
                continue

            previous_state = track.stable_state
            if previous_state is None:
                track.stable_state = candidate
                continue
            occurred_at = now_iso()
            event_type = self.database.change_state(
                patty_id=track.patty_id,
                session_id=self.session_id,
                track_id=track_id,
                previous_state=previous_state,
                new_state=candidate,
                confidence=candidate_confidence,
                video_seconds=observation.video_seconds,
                occurred_at=occurred_at,
            )
            track.stable_state = candidate
            emitted.append(
                EmittedEvent(
                    track_id,
                    event_type,
                    previous_state,
                    candidate,
                    candidate_confidence,
                    occurred_at,
                )
            )

        for track_id, track in self.tracks.items():
            if track_id in seen_track_ids or track.patty_id is None or track.removed:
                continue
            if frame_index - track.last_seen_frame < self.removal_grace_frames:
                continue
            occurred_at = now_iso()
            self.database.remove_patty(
                patty_id=track.patty_id,
                session_id=self.session_id,
                track_id=track_id,
                previous_state=track.stable_state or RAW,
                video_seconds=frame_video_seconds,
                reason="track_missing",
                occurred_at=occurred_at,
            )
            track.removed = True
            emitted.append(
                EmittedEvent(
                    track_id,
                    "REMOVED",
                    track.stable_state,
                    None,
                    None,
                    occurred_at,
                )
            )

        return emitted

    def finalize(self, video_seconds: float | None = None) -> list[EmittedEvent]:
        emitted: list[EmittedEvent] = []
        for track_id, track in self.tracks.items():
            if track.patty_id is None or track.removed:
                continue
            occurred_at = now_iso()
            self.database.remove_patty(
                patty_id=track.patty_id,
                session_id=self.session_id,
                track_id=track_id,
                previous_state=track.stable_state or RAW,
                video_seconds=video_seconds,
                reason="session_ended",
                occurred_at=occurred_at,
            )
            track.removed = True
            emitted.append(
                EmittedEvent(
                    track_id,
                    "REMOVED",
                    track.stable_state,
                    None,
                    None,
                    occurred_at,
                )
            )
        return emitted

    def _stable_candidate(
        self, history: deque[tuple[str, float]]
    ) -> tuple[str | None, float]:
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for state_name, confidence in history:
            totals[state_name] = totals.get(state_name, 0.0) + confidence
            counts[state_name] = counts.get(state_name, 0) + 1

        if not totals:
            return None, 0.0
        candidate = max(totals, key=totals.get)
        candidate_count = counts[candidate]
        if candidate_count < self.min_stable_observations:
            return None, 0.0
        if candidate_count / len(history) < self.stable_ratio:
            return None, 0.0
        return candidate, totals[candidate] / candidate_count
