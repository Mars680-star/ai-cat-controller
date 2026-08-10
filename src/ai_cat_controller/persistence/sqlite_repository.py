"""Small SQLite repository for users, pets, growth, actions, and history."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_cat_controller.core.errors import ActionConflictError, ResourceNotFoundError
from ai_cat_controller.domain.growth import (
    ATTRIBUTE_MAX_UNITS,
    GrowthAttribute,
    GrowthEmotion,
    GrowthEventType,
    GrowthSourceType,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _stable_id(prefix: str, value: str) -> str:
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"ai-cat-controller:{prefix}:{value}")
    return f"{prefix}_{digest.hex[:16]}"


def _random_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class SQLiteRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    login_code TEXT NOT NULL UNIQUE,
                    nickname TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    serial_number TEXT NOT NULL UNIQUE,
                    online INTEGER NOT NULL DEFAULT 1,
                    battery_percent INTEGER NOT NULL DEFAULT 86,
                    charging INTEGER NOT NULL DEFAULT 0,
                    network_status TEXT NOT NULL DEFAULT 'online',
                    network_name TEXT NOT NULL DEFAULT 'Mock Wi-Fi',
                    volume INTEGER NOT NULL DEFAULT 60,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pets (
                    pet_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL UNIQUE REFERENCES devices(device_id),
                    owner_user_id TEXT REFERENCES users(user_id),
                    name TEXT NOT NULL,
                    personality_id TEXT NOT NULL,
                    intimacy_points INTEGER NOT NULL DEFAULT 0,
                    bound_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interaction_events (
                    event_id TEXT PRIMARY KEY,
                    pet_id TEXT NOT NULL REFERENCES pets(pet_id),
                    user_id TEXT NOT NULL REFERENCES users(user_id),
                    event_type TEXT NOT NULL,
                    points_delta INTEGER NOT NULL,
                    points_after INTEGER NOT NULL,
                    request_id TEXT NOT NULL,
                    reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(pet_id, request_id)
                );

                CREATE TABLE IF NOT EXISTS growth_attributes (
                    pet_id TEXT NOT NULL REFERENCES pets(pet_id),
                    attribute_name TEXT NOT NULL,
                    value_units INTEGER NOT NULL DEFAULT 0
                        CHECK(value_units BETWEEN 0 AND 10000),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(pet_id, attribute_name)
                );

                CREATE TABLE IF NOT EXISTS growth_events (
                    event_id TEXT PRIMARY KEY,
                    pet_id TEXT NOT NULL REFERENCES pets(pet_id),
                    user_id TEXT REFERENCES users(user_id),
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    emotion TEXT NOT NULL,
                    engagement_milli INTEGER NOT NULL
                        CHECK(engagement_milli BETWEEN 0 AND 1000),
                    novelty_milli INTEGER NOT NULL
                        CHECK(novelty_milli BETWEEN 0 AND 1000),
                    repetition_milli INTEGER NOT NULL
                        CHECK(repetition_milli BETWEEN 0 AND 1000),
                    attribute_delta_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(pet_id, source_type, source_id)
                );

                CREATE TABLE IF NOT EXISTS growth_tags (
                    pet_id TEXT NOT NULL REFERENCES pets(pet_id),
                    tag_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'replaced')),
                    earned_at TEXT NOT NULL,
                    replaced_at TEXT,
                    triggering_event_id TEXT NOT NULL
                        REFERENCES growth_events(event_id),
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(pet_id, tag_id)
                );

                CREATE TABLE IF NOT EXISTS dialog_history (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    pet_id TEXT NOT NULL REFERENCES pets(pet_id),
                    user_id TEXT NOT NULL REFERENCES users(user_id),
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    voice_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS action_executions (
                    execution_id TEXT PRIMARY KEY,
                    pet_id TEXT NOT NULL REFERENCES pets(pet_id),
                    user_id TEXT NOT NULL REFERENCES users(user_id),
                    action_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(pet_id, request_id)
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    pet_id TEXT NOT NULL REFERENCES pets(pet_id),
                    user_id TEXT NOT NULL REFERENCES users(user_id),
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_interactions_pet_time
                    ON interaction_events(pet_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_dialog_pet_time
                    ON dialog_history(pet_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_growth_events_pet_time
                    ON growth_events(pet_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_growth_events_pet_type
                    ON growth_events(pet_id, event_type);
                CREATE INDEX IF NOT EXISTS idx_growth_tags_pet_status
                    ON growth_tags(pet_id, status);
                CREATE INDEX IF NOT EXISTS idx_dialog_conversation_time
                    ON dialog_history(
                        pet_id, user_id, conversation_id, created_at ASC
                    );
                CREATE INDEX IF NOT EXISTS idx_actions_pet_time
                    ON action_executions(pet_id, created_at DESC);
                """
            )

    def backup_product_data(self) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_id = f"product-data-reset-{timestamp}"
        backup_root = self._path.parent / "backups"
        backup_directory = backup_root / backup_id
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup_root.chmod(0o700)
        backup_directory.mkdir(mode=0o700)
        backup_path = backup_directory / "product-data.db"

        try:
            with self._connect() as source:
                with sqlite3.connect(backup_path) as target:
                    source.backup(target)
            backup_path.chmod(0o600)
        except Exception:
            backup_path.unlink(missing_ok=True)
            try:
                backup_directory.rmdir()
            except OSError:
                pass
            raise

        return {
            "backup_id": backup_id,
            "backup_directory": backup_directory,
            "database_path": backup_path,
        }

    def clear_product_data(self) -> dict[str, int]:
        tables = (
            "feedback",
            "action_executions",
            "dialog_history",
            "growth_tags",
            "growth_events",
            "growth_attributes",
            "interaction_events",
            "pets",
            "devices",
            "users",
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            counts = {
                table: int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                )
                for table in tables
            }
            for table in tables:
                connection.execute(f"DELETE FROM {table}")
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return counts

    def login_user(self, login_code: str, nickname: str) -> dict[str, Any]:
        now = _utc_now()
        user_id = _stable_id("usr", login_code)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users(user_id, login_code, nickname, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(login_code) DO UPDATE SET
                    nickname = excluded.nickname,
                    updated_at = excluded.updated_at
                """,
                (user_id, login_code, nickname, now, now),
            )
            row = connection.execute(
                "SELECT user_id, nickname, created_at FROM users WHERE login_code = ?",
                (login_code,),
            ).fetchone()
        return dict(row)

    def list_user_pets(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*, d.serial_number, d.online, d.battery_percent, d.charging,
                       d.network_status, d.network_name, d.volume, d.last_seen_at
                FROM pets p
                JOIN devices d ON d.device_id = p.device_id
                WHERE p.owner_user_id = ?
                ORDER BY p.bound_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def bind_pet(
        self,
        *,
        user_id: str,
        serial_number: str,
        pet_name: str,
        network_name: str,
        personality_id: str,
    ) -> tuple[dict[str, Any], bool]:
        now = _utc_now()
        device_id = _stable_id("dev", serial_number)
        pet_id = _stable_id("pet", device_id)
        personality_created = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO devices(
                    device_id, serial_number, online, battery_percent, charging,
                    network_status, network_name, volume, last_seen_at, created_at
                )
                VALUES (?, ?, 1, 86, 0, 'online', ?, 60, ?, ?)
                ON CONFLICT(serial_number) DO UPDATE SET
                    online = 1,
                    network_status = 'online',
                    network_name = excluded.network_name,
                    last_seen_at = excluded.last_seen_at
                """,
                (device_id, serial_number, network_name, now, now),
            )
            existing = connection.execute(
                "SELECT owner_user_id, personality_id FROM pets WHERE pet_id = ?",
                (pet_id,),
            ).fetchone()
            if existing is None:
                personality_created = True
                connection.execute(
                    """
                    INSERT INTO pets(
                        pet_id, device_id, owner_user_id, name, personality_id,
                        intimacy_points, bound_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        pet_id,
                        device_id,
                        user_id,
                        pet_name,
                        personality_id,
                        now,
                        now,
                    ),
                )
            else:
                current_owner = existing["owner_user_id"]
                if current_owner is not None and current_owner != user_id:
                    raise ActionConflictError("该设备已经绑定到其他用户")
                connection.execute(
                    """
                    UPDATE pets
                    SET owner_user_id = ?, name = ?, bound_at = ?
                    WHERE pet_id = ?
                    """,
                    (user_id, pet_name, now, pet_id),
                )
            row = self._owned_pet_row(connection, user_id, pet_id)
        return dict(row), personality_created

    @staticmethod
    def _owned_pet_row(
        connection: sqlite3.Connection, user_id: str, pet_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT p.*, d.serial_number, d.online, d.battery_percent, d.charging,
                   d.network_status, d.network_name, d.volume, d.last_seen_at
            FROM pets p
            JOIN devices d ON d.device_id = p.device_id
            WHERE p.pet_id = ? AND p.owner_user_id = ?
            """,
            (pet_id, user_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("宠物不存在或未绑定到当前用户")
        return row

    def get_owned_pet(self, user_id: str, pet_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._owned_pet_row(connection, user_id, pet_id)
        return dict(row)

    def get_pet_by_serial(self, serial_number: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT p.*, d.serial_number, d.online, d.battery_percent,
                       d.charging, d.network_status, d.network_name, d.volume,
                       d.last_seen_at
                FROM pets p
                JOIN devices d ON d.device_id = p.device_id
                WHERE d.serial_number = ?
                """,
                (serial_number,),
            ).fetchone()
        return None if row is None else dict(row)

    def unbind_pet(self, user_id: str, pet_id: str) -> None:
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            connection.execute(
                "UPDATE pets SET owner_user_id = NULL, bound_at = NULL WHERE pet_id = ?",
                (pet_id,),
            )

    def update_pet_settings(
        self,
        user_id: str,
        pet_id: str,
        *,
        name: str | None,
        volume: int | None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            pet = self._owned_pet_row(connection, user_id, pet_id)
            if name is not None:
                connection.execute(
                    "UPDATE pets SET name = ? WHERE pet_id = ?",
                    (name, pet_id),
                )
            if volume is not None:
                connection.execute(
                    "UPDATE devices SET volume = ? WHERE device_id = ?",
                    (volume, pet["device_id"]),
                )
            row = self._owned_pet_row(connection, user_id, pet_id)
        return dict(row)

    def update_device_status(
        self,
        user_id: str,
        pet_id: str,
        *,
        online: bool,
        battery_percent: int,
        charging: bool,
        network_status: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            pet = self._owned_pet_row(connection, user_id, pet_id)
            connection.execute(
                """
                UPDATE devices
                SET online = ?, battery_percent = ?, charging = ?,
                    network_status = ?, last_seen_at = ?
                WHERE device_id = ?
                """,
                (
                    int(online),
                    battery_percent,
                    int(charging),
                    network_status,
                    now,
                    pet["device_id"],
                ),
            )
            row = self._owned_pet_row(connection, user_id, pet_id)
        return dict(row)

    def apply_interaction(
        self,
        *,
        user_id: str,
        pet_id: str,
        event_type: str,
        base_points: int,
        max_per_day: int,
        daily_cap: int,
        enforce_limits: bool,
        request_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        today = _utc_date()
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            pet = self._owned_pet_row(connection, user_id, pet_id)
            existing = connection.execute(
                """
                SELECT event_id, event_type, points_delta, points_after,
                       request_id, reason, created_at
                FROM interaction_events
                WHERE pet_id = ? AND request_id = ?
                """,
                (pet_id, request_id),
            ).fetchone()
            if existing is not None:
                result = dict(existing)
                result["duplicate"] = True
                return result

            event_count = 0
            positive_today = 0
            if enforce_limits:
                event_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM interaction_events
                    WHERE pet_id = ? AND event_type = ?
                      AND substr(created_at, 1, 10) = ?
                    """,
                    (pet_id, event_type, today),
                ).fetchone()[0]
                positive_today = connection.execute(
                    """
                    SELECT COALESCE(SUM(points_delta), 0) FROM interaction_events
                    WHERE pet_id = ? AND points_delta > 0
                      AND substr(created_at, 1, 10) = ?
                    """,
                    (pet_id, today),
                ).fetchone()[0]

            reason: str | None = None
            if enforce_limits and event_count >= max_per_day:
                points_delta = 0
                reason = "该互动今日已达到次数上限"
            elif enforce_limits and base_points > 0:
                remaining = max(daily_cap - int(positive_today), 0)
                points_delta = min(base_points, remaining)
                if points_delta == 0:
                    reason = "今日亲密度增长已达到上限"
                elif points_delta < base_points:
                    reason = "已按今日剩余增长额度计算"
            else:
                points_delta = base_points

            points_after = max(0, int(pet["intimacy_points"]) + points_delta)
            connection.execute(
                "UPDATE pets SET intimacy_points = ? WHERE pet_id = ?",
                (points_after, pet_id),
            )
            event_id = _random_id("evt")
            connection.execute(
                """
                INSERT INTO interaction_events(
                    event_id, pet_id, user_id, event_type, points_delta,
                    points_after, request_id, reason, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    pet_id,
                    user_id,
                    event_type,
                    points_delta,
                    points_after,
                    request_id,
                    reason,
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                ),
            )
        return {
            "event_id": event_id,
            "event_type": event_type,
            "points_delta": points_delta,
            "points_after": points_after,
            "request_id": request_id,
            "reason": reason,
            "created_at": now,
            "duplicate": False,
        }

    def list_interactions(
        self, user_id: str, pet_id: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            rows = connection.execute(
                """
                SELECT event_id, event_type, points_delta, points_after,
                       request_id, reason, created_at
                FROM interaction_events
                WHERE pet_id = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (pet_id, user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_growth_attributes(self, pet_id: str) -> dict[str, int]:
        attributes = {attribute.value: 0 for attribute in GrowthAttribute}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT attribute_name, value_units
                FROM growth_attributes
                WHERE pet_id = ?
                """,
                (pet_id,),
            ).fetchall()
        for row in rows:
            if row["attribute_name"] in attributes:
                attributes[row["attribute_name"]] = int(row["value_units"])
        return attributes

    def list_recent_growth_events(
        self,
        pet_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, source_type, source_id, event_type, topic,
                       emotion, engagement_milli, novelty_milli,
                       repetition_milli, attribute_delta_json, metadata_json,
                       created_at
                FROM growth_events
                WHERE pet_id = ? AND event_type != 'tag_awarded'
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (pet_id, limit),
            ).fetchall()
        return [self._growth_event_dict(row) for row in rows]

    def list_owned_growth_events(
        self,
        user_id: str,
        pet_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            rows = connection.execute(
                """
                SELECT event_id, source_type, source_id, event_type, topic,
                       emotion, engagement_milli, novelty_milli,
                       repetition_milli, attribute_delta_json, metadata_json,
                       created_at
                FROM growth_events
                WHERE pet_id = ? AND user_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (pet_id, user_id, limit),
            ).fetchall()
        return [self._growth_event_dict(row) for row in rows]

    @staticmethod
    def _growth_event_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["engagement"] = int(result.pop("engagement_milli")) / 1000
        result["novelty"] = int(result.pop("novelty_milli")) / 1000
        result["repetition_decay"] = int(result.pop("repetition_milli")) / 1000
        result["attribute_delta"] = json.loads(
            result.pop("attribute_delta_json")
        )
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def growth_event_counts(self, pet_id: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, COUNT(*) AS event_count
                FROM growth_events
                WHERE pet_id = ?
                GROUP BY event_type
                """,
                (pet_id,),
            ).fetchall()
        return {str(row["event_type"]): int(row["event_count"]) for row in rows}

    def apply_growth_event(
        self,
        *,
        user_id: str,
        pet_id: str,
        source_type: str,
        source_id: str,
        event_type: str,
        topic: str,
        emotion: str,
        engagement: float,
        novelty: float,
        repetition_decay: float,
        attribute_delta: dict[str, int],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        valid_source_types = {item.value for item in GrowthSourceType}
        valid_event_types = {item.value for item in GrowthEventType}
        valid_emotions = {item.value for item in GrowthEmotion}
        if source_type not in valid_source_types:
            raise ValueError(f"unknown growth source type: {source_type}")
        if event_type not in valid_event_types:
            raise ValueError(f"unknown growth event type: {event_type}")
        if emotion not in valid_emotions:
            raise ValueError(f"unknown growth emotion: {emotion}")
        if not source_id or len(source_id) > 256:
            raise ValueError("growth source_id must contain 1..256 characters")
        if not topic.strip() or len(topic) > 64:
            raise ValueError("growth topic must contain 1..64 characters")
        factors = (engagement, novelty, repetition_decay)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
            for value in factors
        ):
            raise ValueError("growth factors must be finite values between 0 and 1")
        now = _utc_now()
        event_id = _stable_id(
            "growth",
            f"{pet_id}:{source_type}:{source_id}",
        )
        for attribute_name, delta in attribute_delta.items():
            if attribute_name not in {item.value for item in GrowthAttribute}:
                raise ValueError(f"unknown growth attribute: {attribute_name}")
            if type(delta) is not int or delta < 0 or delta > ATTRIBUTE_MAX_UNITS:
                raise ValueError("invalid growth attribute delta")
        metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        if len(metadata_json.encode("utf-8")) > 16 * 1024:
            raise ValueError("growth metadata exceeds 16 KiB")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_pet_row(connection, user_id, pet_id)
            existing = connection.execute(
                """
                SELECT event_id FROM growth_events
                WHERE pet_id = ? AND source_type = ? AND source_id = ?
                """,
                (pet_id, source_type, source_id),
            ).fetchone()
            if existing is not None:
                return {
                    "event_id": str(existing["event_id"]),
                    "duplicate": True,
                    "attributes": self._growth_attributes_in_connection(
                        connection,
                        pet_id,
                    ),
                }

            applied_delta: dict[str, int] = {}
            for attribute_name, delta in attribute_delta.items():
                current_row = connection.execute(
                    """
                    SELECT value_units FROM growth_attributes
                    WHERE pet_id = ? AND attribute_name = ?
                    """,
                    (pet_id, attribute_name),
                ).fetchone()
                current = 0 if current_row is None else int(current_row[0])
                updated = min(ATTRIBUTE_MAX_UNITS, current + delta)
                applied_delta[attribute_name] = updated - current
                connection.execute(
                    """
                    INSERT INTO growth_attributes(
                        pet_id, attribute_name, value_units, updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(pet_id, attribute_name) DO UPDATE SET
                        value_units = excluded.value_units,
                        updated_at = excluded.updated_at
                    """,
                    (pet_id, attribute_name, updated, now),
                )

            connection.execute(
                """
                INSERT INTO growth_events(
                    event_id, pet_id, user_id, source_type, source_id,
                    event_type, topic, emotion, engagement_milli,
                    novelty_milli, repetition_milli, attribute_delta_json,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    pet_id,
                    user_id,
                    source_type,
                    source_id,
                    event_type,
                    topic,
                    emotion,
                    round(engagement * 1000),
                    round(novelty * 1000),
                    round(repetition_decay * 1000),
                    json.dumps(applied_delta, sort_keys=True),
                    metadata_json,
                    now,
                ),
            )
            attributes = self._growth_attributes_in_connection(connection, pet_id)
        return {
            "event_id": event_id,
            "duplicate": False,
            "event_type": event_type,
            "attribute_delta": applied_delta,
            "attributes": attributes,
            "created_at": now,
        }

    @staticmethod
    def _growth_attributes_in_connection(
        connection: sqlite3.Connection,
        pet_id: str,
    ) -> dict[str, int]:
        attributes = {attribute.value: 0 for attribute in GrowthAttribute}
        rows = connection.execute(
            """
            SELECT attribute_name, value_units
            FROM growth_attributes
            WHERE pet_id = ?
            """,
            (pet_id,),
        ).fetchall()
        for row in rows:
            if row["attribute_name"] in attributes:
                attributes[row["attribute_name"]] = int(row["value_units"])
        return attributes

    def list_growth_tags(self, pet_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT tag_id, status, earned_at, replaced_at,
                       triggering_event_id, evidence_json
                FROM growth_tags
                WHERE pet_id = ?
                ORDER BY earned_at ASC, tag_id ASC
                """,
                (pet_id,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            results.append(item)
        return results

    def award_growth_tags(
        self,
        *,
        user_id: str,
        pet_id: str,
        triggering_event_id: str,
        awards: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not awards:
            return []
        now = _utc_now()
        awarded: list[dict[str, Any]] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_pet_row(connection, user_id, pet_id)
            for award in awards:
                tag_id = str(award["tag_id"])
                evidence = dict(award["evidence"])
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO growth_tags(
                        pet_id, tag_id, status, earned_at, replaced_at,
                        triggering_event_id, evidence_json
                    )
                    VALUES (?, ?, 'active', ?, NULL, ?, ?)
                    """,
                    (
                        pet_id,
                        tag_id,
                        now,
                        triggering_event_id,
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                replaces = tuple(str(item) for item in award.get("replaces", ()))
                if replaces:
                    placeholders = ",".join("?" for _ in replaces)
                    connection.execute(
                        f"""
                        UPDATE growth_tags
                        SET status = 'replaced', replaced_at = ?
                        WHERE pet_id = ? AND status = 'active'
                          AND tag_id IN ({placeholders})
                        """,
                        (now, pet_id, *replaces),
                    )
                tag_event_source_id = f"{tag_id}:{triggering_event_id}"
                tag_event_id = _stable_id(
                    "growth",
                    f"{pet_id}:system:{tag_event_source_id}",
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO growth_events(
                        event_id, pet_id, user_id, source_type, source_id,
                        event_type, topic, emotion, engagement_milli,
                        novelty_milli, repetition_milli, attribute_delta_json,
                        metadata_json, created_at
                    )
                    VALUES (?, ?, ?, 'system', ?, 'tag_awarded', ?, 'positive',
                            1000, 1000, 1000, '{}', ?, ?)
                    """,
                    (
                        tag_event_id,
                        pet_id,
                        user_id,
                        tag_event_source_id,
                        tag_id,
                        json.dumps(
                            {"tag_id": tag_id},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        now,
                    ),
                )
                awarded.append(
                    {
                        "tag_id": tag_id,
                        "earned_at": now,
                        "triggering_event_id": triggering_event_id,
                    }
                )
        return awarded

    def add_dialog_pair(
        self,
        *,
        user_id: str,
        pet_id: str,
        user_content: str,
        assistant_content: str,
        voice_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        conversation_id = _random_id("conv")
        now = _utc_now()
        user_message = {
            "message_id": _random_id("msg"),
            "conversation_id": conversation_id,
            "pet_id": pet_id,
            "user_id": user_id,
            "role": "user",
            "content": user_content,
            "voice_id": None,
            "created_at": now,
        }
        assistant_message = {
            "message_id": _random_id("msg"),
            "conversation_id": conversation_id,
            "pet_id": pet_id,
            "user_id": user_id,
            "role": "assistant",
            "content": assistant_content,
            "voice_id": voice_id,
            "created_at": now,
        }
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            connection.executemany(
                """
                INSERT INTO dialog_history(
                    message_id, conversation_id, pet_id, user_id, role,
                    content, voice_id, created_at
                )
                VALUES (
                    :message_id, :conversation_id, :pet_id, :user_id, :role,
                    :content, :voice_id, :created_at
                )
                """,
                (user_message, assistant_message),
            )
        return conversation_id, [user_message, assistant_message]

    def import_native_dialog_events(
        self,
        *,
        user_id: str,
        pet_id: str,
        events: list[dict[str, Any]],
    ) -> int:
        imported = 0
        with self._connect() as connection:
            pet = self._owned_pet_row(connection, user_id, pet_id)
            serial_number = str(pet["serial_number"])
            bound_at = str(pet["bound_at"] or "")
            for event in events:
                if event["device_serial"] != serial_number:
                    continue
                if bound_at and event["created_at"] < bound_at:
                    continue
                message_id = _stable_id(
                    "msg",
                    f"{serial_number}:{event['event_id']}",
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO dialog_history(
                        message_id, conversation_id, pet_id, user_id, role,
                        content, voice_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        event["conversation_id"],
                        pet_id,
                        user_id,
                        event["role"],
                        event["content"],
                        "volcengine_tts"
                        if event["role"] == "assistant"
                        else None,
                        event["created_at"],
                    ),
                )
                imported += int(cursor.rowcount == 1)
                connection.execute(
                    """
                    UPDATE dialog_history
                    SET conversation_id = ?
                    WHERE message_id = ? AND pet_id = ? AND user_id = ?
                      AND conversation_id != ?
                    """,
                    (
                        event["conversation_id"],
                        message_id,
                        pet_id,
                        user_id,
                        event["conversation_id"],
                    ),
                )
        return imported

    def list_dialogs(
        self, user_id: str, pet_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            rows = connection.execute(
                """
                SELECT message_id, conversation_id, role, content, voice_id, created_at
                FROM dialog_history
                WHERE pet_id = ? AND user_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (pet_id, user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_dialog_conversations(
        self, user_id: str, pet_id: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            rows = connection.execute(
                """
                SELECT
                    h.conversation_id,
                    MIN(h.created_at) AS started_at,
                    MAX(h.created_at) AS updated_at,
                    COUNT(*) AS message_count,
                    SUM(CASE WHEN h.role = 'user' THEN 1 ELSE 0 END)
                        AS user_message_count,
                    SUM(CASE WHEN h.role = 'assistant' THEN 1 ELSE 0 END)
                        AS assistant_message_count,
                    MAX(
                        CASE
                            WHEN h.voice_id = 'volcengine_tts'
                                 OR h.conversation_id LIKE 'native-%'
                            THEN 1 ELSE 0
                        END
                    ) AS device_source,
                    COALESCE(
                        (
                            SELECT d.content
                            FROM dialog_history AS d
                            WHERE d.pet_id = h.pet_id
                              AND d.user_id = h.user_id
                              AND d.conversation_id = h.conversation_id
                              AND d.role = 'user'
                            ORDER BY d.created_at ASC, d.rowid ASC
                            LIMIT 1
                        ),
                        (
                            SELECT d.content
                            FROM dialog_history AS d
                            WHERE d.pet_id = h.pet_id
                              AND d.user_id = h.user_id
                              AND d.conversation_id = h.conversation_id
                            ORDER BY d.created_at ASC, d.rowid ASC
                            LIMIT 1
                        )
                    ) AS preview,
                    (
                        SELECT d.content
                        FROM dialog_history AS d
                        WHERE d.pet_id = h.pet_id
                          AND d.user_id = h.user_id
                          AND d.conversation_id = h.conversation_id
                          AND d.role = 'assistant'
                        ORDER BY d.created_at DESC, d.rowid DESC
                        LIMIT 1
                    ) AS assistant_preview
                FROM dialog_history AS h
                WHERE h.pet_id = ? AND h.user_id = ?
                GROUP BY h.pet_id, h.user_id, h.conversation_id
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (pet_id, user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_dialog_conversation(
        self, user_id: str, pet_id: str, conversation_id: str
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            rows = connection.execute(
                """
                SELECT message_id, conversation_id, role, content, voice_id,
                       created_at
                FROM dialog_history
                WHERE pet_id = ? AND user_id = ? AND conversation_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (pet_id, user_id, conversation_id),
            ).fetchall()
        if not rows:
            raise ResourceNotFoundError("对话会话不存在")
        return [dict(row) for row in rows]

    def dialog_sync_state(self, user_id: str, pet_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            row = connection.execute(
                """
                SELECT COUNT(*) AS message_count,
                       COUNT(DISTINCT conversation_id) AS conversation_count,
                       MAX(created_at) AS latest_message_at
                FROM dialog_history
                WHERE pet_id = ? AND user_id = ?
                """,
                (pet_id, user_id),
            ).fetchone()
        return dict(row)

    def create_action_execution(
        self,
        *,
        user_id: str,
        pet_id: str,
        action_id: str,
        request_id: str,
    ) -> tuple[dict[str, Any], bool]:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_pet_row(connection, user_id, pet_id)
            existing = connection.execute(
                """
                SELECT execution_id, action_id, request_id, status, result,
                       error, created_at, completed_at
                FROM action_executions
                WHERE pet_id = ? AND request_id = ?
                """,
                (pet_id, request_id),
            ).fetchone()
            if existing is not None:
                return dict(existing), False
            execution_id = _random_id("run")
            connection.execute(
                """
                INSERT INTO action_executions(
                    execution_id, pet_id, user_id, action_id, request_id,
                    status, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (execution_id, pet_id, user_id, action_id, request_id, now),
            )
        return {
            "execution_id": execution_id,
            "action_id": action_id,
            "request_id": request_id,
            "status": "pending",
            "result": None,
            "error": None,
            "created_at": now,
            "completed_at": None,
        }, True

    def update_action_execution(
        self,
        execution_id: str,
        *,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        completed_at = (
            _utc_now()
            if status in {"completed", "cancelled", "failed", "timed_out"}
            else None
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE action_executions
                SET status = ?, result = ?, error = ?, completed_at = ?
                WHERE execution_id = ?
                """,
                (status, result, error, completed_at, execution_id),
            )

    def list_action_executions(
        self, user_id: str, pet_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            rows = connection.execute(
                """
                SELECT execution_id, action_id, request_id, status, result,
                       error, created_at, completed_at
                FROM action_executions
                WHERE pet_id = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (pet_id, user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_feedback(
        self,
        *,
        user_id: str,
        pet_id: str,
        category: str,
        content: str,
    ) -> dict[str, Any]:
        feedback_id = _random_id("fb")
        now = _utc_now()
        with self._connect() as connection:
            self._owned_pet_row(connection, user_id, pet_id)
            connection.execute(
                """
                INSERT INTO feedback(
                    feedback_id, pet_id, user_id, category, content, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (feedback_id, pet_id, user_id, category, content, now),
            )
        return {
            "feedback_id": feedback_id,
            "category": category,
            "created_at": now,
        }
