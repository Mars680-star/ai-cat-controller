"""The local gateway launcher never modifies a K1 or exports credentials."""

import json
from pathlib import Path

import pytest

from ai_cat_controller.mini_gateway import cli


def private_file(tmp_path: Path, value: str = "private-placeholder") -> Path:
    path = tmp_path / "secret.txt"
    path.write_text(value)
    path.chmod(0o600)
    return path


def test_launcher_has_only_loopback_and_fixed_development_port() -> None:
    parser = cli.build_parser()
    args = parser.parse_args([])
    assert args.port == 8787
    assert args.database == Path(".data/mini-gateway/sessions.sqlite3")
    assert args.status_device_serial == ""
    for arguments in (["--host", "0.0.0.0"], ["--port", "8000"], ["--app-secret", "hidden"]):
        with pytest.raises(SystemExit):
            parser.parse_args(arguments)


def test_private_config_reader_does_not_expose_values(tmp_path: Path) -> None:
    path = private_file(tmp_path)
    assert cli.read_private_text(path) == "private-placeholder"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private") as error:
        cli.read_private_text(path)
    assert "private-placeholder" not in str(error.value)


def test_private_config_reader_rejects_symlink_empty_large_and_directory(tmp_path: Path) -> None:
    path = private_file(tmp_path)
    link = tmp_path / "link.txt"
    link.symlink_to(path)
    for invalid in (link, tmp_path):
        with pytest.raises(ValueError):
            cli.read_private_text(invalid)
    path.write_text("")
    with pytest.raises(ValueError):
        cli.read_private_text(path)
    path.write_text("x" * 2049)
    with pytest.raises(ValueError):
        cli.read_private_text(path)


@pytest.mark.parametrize("package", ["miniprogram", "wechat-miniprogram"])
def test_credentials_cannot_be_read_from_exported_project(tmp_path: Path, package: str) -> None:
    directory = tmp_path / package
    directory.mkdir()
    with pytest.raises(ValueError):
        cli.read_private_text(private_file(directory))


@pytest.mark.parametrize("arguments", [
    ["--status-pet-id", "pet1"],
    ["--bridge-token-file", "/nonexistent/private.txt"],
    ["--status-pet-id", "pet1", "--bridge-token-file", "/nonexistent/private.txt"],
    ["--status-device-serial", "real_serial"],
])
def test_status_binding_requires_explicit_pet_and_private_bridge_token(arguments: list[str]) -> None:
    args = cli.build_parser().parse_args(arguments)
    with pytest.raises(ValueError, match="together"):
        cli.build_app(args)


def test_gateway_database_cannot_enter_the_exported_package(tmp_path: Path) -> None:
    destination = tmp_path / "miniprogram" / "private.sqlite3"
    args = cli.build_parser().parse_args(["--database", str(destination)])
    with pytest.raises(ValueError, match="outside"):
        cli.build_app(args)
    assert not destination.exists()


