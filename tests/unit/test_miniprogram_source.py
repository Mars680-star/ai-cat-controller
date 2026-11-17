"""Native product entries, guarded live features and isolated diagnostic pages."""

from __future__ import annotations

import json
from pathlib import Path
import re
import struct
from typing import get_args

from ai_cat_controller.mini_gateway.dialog_models import State


ROOT = Path(__file__).resolve().parents[2]
MINIPROGRAM = ROOT / "miniprogram"
EXPECTED_PAGES = {
    "auth", "device", "blind-box", "home", "motion", "growth",
    "growth-personality", "dialog", "conversation", "mijia", "profile", "device-debug", "gateway-setup", "provision",
    "local-pair", "local-network",
}
NAV_IDS = (
    "connection", "home", "motion", "growth", "personality", "history",
    "mijia", "settings",
)


def source_files(suffix: str):
    # Do not descend into node_modules, generated files or personal settings.
    for folder in ("pages", "components", "services", "mocks", "models", "utils", "store"):
        yield from (MINIPROGRAM / folder).rglob(f"*{suffix}")


def test_native_miniprogram_declares_current_pages_and_eight_product_entries() -> None:
    config = json.loads((MINIPROGRAM / "app.json").read_text())
    assert {page.split("/")[1] for page in config["pages"]} == EXPECTED_PAGES
    # The shared 2x4 navigation keeps all eight web entries reachable.
    assert "tabBar" not in config
    nav = (MINIPROGRAM / "components/product-nav/index.ts").read_text()
    assert tuple(re.findall(r"id: '([^']+)'", nav)) == NAV_IDS
    for label in ("连接", "主页", "运动", "培养", "性格", "对话", "米家", "设置"):
        assert f"label: '{label}'" in nav


def test_all_app_json_files_are_valid_without_scanning_dependencies() -> None:
    files = list(source_files(".json"))
    files += [MINIPROGRAM / name for name in ("app.json", "project.config.json", "sitemap.json", "tsconfig.json", "package.json")]
    for path in files:
        json.loads(path.read_text())


def test_every_registered_page_has_native_source_and_configuration() -> None:
    for name in EXPECTED_PAGES:
        page = MINIPROGRAM / "pages" / name / "index"
        for suffix in (".ts", ".json", ".wxml", ".wxss"):
            assert page.with_suffix(suffix).is_file(), f"missing {page}{suffix}"


def test_pages_use_replaceable_service_and_never_import_mock_data_directly() -> None:
    for path in (MINIPROGRAM / "pages").glob("*/index.ts"):
        text = path.read_text()
        assert "mocks/" not in text
        assert "mock-pet-service" not in text
    service = (MINIPROGRAM / "services/pet-service.ts").read_text()
    assert "new MockPetService()" in service


def test_ui_preview_never_calls_network_login_recording_or_device_apis() -> None:
    forbidden = re.compile(
        r"\b(?:wx|wxApi)\.(?:connectSocket|uploadFile|downloadFile|"
        r"getRecorderManager|startRecord|openBluetoothAdapter|createBLEConnection)\s*\("
    )
    request = re.compile(r"\b(?:wx|wxApi)\.request\s*\(")
    login = re.compile(r"\b(?:wx|wxApi)\.login\s*\(")
    debug_client = MINIPROGRAM / "services/dev-gateway-client.ts"
    cloud_client = MINIPROGRAM / "services/cloud-auth-client.ts"
    local_client = MINIPROGRAM / "services/local-device-client.ts"
    for path in [MINIPROGRAM / "app.ts", *source_files(".ts")]:
        text = path.read_text()
        assert not forbidden.search(text), path
        if login.search(text):
            assert path == cloud_client, path
            assert len(login.findall(text)) == 1, path
        if request.search(text):
            assert path in {debug_client, cloud_client, local_client}, path
            assert len(request.findall(text)) == 1, path


