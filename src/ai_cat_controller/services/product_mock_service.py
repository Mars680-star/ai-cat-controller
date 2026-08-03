"""Persistent product workflow used by the browser and future mini program."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import (
    AiCatError,
    ActionConflictError,
    AdapterNotImplementedError,
    AuthenticationError,
    DeviceUnavailableError,
    ResourceNotFoundError,
)
from ai_cat_controller.domain.actions import ACTIONS, ACTION_BY_ID, action_is_unlocked
from ai_cat_controller.domain.intimacy import (
    INTERACTION_RULES,
    INTIMACY_LEVELS,
    level_for_points,
    level_progress,
)
from ai_cat_controller.domain.personalities import PERSONALITIES, PERSONALITY_BY_ID
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.services.motion_service import MotionService

LOGGER = logging.getLogger(__name__)
MAX_NATIVE_DIALOG_FILE_BYTES = 4 * 1024 * 1024
MAX_NATIVE_DIALOG_LINE_BYTES = 16 * 1024


class ProductMockService:
    """Owns Mock login sessions and durable product-facing state."""

    def __init__(
        self,
        repository: SQLiteRepository,
        motion: MotionService,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._motion = motion
        self._settings = settings
        self._sessions: dict[str, str] = {}
        self._finalizers: set[asyncio.Task[None]] = set()
        self._dialog_sync_lock = asyncio.Lock()
        self._dialog_source_signatures: dict[
            tuple[str, str], tuple[int, int]
        ] = {}

    async def initialize(self) -> None:
        await asyncio.to_thread(self._repository.initialize)

    async def login(self, login_code: str, nickname: str) -> dict[str, Any]:
        user = await asyncio.to_thread(
            self._repository.login_user, login_code, nickname
        )
        session_token = secrets.token_urlsafe(24)
        self._sessions[session_token] = user["user_id"]
        pets = await asyncio.to_thread(
            self._repository.list_user_pets, user["user_id"]
        )
        return {
            "session_token": session_token,
            "user": user,
            "pets": [self._pet_summary(pet) for pet in pets],
        }

    def resolve_session(self, session_token: str | None) -> str:
        if not session_token or session_token not in self._sessions:
            raise AuthenticationError("Mock 会话无效，请重新登录")
        return self._sessions[session_token]

    @staticmethod
    def list_personalities() -> list[dict[str, Any]]:
        return [
            {
                "personality_id": item.personality_id,
                "name": item.name,
                "description": item.description,
                "language_style": item.language_style,
                "voice_id": item.voice_id,
                "default_actions": item.default_actions,
                "action_triggers": item.action_triggers,
                "intimacy_styles": [
                    style.model_dump() for style in item.intimacy_styles
                ],
                "prohibited_content": item.prohibited_content,
                "integration_status": "mock_configuration",
            }
            for item in PERSONALITIES
        ]

    async def list_pets(self, user_id: str) -> list[dict[str, Any]]:
        pets = await asyncio.to_thread(self._repository.list_user_pets, user_id)
        return [self._pet_summary(pet) for pet in pets]

    async def bind_pet(
        self,
        *,
        user_id: str,
        serial_number: str,
        pet_name: str,
        network_name: str,
    ) -> dict[str, Any]:
        selected = secrets.choice(PERSONALITIES)
        pet, personality_created = await asyncio.to_thread(
            self._repository.bind_pet,
            user_id=user_id,
            serial_number=serial_number,
            pet_name=pet_name,
            network_name=network_name,
            personality_id=selected.personality_id,
        )
        return {
            "pet": self._pet_summary(pet),
            "blind_box_revealed": personality_created,
            "personality": self._personality_summary(
                PERSONALITY_BY_ID[pet["personality_id"]]
            ),
            "persistence": "device_bound",
        }

    async def dashboard(self, user_id: str, pet_id: str) -> dict[str, Any]:
        pet = await asyncio.to_thread(self._repository.get_owned_pet, user_id, pet_id)
        points = int(pet["intimacy_points"])
        level = level_for_points(points)
        personality = PERSONALITY_BY_ID[pet["personality_id"]]
        unlocked_actions = [
            action.action_id
            for action in ACTIONS
            if action_is_unlocked(action, personality.personality_id, level.level)
        ]
        return {
            "pet": self._pet_summary(pet),
            "personality": self._personality_summary(personality),
            "intimacy": {
                "points": points,
                "level": level.model_dump(),
                "progress": level_progress(points),
                "address": personality.style_for_level(level.level).address,
            },
            "unlocked_action_ids": unlocked_actions,
        }

    async def intimacy(self, user_id: str, pet_id: str) -> dict[str, Any]:
        dashboard = await self.dashboard(user_id, pet_id)
        points = dashboard["intimacy"]["points"]
        current_level = level_for_points(points)
        history = await asyncio.to_thread(
            self._repository.list_interactions, user_id, pet_id, 30
        )
        next_level = (
            INTIMACY_LEVELS[current_level.level + 1]
            if current_level.level + 1 < len(INTIMACY_LEVELS)
            else None
        )
        return {
            **dashboard["intimacy"],
            "daily_growth_cap": self._settings.intimacy_daily_cap,
            "rules": [rule.model_dump() for rule in INTERACTION_RULES.values()],
            "history": history,
            "current_unlocks": current_level.unlocks,
            "next_unlocks": next_level.unlocks if next_level else (),
        }

    async def add_interaction(
        self,
        *,
        user_id: str,
        pet_id: str,
        event_type: str,
        request_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        rule = INTERACTION_RULES[event_type]
        event = await asyncio.to_thread(
            self._repository.apply_interaction,
            user_id=user_id,
            pet_id=pet_id,
            event_type=event_type,
            base_points=rule.points,
            max_per_day=rule.max_per_day,
            daily_cap=self._settings.intimacy_daily_cap,
            request_id=request_id,
            metadata=metadata,
        )
        event["level"] = level_for_points(event["points_after"]).model_dump()
        event["progress"] = level_progress(event["points_after"])
        return event

    async def action_catalog(
        self, user_id: str, pet_id: str
    ) -> list[dict[str, Any]]:
        pet = await asyncio.to_thread(self._repository.get_owned_pet, user_id, pet_id)
        level = level_for_points(int(pet["intimacy_points"])).level
        catalog: list[dict[str, Any]] = []
        for action in ACTIONS:
            capabilities = tuple(
                component.capability for component in action.components
            )
            unavailable_reason = self._motion.unavailable_reason(capabilities)
            catalog.append(
                {
                    **action.model_dump(mode="json"),
                    "unlocked": action_is_unlocked(
                        action, pet["personality_id"], level
                    ),
                    "available": unavailable_reason is None,
                    "unavailable_reason": unavailable_reason,
                    "parameters_editable": False,
                    "safety_profile": "validated_preset_v1",
                }
            )
        return catalog

    async def execute_action(
        self,
        *,
        user_id: str,
        pet_id: str,
        action_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        action = ACTION_BY_ID.get(action_id)
        if action is None:
            raise ResourceNotFoundError("预设动作不存在")
        pet = await asyncio.to_thread(self._repository.get_owned_pet, user_id, pet_id)
        if not bool(pet["online"]):
            raise DeviceUnavailableError("设备离线，无法执行动作")
        level = level_for_points(int(pet["intimacy_points"])).level
        if not action_is_unlocked(action, pet["personality_id"], level):
            raise ActionConflictError("当前性格或亲密度尚未解锁该动作")
        capabilities = tuple(
            component.capability for component in action.components
        )
        unavailable_reason = self._motion.unavailable_reason(capabilities)
        if unavailable_reason is not None:
            raise AdapterNotImplementedError(
                unavailable_reason,
                details={
                    "capabilities": [
                        capability.value
                        for capability in capabilities
                        if not self._motion.supports(capability)
                    ]
                },
            )

        execution, created = await asyncio.to_thread(
            self._repository.create_action_execution,
            user_id=user_id,
            pet_id=pet_id,
            action_id=action_id,
            request_id=request_id,
        )
        if not created:
            return {**execution, "duplicate": True}

        try:
            result = await self._motion.run_sequence(
                action_name=action.action_id,
                steps=tuple(
                    (
                        component.capability,
                        component.speed,
                        component.duration_ms,
                    )
                    for component in action.components
                ),
                request_id=request_id,
            )
        except Exception as exc:
            await asyncio.to_thread(
                self._repository.update_action_execution,
                execution["execution_id"],
                status="failed",
                error=str(exc),
            )
            raise

        await asyncio.to_thread(
            self._repository.update_action_execution,
            execution["execution_id"],
            status="running",
        )
        token = result["execution_token"]
        finalizer = asyncio.create_task(
            self._finalize_action(execution["execution_id"], token),
            name=f"product-action-{execution['execution_id']}",
        )
        self._finalizers.add(finalizer)
        finalizer.add_done_callback(self._finalizers.discard)
        return {
            **execution,
            "status": "running",
            "duplicate": False,
        }

    async def _finalize_action(self, execution_id: str, token: str) -> None:
        try:
            outcome = await self._motion.await_execution(token)
            status = {
                "completed": "completed",
                "cancelled": "cancelled",
                "timed_out": "timed_out",
                "failed": "failed",
            }[outcome]
            await asyncio.to_thread(
                self._repository.update_action_execution,
                execution_id,
                status=status,
                result="预设动作执行完成" if status == "completed" else None,
                error=None if status == "completed" else f"动作结果: {status}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await asyncio.to_thread(
                self._repository.update_action_execution,
                execution_id,
                status="failed",
                error=str(exc),
            )

    async def action_executions(
        self, user_id: str, pet_id: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._repository.list_action_executions, user_id, pet_id, 20
        )

    async def send_dialog(
        self,
        *,
        user_id: str,
        pet_id: str,
        content: str,
        trigger_action: bool,
    ) -> dict[str, Any]:
        pet = await asyncio.to_thread(self._repository.get_owned_pet, user_id, pet_id)
        personality = PERSONALITY_BY_ID[pet["personality_id"]]
        level = level_for_points(int(pet["intimacy_points"]))
        style = personality.style_for_level(level.level)
        template = personality.response_templates[
            len(content) % len(personality.response_templates)
        ]
        assistant_content = (
            f"{template.format(address=style.address)} "
            f"你刚才提到“{content[:60]}”，这是本地产品 Mock 回应。"
        )
        conversation_id, messages = await asyncio.to_thread(
            self._repository.add_dialog_pair,
            user_id=user_id,
            pet_id=pet_id,
            user_content=content,
            assistant_content=assistant_content,
            voice_id=personality.voice_id,
        )
        intimacy_event = await self.add_interaction(
            user_id=user_id,
            pet_id=pet_id,
            event_type="valid_dialog",
            request_id=f"dialog:{conversation_id}",
            metadata={"conversation_id": conversation_id},
        )

        action_result: dict[str, Any] | None = None
        if trigger_action:
            unlocked_defaults = [
                action_id
                for action_id in personality.default_actions
                if action_id in ACTION_BY_ID
                and action_is_unlocked(
                    ACTION_BY_ID[action_id],
                    personality.personality_id,
                    level.level,
                )
            ]
            if unlocked_defaults:
                try:
                    action_result = await self.execute_action(
                        user_id=user_id,
                        pet_id=pet_id,
                        action_id=unlocked_defaults[0],
                        request_id=f"dialog-action:{conversation_id}",
                    )
                except AiCatError as exc:
                    action_result = {
                        "status": "skipped",
                        "reason": exc.message,
                    }

        return {
            "conversation_id": conversation_id,
            "messages": messages,
            "voice_id": personality.voice_id,
            "voice_integration_status": "mock_only",
            "style": style.model_dump(),
            "intimacy_event": intimacy_event,
            "action": action_result,
        }

    async def dialog_history(
        self, user_id: str, pet_id: str
    ) -> list[dict[str, Any]]:
        await self._sync_native_dialog_events(user_id, pet_id)
        return await asyncio.to_thread(
            self._repository.list_dialogs, user_id, pet_id, 50
        )

    async def dialog_conversations(
        self, user_id: str, pet_id: str, limit: int = 30
    ) -> dict[str, Any]:
        sync = await self._sync_native_dialog_events(user_id, pet_id)
        rows = await asyncio.to_thread(
            self._repository.list_dialog_conversations,
            user_id,
            pet_id,
            limit,
        )
        return {
            "conversations": [self._conversation_summary(row) for row in rows],
            "sync": sync,
        }

    async def dialog_conversation(
        self, user_id: str, pet_id: str, conversation_id: str
    ) -> dict[str, Any]:
        sync = await self._sync_native_dialog_events(user_id, pet_id)
        messages = await asyncio.to_thread(
            self._repository.get_dialog_conversation,
            user_id,
            pet_id,
            conversation_id,
        )
        return {
            "conversation": self._conversation_summary_from_messages(messages),
            "messages": messages,
            "sync": sync,
        }

    async def _sync_native_dialog_events(
        self, user_id: str, pet_id: str
    ) -> dict[str, Any]:
        source_signature: tuple[int, int] | None = None
        source_updated_at: str | None = None
        try:
            source_stat = self._settings.dialog_event_path.stat()
            source_signature = (source_stat.st_mtime_ns, source_stat.st_size)
            source_updated_at = datetime.fromtimestamp(
                source_stat.st_mtime,
                timezone.utc,
            ).isoformat()
        except OSError:
            pass

        owner_key = (user_id, pet_id)
        async with self._dialog_sync_lock:
            imported = 0
            if (
                source_signature is not None
                and self._dialog_source_signatures.get(owner_key)
                != source_signature
            ):
                events = await asyncio.to_thread(self._read_native_dialog_events)
                if events:
                    imported = await asyncio.to_thread(
                        self._repository.import_native_dialog_events,
                        user_id=user_id,
                        pet_id=pet_id,
                        events=events,
                    )
                    if imported:
                        LOGGER.info("imported %d native dialog messages", imported)
                self._dialog_source_signatures[owner_key] = source_signature
            state = await asyncio.to_thread(
                self._repository.dialog_sync_state,
                user_id,
                pet_id,
            )

        latest_message_at = state["latest_message_at"]
        return {
            "imported_messages": imported,
            "message_count": int(state["message_count"]),
            "conversation_count": int(state["conversation_count"]),
            "latest_message_at": latest_message_at,
            "source_updated_at": source_updated_at,
            "source_available": source_updated_at is not None,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "revision": f"{state['message_count']}:{latest_message_at or ''}",
        }

    @staticmethod
    def _conversation_summary(row: dict[str, Any]) -> dict[str, Any]:
        user_count = int(row["user_message_count"])
        assistant_count = int(row["assistant_message_count"])
        if user_count and assistant_count:
            status = "complete"
        elif user_count:
            status = "waiting_assistant"
        else:
            status = "assistant_only"
        started_at = str(row["started_at"])
        updated_at = str(row["updated_at"])
        try:
            duration_seconds = max(
                int(
                    (
                        datetime.fromisoformat(updated_at)
                        - datetime.fromisoformat(started_at)
                    ).total_seconds()
                ),
                0,
            )
        except ValueError:
            duration_seconds = 0
        return {
            "conversation_id": row["conversation_id"],
            "status": status,
            "source": "device" if int(row["device_source"]) else "mock",
            "started_at": started_at,
            "updated_at": updated_at,
            "duration_seconds": duration_seconds,
            "message_count": int(row["message_count"]),
            "user_message_count": user_count,
            "assistant_message_count": assistant_count,
            "preview": row["preview"],
            "assistant_preview": row["assistant_preview"],
        }

    @classmethod
    def _conversation_summary_from_messages(
        cls, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        user_messages = [item for item in messages if item["role"] == "user"]
        assistant_messages = [
            item for item in messages if item["role"] == "assistant"
        ]
        row = {
            "conversation_id": messages[0]["conversation_id"],
            "started_at": messages[0]["created_at"],
            "updated_at": messages[-1]["created_at"],
            "message_count": len(messages),
            "user_message_count": len(user_messages),
            "assistant_message_count": len(assistant_messages),
            "device_source": int(
                any(
                    item.get("voice_id") == "volcengine_tts"
                    or str(item["conversation_id"]).startswith("native-")
                    for item in messages
                )
            ),
            "preview": (
                user_messages[0]["content"]
                if user_messages
                else messages[0]["content"]
            ),
            "assistant_preview": (
                assistant_messages[-1]["content"] if assistant_messages else None
            ),
        }
        return cls._conversation_summary(row)

    def _read_native_dialog_events(self) -> list[dict[str, Any]]:
        path = self._settings.dialog_event_path
        try:
            if path.stat().st_size > MAX_NATIVE_DIALOG_FILE_BYTES:
                LOGGER.warning("native dialog event file exceeds size limit: %s", path)
                return []
            lines = path.read_bytes().splitlines()
        except FileNotFoundError:
            return []
        except OSError:
            LOGGER.exception("failed to read native dialog events: %s", path)
            return []

        events: list[dict[str, Any]] = []
        for line in lines:
            if not line or len(line) > MAX_NATIVE_DIALOG_LINE_BYTES:
                continue
            try:
                payload = json.loads(line)
                event_id = str(payload["event_id"]).strip()
                source_conversation_id = str(payload["conversation_id"]).strip()
                device_serial = str(payload["device_serial"]).strip()
                role = str(payload["role"]).strip()
                content = str(payload["content"]).strip()
                created_at_ms = int(payload["created_at_ms"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                not event_id
                or len(event_id) > 256
                or not source_conversation_id
                or len(source_conversation_id) > 256
                or not device_serial
                or len(device_serial) > 64
                or role not in {"user", "assistant"}
                or not content
                or len(content) > 8192
                or created_at_ms <= 0
            ):
                continue
            events.append(
                {
                    "event_id": event_id,
                    "conversation_id": source_conversation_id,
                    "source_conversation_id": source_conversation_id,
                    "device_serial": device_serial,
                    "role": role,
                    "content": content,
                    "created_at": datetime.fromtimestamp(
                        created_at_ms / 1000,
                        timezone.utc,
                    ).isoformat(),
                }
            )
        return self._normalize_native_turn_ids(events)

    @staticmethod
    def _normalize_native_turn_ids(
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        active_turns: dict[tuple[str, str], dict[str, Any]] = {}
        for event in events:
            key = (event["device_serial"], event["source_conversation_id"])
            active = active_turns.get(key)
            starts_new_turn = active is None
            if event["role"] == "user":
                starts_new_turn = starts_new_turn or bool(
                    active and (active["user_seen"] or active["assistant_seen"])
                )
            else:
                starts_new_turn = starts_new_turn or bool(
                    active and active["assistant_seen"]
                )
            if starts_new_turn:
                digest = hashlib.sha256(
                    (
                        f"{event['device_serial']}:"
                        f"{event['source_conversation_id']}:"
                        f"{event['event_id']}"
                    ).encode("utf-8")
                ).hexdigest()[:24]
                active = {
                    "conversation_id": f"native-turn-{digest}",
                    "user_seen": False,
                    "assistant_seen": False,
                }
                active_turns[key] = active
            event["conversation_id"] = active["conversation_id"]
            active[f"{event['role']}_seen"] = True
            event.pop("source_conversation_id", None)
        return events

    async def update_settings(
        self,
        *,
        user_id: str,
        pet_id: str,
        name: str | None,
        volume: int | None,
    ) -> dict[str, Any]:
        pet = await asyncio.to_thread(
            self._repository.update_pet_settings,
            user_id,
            pet_id,
            name=name,
            volume=volume,
        )
        return self._pet_summary(pet)

    async def unbind(self, user_id: str, pet_id: str) -> None:
        await asyncio.to_thread(self._repository.unbind_pet, user_id, pet_id)

    async def feedback(
        self,
        *,
        user_id: str,
        pet_id: str,
        category: str,
        content: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._repository.add_feedback,
            user_id=user_id,
            pet_id=pet_id,
            category=category,
            content=content,
        )

    async def update_mock_device_status(
        self,
        *,
        user_id: str,
        pet_id: str,
        online: bool,
        battery_percent: int,
        charging: bool,
        network_status: str,
    ) -> dict[str, Any]:
        pet = await asyncio.to_thread(
            self._repository.update_device_status,
            user_id,
            pet_id,
            online=online,
            battery_percent=battery_percent,
            charging=charging,
            network_status=network_status,
        )
        return self._pet_summary(pet)

    async def shutdown(self) -> None:
        if self._finalizers:
            await asyncio.gather(*tuple(self._finalizers), return_exceptions=True)

    @staticmethod
    def _personality_summary(personality: Any) -> dict[str, Any]:
        return {
            "personality_id": personality.personality_id,
            "name": personality.name,
            "description": personality.description,
            "language_style": personality.language_style,
            "voice_id": personality.voice_id,
        }

    @staticmethod
    def _pet_summary(pet: dict[str, Any]) -> dict[str, Any]:
        return {
            **pet,
            "online": bool(pet["online"]),
            "charging": bool(pet["charging"]),
        }
