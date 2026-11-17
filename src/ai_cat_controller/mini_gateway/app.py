"""Standalone session app; optional fixed motion routes are disabled by default."""

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import ClientDisconnect

from .auth import Session, bearer_token, utc_expiry
from .config import MiniGatewayConfig
from .motion import MotionError
from .product import ProductError
from .product_models import Dashboard, Growth, InteractionRequest, IntimacyView, Receipt, validate_public
from .dialog import DialogReadError
from .dialog_models import Detail as DialogDetail, ListView as DialogList, Status as DialogStatus, validate_view as validate_dialog
from .dialog_control import DialogControlError
from .dialog_control_models import Command as DialogCommand, Control as DialogControl, Receipt as DialogReceipt, validate_control
from .preferences import PreferencesError
from .preferences_models import Command as PreferencesCommand, Snapshot as PreferencesSnapshot, Receipt as PreferencesReceipt, validate_preferences
from .lifecycle import LifecycleError
from .lifecycle_models import (Command as LifecycleCommand, ScopeRequest as LifecycleScope,
    Snapshot as LifecycleSnapshot, Challenge as LifecycleChallenge, Receipt as LifecycleReceipt,
    validate_lifecycle)
from .store import CodeConsumed, LoginRateLimited, SessionStore
from .wechat import IdentityProvider, WeChatCodeProvider

LOGGER = logging.getLogger(__name__)


