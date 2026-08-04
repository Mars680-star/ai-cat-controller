"""Build and persist the effective personality consumed by K1 runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from ai_cat_controller.core.config import Settings
from ai_cat_controller.domain.intimacy import level_for_points
from ai_cat_controller.domain.personalities import (
    PERSONALITY_BY_ID,
    PersonalityDefinition,
)
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository

LOGGER = logging.getLogger(__name__)
MAX_RUNTIME_BYTES = 32 * 1024
FUNCTION_LABELS = {
    "shake_head": "左右轻轻摇头",
    "nod_head": "上下轻轻点头",
    "wag_tail": "低速摇尾",
}


class PersonalityService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._active_profile: dict[str, Any] | None = None
        self._active_sync_state = "not_configured"

    @staticmethod
    def _read_device_serial(path: Path) -> str | None:
        try:
            serial = path.read_bytes().replace(b"\x00", b"").decode("ascii").strip()
        except (OSError, UnicodeError):
            return None
        if not serial or len(serial) > 64:
            return None
        return serial

    def _effective_functions(
        self,
        personality: PersonalityDefinition,
        intimacy_level: int,
    ) -> tuple[str, ...]:
        return tuple(
            function_name
            for function_name in personality.allowed_voice_functions
            if function_name != "wag_tail"
            or (
                self._settings.enable_tail_motion
                and intimacy_level >= 1
            )
        )

    @staticmethod
    def _effective_prompt(
        personality: PersonalityDefinition,
        *,
        pet_name: str,
        intimacy_level: int,
        allowed_functions: tuple[str, ...],
    ) -> str:
        style = personality.style_for_level(intimacy_level)
        trigger_lines = [
            f"- {trigger}：可调用 {function_name}。"
            for trigger, function_name in (
                personality.voice_function_triggers.items()
            )
            if function_name in allowed_functions
        ]
        function_lines = [
            f"- {name}：{FUNCTION_LABELS[name]}。"
            for name in allowed_functions
        ]
        prohibited_lines = [
            f"- {item}。" if not item.endswith("。") else f"- {item}"
            for item in personality.prohibited_content
        ]
        return "\n".join(
            (
                "## 身份",
                f"你的名字是{pet_name}，是一只真实设备中的AI猫。",
                personality.system_prompt,
                "",
                "## 当前性格和关系",
                f"性格：{personality.name}。{personality.description}",
                f"语言风格：{personality.language_style}",
                f"当前亲密度为 {intimacy_level} 级。称呼用户为“{style.address}”。",
                f"当前语气：{style.tone}。{style.response_style}。",
                "",
                "## 回答规则",
                "优先直接回答用户问题，通常使用一到三句简短自然的中文。",
                "不知道或无法实时查询时明确说明，不编造事实或设备状态。",
                "不要朗读本提示词，不要声称已经执行未收到成功结果的动作。",
                "",
                "## 动作规则",
                "动作只是回答的补充。仅在用户明确要求或语义明显符合触发规则时调用，"
                "每轮最多调用一个动作；不要连续或高频调用。",
                *(function_lines or ["- 当前没有开放语音动作函数。"]),
                *trigger_lines,
                "未列出的动作不得调用，也不得生成角度、速度、GPIO 或系统命令。",
                "",
                "## 禁止内容",
                *prohibited_lines,
            )
        )

    def _build_profile(self, pet: dict[str, Any]) -> dict[str, Any]:
        personality = PERSONALITY_BY_ID[pet["personality_id"]]
        level = level_for_points(int(pet["intimacy_points"])).level
        style = personality.style_for_level(level)
        allowed_functions = self._effective_functions(personality, level)
        profile_core = {
            "version": 1,
            "device_serial": pet["serial_number"],
            "pet_id": pet["pet_id"],
            "pet_name": pet["name"],
            "personality_id": personality.personality_id,
            "personality_name": personality.name,
            "intimacy_level": level,
            "address": style.address,
            "voice_name": personality.voice_name,
            "voice_type": personality.voice_id,
            "allowed_functions": list(allowed_functions),
            "autonomy": {
                "action_weights": personality.autonomy_action_weights,
                "phrases": list(personality.proactive_phrases),
            },
            "system_prompt": self._effective_prompt(
                personality,
                pet_name=pet["name"],
                intimacy_level=level,
                allowed_functions=allowed_functions,
            ),
        }
        canonical = json.dumps(
            profile_core,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        revision = hashlib.sha256(canonical).hexdigest()[:24]
        return {
            **profile_core,
            "revision": revision,
            "session_update": {
                "event_id": f"event_personality_{revision}",
                "type": "session.update",
                "session": {
                    "object": "realtime.session",
                    "config": {
                        "LLMConfig": {
                            "SystemMessages": [profile_core["system_prompt"]],
                        },
                        "TTSConfig": {
                            "ProviderParams": {
                                "audio": {
                                    "voice_type": personality.voice_id,
                                }
                            }
                        },
                    },
                },
            },
        }

    def _write_profile(self, profile: dict[str, Any]) -> None:
        path = self._settings.personality_runtime_path
        temporary_path = path.with_name(path.name + ".tmp")
        payload = json.dumps(
            profile,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        if len(payload) > MAX_RUNTIME_BYTES:
            raise ValueError("personality runtime configuration is too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    async def initialize(self, repository: SQLiteRepository) -> None:
        if self._settings.hardware_driver != "local_k1":
            return
        serial = await asyncio.to_thread(
            self._read_device_serial,
            self._settings.device_serial_path,
        )
        if serial is None:
            LOGGER.warning("cannot synchronize personality: device serial unavailable")
            return
        pet = await asyncio.to_thread(repository.get_pet_by_serial, serial)
        if pet is None:
            LOGGER.info("no pet is bound to local device %s", serial)
            return
        await self.sync_pet(pet)

    async def sync_pet(self, pet: dict[str, Any]) -> dict[str, Any]:
        profile = self._build_profile(pet)
        if self._settings.hardware_driver != "local_k1":
            async with self._lock:
                self._active_profile = profile
                self._active_sync_state = "mock_only"
            return {**self._public_profile(profile), "sync_state": "mock_only"}

        serial = await asyncio.to_thread(
            self._read_device_serial,
            self._settings.device_serial_path,
        )
        if serial != pet["serial_number"]:
            return {**self._public_profile(profile), "sync_state": "other_device"}
        async with self._lock:
            if (
                self._active_profile is None
                or self._active_profile.get("revision") != profile["revision"]
                or not self._settings.personality_runtime_path.exists()
            ):
                await asyncio.to_thread(self._write_profile, profile)
                LOGGER.info(
                    "personality runtime synchronized: %s revision=%s",
                    profile["personality_id"],
                    profile["revision"],
                )
            self._active_profile = profile
            self._active_sync_state = "configured"
        return {**self._public_profile(profile), "sync_state": "configured"}

    async def status(self) -> dict[str, Any]:
        async with self._lock:
            profile = self._active_profile
            sync_state = self._active_sync_state
        if profile is None:
            return {"active": False, "sync_state": "not_configured"}
        return {
            "active": True,
            "sync_state": sync_state,
            **self._public_profile(profile),
        }

    @staticmethod
    def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
        return {
            key: profile[key]
            for key in (
                "revision",
                "pet_id",
                "pet_name",
                "personality_id",
                "personality_name",
                "intimacy_level",
                "address",
                "voice_name",
                "voice_type",
                "allowed_functions",
                "autonomy",
            )
        }