def test_read_only_debug_client_is_fixed_loopback_get_without_persisted_credentials() -> None:
    text = (MINIPROGRAM / "services/dev-gateway-client.ts").read_text()
    endpoint = "http://127.0.0.1:8765/v1/status"
    assert len(re.findall(r"\b(?:wx|wxApi)\.request\s*\(", text)) == 1
    assert set(re.findall(r"""['"](https?://[^'"\s]+)['"]""", text)) == {endpoint}
    # A literal endpoint or its immutable constant is allowed, never user input.
    constants = re.findall(
        rf"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*['\"]{re.escape(endpoint)}['\"]",
        text,
    )
    allowed_urls = {f"'{endpoint}'", f'"{endpoint}"', *constants}
    urls = re.findall(r"\burl\s*:\s*([^,\n}]+)", text)
    assert urls and all(value.strip() in allowed_urls for value in urls)
    methods = re.findall(r"\bmethod\s*:\s*['\"]([^'\"]+)['\"]", text)
    assert methods == ["GET"]
    assert "Authorization" in text
    persist = re.compile(r"\b(?:wx|wxApi)\.(?:setStorageSync|setStorage|setUserCloudStorage|setBackgroundFetchToken)\s*\(")
    for path in (MINIPROGRAM / "services/dev-gateway-client.ts", MINIPROGRAM / "pages/device-debug/index.ts"):
        source = path.read_text()
        assert not persist.search(source), path
        assert not re.search(r"\b(?:localStorage|sessionStorage)\b", source), path
    page = (MINIPROGRAM / "pages/device-debug/index.ts").read_text()
    assert re.search(r"createDevGatewayClient\(\s*wx\s*\)", page)


def test_read_only_debug_entry_is_explicit_without_low_level_hardware_controls() -> None:
    view = (MINIPROGRAM / "pages/device/index.wxml").read_text()
    assert "AI猫只读联调" in view
    assert 'bindtap="openDeviceDebug"' in view
    assert view.index("AI猫只读联调") < view.index('wx:if="{{!bound && mockReady}}"')
    assert '<local-connect ' in view
    assert view.index('<local-connect ') < view.index('wx:if="{{advancedOpen}}"')
    debug = (MINIPROGRAM / "pages/device-debug/index.wxml").read_text()
    assert "只读" in debug
    assert "开发者工具" in debug
    assert not re.search(r"gpio|pwm|i2c|angle_degrees|duration_ms|原始寄存器|脉冲上限", debug, re.I)
    package = json.loads((MINIPROGRAM / "package.json").read_text())
    for name, filename in (
        ("smoke:dev-gateway", "smoke-dev-gateway-client.cjs"),
        ("smoke:device-debug", "smoke-device-debug.cjs"),
        ("smoke:debug-entry", "smoke-debug-entry.cjs"),
    ):
        assert package["scripts"][name] == f"node scripts/{filename}"
        assert f"npm run {name}" in package["scripts"]["test"]


def test_preview_identity_and_current_brand_are_visible() -> None:
    frame = (MINIPROGRAM / "components/app-page/index.wxml").read_text()
    assert "AI 狐狸伴侣" in frame
    assert "界面预览 · 未连接设备" in frame
    auth = (MINIPROGRAM / "pages/auth/index.wxml").read_text()
    assert "AI 狐狸伴侣" in auth
    assert "本地预览" in auth


def test_public_project_config_contains_no_personal_appid_or_credentials() -> None:
    config = json.loads((MINIPROGRAM / "project.config.json").read_text())
    assert config["appid"] == "touristappid"
    assert config["compileType"] == "miniprogram"
    assert "typescript" in config["setting"]["useCompilerPlugins"]
    for path in source_files(".ts"):
        text = path.read_text()
        if path == MINIPROGRAM / "services/development-gateway.ts":
            # One deliberate, non-secret development pin, NOT arbitrary device
            # addresses throughout product code. Runtime environment restrictions
            # are exercised by cloud-auth and shared-session smoke regressions.
            assert re.findall(r"https?://(?:192\.168\.|10\.\d+\.)[0-9.]+(?::[0-9]+)?", text) == [
                "http://192.168.1.110:8788"
            ]
            assert "envVersion !== 'develop'" in text
            assert "device.platform === 'android'" in text
        else:
            assert not re.search(r"https?://(?:192\.168\.|10\.\d+\.)", text), path
        assert not re.search(r"setStorageSync\(\s*['\"][^'\"]*(?:apiKey|productSecret|accessKey)", text, re.I), path


def test_pages_do_not_submit_raw_motor_parameters() -> None:
    for path in (MINIPROGRAM / "pages").glob("*/index.ts"):
        text = path.read_text().lower()
        for name in ("gpio", "angle_degrees", "duration_ms", "service_name"):
            assert name not in text, path