def test_launcher_passes_only_private_credentials_and_never_starts_device(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(cli, "create_app", lambda config, **kwargs: captured.update(config=config, **kwargs) or "app")
    monkeypatch.setattr(cli, "LocalStatusReader", lambda *, bridge_token, expected_serial: ("local-only-reader", bridge_token, expected_serial))
    app_secret = private_file(tmp_path, "a" * 32)
    bridge_token = tmp_path / "bridge-token.txt"
    bridge_token.write_text("b" * 43)
    bridge_token.chmod(0o600)
    args = cli.build_parser().parse_args([
        "--database", str(tmp_path / "gateway.sqlite3"),
        "--app-id", "wx1234567890abcdef",
        "--app-secret-file", str(app_secret),
        "--status-pet-id", "pet_test",
        "--status-device-serial", "real_serial",
        "--bridge-token-file", str(bridge_token),
    ])
    assert cli.build_app(args) == "app"
    assert captured["config"].app_secret == "a" * 32
    assert captured["config"].status_pet_id == "pet_test"
    assert captured["status_reader"] == ("local-only-reader", "b" * 43, "real_serial")
    assert not (tmp_path / "gateway.sqlite3").exists()


def test_default_app_starts_unconfigured_and_has_no_fake_identity(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    monkeypatch.delenv("AI_CAT_MINI_APP_ID", raising=False)
    monkeypatch.setattr(cli, "create_app", lambda config, **kwargs: captured.update(config=config, **kwargs) or "app")
    args = cli.build_parser().parse_args(["--database", str(tmp_path / "gateway.sqlite3")])
    assert cli.build_app(args) == "app"
    assert captured["config"].app_secret == ""
    assert captured["config"].app_id == ""
    assert captured["status_reader"] is None


def test_main_hides_invalid_private_path_and_does_not_run_server(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as error:
        cli.main(["--app-secret-file", str(tmp_path / "sensitive-name")])
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "sensitive-name" not in output
    assert "Traceback" not in output


def motion_arguments(tmp_path):
    secret = private_file(tmp_path, "a" * 32)
    token = tmp_path / "bridge-token.txt"
    token.write_text("b" * 43)
    token.chmod(0o600)
    environment = tmp_path / "device.env"
    environment.write_text("AI_CAT_HARDWARE_DRIVER=local_k1\nAI_CAT_API_KEY='private-test-device-key'\n")
    environment.chmod(0o600)
    return ["--database", str(tmp_path / "sessions.sqlite3"), "--app-id", "wx1234567890abcdef",
            "--app-secret-file", str(secret), "--bridge-token-file", str(token),
            "--status-pet-id", "pet_one", "--status-device-serial", "real_serial",
            "--motion-upstream", "http://192.168.1.2:8000", "--motion-device-env-file", str(environment)]


def test_motion_is_never_enabled_by_default():
    assert cli.build_parser().parse_args([]).enable_motion is False
    assert cli.build_parser().parse_args([]).enable_interactions is False
    assert cli.build_parser().parse_args([]).enable_dialog_readonly is False
    assert cli.build_parser().parse_args([]).enable_dialog_control is False
    assert cli.build_parser().parse_args([]).enable_preferences is False
    assert cli.build_parser().parse_args([]).enable_feedback is False
    assert cli.build_parser().parse_args([]).preferences_database is None


@pytest.mark.parametrize("extra", [["--enable-preferences"], ["--enable-feedback"], ["--preferences-database", "preferences.sqlite3"]])
def test_preferences_require_all_private_configuration_but_not_dialog_capability(tmp_path, extra):
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + extra)
    with pytest.raises(ValueError, match="together"):
        cli.build_app(args)
    assert not (tmp_path / "motion.sqlite3").exists()


@pytest.mark.parametrize("name", ["sessions.sqlite3", "motion.sqlite3", "product.sqlite3", "dialog.sqlite3", "miniprogram/export.sqlite3"])
def test_preferences_ledger_cannot_alias_other_four_databases_or_export(tmp_path, name):
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--enable-preferences",
        "--preferences-database", str(tmp_path / name)])
    with pytest.raises(ValueError, match="separate"):
        cli.build_app(args)
    assert not (tmp_path / "motion.sqlite3").exists()


def test_preferences_hardlink_alias_rejected_before_any_other_store_opened(tmp_path):
    import os
    original = tmp_path / "dialog.sqlite3"
    original.write_bytes(b"preserve-other-ledger")
    os.link(original, tmp_path / "alias.sqlite3")
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--enable-feedback",
        "--preferences-database", str(tmp_path / "alias.sqlite3")])
    with pytest.raises(ValueError, match="separate"):
        cli.build_app(args)
    assert original.read_bytes() == b"preserve-other-ledger" and not (tmp_path / "motion.sqlite3").exists()


def test_preferences_and_feedback_are_independent_optins_without_dialog_or_motor_enablement(tmp_path, monkeypatch):
    captured = {}
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    class Motion:
        def __init__(self, **kwargs):
            captured["motion"] = kwargs
        def close(self):
            pass
    monkeypatch.setattr(cli, "LocalMotionController", Motion)
    monkeypatch.setattr(cli, "LocalProductService", lambda **kwargs: "product")
    monkeypatch.setattr(cli, "LocalPreferencesService", lambda **kwargs: captured.update(preferences=kwargs) or "preferences")
    monkeypatch.setattr(cli, "create_app", lambda config, **kwargs: captured.update(app=kwargs) or "app")
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--enable-feedback"])
    assert cli.build_app(args) == "app"
    assert captured["preferences"]["feedback_enabled"] is True
    assert captured["preferences"]["settings_enabled"] is False and captured["motion"]["enabled"] is False
    assert captured["app"]["preferences_service"] == "preferences"
    assert captured["app"]["dialog_reader"] is None and captured["app"]["dialog_controller"] is None
    assert "product_session" not in captured["app"]


