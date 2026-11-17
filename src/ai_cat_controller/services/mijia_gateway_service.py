"""Preparation state and allowlisted commands for a future Mi Home connector.

This boundary intentionally stores no Xiaomi credentials and does not claim a
platform product model. MIoT instance identifiers stay unset until the real
product SPEC is approved.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.core.operation_gate import DeviceOperationGate, device_operation
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.schemas.mijia import MijiaActionId, MijiaPropertyId
from ai_cat_controller.services.device_service import DeviceService
from ai_cat_controller.services.dialog_service import DialogService
from ai_cat_controller.services.motion_service import MotionService
from ai_cat_controller.services.product_mock_service import ProductMockService

MIJIA_SPEC_REVISION = "mijia-preparation-v1"

MIJIA_PROPERTIES: tuple[dict[str, Any], ...] = (
    {
        "property_id": "online",
        "name": "在线状态",
        "access": ["read", "notify"],
        "value_type": "bool",
        "siid": None,
        "piid": None,
    },
    {
        "property_id": "battery_level",
        "name": "电量",
        "access": ["read", "notify"],
        "value_type": "uint8",
        "value_range": [0, 100, 1],
        "siid": None,
        "piid": None,
    },
    {
        "property_id": "charging",
        "name": "充电状态",
        "access": ["read", "notify"],
        "value_type": "bool",
        "siid": None,
        "piid": None,
    },
    {
        "property_id": "volume",
        "name": "音量",
        "access": ["read", "write", "notify"],
        "value_type": "uint8",
        "value_range": [0, 100, 1],
        "siid": None,
        "piid": None,
    },
    {
        "property_id": "intimacy_level",
        "name": "亲密等级",
        "access": ["read", "notify"],
        "value_type": "uint8",
        "value_range": [0, 4, 1],
        "siid": None,
        "piid": None,
    },
    {
        "property_id": "intimacy_points",
        "name": "亲密度",
        "access": ["read", "notify"],
        "value_type": "uint16",
        "value_range": [0, 9999, 1],
        "siid": None,
        "piid": None,
    },
    {
        "property_id": "dialog_state",
        "name": "互动状态",
        "access": ["read", "notify"],
        "value_type": "enum",
        "values": [
            "ready",
            "listening",
            "thinking",
            "answering",
            "interrupted",
            "offline",
            "unavailable",
        ],
        "siid": None,
        "piid": None,
    },
    {
        "property_id": "motion_state",
        "name": "动作状态",
        "access": ["read", "notify"],
        "value_type": "enum",
        "values": ["idle", "active"],
        "siid": None,
        "piid": None,
    },
)

MIJIA_ACTIONS: tuple[dict[str, Any], ...] = (
    {"action_id": "head_nod", "name": "点头", "siid": None, "aiid": None},
    {"action_id": "head_shake", "name": "摇头", "siid": None, "aiid": None},
    {"action_id": "tail_wag", "name": "摇尾", "siid": None, "aiid": None},
    {
        "action_id": "stop_motion",
        "name": "停止动作",
        "siid": None,
        "aiid": None,
    },
    {
        "action_id": "start_listening",
        "name": "开始聆听",
        "siid": None,
        "aiid": None,
    },
    {
        "action_id": "interrupt_dialog",
        "name": "结束互动",
        "siid": None,
        "aiid": None,
    },
)

MIJIA_EVENTS: tuple[dict[str, Any], ...] = (
    {
        "event_id": "action_completed",
        "name": "动作完成",
        "implemented": False,
        "siid": None,
        "eiid": None,
    },
    {
        "event_id": "level_changed",
        "name": "亲密等级变化",
        "implemented": False,
        "siid": None,
        "eiid": None,
    },
    {
        "event_id": "device_error",
        "name": "设备异常",
        "implemented": False,
        "siid": None,
        "eiid": None,
    },
)

PRODUCT_ACTION_BY_MIJIA_ID = {
    "head_nod": "head_nod",
    "head_shake": "head_shake",
    "tail_wag": "tail_wag",
}


class MijiaGatewayService:
    def __init__(
        self,
        repository: SQLiteRepository,
        product: ProductMockService,
        device: DeviceService,
        dialog: DialogService,
        motion: MotionService,
        *,
        maintenance_gate: DeviceOperationGate | None = None,
    ) -> None:
        self._repository = repository
        self._maintenance_gate = maintenance_gate
        self._product = product
        self._device = device
        self._dialog = dialog
        self._motion = motion
        self._command_lock = asyncio.Lock()
        self._safety_command_lock = asyncio.Lock()

    def _actions(self) -> tuple[dict[str, Any], ...]:
        return tuple(item for item in MIJIA_ACTIONS if item["action_id"] != "tail_wag" or self._product.has_tail)

    def spec_contract(self) -> dict[str, Any]:
        return {
            "provider": "mijia",
            "spec_revision": MIJIA_SPEC_REVISION,
            "mapping_state": "awaiting_platform_spec",
            "miot_model": None,
            "services": [
                {
                    "service_id": "ai_cat_companion",
                    "name": "AI 狐狸伴侣",
                    "siid": None,
                }
            ],
            "properties": [dict(item) for item in MIJIA_PROPERTIES],
            "actions": [dict(item) for item in self._actions()],
            "events": [dict(item) for item in MIJIA_EVENTS],
        }

    @staticmethod
    def _status_payload(
        integration: dict[str, Any] | None,
        *,
        changed: bool = False,
        duplicate: bool = False,
    ) -> dict[str, Any]:
        prepared = bool(integration and integration["status"] == "prepared")
        return {
            "provider": "mijia",
            "status": "prepared" if prepared else "not_prepared",
            "gateway_device_id": (
                integration["gateway_device_id"] if prepared else None
            ),
            "platform_linked": False,
            "credentials_configured": False,
            "miot_model": integration["miot_model"] if prepared else None,
            "spec_revision": MIJIA_SPEC_REVISION,
            "prepared_at": integration["prepared_at"] if integration else None,
            "updated_at": integration["updated_at"] if integration else None,
            "changed": changed,
            "duplicate": duplicate,
        }

    async def status(self, user_id: str, pet_id: str) -> dict[str, Any]:
        integration = await asyncio.to_thread(
            self._repository.get_mijia_integration,
            user_id,
            pet_id,
        )
        return self._status_payload(integration)

    async def spec(self, user_id: str, pet_id: str) -> dict[str, Any]:
        await asyncio.to_thread(self._repository.get_owned_pet, user_id, pet_id)
        return self.spec_contract()

    @device_operation
    async def prepare(
        self,
        user_id: str,
        pet_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        integration, changed, duplicate = await asyncio.to_thread(
            self._repository.prepare_mijia_integration,
            user_id=user_id,
            pet_id=pet_id,
            request_id=request_id,
            spec_revision=MIJIA_SPEC_REVISION,
        )
        return self._status_payload(
            integration,
            changed=changed,
            duplicate=duplicate,
        )

    @device_operation
    async def remove(
        self,
        user_id: str,
        pet_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        integration, changed, duplicate = await asyncio.to_thread(
            self._repository.remove_mijia_integration,
            user_id=user_id,
            pet_id=pet_id,
            request_id=request_id,
        )
        return self._status_payload(
            integration,
            changed=changed,
            duplicate=duplicate,
        )

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    async def _replayed_result(
        self,
        *,
        gateway_device_id: str,
        request_id: str,
        operation: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        record = await asyncio.to_thread(
            self._repository.get_mijia_gateway_request,
            gateway_device_id,
            request_id,
        )
        if record is None:
            return None
        if (
            record["operation"] != operation
            or record["payload_hash"] != payload_hash
        ):
            raise ActionConflictError("request_id 已用于其他米家网关操作")
        return {**record["result"], "duplicate": True}

    @device_operation
    async def _save_result(
        self,
        *,
        gateway_device_id: str,
        request_id: str,
        operation: str,
        payload_hash: str,
        result: dict[str, Any],
        expected_context: dict[str, Any] | None = None,
        expected_epoch: int | None = None,
    ) -> None:
        if self._maintenance_gate is not None and expected_epoch is not None and self._maintenance_gate.epoch != expected_epoch:
            raise ActionConflictError("设备维护代次已变化，请重新核对")
        current = await asyncio.to_thread(
            self._repository.get_mijia_gateway_context, gateway_device_id,
        )
        if expected_context is not None and any(
            current[key] != expected_context[key]
            for key in ("user_id", "pet_id", "device_id")
        ):
            raise ActionConflictError("米家网关归属已变化，请重新核对")
        await asyncio.to_thread(
            self._repository.save_mijia_gateway_request,
            gateway_device_id=gateway_device_id,
            request_id=request_id,
            operation=operation,
            payload_hash=payload_hash,
            result=result,
        )

    async def _gateway_state(
        self,
        gateway_device_id: str,
    ) -> tuple[dict[str, Any], dict[MijiaPropertyId, Any], str]:
        context = await asyncio.to_thread(
            self._repository.get_mijia_gateway_context,
            gateway_device_id,
        )
        dashboard, hardware = await asyncio.gather(
            self._product.dashboard(context["user_id"], context["pet_id"]),
            self._device.get_device_status(),
        )
        pet = dashboard["pet"]
        intimacy = dashboard["intimacy"]
        battery_available = hardware.get("battery_available")
        battery_level = hardware.get("battery_percent")
        if battery_available is False:
            battery_level = None
        if battery_level is None and battery_available is not False:
            battery_level = pet.get("battery_percent")
        charging = hardware.get("charging")
        if charging is None:
            charging = pet.get("charging")
        volume = hardware.get("output_volume_percent")
        if volume is None:
            volume = pet.get("volume")
        raw_dialog_state = str(hardware.get("dialog_state", "unavailable"))
        dialog_state = {
            "idle": "ready",
            "awake": "listening",
            "speaking": "answering",
        }.get(raw_dialog_state, raw_dialog_state)
        if dialog_state not in {
            "ready",
            "listening",
            "thinking",
            "answering",
            "interrupted",
            "offline",
        }:
            dialog_state = "unavailable"
        motion_state = (
            "idle" if hardware.get("current_action") == "idle" else "active"
        )
        values: dict[MijiaPropertyId, Any] = {
            "online": bool(hardware.get("connected", pet.get("online", False))),
            "battery_level": battery_level,
            "charging": charging,
            "volume": volume,
            "intimacy_level": intimacy["level"]["level"],
            "intimacy_points": intimacy["points"],
            "dialog_state": dialog_state,
            "motion_state": motion_state,
        }
        return context, values, datetime.now(timezone.utc).isoformat()

    async def snapshot(self, gateway_device_id: str) -> dict[str, Any]:
        context, values, updated_at = await self._gateway_state(gateway_device_id)
        catalog = await self._product.action_catalog(context["user_id"], context["pet_id"])
        executable = {item["action_id"] for item in catalog if item["available"] and item["unlocked"]}
        return {
            "gateway_device_id": gateway_device_id,
            "spec_revision": context["spec_revision"],
            "device": {
                "name": context["name"],
                "online": values["online"],
                "battery_level": values["battery_level"],
                "charging": values["charging"],
                "volume": values["volume"],
            },
            "intimacy": {
                "level": values["intimacy_level"],
                "points": values["intimacy_points"],
            },
            "states": {
                "dialog": values["dialog_state"],
                "motion": values["motion_state"],
            },
            "available_action_ids": [
                item["action_id"] for item in self._actions()
                if item["action_id"] not in PRODUCT_ACTION_BY_MIJIA_ID
                or PRODUCT_ACTION_BY_MIJIA_ID[item["action_id"]] in executable
            ],
            "updated_at": updated_at,
        }

    async def read_properties(
        self,
        gateway_device_id: str,
        properties: list[MijiaPropertyId],
    ) -> dict[str, Any]:
        context, values, updated_at = await self._gateway_state(gateway_device_id)
        return {
            "gateway_device_id": gateway_device_id,
            "spec_revision": context["spec_revision"],
            "properties": {property_id: values[property_id] for property_id in properties},
            "updated_at": updated_at,
        }

    @device_operation
    async def write_volume(
        self,
        gateway_device_id: str,
        *,
        value: int,
        request_id: str,
    ) -> dict[str, Any]:
        operation = "write:volume"
        payload_hash = self._payload_hash({"property_id": "volume", "value": value})
        async with self._command_lock:
            context = await asyncio.to_thread(
                self._repository.get_mijia_gateway_context,
                gateway_device_id,
            )
            replayed = await self._replayed_result(
                gateway_device_id=gateway_device_id,
                request_id=request_id,
                operation=operation,
                payload_hash=payload_hash,
            )
            if replayed is not None:
                return replayed
            await self._product.update_settings(
                user_id=context["user_id"],
                pet_id=context["pet_id"],
                name=None,
                volume=value,
            )
            result = {
                "property_id": "volume",
                "value": value,
                "request_id": request_id,
                "duplicate": False,
            }
            await self._save_result(
                gateway_device_id=gateway_device_id,
                request_id=request_id,
                operation=operation,
                payload_hash=payload_hash,
                result=result,
                expected_context=context,
            )
            return result

    async def invoke_action(
        self,
        gateway_device_id: str,
        action_id: MijiaActionId,
        request_id: str,
    ) -> dict[str, Any]:
        if action_id in {"stop_motion", "interrupt_dialog"}:
            return await self._invoke_safe_action(gateway_device_id, action_id, request_id)
        return await self._invoke_ordinary_action(gateway_device_id, action_id, request_id)

    async def _invoke_safe_action(
        self,
        gateway_device_id: str,
        action_id: MijiaActionId,
        request_id: str,
    ) -> dict[str, Any]:
        operation = f"action:{action_id}"
        payload_hash = self._payload_hash({"action_id": action_id})
        ticket_epoch = self._maintenance_gate.epoch if self._maintenance_gate is not None else None
        ticket_invalid = False
        if self._maintenance_gate is not None:
            try:
                self._maintenance_gate.checkpoint()
            except ActionConflictError:
                # Safety may still run for an old ingress ticket; only its
                # subsequent ordinary receipt write must be rejected honestly.
                ticket_invalid = True
        # Do not queue an emergency stop behind an ordinary in-flight command.
        async with self._safety_command_lock:
            context = await asyncio.to_thread(
                self._repository.get_mijia_gateway_context, gateway_device_id,
            )
            replayed = await self._replayed_result(
                gateway_device_id=gateway_device_id, request_id=request_id,
                operation=operation, payload_hash=payload_hash,
            )
            if replayed is not None:
                return replayed
            if action_id == "stop_motion":
                action = await self._motion.stop()
                details = {"stopped": action["stopped"]}
            else:
                action = await self._dialog.interrupt(request_id=request_id)
                details = {"dialog_state": action["dialog_state"]}
            result = {
                "action_id": action_id, "request_id": request_id,
                "status": "completed", "duplicate": False, **details,
            }
            try:
                await self._save_result(
                    gateway_device_id=gateway_device_id, request_id=request_id,
                    operation=operation, payload_hash=payload_hash, result=result,
                    expected_context=context,
                    expected_epoch=ticket_epoch,
                )
            except ActionConflictError:
                if self._maintenance_gate is not None and (
                    ticket_invalid or self._maintenance_gate.closing or self._maintenance_gate.epoch != ticket_epoch
                ):
                    raise ActionConflictError(
                        "停止/打断已请求但维护中未记录回执，请核对状态"
                    ) from None
                raise
            return result

    @device_operation
    async def _invoke_ordinary_action(
        self,
        gateway_device_id: str,
        action_id: MijiaActionId,
        request_id: str,
    ) -> dict[str, Any]:
        operation = f"action:{action_id}"
        payload_hash = self._payload_hash({"action_id": action_id})
        async with self._command_lock:
            context = await asyncio.to_thread(
                self._repository.get_mijia_gateway_context,
                gateway_device_id,
            )
            replayed = await self._replayed_result(
                gateway_device_id=gateway_device_id,
                request_id=request_id,
                operation=operation,
                payload_hash=payload_hash,
            )
            if replayed is not None:
                return replayed

            details: dict[str, Any] = {}
            if action_id in PRODUCT_ACTION_BY_MIJIA_ID:
                action = await self._product.execute_action(
                    user_id=context["user_id"],
                    pet_id=context["pet_id"],
                    action_id=PRODUCT_ACTION_BY_MIJIA_ID[action_id],
                    request_id=f"mijia:{gateway_device_id}:{request_id}",
                )
                action_status = action.get("status", "accepted")
                if action.get("execution_id"):
                    details["execution_id"] = action["execution_id"]
            elif action_id == "start_listening":
                action = await self._dialog.wake(request_id=request_id)
                action_status = "completed"
                details["dialog_state"] = action["dialog_state"]
            else:
                raise ActionConflictError("不支持该米家网关操作")

            result = {
                "action_id": action_id,
                "request_id": request_id,
                "status": action_status,
                "duplicate": False,
                **details,
            }
            await self._save_result(
                gateway_device_id=gateway_device_id,
                request_id=request_id,
                operation=operation,
                payload_hash=payload_hash,
                result=result,
                expected_context=context,
            )
            return result