def test_motion_product_page_is_one_guarded_live_layout_without_preview_controls() -> None:
    page = (MINIPROGRAM / "pages/motion/index.ts").read_text()
    view = (MINIPROGRAM / "pages/motion/index.wxml").read_text()
    config = json.loads((MINIPROGRAM / "pages/motion/index.json").read_text())
    for legacy in ("getPetService", "getBoundPet", "getPending", "setPending"):
        assert legacy not in page
    for legacy in ("使用真实设备", "界面预览", 'bindtap="useRealDevice"', 'bindpreview="showPreview"'):
        assert legacy not in view
    assert "live-device-panel" not in config["usingComponents"]
    assert 'hideModeBanner="{{true}}"' in view
    assert 'liveDevice="{{true}}"' in view
    for name in ("动作", "记录", "停止", "未解锁", "缺少授权", "不自动重试未决动作"):
        assert name in view
    action_ids = re.findall(r"actionId: '([^']+)'", page)
    assert set(action_ids) == {
        "head_shake", "head_nod", "tail_wag", "proud_pose",
        "quiet_companion", "greeting_combo", "celebration_combo",
    }
    assert len(action_ids) == 7
    assert "connected: false" not in page
    assert "{{item.reason || item.description}}" in view
    assert "confirm-sheet" not in config["usingComponents"]
    assert "liveConfirmMotion" not in page and "wx.showModal" not in page
    assert "this.realMotion.prepare(actionId)" in page
    assert "this.realMotion.confirm(token)" in page
    assert "轻点即执行一次" in view
    style = (MINIPROGRAM / "pages/motion/index.wxss").read_text()
    assert '.motion-dock { position: fixed;' in style
    assert "repeat(2, minmax(0, 1fr))" in style
    assert "结果待核实，已暂停新动作" in view and "待核对" in page
    assert "其他动作尚未接入" not in view
    assert not re.search(r"\{\{[^}]*\b(?:progress|remainingSeconds)\b[^}]*\}\}", view)


def test_motion_frame_banner_override_is_opt_in_and_does_not_change_other_pages() -> None:
    frame = (MINIPROGRAM / "components/app-page/index.ts").read_text()
    view = (MINIPROGRAM / "components/app-page/index.wxml").read_text()
    assert "hideModeBanner: { type: Boolean, value: false }" in frame
    assert "!localProvisioning && (!hideModeBanner ||" in view
    assert view.count('class="preview-banner"') == 1
    assert 'aria-expanded="{{showConnectionDetails}}"' in view
    assert "本地加密" in view and "HTTP 明文" in view and "电脑须开机" in view
    for name in ("home", "dialog", "profile", "mijia", "growth", "growth-personality"):
        assert "hideModeBanner" not in (MINIPROGRAM / "pages" / name / "index.wxml").read_text()
    connection = (MINIPROGRAM / "pages/device/index.wxml").read_text()
    assert 'hideModeBanner="{{true}}"' in connection
    assert '<local-connect ' in connection and 'previewToolsOpen && !liveMode' in connection
    assert "演示数据" in connection


def test_motion_record_count_does_not_pretend_unread_or_failed_history_is_empty() -> None:
    component = (MINIPROGRAM / "components/record-disclosure/index.ts").read_text()
    template = (MINIPROGRAM / "components/record-disclosure/index.wxml").read_text()
    view = (MINIPROGRAM / "pages/motion/index.wxml").read_text()
    assert "countText: { type: String, value: '' }" in component
    assert "countText || count + ' 条'" in template
    assert 'aria-expanded="{{open}}"' in template
    assert "historyError ? '读取失败' : historyLoading ? '读取中' : history === null ? '未读取' : ''" in view


def test_three_product_pages_register_live_data_without_replacing_the_mock_service() -> None:
    service = (MINIPROGRAM / "services/pet-service.ts").read_text()
    assert "new MockPetService()" in service
    for name in ("home", "growth", "growth-personality"):
        page = (MINIPROGRAM / "pages" / name / "index.ts").read_text()
        template = (MINIPROGRAM / "pages" / name / "index.wxml").read_text()
        config = json.loads((MINIPROGRAM / "pages" / name / "index.json").read_text())
        assert "createLiveProduct" in page
        assert "attachProductPage" in page and "detachProductPage" in page
        assert "live-product-panel" in config["usingComponents"]
        assert "live-product-panel" in template
        assert "getPetService" in page  # Other routes retain explicit local preview.
    package = json.loads((MINIPROGRAM / "package.json").read_text())
    for name in ("live-product", "live-product-pages"):
        assert package["scripts"]["smoke:" + name] == f"node scripts/smoke-{name}.cjs"
        assert "npm run smoke:" + name in package["scripts"]["test"]