def test_failed_preferences_constructor_releases_all_prior_private_resources(tmp_path, monkeypatch):
    closed = []
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    def resource(name):
        class Resource:
            def __init__(self, **kwargs):
                pass
            def close(self):
                closed.append(name)
        return Resource
    for attribute, name in [("LocalMotionController", "motion"), ("LocalProductService", "product"),
            ("LocalDialogReader", "reader"), ("LocalDialogController", "dialog")]:
        monkeypatch.setattr(cli, attribute, resource(name))
    def fail(**kwargs):
        raise ValueError("synthetic invalid preferences ledger")
    monkeypatch.setattr(cli, "LocalPreferencesService", fail)
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--enable-dialog-readonly", "--enable-dialog-control", "--enable-preferences"])
    with pytest.raises(ValueError):
        cli.build_app(args)
    assert closed == ["dialog", "reader", "product", "motion"]


@pytest.mark.parametrize("extra", [["--enable-dialog-control"], ["--dialog-database", "dialog.sqlite3"]])
def test_dialog_control_requires_readonly_capability_and_complete_private_config(tmp_path, extra):
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + extra)
    with pytest.raises(ValueError, match="together"):
        cli.build_app(args)
    assert not (tmp_path / "motion.sqlite3").exists()


@pytest.mark.parametrize("name", ["sessions.sqlite3", "motion.sqlite3", "product.sqlite3", "miniprogram/export.sqlite3"])
def test_dialog_ledger_must_not_alias_any_other_database_or_export(tmp_path, name):
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--enable-dialog-readonly",
        "--enable-dialog-control", "--dialog-database", str(tmp_path / name)])
    with pytest.raises(ValueError, match="separate"):
        cli.build_app(args)
    assert not (tmp_path / "motion.sqlite3").exists()


def test_dialog_database_hardlink_is_rejected_before_any_other_ledger_is_opened(tmp_path):
    import os
    original = tmp_path / "sessions.sqlite3"
    original.write_bytes(b"preserve-this-database")
    os.link(original, tmp_path / "alias.sqlite3")
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--enable-dialog-readonly",
        "--enable-dialog-control", "--dialog-database", str(tmp_path / "alias.sqlite3")])
    with pytest.raises(ValueError, match="separate"):
        cli.build_app(args)
    assert original.read_bytes() == b"preserve-this-database"
    assert not (tmp_path / "motion.sqlite3").exists()


def test_dialog_readonly_requires_genuine_authorization_without_creating_any_database(tmp_path):
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + ["--enable-dialog-readonly"])
    with pytest.raises(ValueError, match="together"):
        cli.build_app(args)
    assert not (tmp_path / "motion.sqlite3").exists()


def test_failed_dialog_reader_construction_releases_existing_ledger_locks(tmp_path, monkeypatch):
    closed = []
    class Resource:
        def __init__(self, name):
            self.name = name
        def close(self):
            closed.append(self.name)
    monkeypatch.setattr(cli, "LocalMotionController", lambda **kwargs: Resource("motion"))
    monkeypatch.setattr(cli, "LocalProductService", lambda **kwargs: Resource("product"))
    def fail(**kwargs):
        raise ValueError("Invalid read-only configuration")
    monkeypatch.setattr(cli, "LocalDialogReader", fail)
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--enable-dialog-readonly"])
    with pytest.raises(ValueError):
        cli.build_app(args)
    assert closed == ["product", "motion"]


