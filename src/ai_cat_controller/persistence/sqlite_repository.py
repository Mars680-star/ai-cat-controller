"""Small SQLite repository for users, pets, growth, actions, and history."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_cat_controller.core.errors import ActionConflictError, ResourceNotFoundError


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
                CREATE INDEX IF NOT EXISTS idx_actions_pet_time
                    ON action_executions(pet_id, created_at DESC);
                """
            )

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
            if event_count >= max_per_day:
                points_delta = 0
                reason = "该互动今日已达到次数上限"
            elif base_points > 0:
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
