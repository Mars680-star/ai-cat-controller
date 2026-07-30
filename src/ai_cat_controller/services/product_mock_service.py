"""Persistent product workflow used by the browser and future mini program."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import (
    AiCatError,
    ActionConflictError,
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
        return [
            {
                **action.model_dump(mode="json"),
                "unlocked": action_is_unlocked(
                    action, pet["personality_id"], level
                ),
                "parameters_editable": False,
                "safety_profile": "validated_preset_v1",
            }
            for action in ACTIONS
        ]

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
        return await asyncio.to_thread(
            self._repository.list_dialogs, user_id, pet_id, 50
        )

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