@pytest.mark.parametrize("arguments", [["--enable-interactions"], ["--product-database", "product.sqlite3"]])
def test_product_configuration_requires_explicit_genuine_webpage_authorization(tmp_path, arguments):
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + arguments)
    with pytest.raises(ValueError, match="together"):
        cli.build_app(args)
    assert not (tmp_path / "sessions.sqlite3").exists()


@pytest.mark.parametrize("destination", ["sessions.sqlite3", "motion.sqlite3", "miniprogram/private.sqlite3"])
def test_product_ledger_must_not_alias_session_motion_or_exported_package(tmp_path, destination):
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--product-database", str(tmp_path / destination)])
    with pytest.raises(ValueError, match="separate"):
        cli.build_app(args)
    assert not (tmp_path / "motion.sqlite3").exists()


def test_product_interactions_are_explicit_and_independent_of_motion_enablement(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "LocalMotionController", lambda **kwargs: captured.update(motion=kwargs) or "motion")
    monkeypatch.setattr(cli, "LocalProductService", lambda **kwargs: captured.update(product=kwargs) or "product")
    monkeypatch.setattr(cli, "create_app", lambda config, **kwargs: captured.update(app=kwargs) or "app")
    authorization = tmp_path / "product.json"
    authorization.write_text(json.dumps({"session_token": "a" * 32, "pet_id": "pet_existing"}))
    authorization.chmod(0o600)
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + [
        "--motion-product-session-file", str(authorization), "--enable-interactions"])
    assert cli.build_app(args) == "app"
    assert captured["product"]["interactions_enabled"] is True
    assert captured["motion"]["enabled"] is False
    assert captured["product"]["db_path"] == tmp_path / "product.sqlite3"
    assert captured["app"]["product_service"] == "product"
    assert not (tmp_path / "product.sqlite3").exists()


@pytest.mark.parametrize("arguments", [["--enable-motion"], ["--motion-upstream", "http://192.168.1.2:8000"],
                                       ["--motion-database", "motion.sqlite3"]])
def test_motion_requires_complete_private_configuration_before_any_device_connection(tmp_path, arguments):
    args = cli.build_parser().parse_args(["--database", str(tmp_path / "sessions.sqlite3"), *arguments])
    with pytest.raises(ValueError, match="together"):
        cli.build_app(args)
    assert not (tmp_path / "sessions.sqlite3").exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_motion_configuration_never_starts_network_or_passes_client_secrets(tmp_path, monkeypatch, enabled):
    captured = {}
    monkeypatch.setattr(cli, "LocalMotionController", lambda **kwargs: captured.update(motion=kwargs) or "controller")
    monkeypatch.setattr(cli, "create_app", lambda config, **kwargs: captured.update(app=kwargs) or "app")
    arguments = motion_arguments(tmp_path)
    if enabled:
        arguments.append("--enable-motion")
    assert cli.build_app(cli.build_parser().parse_args(arguments)) == "app"
    assert captured["motion"]["enabled"] is enabled
    assert captured["motion"]["expected_serial"] == "real_serial"
    assert captured["motion"]["api_key"] == "private-test-device-key"
    assert captured["motion"]["db_path"] == tmp_path / "motion.sqlite3"
    assert captured["app"]["motion_controller"] == "controller"
    assert not (tmp_path / "motion.sqlite3").exists()


@pytest.mark.parametrize("destination", ["sessions.sqlite3", "miniprogram/private.sqlite3"])
def test_motion_ledger_cannot_be_session_database_or_exported_package(tmp_path, destination):
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + ["--motion-database", str(tmp_path / destination)])
    with pytest.raises(ValueError, match="separate"):
        cli.build_app(args)


def test_motion_ledger_hard_link_to_sessions_is_rejected(tmp_path):
    import os

    (tmp_path / "sessions.sqlite3").write_bytes(b"do-not-change")
    os.link(tmp_path / "sessions.sqlite3", tmp_path / "hardlink.sqlite3")
    args = cli.build_parser().parse_args(motion_arguments(tmp_path) + ["--motion-database", str(tmp_path / "hardlink.sqlite3")])
    with pytest.raises(ValueError, match="separate"):
        cli.build_app(args)
    assert (tmp_path / "sessions.sqlite3").read_bytes() == b"do-not-change"


