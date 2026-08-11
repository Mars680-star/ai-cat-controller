"""Persistent product workflow used by the browser and future mini program."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter, Capability
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import (
    AiCatError,
    ActionConflictError,
    AdapterNotImplementedError,
    AuthenticationError,
    DeviceUnavailableError,
    OperationDisabledError,
    ResourceNotFoundError,
)
from ai_cat_controller.domain.actions import ACTIONS, ACTION_BY_ID, action_is_unlocked
from ai_cat_controller.domain.growth import (
    GrowthEmotion,
    GrowthEventType,
)
from ai_cat_controller.domain.intimacy import (
    INTERACTION_RULES,
    INTIMACY_LEVELS,
    level_for_points,
    level_progress,
)
from ai_cat_controller.domain.personalities import PERSONALITIES, PERSONALITY_BY_ID
from ai_cat_controller.local_speech import LocalPhrasePlayer
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.services.growth_service import GrowthService
from ai_cat_controller.services.motion_service import MotionService
from ai_cat_controller.services.personality_service import PersonalityService

LOGGER = logging.getLogger(__name__)
MAX_NATIVE_DIALOG_FILE_BYTES = 4 * 1024 * 1024
MAX_NATIVE_DIALOG_LINE_BYTES = 16 * 1024
TOUCH_ACTION_BY_SENSOR = {
    "head": "head_nod",
    "back": "head_nod",
    "nose": "head_shake",
    "left_foot": "head_shake",
    "right_foot": "head_shake",
}
TOUCH_MOTION_READY_STATES = {"offline", "ready", "interrupted"}
PRODUCT_DATA_RESET_READY_STATES = {"offline", "ready", "interrupted", "unavailable"}


class ProductMockService:
    """Owns Mock login sessions and durable product-facing state."""

    def __init__(
        self,
        repository: SQLiteRepository,
        motion: MotionService,
        settings: Settings,
        personality: PersonalityService,
        adapter: AiCatAdapter,
    ) -> None:
        self._repository = repository
        self._motion = motion
        self._settings = settings
        self._personality = personality
        self._adapter = adapter
        self._growth = GrowthService(repository)
        self._touch_phrase_player = LocalPhrasePlayer(
            asset_root=settings.touch_speech_asset_root,
            marker_path=settings.touch_speech_marker_path,
            player_path=settings.touch_speech_player_path,
        )
        self._sessions: dict[str, str] = {}
        self._finalizers: set[asyncio.Task[None]] = set()
        self._dialog_sync_lock = asyncio.Lock()
        self._touch_motion_lock = asyncio.Lock()
        self._touch_speech_lock = asyncio.Lock()
        self._product_data_reset_lock = asyncio.Lock()
        self._last_touch_motion_at = 0.0
        self._last_touch_speech_at: dict[str, float] = {}
        self._dialog_source_signatures: dict[
            tuple[str, str], tuple[int, int]
        ] = {}

    async def initialize(self) -> None:
        await asyncio.to_thread(self._repository.initialize)
        await self._initialize_personality()
        await self._sync_bound_native_dialog_events()

    async def _initialize_personality(self) -> None:
        if self._settings.hardware_driver != "local_k1":
            return
        serial = await asyncio.to_thread(
            self._personality.read_device_serial,
            self._settings.device_serial_path,
        )
        if serial is None:
            LOGGER.warning("cannot synchronize personality: device serial unavailable")
            return
        pet = await asyncio.to_thread(self._repository.get_pet_by_serial, serial)
        if pet is None:
            LOGGER.info("no pet is bound to local device %s", serial)
            return
        await self._sync_personality(pet)

    async def _sync_personality(self, pet: dict[str, Any]) -> dict[str, Any]:
        behavior_profile = await self._growth.behavior_profile(pet)
        return await self._personality.sync_pet(pet, behavior_profile)

    async def _sync_bound_native_dialog_events(self) -> None:
        events = await asyncio.to_thread(self._read_native_dialog_events)
        device_serials = sorted({event["device_serial"] for event in events})
        for device_serial in device_serials:
            pet = await asyncio.to_thread(
                self._repository.get_pet_by_serial,
                device_serial,
            )
            if pet is None or not pet.get("owner_user_id"):
                continue
            await self._sync_native_dialog_events(
                str(pet["owner_user_id"]),
                str(pet["pet_id"]),
            )

    def _action_is_unlocked(
        self, action: Any, personality_id: str, intimacy_level: int
    ) -> bool:
        return self._settings.debug_unlock_all_actions or action_is_unlocked(
            action, personality_id, intimacy_level
        )

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
                "voice_name": item.voice_name,
                "integration_status": "volcengine_runtime_target",
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
        personality_sync = await self._sync_personality(pet)
        return {
            "pet": self._pet_summary(pet),
            "blind_box_revealed": personality_created,
            "personality": self._personality_summary(
                PERSONALITY_BY_ID[pet["personality_id"]]
            ),
            "persistence": "device_bound",
            "personality_sync": personality_sync,
        }

    async def dashboard(self, user_id: str, pet_id: str) -> dict[str, Any]:
        pet = await asyncio.to_thread(self._repository.get_owned_pet, user_id, pet_id)
        points = int(pet["intimacy_points"])
        level = level_for_points(points)
        personality = PERSONALITY_BY_ID[pet["personality_id"]]
        unlocked_actions = [
            action.action_id
            for action in ACTIONS
            if self._action_is_unlocked(
                action, personality.personality_id, level.level
            )
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
            "debug_unlimited_touch_intimacy": (
                self._settings.debug_unlimited_touch_intimacy
            ),
            "rules": [rule.model_dump() for rule in INTERACTION_RULES.values()],
            "history": history,
            "current_unlocks": current_level.unlocks,
            "next_unlocks": next_level.unlocks if next_level else (),
        }

    def _growth_debug_enabled(self) -> bool:
        return (
            self._settings.hardware_driver == "mock"
            or self._settings.enable_debug_growth
        )

    async def growth_state(self, user_id: str, pet_id: str) -> dict[str, Any]:
        return await self._growth.state(
            user_id=user_id,
            pet_id=pet_id,
            include_debug_values=self._growth_debug_enabled(),
        )

    async def debug_growth(
        self,
        *,
        user_id: str,
        pet_id: str,
        request_id: str,
        event_type: str,
        count: int,
        topic: str,
        emotion: str,
        engagement: float,
    ) -> dict[str, Any]:
        if not self._growth_debug_enabled():
            raise OperationDisabledError(
                "当前部署未启用成长人格调试接口"
            )
        result = await self._growth.record_debug_events(
            user_id=user_id,
            pet_id=pet_id,
            request_id=request_id,
            event_type=GrowthEventType(event_type),
            count=count,
            topic=topic,
            emotion=GrowthEmotion(emotion),
            engagement=engagement,
        )
        if result["behavior_changed"]:
            pet = await asyncio.to_thread(
                self._repository.get_owned_pet,
                user_id,
                pet_id,
            )
            await self._sync_personality(pet)
        return {
            "batch": result,
            "growth": await self.growth_state(user_id, pet_id),
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
        enforce_limits = not (
            event_type == "touch"
            and self._settings.debug_unlimited_touch_intimacy
        )
        event = await asyncio.to_thread(
            self._repository.apply_interaction,
            user_id=user_id,
            pet_id=pet_id,
            event_type=event_type,
            base_points=rule.points,
            max_per_day=rule.max_per_day,
            daily_cap=self._settings.intimacy_daily_cap,
            enforce_limits=enforce_limits,
            request_id=request_id,
            metadata=metadata,
        )
        event["level"] = level_for_points(event["points_after"]).model_dump()
        event["progress"] = level_progress(event["points_after"])
        event["growth"] = await self._safe_record_interaction_growth(
            user_id=user_id,
            pet_id=pet_id,
            request_id=request_id,
            event_type=event_type,
            metadata=metadata,
        )
        pet = await asyncio.to_thread(
            self._repository.get_owned_pet,
            user_id,
            pet_id,
        )
        event["personality_sync"] = await self._sync_personality(pet)
        return event

    async def _safe_record_interaction_growth(
        self,
        *,
        user_id: str,
        pet_id: str,
        request_id: str,
        event_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            growth = await self._growth.record_interaction(
                user_id=user_id,
                pet_id=pet_id,
                source_id=request_id,
                interaction_type=event_type,
                metadata=metadata,
            )
        except Exception:
            LOGGER.exception("interaction growth skipped: %s", request_id)
            return None
        return self._growth_result_summary(growth)

    async def _safe_record_dialog_growth(
        self,
        *,
        user_id: str,
        pet_id: str,
        conversation_id: str,
        user_content: str,
        source: str,
    ) -> dict[str, Any] | None:
        try:
            growth = await self._growth.record_dialog(
                user_id=user_id,
                pet_id=pet_id,
                conversation_id=conversation_id,
                user_content=user_content,
                metadata={"source": source},
            )
        except Exception:
            LOGGER.exception("dialog growth skipped: %s", conversation_id)
            return None
        return self._growth_result_summary(growth)

    @staticmethod
    def _growth_result_summary(
        growth: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if growth is None:
            return None
        return {
            key: growth[key]
            for key in (
                "event_id",
                "duplicate",
                "attribute_delta",
                "awarded_tags",
                "behavior_changed",
            )
            if key in growth
        }

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
                    "unlocked": self._action_is_unlocked(
                        action, pet["personality_id"], level
                    ),
                    "debug_unlocked": (
                        self._settings.debug_unlock_all_actions
                        and not action_is_unlocked(
                            action, pet["personality_id"], level
                        )
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
        if not self._action_is_unlocked(
            action, pet["personality_id"], level
        ):
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
                preset_name=action.hardware_preset,
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
        growth = await self._safe_record_dialog_growth(
            user_id=user_id,
            pet_id=pet_id,
            conversation_id=conversation_id,
            user_content=content,
            source="mock",
        )
        if growth and growth.get("behavior_changed"):
            pet = await asyncio.to_thread(
                self._repository.get_owned_pet,
                user_id,
                pet_id,
            )
            await self._sync_personality(pet)

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
            "growth_event": growth,
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
        growth_changed = False
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
                    growth_changed = await self._record_native_dialog_growth(
                        user_id=user_id,
                        pet_id=pet_id,
                        events=events,
                    )
                self._dialog_source_signatures[owner_key] = source_signature
            state = await asyncio.to_thread(
                self._repository.dialog_sync_state,
                user_id,
                pet_id,
            )

        if growth_changed:
            pet = await asyncio.to_thread(
                self._repository.get_owned_pet,
                user_id,
                pet_id,
            )
            await self._sync_personality(pet)

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

    async def _record_native_dialog_growth(
        self,
        *,
        user_id: str,
        pet_id: str,
        events: list[dict[str, Any]],
    ) -> bool:
        conversations: dict[str, dict[str, str]] = {}
        for event in events:
            conversation = conversations.setdefault(
                str(event["conversation_id"]),
                {},
            )
            conversation.setdefault(str(event["role"]), str(event["content"]))
        behavior_changed = False
        for conversation_id, messages in conversations.items():
            if "user" not in messages or "assistant" not in messages:
                continue
            result = await self._safe_record_dialog_growth(
                user_id=user_id,
                pet_id=pet_id,
                conversation_id=conversation_id,
                user_content=messages["user"],
                source="native",
            )
            behavior_changed = behavior_changed or bool(
                result and result.get("behavior_changed")
            )
        return behavior_changed

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
        await asyncio.to_thread(
            self._repository.get_owned_pet,
            user_id,
            pet_id,
        )
        if volume is not None:
            if not self._adapter.supports(Capability.OUTPUT_VOLUME):
                raise AdapterNotImplementedError(
                    self._adapter.capability_unavailable_reason(
                        Capability.OUTPUT_VOLUME
                    )
                )
            await self._adapter.set_output_volume(volume)
        pet = await asyncio.to_thread(
            self._repository.update_pet_settings,
            user_id,
            pet_id,
            name=name,
            volume=volume,
        )
        await self._sync_personality(pet)
        return self._pet_summary(pet)

    async def add_device_touch(
        self,
        *,
        device_serial: str,
        request_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        pet = await asyncio.to_thread(
            self._repository.get_pet_by_serial,
            device_serial,
        )
        if pet is None or not pet.get("owner_user_id"):
            LOGGER.info("ignoring touch from unbound device %s", device_serial)
            return None
        event = await self.add_interaction(
            user_id=str(pet["owner_user_id"]),
            pet_id=str(pet["pet_id"]),
            event_type="touch",
            request_id=request_id,
            metadata=metadata,
        )
        event["touch_action"] = await self._trigger_touch_action(
            user_id=str(pet["owner_user_id"]),
            pet_id=str(pet["pet_id"]),
            request_id=request_id,
            sensor=str(metadata.get("sensor", "")),
            duplicate=bool(event.get("duplicate")),
        )
        return event

    async def _trigger_touch_action(
        self,
        *,
        user_id: str,
        pet_id: str,
        request_id: str,
        sensor: str,
        duplicate: bool,
    ) -> dict[str, Any]:
        action_id = TOUCH_ACTION_BY_SENSOR.get(sensor)
        if duplicate:
            return {"status": "skipped", "reason": "duplicate_touch"}
        if action_id is None:
            return {"status": "skipped", "reason": "unknown_sensor"}

        dialog_status = await self._adapter.get_dialog_status()
        dialog_state = str(dialog_status.get("state", "unavailable"))
        if (
            dialog_status.get("session_active")
            or dialog_status.get("stale")
            or dialog_state not in TOUCH_MOTION_READY_STATES
        ):
            return {
                "status": "skipped",
                "reason": "dialog_busy",
                "dialog_state": dialog_state,
            }

        if not self._settings.enable_touch_motion:
            result: dict[str, Any] = {
                "status": "skipped",
                "reason": "touch_motion_disabled",
            }
            self._add_touch_speech_status(result, sensor, request_id)
            return result

        async with self._touch_motion_lock:
            now = time.monotonic()
            if (
                now - self._last_touch_motion_at
                < self._settings.touch_motion_cooldown_seconds
            ):
                result = {
                    "status": "skipped",
                    "reason": "touch_motion_cooldown",
                }
                self._add_touch_speech_status(result, sensor, request_id)
                return result
            try:
                result = await self.execute_action(
                    user_id=user_id,
                    pet_id=pet_id,
                    action_id=action_id,
                    request_id=f"{request_id}-motion",
                )
            except AiCatError as exc:
                LOGGER.info("touch motion skipped: %s", exc.message)
                result = {
                    "status": "skipped",
                    "reason": exc.code,
                    "message": exc.message,
                }
                self._add_touch_speech_status(result, sensor, request_id)
                return result
            except Exception as exc:
                LOGGER.exception("touch motion failed")
                result = {
                    "status": "failed",
                    "reason": type(exc).__name__,
                }
                self._add_touch_speech_status(result, sensor, request_id)
                return result
            self._last_touch_motion_at = now
            speech_scheduled = self._schedule_touch_speech(sensor, request_id)
            return {
                "status": "started",
                "action_id": action_id,
                "execution_id": result["execution_id"],
                "speech_scheduled": speech_scheduled,
            }

    def _add_touch_speech_status(
        self,
        result: dict[str, Any],
        sensor: str,
        request_id: str,
    ) -> None:
        if self._settings.enable_touch_speech:
            result["speech_scheduled"] = self._schedule_touch_speech(
                sensor,
                request_id,
            )

    def _schedule_touch_speech(self, sensor: str, request_id: str) -> bool:
        if not self._settings.enable_touch_speech:
            return False
        now = time.monotonic()
        if (
            now - self._last_touch_speech_at.get(sensor, 0.0)
            < self._settings.touch_speech_cooldown_seconds
        ):
            LOGGER.info("touch phrase suppressed by sensor cooldown: %s", sensor)
            return False
        self._last_touch_speech_at[sensor] = now
        speech_task = asyncio.create_task(
            self._play_touch_phrase_after_motion(sensor),
            name=f"touch-speech-{request_id}",
        )
        self._finalizers.add(speech_task)
        speech_task.add_done_callback(self._finalizers.discard)
        return True

    async def _play_touch_phrase_after_motion(
        self,
        sensor: str,
    ) -> None:
        try:
            async with self._touch_speech_lock:
                await self._motion.wait_until_idle(
                    timeout=self._settings.command_timeout_seconds + 5.0
                )
                dialog_status = await self._adapter.get_dialog_status()
                if (
                    dialog_status.get("session_active")
                    or dialog_status.get("stale")
                    or str(dialog_status.get("state", "unavailable"))
                    not in TOUCH_MOTION_READY_STATES
                ):
                    LOGGER.info("touch phrase skipped because dialog became busy")
                    return
                path = await asyncio.to_thread(
                    self._touch_phrase_player.play_touch,
                    sensor,
                )
                LOGGER.info("touch phrase played: %s", path)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("touch phrase playback skipped")

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

    def _runtime_data_paths(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("personality-runtime.json", self._settings.personality_runtime_path),
            ("dialog-events.jsonl", self._settings.dialog_event_path),
            ("dialog-text-request.json", self._settings.dialog_text_request_path),
            ("dialog-runtime-config.json", self._settings.dialog_config_path),
        )

    def _archive_runtime_data(
        self, backup_directory: Path
    ) -> list[tuple[Path, Path]]:
        archived: list[tuple[Path, Path]] = []
        database_path = self._repository.path.absolute()
        try:
            for archive_name, source_path in self._runtime_data_paths():
                source_path = source_path.absolute()
                try:
                    source_stat = source_path.lstat()
                except FileNotFoundError:
                    continue
                if source_path == database_path:
                    raise ActionConflictError("运行时数据路径不能与产品数据库相同")
                if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(
                    source_stat.st_mode
                ):
                    raise ActionConflictError(
                        "运行时数据文件类型异常，已取消格式化",
                        details={"file": archive_name},
                    )
                destination_path = backup_directory / archive_name
                os.replace(source_path, destination_path)
                destination_path.chmod(0o600)
                archived.append((source_path, destination_path))
        except Exception:
            self._restore_runtime_data(archived)
            raise
        return archived

    @staticmethod
    def _restore_runtime_data(archived: list[tuple[Path, Path]]) -> None:
        for source_path, destination_path in reversed(archived):
            try:
                source_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination_path, source_path)
            except OSError:
                LOGGER.exception("failed to restore runtime data %s", source_path)

    async def reset_product_data(self, *, user_id: str) -> dict[str, Any]:
        if not self._settings.enable_product_data_reset:
            raise OperationDisabledError("当前部署未启用体验数据格式化功能")

        async with self._product_data_reset_lock:
            dialog_status = await self._adapter.get_dialog_status()
            dialog_state = str(dialog_status.get("state", "unavailable"))
            stale_active_state = bool(
                dialog_status.get("stale")
            ) and dialog_state not in {"offline", "unavailable"}
            if (
                dialog_status.get("session_active")
                or stale_active_state
                or dialog_state not in PRODUCT_DATA_RESET_READY_STATES
            ):
                raise ActionConflictError(
                    "语音对话正在进行，请先打断并结束后再格式化",
                    details={"dialog_state": dialog_state},
                )

            if self._adapter.supports(Capability.STOP_MOTION):
                await self._motion.stop()
            if self._finalizers:
                await asyncio.gather(
                    *tuple(self._finalizers),
                    return_exceptions=True,
                )

            backup = await asyncio.to_thread(self._repository.backup_product_data)
            archived = await asyncio.to_thread(
                self._archive_runtime_data,
                backup["backup_directory"],
            )
            try:
                deleted = await asyncio.to_thread(
                    self._repository.clear_product_data
                )
            except Exception:
                await asyncio.to_thread(self._restore_runtime_data, archived)
                raise

            await self._personality.clear_runtime()
            self._sessions.clear()
            self._dialog_source_signatures.clear()
            self._last_touch_motion_at = 0.0
            self._last_touch_speech_at.clear()
            LOGGER.warning(
                "product experience data reset by %s; backup=%s",
                user_id,
                backup["backup_id"],
            )
            return {
                "reset": True,
                "backup_id": backup["backup_id"],
                "deleted": deleted,
                "archived_files": [path.name for _, path in archived],
            }

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
            "voice_name": personality.voice_name,
            "voice_id": personality.voice_id,
        }

    @staticmethod
    def _pet_summary(pet: dict[str, Any]) -> dict[str, Any]:
        return {
            **pet,
            "online": bool(pet["online"]),
            "charging": bool(pet["charging"]),
        }