class _Login(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(min_length=1, max_length=256)

    @field_validator("code")
    @classmethod
    def visible_code(cls, value: str) -> str:
        if any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise ValueError("Invalid code")
        return value


def _error(status: int, message: str, *, code: str | None = None) -> JSONResponse:
    return JSONResponse({"success": False, "message": message, **({"code": code} if code else {})}, status_code=status,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate request field")
        result[key] = value
    return result


class _Unauthorized(Exception):
    pass


class _MotionAccess(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


_REQUEST_ID = r"^[A-Za-z0-9_.:-]{1,64}$"
_OPAQUE_ID = r"^[A-Za-z0-9_-]{1,128}$"
_MOTION_BODY_ROUTE = re.compile(r"/v1/pets/[A-Za-z0-9_-]{1,128}/motion/(?:execute|stop)")
_INTERACTION_BODY_ROUTE = re.compile(r"/v1/pets/[A-Za-z0-9_-]{1,128}/interactions")
_DIALOG_COMMAND_ROUTE = re.compile(r"/v1/pets/[A-Za-z0-9_-]{1,128}/dialog/commands")
_PREFERENCES_COMMAND_ROUTE = re.compile(r"/v1/pets/[A-Za-z0-9_-]{1,128}/preferences/commands")
_LIFECYCLE_BODY_ROUTE = re.compile(r"/v1/pets/[A-Za-z0-9_-]{1,128}/lifecycle/(?:challenges|commands)")
_MOTION_IDS = {"head_nod", "head_shake", "tail_wag", "proud_pose", "quiet_companion", "greeting_combo", "celebration_combo"}
_MotionActionId = Literal["head_nod", "head_shake", "tail_wag", "proud_pose", "quiet_companion", "greeting_combo", "celebration_combo"]


class _ExecuteMotion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    actionId: _MotionActionId
    requestId: str = Field(pattern=_REQUEST_ID)


class _StopMotion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requestId: str = Field(pattern=_REQUEST_ID)
    executionId: str | None = Field(default=None, pattern=_OPAQUE_ID)


class _MotionAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    actionId: _MotionActionId
    name: str = Field(min_length=1, max_length=40)
    available: bool
    reason: str = Field(max_length=160)


class _MotionExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    executionId: str = Field(pattern=_OPAQUE_ID)
    actionId: Literal["head_nod", "head_shake", "tail_wag", "proud_pose", "quiet_companion", "greeting_combo", "celebration_combo", "stop"]
    status: Literal["sending", "running", "unknown", "stopped", "ended_unverified", "failed"]
    requestId: str = Field(pattern=_REQUEST_ID)
    updatedAt: str = Field(min_length=20, max_length=40)


class _MotionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    verified: bool
    deviceBusy: bool
    hasTail: bool | None = None
    deviceMotionEnabled: bool | None = None
    actions: list[_MotionAction] = Field(min_length=3, max_length=7)
    execution: _MotionExecution | None
    sampledAt: str


class _MotionHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[_MotionExecution] = Field(max_length=20)
    limit: Literal[20]


def _motion_timestamps(timestamps):
    for timestamp in timestamps:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", timestamp):
            raise ValueError("Invalid motion timestamp")
        datetime.fromisoformat(timestamp[:-1] + "+00:00")


def _motion_result(value):
    result = _MotionSnapshot.model_validate(value)
    expected_ids = {"head_nod", "head_shake", "quiet_companion"} if result.hasTail is False else _MOTION_IDS
    if len(result.actions) != len(expected_ids) or {action.actionId for action in result.actions} != expected_ids:
        raise ValueError("Invalid motion snapshot")
    if result.hasTail is False and not result.verified:
        raise ValueError("Unverified motion topology")
    timestamps = [result.sampledAt] + ([result.execution.updatedAt] if result.execution else [])
    _motion_timestamps(timestamps)
    if (not result.enabled or not result.verified or result.deviceBusy or result.deviceMotionEnabled is False) and any(action.available for action in result.actions):
        raise ValueError("Invalid motion availability")
    return result.model_dump(exclude_unset=True)


def _motion_history_result(value):
    result = _MotionHistory.model_validate(value)
    _motion_timestamps(item.updatedAt for item in result.items)
    if len({item.executionId for item in result.items}) != len(result.items):
        raise ValueError("Invalid motion history")
    return result.model_dump()


def create_app(config: MiniGatewayConfig, *, provider: IdentityProvider | None = None,
               store: SessionStore | None = None, status_reader=None,
               motion_controller=None,
               product_service=None,
               dialog_reader=None,
               dialog_controller=None,
               preferences_service=None,
               lifecycle_service=None,
               transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if lifecycle_service is not None:
                await lifecycle_service.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store if store is not None else SessionStore(config.db_path)
    app.state.status_reader = status_reader
    app.state.motion_controller = motion_controller
    app.state.product_service = product_service
    app.state.dialog_reader = dialog_reader
    app.state.dialog_controller = dialog_controller
    app.state.preferences_service = preferences_service
    app.state.lifecycle_service = lifecycle_service
    if lifecycle_service is not None:
        async def retire_authorization(*, user_id, pet_id, kind):
            # Called by the independent ledger only after its trusted executor
            # reports actual completion. Missing executors never reach here.
            if kind not in {"unbind", "product_reset"}:
                raise ValueError("Unexpected lifecycle retirement")
            await asyncio.to_thread(app.state.store.revoke_pet, user_id, pet_id)
        lifecycle_service.bind_retirement_handler(retire_authorization)
    app.state.provider = provider if provider is not None else (
        WeChatCodeProvider(config.app_id, config.app_secret, transport) if config.wechat_configured else None
    )

    @app.middleware("http")
    async def bounded_request(request: Request, call_next):
        if request.url.query:
            return _error(400, "不接受查询参数或查询令牌。")

        async def read_body():
            raw = bytearray()
            maximum = 4096 if request.method == "POST" and (
                _DIALOG_COMMAND_ROUTE.fullmatch(request.url.path) or _PREFERENCES_COMMAND_ROUTE.fullmatch(request.url.path)
                or _LIFECYCLE_BODY_ROUTE.fullmatch(request.url.path)) else 1024
            async for chunk in request.stream():
                if len(raw) + len(chunk) > maximum:
                    return None
                raw.extend(chunk)
            return bytes(raw)

        try:
            raw = await asyncio.wait_for(read_body(), timeout=1.0)
        except (TimeoutError, ClientDisconnect):
            return _error(400, "请求内容不可用。")
        if raw is None:
            return _error(413, "请求内容过大。")
        body_route = (request.url.path == "/v1/auth/wechat" or _MOTION_BODY_ROUTE.fullmatch(request.url.path)
                      or _INTERACTION_BODY_ROUTE.fullmatch(request.url.path) or _DIALOG_COMMAND_ROUTE.fullmatch(request.url.path)
                      or _PREFERENCES_COMMAND_ROUTE.fullmatch(request.url.path)
                      or _LIFECYCLE_BODY_ROUTE.fullmatch(request.url.path))
        if raw and not (request.method == "POST" and body_route):
            return _error(400, "该接口不接受请求内容。")
        request.state.gateway_body = raw
        response = await call_next(request)
        # Fixed operation/status only: distinguish a rejected request from one
        # that never arrived without logging identities, bearer or request data.
        motion_operation = _MOTION_BODY_ROUTE.fullmatch(request.url.path)
        if request.method == "POST" and motion_operation and response.status_code >= 400:
            LOGGER.warning("mini motion rejected: operation=%s status=%d",
                           "stop" if request.url.path.endswith("/stop") else "execute",
                           response.status_code)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(_Unauthorized)
    async def unauthorized(_request, _exception):
        return _error(401, "会话无效或已过期，请重新登录。")

    @app.exception_handler(_MotionAccess)
    async def motion_access_error(_request, exception):
        return _error(exception.status, exception.message)

    async def session(request: Request) -> Session:
        token = bearer_token(request.headers.get("authorization", ""))
        if token is None:
            raise _Unauthorized()
        try:
            result = await asyncio.to_thread(app.state.store.resolve_session, token, config.app_id)
        except Exception:
            result = None
        if result is None:
            raise _Unauthorized()
        return result

    @app.get("/health")
    async def health():
        controller = app.state.motion_controller
        return {"success": True, "data": {"mode": "local-development", "wechatConfigured": config.wechat_configured,
                                          "readOnly": not bool((controller is not None and controller.enabled) or
                                              (app.state.product_service is not None and app.state.product_service.interactions_enabled) or
                                              (app.state.dialog_controller is not None and app.state.dialog_controller.enabled) or
                                              (app.state.preferences_service is not None and
                                               (app.state.preferences_service.settings_enabled or app.state.preferences_service.feedback_enabled)) or
                                              (app.state.lifecycle_service is not None and app.state.lifecycle_service.execution_configured))}}

    @app.post("/v1/auth/wechat")
    async def wechat_login(request: Request):
        if not config.wechat_configured or app.state.provider is None:
            return _error(503, "微信登录尚未配置。")
        try:
            payload = _Login.model_validate(json.loads(request.state.gateway_body))
        except (ValidationError, ValueError, UnicodeError):
            return _error(400, "登录请求格式无效。")
        try:
            client = request.client.host if request.client else "unknown"
            await asyncio.to_thread(app.state.store.consume_code, config.app_id, payload.code, client)
        except CodeConsumed:
            return _error(409, "登录凭证已使用，请重新获取。")
        except LoginRateLimited:
            return _error(429, "登录请求过于频繁，请稍后再试。")
        except Exception:
            return _error(503, "登录服务暂不可用。")
        try:
            openid = await app.state.provider.exchange(payload.code)
            issued = await asyncio.to_thread(app.state.store.issue_session, config.app_id, openid, config.session_ttl_seconds)
        except Exception:
            return _error(503, "微信登录暂不可用，请重新获取登录凭证。")
        return {"success": True, "data": {"accessToken": issued.access_token, "expiresAt": utc_expiry(issued.expires_at), "userId": issued.user_id}}

    @app.get("/v1/session")
    async def current_session(current: Session = Depends(session)):
        return {"success": True, "data": {"userId": current.user_id, "expiresAt": utc_expiry(current.expires_at)}}

    @app.post("/v1/auth/logout")
    async def logout(request: Request, _current: Session = Depends(session)):
        try:
            await asyncio.to_thread(app.state.store.revoke_session, bearer_token(request.headers.get("authorization", "")), config.app_id)
        except Exception:
            return _error(503, "会话服务暂不可用。")
        return {"success": True, "data": {"loggedOut": True}}

    @app.get("/v1/pets")
    async def pets(current: Session = Depends(session)):
        try:
            result = await asyncio.to_thread(app.state.store.list_pets, current.user_id)
        except Exception:
            return _error(503, "设备列表暂不可用。")
        return {"success": True, "data": result}

    @app.get("/v1/pets/{pet_id}/status")
    async def pet_status(pet_id: str, request: Request, current: Session = Depends(session)):
        try:
            allowed = await asyncio.to_thread(app.state.store.authorize, current.user_id, pet_id)
        except Exception:
            return _error(503, "设备状态暂不可用。")
        if not allowed:
            return _error(404, "未找到设备。")
        if not config.status_pet_id or pet_id != config.status_pet_id or app.state.status_reader is None:
            return _error(503, "该设备尚未配置只读连接。")
        reader = app.state.status_reader
        expected_serial = getattr(reader, "expected_serial", None)
        if expected_serial is None:
            return _error(503, "该设备尚未配置完整标识校验。")
        try:
            if not await asyncio.to_thread(
                app.state.store.registered_device_matches, pet_id, expected_serial,
            ):
                return _error(503, "设备登记与只读连接不匹配。")
            result = await reader.read()
            if not isinstance(result, dict):
                raise ValueError("Invalid status reader result")
            # Do not deliver a slow in-flight response after ownership was revoked.
            allowed = await asyncio.to_thread(app.state.store.authorize, current.user_id, pet_id)
            identity_matches = await asyncio.to_thread(
                app.state.store.registered_device_matches, pet_id, expected_serial,
            )
        except Exception:
            return _error(503, "设备状态暂不可用。")
        # A valid owner is not enough after their bearer has expired or been
        # revoked during a slow read. Keep this outside the safe 503 handler so
        # invalid sessions are consistently reported as 401.
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        if not allowed:
            return _error(404, "未找到设备。")
        if not identity_matches:
            return _error(503, "设备登记与只读连接不匹配。")
        return {"success": True, "data": result}

    async def motion_access(pet_id: str, request: Request, current: Session):
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        try:
            allowed = await asyncio.to_thread(app.state.store.authorize, current.user_id, pet_id)
        except Exception:
            raise _MotionAccess(503, "动作授权暂不可用。") from None
        if not allowed:
            raise _MotionAccess(404, "未找到设备。")
        controller = app.state.motion_controller
        serial = getattr(controller, "expected_serial", None)
        if (controller is None or not serial or pet_id != config.status_pet_id or
                serial != getattr(app.state.status_reader, "expected_serial", None)):
            raise _MotionAccess(503, "该设备尚未配置动作连接。")
        try:
            matches = await asyncio.to_thread(app.state.store.registered_device_matches, pet_id, serial)
        except Exception:
            matches = False
        if not matches:
            raise _MotionAccess(503, "设备登记与动作连接不匹配。")
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        return controller

    async def motion_call(pet_id: str, request: Request, current: Session, operation: str):
        controller = await motion_access(pet_id, request, current)
        payload = None
        if operation in ("execute", "stop"):
            try:
                model = _ExecuteMotion if operation == "execute" else _StopMotion
                payload = model.model_validate(json.loads(request.state.gateway_body))
            except (ValidationError, ValueError, UnicodeError):
                return _error(400, "动作请求只接受固定动作和请求编号。", code="MOTION_INVALID")

        async def authorize():
            await motion_access(pet_id, request, current)

        try:
            if operation == "read":
                result = await controller.snapshot(user_id=current.user_id, pet_id=pet_id)
            elif operation == "history":
                result = await controller.history(user_id=current.user_id, pet_id=pet_id)
            elif operation == "execute":
                result = await controller.execute(user_id=current.user_id, pet_id=pet_id,
                                                  action_id=payload.actionId, request_id=payload.requestId,
                                                  authorize=authorize)
            else:
                result = await controller.stop(user_id=current.user_id, pet_id=pet_id,
                                               request_id=payload.requestId, execution_id=payload.executionId,
                                               authorize=authorize)
            result = _motion_history_result(result) if operation == "history" else _motion_result(result)
        except (_Unauthorized, _MotionAccess):
            raise
        except MotionError as error:
            return _error(error.status_code, str(error), code=error.code)
        except Exception:
            return _error(503, "动作服务暂不可用，请检查连接后重试。", code="MOTION_UNAVAILABLE")
        await motion_access(pet_id, request, current)
        return {"success": True, "data": result}

    @app.get("/v1/pets/{pet_id}/motion")
    async def get_motion(pet_id: str, request: Request, current: Session = Depends(session)):
        return await motion_call(pet_id, request, current, "read")

    @app.get("/v1/pets/{pet_id}/motion/history")
    async def get_motion_history(pet_id: str, request: Request, current: Session = Depends(session)):
        return await motion_call(pet_id, request, current, "history")

    @app.post("/v1/pets/{pet_id}/motion/execute")
    async def execute_motion(pet_id: str, request: Request, current: Session = Depends(session)):
        return await motion_call(pet_id, request, current, "execute")

    @app.post("/v1/pets/{pet_id}/motion/stop")
    async def stop_motion(pet_id: str, request: Request, current: Session = Depends(session)):
        return await motion_call(pet_id, request, current, "stop")

    async def product_access(pet_id: str, request: Request, current: Session):
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        try:
            allowed = await asyncio.to_thread(app.state.store.authorize, current.user_id, pet_id)
        except Exception:
            raise _MotionAccess(503, "资料授权暂不可用。") from None
        if not allowed:
            raise _MotionAccess(404, "未找到设备。")
        service = app.state.product_service
        serial = getattr(service, "expected_serial", None)
        if (service is None or not serial or pet_id != config.status_pet_id or
                serial != getattr(app.state.status_reader, "expected_serial", None)):
            raise _MotionAccess(503, "该设备尚未配置真实资料连接。")
        try:
            matches = await asyncio.to_thread(app.state.store.registered_device_matches, pet_id, serial)
        except Exception:
            matches = False
        if not matches:
            raise _MotionAccess(503, "设备登记与资料连接不匹配。")
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        return service

    async def product_call(pet_id: str, request: Request, current: Session, operation: str):
        service = await product_access(pet_id, request, current)
        payload = None
        if operation == "interactions":
            try:
                payload = InteractionRequest.model_validate(json.loads(request.state.gateway_body, object_pairs_hook=_unique_json_object))
            except (ValidationError, ValueError, UnicodeError):
                return _error(400, "培养互动只接受固定事件和请求编号。", code="INTERACTION_INVALID")

        async def authorize():
            await product_access(pet_id, request, current)

        try:
            if operation == "interactions":
                result = await service.interact(user_id=current.user_id, pet_id=pet_id,
                                               event_type=payload.eventType, request_id=payload.requestId,
                                               authorize=authorize)
                model = Receipt
            else:
                result = await service.read(operation)
                model = {"dashboard": Dashboard, "intimacy": IntimacyView, "growth": Growth}[operation]
            result = validate_public(model, result)
        except (_Unauthorized, _MotionAccess):
            raise
        except ProductError as error:
            return _error(error.status_code, str(error), code=error.code)
        except Exception:
            return _error(503, "真实资料暂不可用，请检查授权和连接。", code="PRODUCT_UNAVAILABLE")
        await product_access(pet_id, request, current)
        return {"success": True, "data": result}

    @app.get("/v1/pets/{pet_id}/dashboard")
    async def get_product_dashboard(pet_id: str, request: Request, current: Session = Depends(session)):
        return await product_call(pet_id, request, current, "dashboard")

    @app.get("/v1/pets/{pet_id}/intimacy")
    async def get_product_intimacy(pet_id: str, request: Request, current: Session = Depends(session)):
        return await product_call(pet_id, request, current, "intimacy")

    @app.get("/v1/pets/{pet_id}/growth")
    async def get_product_growth(pet_id: str, request: Request, current: Session = Depends(session)):
        return await product_call(pet_id, request, current, "growth")

    @app.post("/v1/pets/{pet_id}/interactions")
    async def post_product_interaction(pet_id: str, request: Request, current: Session = Depends(session)):
        return await product_call(pet_id, request, current, "interactions")

    async def dialog_access(pet_id: str, request: Request, current: Session):
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        try:
            allowed = await asyncio.to_thread(app.state.store.authorize, current.user_id, pet_id)
        except Exception:
            raise _MotionAccess(503, "对话授权暂不可用。") from None
        if not allowed:
            raise _MotionAccess(404, "未找到设备。")
        reader = app.state.dialog_reader
        serial = getattr(reader, "expected_serial", None)
        if (reader is None or not serial or pet_id != config.status_pet_id or
                serial != getattr(app.state.status_reader, "expected_serial", None)):
            raise _MotionAccess(503, "该设备尚未配置只读对话接口。")
        try:
            matched = await asyncio.to_thread(app.state.store.registered_device_matches, pet_id, serial)
        except Exception:
            matched = False
        if not matched:
            raise _MotionAccess(503, "设备登记与对话连接不匹配。")
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        return reader

    async def dialog_call(pet_id, request, current, operation, conversation_id=None, revision=None):
        reader = await dialog_access(pet_id, request, current)
        if ((conversation_id is not None and re.fullmatch(r"conv_[0-9a-f]{48}", conversation_id) is None) or
                (revision is not None and re.fullmatch(r"[0-9a-f]{64}", revision) is None)):
            return _error(400, "只读对话编号无效。", code="DIALOG_READ_INVALID")
        try:
            result = await reader.read(user_id=current.user_id, pet_id=pet_id, operation=operation,
                                       conversation_id=conversation_id, revision=revision)
            model = {"status": DialogStatus, "list": DialogList, "detail": DialogDetail}[operation]
            result = validate_dialog(model, result)
        except DialogReadError as error:
            return _error(error.status_code, str(error), code=error.code)
        except Exception:
            return _error(503, "只读对话暂不可用，请刷新授权和连接。", code="DIALOG_READ_UNAVAILABLE")
        await dialog_access(pet_id, request, current)
        return {"success": True, "data": result}

    @app.get("/v1/pets/{pet_id}/dialog")
    async def get_live_dialog(pet_id: str, request: Request, current: Session = Depends(session)):
        return await dialog_call(pet_id, request, current, "status")

    @app.get("/v1/pets/{pet_id}/dialog/conversations")
    async def get_dialog_list(pet_id: str, request: Request, current: Session = Depends(session)):
        return await dialog_call(pet_id, request, current, "list")

    @app.get("/v1/pets/{pet_id}/dialog/conversations/since/{revision}")
    async def get_changed_dialog_list(pet_id: str, revision: str, request: Request, current: Session = Depends(session)):
        return await dialog_call(pet_id, request, current, "list", revision=revision)

    @app.get("/v1/pets/{pet_id}/dialog/conversations/{conversation_id}")
    async def get_dialog_detail(pet_id: str, conversation_id: str, request: Request, current: Session = Depends(session)):
        return await dialog_call(pet_id, request, current, "detail", conversation_id)

    @app.get("/v1/pets/{pet_id}/dialog/conversations/{conversation_id}/since/{revision}")
    async def get_changed_dialog_detail(pet_id: str, conversation_id: str, revision: str, request: Request,
                                        current: Session = Depends(session)):
        return await dialog_call(pet_id, request, current, "detail", conversation_id, revision)

    async def dialog_control_access(pet_id, request, current):
        await dialog_access(pet_id, request, current)
        controller = app.state.dialog_controller
        if (controller is None or getattr(controller, "expected_serial", None) !=
                getattr(app.state.dialog_reader, "expected_serial", None)):
            raise _MotionAccess(503, "该设备尚未配置对话控制。")
        return controller

    async def dialog_control_call(pet_id, request, current, operation, request_id=None):
        controller = await dialog_control_access(pet_id, request, current)
        payload = None
        if operation == "submit":
            try:
                payload = DialogCommand.model_validate(json.loads(request.state.gateway_body, object_pairs_hook=_unique_json_object))
            except (ValidationError, ValueError, UnicodeError):
                return _error(400, "对话命令格式无效。", code="DIALOG_COMMAND_INVALID")
        if request_id is not None and re.fullmatch(_REQUEST_ID, request_id) is None:
            return _error(400, "对话请求编号无效。", code="DIALOG_COMMAND_INVALID")

        async def authorize():
            await dialog_control_access(pet_id, request, current)

        try:
            if operation == "snapshot":
                result = await controller.snapshot(user_id=current.user_id, pet_id=pet_id)
                model = DialogControl
            elif operation == "receipt":
                result = await controller.receipt(user_id=current.user_id, pet_id=pet_id, request_id=request_id)
                model = DialogReceipt
            else:
                result = await controller.submit(user_id=current.user_id, pet_id=pet_id,
                    request_id=payload.requestId, kind=payload.kind, content=payload.content, authorize=authorize)
                model = DialogReceipt
            result = validate_control(model, result)
        except (_Unauthorized, _MotionAccess):
            raise
        except DialogControlError as error:
            return _error(error.status_code, str(error), code=error.code)
        except Exception:
            return _error(503, "对话命令暂不可用，请查询原请求回执。", code="DIALOG_CONTROL_UNAVAILABLE")
        await dialog_control_access(pet_id, request, current)
        return {"success": True, "data": result}

    @app.get("/v1/pets/{pet_id}/dialog/control")
    async def get_dialog_control(pet_id: str, request: Request, current: Session = Depends(session)):
        return await dialog_control_call(pet_id, request, current, "snapshot")

    @app.post("/v1/pets/{pet_id}/dialog/commands")
    async def submit_dialog_command(pet_id: str, request: Request, current: Session = Depends(session)):
        return await dialog_control_call(pet_id, request, current, "submit")

    @app.get("/v1/pets/{pet_id}/dialog/requests/{request_id}")
    async def get_dialog_receipt(pet_id: str, request_id: str, request: Request, current: Session = Depends(session)):
        return await dialog_control_call(pet_id, request, current, "receipt", request_id)

    async def preferences_access(pet_id, request, current):
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        try:
            allowed = await asyncio.to_thread(app.state.store.authorize, current.user_id, pet_id)
        except Exception:
            raise _MotionAccess(503, "设置授权暂不可用。") from None
        if not allowed:
            raise _MotionAccess(404, "未找到设备。")
        service = app.state.preferences_service
        serial = getattr(service, "expected_serial", None)
        if (service is None or not serial or pet_id != config.status_pet_id or
                serial != getattr(app.state.status_reader, "expected_serial", None)):
            raise _MotionAccess(503, "该设备尚未配置设置与反馈连接。")
        try:
            matched = await asyncio.to_thread(app.state.store.registered_device_matches, pet_id, serial)
        except Exception:
            matched = False
        if not matched:
            raise _MotionAccess(503, "设备登记与设置连接不匹配。")
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()
        return service

    async def preferences_call(pet_id, request, current, operation, request_id=None):
        service = await preferences_access(pet_id, request, current)
        payload = None
        if operation == "submit":
            try:
                payload = PreferencesCommand.model_validate(json.loads(request.state.gateway_body, object_pairs_hook=_unique_json_object))
            except (ValidationError, ValueError, UnicodeError):
                return _error(400, "普通设置或反馈格式无效。", code="PREFERENCES_INVALID")
        if request_id is not None and re.fullmatch(_REQUEST_ID, request_id) is None:
            return _error(400, "设置请求编号无效。", code="PREFERENCES_INVALID")

        async def authorize():
            await preferences_access(pet_id, request, current)

        try:
            if operation == "snapshot":
                result = await service.snapshot(user_id=current.user_id, pet_id=pet_id)
                model = PreferencesSnapshot
            elif operation == "receipt":
                result = await service.receipt(user_id=current.user_id, pet_id=pet_id, request_id=request_id)
                model = PreferencesReceipt
            else:
                result = await service.submit(user_id=current.user_id, pet_id=pet_id, request_id=payload.requestId,
                    kind=payload.kind, changes=payload.changes.model_dump(exclude_unset=True) if payload.changes else None,
                    category=payload.category, content=payload.content, authorize=authorize)
                model = PreferencesReceipt
            result = validate_preferences(model, result)
        except (_Unauthorized, _MotionAccess):
            raise
        except PreferencesError as error:
            return _error(error.status_code, str(error), code=error.code)
        except Exception:
            return _error(503, "设置或反馈结果暂不可用，请查询原请求回执。", code="PREFERENCES_UNAVAILABLE")
        await preferences_access(pet_id, request, current)
        return {"success": True, "data": result}

    @app.get("/v1/pets/{pet_id}/preferences")
    async def get_preferences(pet_id: str, request: Request, current: Session = Depends(session)):
        return await preferences_call(pet_id, request, current, "snapshot")

    @app.post("/v1/pets/{pet_id}/preferences/commands")
    async def submit_preferences(pet_id: str, request: Request, current: Session = Depends(session)):
        return await preferences_call(pet_id, request, current, "submit")

    @app.get("/v1/pets/{pet_id}/preferences/requests/{request_id}")
    async def get_preferences_receipt(pet_id: str, request_id: str, request: Request, current: Session = Depends(session)):
        return await preferences_call(pet_id, request, current, "receipt", request_id)

    async def lifecycle_authenticate(request, current):
        latest = await session(request)
        if latest.user_id != current.user_id:
            raise _Unauthorized()

    async def lifecycle_access(pet_id, request, current, *, receipt=False):
        await lifecycle_authenticate(request, current)
        if re.fullmatch(_OPAQUE_ID, pet_id) is None:
            raise _MotionAccess(404, "未找到设备。")
        if not receipt:
            try:
                allowed = await asyncio.to_thread(app.state.store.authorize, current.user_id, pet_id)
            except Exception:
                raise _MotionAccess(503, "数据操作授权暂不可用。") from None
            if not allowed:
                raise _MotionAccess(404, "未找到设备。")
        service = app.state.lifecycle_service
        serial = getattr(service, "expected_serial", None)
        if (service is None or not serial or pet_id != config.status_pet_id or
                serial != getattr(app.state.status_reader, "expected_serial", None)):
            raise _MotionAccess(503, "该设备尚未配置数据操作安全检查。")
        try:
            matched = await asyncio.to_thread(app.state.store.registered_device_matches, pet_id, serial)
        except Exception:
            matched = False
        if not matched:
            raise _MotionAccess(503, "设备登记与数据操作连接不匹配。")
        await lifecycle_authenticate(request, current)
        return service

    async def lifecycle_call(pet_id, request, current, operation, request_id=None):
        service = await lifecycle_access(pet_id, request, current, receipt=operation == "receipt")
        payload = None
        if operation in {"challenge", "submit"}:
            try:
                model = LifecycleScope if operation == "challenge" else LifecycleCommand
                payload = model.model_validate(json.loads(request.state.gateway_body, object_pairs_hook=_unique_json_object))
            except (ValidationError, ValueError, UnicodeError):
                return _error(400, "数据操作必须使用固定范围、请求编号和独立确认。", code="LIFECYCLE_INVALID")
        if request_id is not None and re.fullmatch(_REQUEST_ID, request_id) is None:
            return _error(400, "数据操作请求编号无效。", code="LIFECYCLE_INVALID")

        async def authenticate():
            await lifecycle_authenticate(request, current)

        async def authorize():
            await lifecycle_access(pet_id, request, current)

        try:
            values = dict(user_id=current.user_id, pet_id=pet_id, authenticate=authenticate)
            if operation == "snapshot":
                result = await service.snapshot(**values, authorize=authorize)
                model = LifecycleSnapshot
            elif operation == "challenge":
                result = await service.challenge(**values, authorize=authorize, kind=payload.kind, scope=payload.scope)
                model = LifecycleChallenge
            elif operation == "submit":
                result = await service.submit(**values, authorize=authorize, command=payload.model_dump())
                model = LifecycleReceipt
            else:
                result = await service.receipt(**values, request_id=request_id)
                model = LifecycleReceipt
            result = validate_lifecycle(model, result)
        except (_Unauthorized, _MotionAccess):
            raise
        except LifecycleError as error:
            return _error(error.status_code, str(error), code=error.code)
        except Exception:
            return _error(503, "数据操作结果尚未确认，请保留原编号核对。", code="LIFECYCLE_UNAVAILABLE")
        await lifecycle_authenticate(request, current)
        # Completion can intentionally revoke pet ownership. Only read-only
        # snapshots/challenges require owner revalidation after the result.
        if operation in {"snapshot", "challenge"}:
            await lifecycle_access(pet_id, request, current)
        return {"success": True, "data": result}

    @app.get("/v1/pets/{pet_id}/lifecycle")
    async def get_lifecycle(pet_id: str, request: Request, current: Session = Depends(session)):
        return await lifecycle_call(pet_id, request, current, "snapshot")

    @app.post("/v1/pets/{pet_id}/lifecycle/challenges")
    async def create_lifecycle_challenge(pet_id: str, request: Request, current: Session = Depends(session)):
        return await lifecycle_call(pet_id, request, current, "challenge")

    @app.post("/v1/pets/{pet_id}/lifecycle/commands")
    async def submit_lifecycle_command(pet_id: str, request: Request, current: Session = Depends(session)):
        return await lifecycle_call(pet_id, request, current, "submit")

    @app.get("/v1/pets/{pet_id}/lifecycle/requests/{request_id}")
    async def get_lifecycle_receipt(pet_id: str, request_id: str, request: Request, current: Session = Depends(session)):
        return await lifecycle_call(pet_id, request, current, "receipt", request_id)

    return app