def test_real_product_panel_does_not_accept_credentials_or_issue_requests() -> None:
    source = (MINIPROGRAM / "components/live-product-panel/index.ts").read_text()
    template = (MINIPROGRAM / "components/live-product-panel/index.wxml").read_text()
    for forbidden in ("wx.request", "wx.login", "setStorage", "apiKey", "session_token"):
        assert forbidden not in source
        assert forbidden not in template
    assert "triggerEvent" in source
    assert "真实数据 · 互动和保存仍需确认" in source
    assert "不会自动认领" in template
    assert "selectionLocked || !view.canSelect || busy" in template
    assert 'wx:if="{{optionsOpen || !view.selectedPetId}}"' in template
    assert template.index('wx:if="{{view.error}}"') < template.index('optionsOpen || !view.selectedPetId')
    assert "{{caption}}" in template


def test_real_dialog_keeps_readonly_lane_and_separately_guarded_explicit_controls() -> None:
    for name in ("dialog", "conversation"):
        source = (MINIPROGRAM / f"pages/{name}/index.ts").read_text()
        template = (MINIPROGRAM / f"pages/{name}/index.wxml").read_text()
        assert "createLiveDialog" in source
        assert "attachLivePage" in source
        assert "live-product-panel" in template
        assert "只读" in template
        assert "wx.request" not in source
    dialog = (MINIPROGRAM / "pages/dialog/index.ts").read_text()
    assert "this.data.liveMode" in dialog
    view = (MINIPROGRAM / "pages/dialog/index.wxml").read_text()
    for handler in ("startListening", "interrupt", "sendText"):
        assert f'bindtap="{handler}"' in view
    for guard in ("liveMode ? !liveCanListen : controlling", "liveMode ? !liveCanInterrupt : controlling",
                  "liveMode ? !liveCanSendText : !canSend", 'disabled="{{liveMode && !liveDraftEditable}}"'):
        assert guard in view
    assert "createLiveDialogControl" in dialog
    assert "wx.showModal" in dialog and "this.realControl.prepare(kind)" in dialog
    assert "this.realControl.confirm(token)" in dialog
    assert 'bindtap="checkOriginalReceipt"' in view
    client = (MINIPROGRAM / "services/live-dialog.ts").read_text()
    for forbidden in ("wx.login", "wx.request", "setStorage", "session_token", "sendDialogCommand"):
        assert forbidden not in client
    package = json.loads((MINIPROGRAM / "package.json").read_text())
    for name in ("live-dialog", "live-dialog-pages", "live-dialog-control"):
        assert package["scripts"]["smoke:" + name] == f"node scripts/smoke-{name}.cjs"
        assert "npm run smoke:" + name in package["scripts"]["test"]


def test_real_dialog_status_allowlists_and_labels_match_the_server_contract() -> None:
    expected = set(get_args(State))
    for filename, constant in (("live-dialog.ts", "DIALOG_STATES"), ("live-dialog-control.ts", "STATES"),
                               ("dev-gateway-client.ts", "DIALOG_STATES")):
        source = (MINIPROGRAM / "services" / filename).read_text()
        literal = re.search(rf"const {constant} = \[([^\]]+)\]", source)
        assert literal is not None
        states = re.findall(r"'([a-z_]+)'", literal[1])
        assert len(states) == len(set(states)) and set(states) == expected
    page = (MINIPROGRAM / "pages/dialog/index.ts").read_text()
    labels = re.findall(r"^  ([a-z_]+): \{ text:", page, re.MULTILINE)
    assert len(labels) == len(set(labels)) and set(labels) == expected


def test_package_hygiene_runs_before_other_miniprogram_checks() -> None:
    package = json.loads((MINIPROGRAM / "package.json").read_text())
    assert package["scripts"]["check:package"] == "node scripts/check-package-safety.mjs"
    assert package["scripts"]["smoke:package"] == "node scripts/smoke-package-safety.mjs"
    assert package["scripts"]["test"].startswith("npm run check:package && npm run smoke:package &&")


def test_wxml_uses_native_tags_and_precomputed_values() -> None:
    unsupported = re.compile(r"</?(?:strong|small|time|output|div|span|h[1-6])(?:\s|>)")
    method = re.compile(r"\.(?:slice|replace|trim|map|filter)\(")
    for path in source_files(".wxml"):
        text = path.read_text()
        assert not unsupported.search(text), path
        assert not method.search(text), path


