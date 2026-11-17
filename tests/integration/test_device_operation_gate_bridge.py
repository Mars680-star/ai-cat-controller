"""Actual ASGI/services/SQLite gate bridge races; temporary Mock device only.

The coroutine and thread barriers are explicit, so a cancelled HTTP caller or
an old resolved identity cannot accidentally make this a happy-path-only test.
No native process, network, credentials or real hardware is used.
"""

import asyncio
from contextlib import asynccontextmanager
from threading import Event as ThreadEvent

import httpx
import pytest
from fastapi import Request

from ai_cat_controller.api.product import mock_user_id
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.main import create_app


@asynccontextmanager
async def stack(tmp_path):
    settings = Settings(
        hardware_driver="mock", enable_growth_personality_v1=True,
        enable_product_data_reset=True, motion_cooldown_seconds=0.0,
        service_status_cache_seconds=0.0, data_path=tmp_path / "product.db",
        personality_runtime_path=tmp_path / "personality-runtime.json",
        dialog_event_path=tmp_path / "dialog-events.jsonl",
        dialog_config_path=tmp_path / "dialog-config.json",
        dialog_text_request_path=tmp_path / "dialog-request.json",
        device_serial_path=tmp_path / "serial.txt",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock.test") as client:
            login = await client.post("/api/v1/auth/mock-login", json={"login_code": "operation-gate-owner", "nickname": "合成用户"})
            assert login.status_code == 200
            headers = {"X-Mock-Session": login.json()["data"]["session_token"]}
            user = app.state.services.product.resolve_session(headers["X-Mock-Session"])
            bound = await client.post("/api/v1/pets/bind", headers=headers,
                json={"device_serial": "K1-OPERATION-GATE-SYNTHETIC", "pet_name": "小安", "network_name": "Synthetic"})
            assert bound.status_code == 200
            yield app, client, headers, user, bound.json()["data"]["pet"]["pet_id"]


def observe_closing(monkeypatch, gate):
    closing = asyncio.Event()
    original = gate.maintenance

    async def maintenance(operation, *, quiesce=None):
        async def observed():
            assert gate.closing is True
            closing.set()
            if quiesce is not None:
                await quiesce()
        return await original(operation, quiesce=observed)

    monkeypatch.setattr(gate, "maintenance", maintenance)
    return closing


async def reset(client, headers):
    return await client.post("/api/v1/admin/reset-product-data", headers=headers,
        json={"confirmation": "RESET_PRODUCT_DATA"})


@pytest.mark.asyncio
async def test_main_shares_one_gate_across_product_and_all_device_services(tmp_path):
    async with stack(tmp_path) as (app, _, _, _, _):
        services = app.state.services
        gate = services.maintenance_gate
        assert gate.active_operations == 0 and gate.closing is False
        for service in (services.product, services.motion, services.dialog, services.device, services.mijia):
            assert service._maintenance_gate is gate


@pytest.mark.asyncio
async def test_cross_loop_asgi_is_rejected_and_owner_portal_transport_preserves_real_gate(client, product_app_transport):
    gate = client.app.state.services.maintenance_gate
    assert gate._loop is not asyncio.get_running_loop()
    request = httpx.Request("PATCH", "http://mock.test/api/v1/dialog/config", json={"follow_up_seconds": 41})
    direct = httpx.ASGITransport(app=client.app)
    response = await direct.handle_async_request(request)
    assert response.status_code == 409
    assert (await response.aread()).find(b"action_conflict") >= 0
    await response.aclose()
    assert not client.app.state.services.settings.dialog_config_path.exists()

    response = await product_app_transport.handle_async_request(request)
    assert response.status_code == 200
    assert response.json()["data"]["follow_up_seconds"] == 41
    assert product_app_transport.owner_loop_checks == 1
    assert gate.active_operations == 0


@pytest.mark.asyncio
async def test_exclusive_gate_rejects_new_http_writes_but_stop_interrupt_remain_available(tmp_path):
    async with stack(tmp_path) as (app, client, headers, _, pet):
        gate = app.state.services.maintenance_gate
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold():
            entered.set()
            await release.wait()

        task = asyncio.create_task(gate.maintenance(hold()))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            routes = [
                ("PATCH", f"/api/v1/pets/{pet}/settings", {"name": "维护期间禁止写"}),
                ("POST", f"/api/v1/pets/{pet}/feedback", {"content": "维护期间的反馈"}),
                ("POST", f"/api/v1/pets/{pet}/dialogs", {"content": "维护期间不能问", "trigger_action": False}),
                ("POST", "/api/v1/dialog/wake", {"request_id": "gate-wake"}),
                ("POST", "/api/v1/dialog/text", {"request_id": "gate-text", "content": "不能发出"}),
                ("PATCH", "/api/v1/dialog/config", {"follow_up_seconds": 60}),
                ("POST", "/api/v1/motion/head/nod", {"request_id": "gate-motion"}),
                ("POST", f"/api/v1/integrations/mijia/pets/{pet}/prepare", {"request_id": "gate-mijia"}),
            ]
            for method, url, body in routes:
                response = await client.request(method, url, json=body, headers=headers)
                assert response.status_code == 409, (url, response.status_code, response.text)
                assert response.json()["data"]["code"] == "action_conflict"
            stop = await client.post("/api/v1/motion/stop")
            interrupt = await client.post("/api/v1/dialog/interrupt", json={"request_id": "safety-interrupt"})
            assert stop.status_code == interrupt.status_code == 200
            assert not app.state.services.settings.dialog_config_path.exists()
            assert app.state.services.adapter.action_count == 0
            assert app.state.services.adapter.output_volume_percent == 60
        finally:
            release.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_volume_await_and_later_sqlite_write_settle_before_reset(tmp_path, monkeypatch):
    async with stack(tmp_path) as (app, client, headers, _, pet):
        services = app.state.services
        started, release = asyncio.Event(), asyncio.Event()
        order = []
        original_volume = services.adapter.set_output_volume
        original_update = services.product._repository.update_pet_settings
        original_clear = services.product._repository.clear_product_data

        async def volume(value):
            started.set()
            await release.wait()
            await original_volume(value)

        def update(*args, **kwargs):
            order.append("settings-sqlite")
            return original_update(*args, **kwargs)

        def clear():
            order.append("reset-sqlite")
            return original_clear()

        monkeypatch.setattr(services.adapter, "set_output_volume", volume)
        monkeypatch.setattr(services.product._repository, "update_pet_settings", update)
        monkeypatch.setattr(services.product._repository, "clear_product_data", clear)
        closing = observe_closing(monkeypatch, services.maintenance_gate)
        save = asyncio.create_task(client.patch(f"/api/v1/pets/{pet}/settings", headers=headers, json={"name": "旧工作收尾", "volume": 33}))
        await asyncio.wait_for(started.wait(), 2)
        maintenance = asyncio.create_task(reset(client, headers))
        await asyncio.wait_for(closing.wait(), 2)
        try:
            assert not maintenance.done() and order == []
            denied = await client.patch(f"/api/v1/pets/{pet}/settings", headers=headers, json={"volume": 12})
            assert denied.status_code == 409
        finally:
            release.set()
        assert (await asyncio.wait_for(save, 3)).status_code == 200
        response = await asyncio.wait_for(maintenance, 3)
        assert response.status_code == 200
        assert order == ["settings-sqlite", "reset-sqlite"]
        with services.product._repository._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_cancelled_http_caller_cannot_leave_thread_write_after_reset(tmp_path, monkeypatch):
    async with stack(tmp_path) as (app, client, headers, _, pet):
        services = app.state.services
        loop = asyncio.get_running_loop()
        thread_started, release = asyncio.Event(), ThreadEvent()
        original = services.product._repository.update_pet_settings
        order = []

        def delayed(*args, **kwargs):
            loop.call_soon_threadsafe(thread_started.set)
            assert release.wait(5), "test must release the synthetic SQLite worker"
            result = original(*args, **kwargs)
            order.append("thread-finished")
            return result

        monkeypatch.setattr(services.product._repository, "update_pet_settings", delayed)
        closing = observe_closing(monkeypatch, services.maintenance_gate)
        save = asyncio.create_task(client.patch(f"/api/v1/pets/{pet}/settings", headers=headers, json={"name": "延迟线程写入"}))
        await asyncio.wait_for(thread_started.wait(), 2)
        save.cancel()
        with pytest.raises(asyncio.CancelledError):
            await save
        maintenance = asyncio.create_task(reset(client, headers))
        await asyncio.wait_for(closing.wait(), 2)
        try:
            assert not maintenance.done() and order == []
        finally:
            release.set()
        response = await asyncio.wait_for(maintenance, 3)
        assert response.status_code == 200 and order == ["thread-finished"]
        with services.product._repository._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 0
        assert services.maintenance_gate.active_operations == 0


@pytest.mark.asyncio
async def test_http_ingress_epoch_rejects_old_resolved_auth_after_maintenance(tmp_path):
    async with stack(tmp_path) as (app, client, headers, user, pet):
        authorized, release = asyncio.Event(), asyncio.Event()
        held = False

        async def delayed_auth(request: Request):
            nonlocal held
            current = app.state.services.product.resolve_session(request.headers.get("X-Mock-Session"))
            if request.url.path.endswith("/feedback") and not held:
                held = True
                authorized.set()
                await release.wait()
            return current

        app.dependency_overrides[mock_user_id] = delayed_auth
        old = asyncio.create_task(client.post(f"/api/v1/pets/{pet}/feedback", headers=headers, json={"content": "旧授权请求不能复活"}))
        await asyncio.wait_for(authorized.wait(), 2)
        try:
            cleared = await app.state.services.product.clear_dialog_memory(user_id=user, pet_id=pet, request_id="gate-epoch-clear")
            assert cleared["cleared"] is True
        finally:
            release.set()
        response = await asyncio.wait_for(old, 2)
        assert response.status_code == 409
        fresh = await client.post(f"/api/v1/pets/{pet}/feedback", headers=headers, json={"content": "新请求可以明确提交"})
        assert fresh.status_code == 200
        with app.state.services.product._repository._connect() as connection:
            rows = connection.execute("SELECT content FROM feedback").fetchall()
            assert [row[0] for row in rows] == ["新请求可以明确提交"]


@pytest.mark.asyncio
async def test_inherited_unreserved_child_cannot_reuse_expired_request_after_clear(tmp_path):
    async with stack(tmp_path) as (app, _, _, user, pet):
        gate = app.state.services.maintenance_gate
        release = asyncio.Event()

        async def delayed_child():
            await release.wait()
            return await app.state.services.dialog.update_config(77)

        async def parent():
            return asyncio.create_task(delayed_child())

        child = await gate.operation(parent())
        await app.state.services.product.clear_dialog_memory(user_id=user, pet_id=pet, request_id="expire-inherited-child")
        release.set()
        with pytest.raises(ActionConflictError):
            await asyncio.wait_for(child, 2)
        assert not app.state.services.settings.dialog_config_path.exists()


@pytest.mark.asyncio
async def test_reserved_background_child_finishes_service_write_before_exclusive_body(tmp_path):
    async with stack(tmp_path) as (app, _, _, user, pet):
        services = app.state.services
        gate = services.maintenance_gate
        release, quiesced, exclusive = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def child_work():
            await release.wait()
            return await services.product.feedback(user_id=user, pet_id=pet, category="other", content="预留子任务必须收尾")

        async def parent():
            return gate.spawn(child_work(), name="synthetic-reserved-child")

        child = await gate.operation(parent())

        async def quiesce():
            assert gate.closing
            quiesced.set()

        async def body():
            exclusive.set()

        maintenance = asyncio.create_task(gate.maintenance(body(), quiesce=quiesce))
        await asyncio.wait_for(quiesced.wait(), 2)
        try:
            assert not exclusive.is_set()
        finally:
            release.set()
        await asyncio.wait_for(child, 2)
        await asyncio.wait_for(maintenance, 2)
        assert exclusive.is_set() and gate.active_operations == 0
        with services.product._repository._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_operation_cannot_upgrade_itself_to_maintenance_and_deadlock(tmp_path):
    async with stack(tmp_path) as (app, _, _, _, _):
        gate = app.state.services.maintenance_gate
        ran = False

        async def destructive():
            nonlocal ran
            ran = True

        async def normal():
            return await gate.maintenance(destructive())

        with pytest.raises(ActionConflictError):
            await asyncio.wait_for(gate.operation(normal()), 2)
        assert ran is False and gate.closing is False and gate.active_operations == 0


@pytest.mark.asyncio
async def test_product_action_finalizer_thread_settles_before_reset(tmp_path, monkeypatch):
    async with stack(tmp_path) as (app, client, headers, _, pet):
        services = app.state.services
        loop = asyncio.get_running_loop()
        finalizer_started, release = asyncio.Event(), ThreadEvent()
        original = services.product._repository.update_action_execution
        writes = []

        async def inert_motion(*args, **kwargs):
            return None

        def delayed(execution_id, **kwargs):
            if kwargs["status"] != "running":
                loop.call_soon_threadsafe(finalizer_started.set)
                assert release.wait(5), "release the synthetic finalizer worker"
                writes.append("finalized")
            return original(execution_id, **kwargs)

        monkeypatch.setattr(services.adapter, "nod_head", inert_motion)
        monkeypatch.setattr(services.product._repository, "update_action_execution", delayed)
        response = await client.post(f"/api/v1/pets/{pet}/actions/head_nod/execute", headers=headers, json={"request_id": "gate-finalizer"})
        assert response.status_code == 202, response.text
        await asyncio.wait_for(finalizer_started.wait(), 2)
        closing = observe_closing(monkeypatch, services.maintenance_gate)
        maintenance = asyncio.create_task(reset(client, headers))
        await asyncio.wait_for(closing.wait(), 2)
        try:
            assert not maintenance.done() and writes == []
        finally:
            release.set()
        result = await asyncio.wait_for(maintenance, 3)
        assert result.status_code == 200 and writes == ["finalized"]
        with services.product._repository._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM action_executions").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_cancelled_optional_celebration_cannot_release_its_live_sqlite_thread(tmp_path, monkeypatch):
    async with stack(tmp_path) as (app, _, _, user, pet):
        services = app.state.services
        loop = asyncio.get_running_loop()
        thread_started, thread_finished, quiesce_started, exclusive = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
        release = ThreadEvent()
        original = services.product._repository.create_action_execution
        order = []

        def held_intent(*args, **kwargs):
            loop.call_soon_threadsafe(thread_started.set)
            assert release.wait(5), "release the synthetic celebration SQLite worker"
            try:
                result = original(*args, **kwargs)
                order.append("sqlite-thread-finished")
                return result
            finally:
                loop.call_soon_threadsafe(thread_finished.set)

        monkeypatch.setattr(services.product._repository, "create_action_execution", held_intent)
        assert services.product._schedule_level_up_celebration(
            user_id=user, pet_id=pet, interaction_event_id="synthetic-optional-event", to_level=2,
        )
        await asyncio.wait_for(thread_started.wait(), 2)

        async def quiesce():
            quiesce_started.set()
            await services.product._quiesce_reset()

        async def erase():
            order.append("exclusive")
            exclusive.set()

        maintenance = asyncio.create_task(services.maintenance_gate.maintenance(erase(), quiesce=quiesce))
        await asyncio.wait_for(quiesce_started.wait(), 2)
        try:
            # Drain runnable cancellation/finalizer callbacks without a wall-clock
            # delay. The SQLite worker remains deliberately held by ThreadEvent.
            for _ in range(25):
                turn = loop.create_future()
                loop.call_soon(turn.set_result, None)
                await turn
            assert not exclusive.is_set(), "a cancelled coroutine is not proof its SQLite worker stopped"
            assert services.maintenance_gate.active_operations > 0
        finally:
            release.set()
            await asyncio.wait_for(thread_finished.wait(), 3)
            await asyncio.wait_for(maintenance, 3)
        assert order == ["sqlite-thread-finished", "exclusive"]


@pytest.mark.asyncio
async def test_http_drain_timeout_returns_conflict_but_keeps_gate_closed_until_real_cleanup(tmp_path, monkeypatch):
    async with stack(tmp_path) as (app, client, headers, _, pet):
        services = app.state.services
        gate = services.maintenance_gate
        monkeypatch.setattr(gate, "_timeout", .02)  # Test-only budget; no product configuration change.
        loop = asyncio.get_running_loop()
        started, finished = asyncio.Event(), asyncio.Event()
        release = ThreadEvent()
        original = services.product._repository.update_action_execution

        async def inert_motion(*args, **kwargs):
            return None

        def held_finalizer(execution_id, **kwargs):
            if kwargs["status"] != "running":
                loop.call_soon_threadsafe(started.set)
                assert release.wait(5), "release the synthetic outstanding finalizer"
                try:
                    return original(execution_id, **kwargs)
                finally:
                    loop.call_soon_threadsafe(finished.set)
            return original(execution_id, **kwargs)

        monkeypatch.setattr(services.adapter, "nod_head", inert_motion)
        monkeypatch.setattr(services.product._repository, "update_action_execution", held_finalizer)
        accepted = await client.post(f"/api/v1/pets/{pet}/actions/head_nod/execute", headers=headers,
            json={"request_id": "gate-timeout-finalizer"})
        assert accepted.status_code == 202
        await asyncio.wait_for(started.wait(), 2)
        request = asyncio.create_task(reset(client, headers))
        try:
            done, _ = await asyncio.wait([request], timeout=.3)
            assert request in done, "drain timeout must not wait for cancellation-resistant cleanup"
            response = request.result()
            assert response.status_code == 409 and not finished.is_set()
            assert gate.closing and gate.active_operations > 0
            denied = await client.patch(f"/api/v1/pets/{pet}/settings", headers=headers, json={"name": "清理未结束禁止写"})
            assert denied.status_code == 409
            with services.product._repository._connect() as connection:
                assert connection.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 1
                assert connection.execute("SELECT COUNT(*) FROM action_executions").fetchone()[0] == 1
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 3)
            await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), 3)
        async with asyncio.timeout(2):
            while gate.closing:
                turn = loop.create_future()
                loop.call_soon(turn.set_result, None)
                await turn
        assert gate.active_operations == 0
        with services.product._repository._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 1