@pytest.mark.parametrize("raw", ["", "AI_CAT_API_KEY=\n", "AI_CAT_API_KEY=one\nAI_CAT_API_KEY=two", "AI_CAT_API_KEY=bad key"])
def test_device_credential_extraction_rejects_invalid_or_duplicate_values(tmp_path, raw):
    value = private_file(tmp_path, raw)
    with pytest.raises(ValueError):
        cli.read_device_api_key(value)


def test_private_device_environment_is_bounded_and_not_executed(tmp_path):
    value = private_file(tmp_path, "# " + "a" * 3000 + "\nAI_CAT_API_KEY=literal-placeholder\n")
    assert cli.read_device_api_key(value) == "literal-placeholder"
    value.write_text("x" * 16385)
    with pytest.raises(ValueError):
        cli.read_device_api_key(value)


def test_product_authorization_is_absent_by_default_and_never_implies_enablement():
    args = cli.build_parser().parse_args([])
    assert args.motion_product_session_file is None and args.enable_motion is False


@pytest.mark.parametrize("raw", [
    "not JSON", "[]", "{}", '{"session_token":"upstream-private-placeholder"}',
    '{"session_token":"upstream-private-placeholder","pet_id":"pet_one","extra":"forbidden"}',
    '{"session_token":"upstream-private-placeholder","pet_id":"pet_one","pet_id":"other"}',
    '{"session_token":"short","pet_id":"pet_one"}',
    '{"session_token":"upstream-private-placeholder","pet_id":"../other"}',
    '{"session_token":123,"pet_id":"pet_one"}',
    '{"session_token":"token with whitespace","pet_id":"pet_one"}',
])
def test_product_authorization_file_has_exact_strict_bounded_contract(tmp_path, raw):
    path = private_file(tmp_path, raw)
    with pytest.raises(ValueError) as error:
        cli.read_product_authorization(path)
    assert "upstream-private-placeholder" not in str(error.value)
    assert str(path) not in str(error.value)


@pytest.mark.parametrize("unsafe", ["mode", "symlink", "package", "oversized"])
def test_product_authorization_uses_same_private_external_file_boundary(tmp_path, unsafe):
    path = private_file(tmp_path, json.dumps({"session_token": "upstream-private-placeholder", "pet_id": "pet_one"}))
    if unsafe == "mode":
        path.chmod(0o644)
    elif unsafe == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    elif unsafe == "package":
        package = tmp_path / "wechat-miniprogram"
        package.mkdir()
        path = private_file(package, path.read_text())
    else:
        path.write_text("x" * 2049)
    with pytest.raises(ValueError, match="product authorization"):
        cli.read_product_authorization(path)


def test_product_session_file_alone_cannot_create_controller_or_identity(tmp_path):
    path = private_file(tmp_path)
    args = cli.build_parser().parse_args(["--database", str(tmp_path / "sessions.sqlite3"),
                                          "--motion-product-session-file", str(path)])
    with pytest.raises(ValueError, match="together"):
        cli.build_app(args)
    assert not (tmp_path / "sessions.sqlite3").exists()


def test_product_authorization_is_read_only_explicit_and_stays_server_side(tmp_path, monkeypatch):
    captured = {}
    arguments = motion_arguments(tmp_path)
    path = tmp_path / "genuine-web-session.json"
    original = json.dumps({"session_token": "upstream-private-placeholder", "pet_id": "pet_explicit_web_owner"})
    path.write_text(original)
    path.chmod(0o600)
    monkeypatch.setattr(cli, "LocalMotionController", lambda **kwargs: captured.update(controller=kwargs) or "controller")
    monkeypatch.setattr(cli, "create_app", lambda config, **kwargs: captured.update(app=kwargs) or "app")
    assert cli.build_app(cli.build_parser().parse_args(arguments + ["--motion-product-session-file", str(path)])) == "app"
    assert captured["controller"]["product_session"] == "upstream-private-placeholder"
    assert captured["controller"]["product_pet_id"] == "pet_explicit_web_owner"
    assert captured["controller"]["enabled"] is False
    assert captured["app"]["motion_controller"] == "controller"
    assert "product_session" not in captured["app"] and path.read_text() == original