def test_all_static_runtime_image_references_exist_and_use_local_png() -> None:
    for suffix in (".ts", ".wxml"):
        for path in source_files(suffix):
            text = path.read_text()
            assert not re.search(r"/assets/[^'\"\s<>]*\.svg", text), path
            for value in re.findall(r"""['"](/assets/[^'"\s<>]+)['"]""", text):
                if "{{" not in value and "$" + "{" not in value:
                    assert (MINIPROGRAM / value.lstrip("/")).is_file(), (path, value)


def test_local_avatar_is_512_png_with_small_package_budget() -> None:
    raw = (MINIPROGRAM / "assets/ai-cat-avatar.png").read_bytes()
    assert len(raw) <= 300 * 1024
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", raw[16:24]) == (512, 512)


def test_mijia_and_three_distinct_danger_uis_are_present() -> None:
    mijia = (MINIPROGRAM / "pages/mijia/index.wxml").read_text()
    for text in ("米家连接", "可查看状态", "可执行操作", "后续通知"):
        assert text in mijia
    settings = (MINIPROGRAM / "pages/profile/index.wxml").read_text()
    for text in ("解绑设备", "清除全部记忆", "一键清除记忆", "重置全部数据", "完整对话"):
        assert text in settings


def test_six_growth_dimensions_and_dialog_controls_remain_available() -> None:
    growth_data = (MINIPROGRAM / "mocks/growth.ts").read_text()
    for label in ("好奇", "共情", "求知", "活力", "淘气", "自律"):
        assert label in growth_data
    dialog = (MINIPROGRAM / "pages/dialog/index.wxml").read_text()
    for value in ("startListening", "interrupt", "sendText", 'maxlength="{{liveMode ? 1000 : 500}}"'):
        assert value in dialog
    # A Unicode scalar can occupy two UTF-16 units in the native textarea.
    # Real questions remain limited to 500 scalars; preview keeps its old 500.
    control = (MINIPROGRAM / "services/live-dialog-control.ts").read_text()
    assert "codePoints(value) <= 500" in control
    assert "dialogTextValid(value.content)" in control


def test_page_timers_are_cleaned_up_when_hidden_and_unloaded() -> None:
    for path in (MINIPROGRAM / "pages").glob("*/index.ts"):
        text = path.read_text()
        if "setInterval(" in text or "setTimeout(" in text:
            assert "onHide()" in text, path
            assert "onUnload()" in text, path
            assert "clearInterval(" in text or "clearTimeout(" in text, path


def test_navigation_reserves_bottom_safe_area_and_accessible_touch_targets() -> None:
    frame = (MINIPROGRAM / "components/app-page/index.wxss").read_text()
    nav = (MINIPROGRAM / "components/product-nav/index.wxss").read_text()
    assert "safe-area-inset-bottom" in frame
    assert "safe-area-inset-bottom" in nav
    assert "min-height: 88rpx" in nav


def test_navigation_has_component_local_four_column_button_geometry() -> None:
    # app.wxss button resets do not cross an isolated component boundary.
    # Explicit item geometry prevents native button minimum widths from
    # pushing entries 3/4 and 7/8 outside the viewport.
    text = (MINIPROGRAM / "components/product-nav/index.wxss").read_text()
    shell = re.search(r"\.nav-shell\s*\{([^}]+)\}", text).group(1)
    item = re.search(r"\.nav-item\s*\{([^}]+)\}", text).group(1)
    for rule in ("display: flex", "flex-wrap: wrap", "box-sizing: border-box"):
        assert rule in shell
    for rule in (
        "flex: 0 0 25%", "width: 25%", "max-width: 25%", "min-width: 0",
        "box-sizing: border-box", "margin: 0", "padding:",
    ):
        assert rule in item
    assert ".nav-item::after" in text


def test_confirmation_prevents_background_drag_and_retry_has_touch_target() -> None:
    sheet = (MINIPROGRAM / "components/confirm-sheet/index.wxml").read_text()
    assert 'catchtouchmove="stop"' in sheet
    frame = (MINIPROGRAM / "components/app-page/index.wxss").read_text()
    retry = re.search(r"\.retry\s*\{([^}]+)\}", frame).group(1)
    for rule in ("min-height: 88rpx", "box-sizing: border-box", "margin: 0"):
        assert rule in retry
