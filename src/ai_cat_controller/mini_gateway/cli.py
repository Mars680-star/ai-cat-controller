"""Loopback-only launcher for the separate mini-program login gateway.

This process never starts the controller, a device adapter or background motion.
Credentials stay in private external files. Optional motion stays disabled unless
the local operator explicitly enables it after a supervised hardware check.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
from contextlib import closing
from urllib.parse import quote

import uvicorn

from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.motion import LocalMotionController
from ai_cat_controller.mini_gateway.product import LocalProductService
from ai_cat_controller.mini_gateway.dialog import LocalDialogReader
from ai_cat_controller.mini_gateway.dialog_control import LocalDialogController
from ai_cat_controller.mini_gateway.preferences import LocalPreferencesService
from ai_cat_controller.mini_gateway.lifecycle import LocalLifecycleService
from ai_cat_controller.mini_gateway.status_reader import LocalStatusReader


def read_private_text(path: Path, *, max_bytes: int = 2048) -> str:
    """Read a bounded regular 0600/0400 file without echoing paths or secrets."""
    descriptor = None
    try:
        if path.is_symlink() or any(
            part.lower() in {"miniprogram", "wechat-miniprogram"}
            for part in path.resolve().parts
        ):
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > max_bytes:
            raise ValueError
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = None
            value = stream.read(max_bytes + 1).strip()
        if not value or len(value) > max_bytes:
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError):
        raise ValueError("Credentials require a private regular file outside the mini-program package") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_device_api_key(path: Path) -> str:
    """Extract one literal key; do not execute, expand or expose environment text."""
    raw = read_private_text(path, max_bytes=16_384)
    values = [line.partition("=")[2].strip() for line in raw.splitlines()
              if line.startswith("AI_CAT_API_KEY=")]
    if len(values) != 1:
        raise ValueError("Invalid private device credential")
    value = values[0]
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    if not 1 <= len(value) <= 512 or not value.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("Invalid private device credential")
    return value


def read_product_authorization(path: Path) -> tuple[str, str]:
    """Accept only an explicitly supplied genuine webpage session and pet.

    This function never signs in to the product, selects the first owner, claims
    a device, saves the token, or includes private values in an error message.
    """
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        value = json.loads(read_private_text(path), object_pairs_hook=unique_object)
        if type(value) is not dict or set(value) != {"session_token", "pet_id"}:
            raise ValueError
        token, pet = value["session_token"], value["pet_id"]
        if (not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token) is None
                or not isinstance(pet, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", pet) is None):
            raise ValueError
        return token, pet
    except (TypeError, ValueError, UnicodeError):
        pass
    raise ValueError("Invalid private product authorization") from None


def read_existing_product_owner_pin(path: Path) -> str | None:
    """Read only an existing hash; never create/populate a missing owner pin."""
    if not path.exists():
        return None
    try:
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ValueError
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError
        if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm')):
            raise ValueError
        uri = "file:" + quote(str(path.absolute()), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"product_meta", "product_receipts"}:
                raise ValueError
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():
                raise ValueError
            if connection.execute("SELECT value FROM product_meta WHERE key='schema'").fetchone() != ('mini_product_v1',):
                raise ValueError
            row = connection.execute("SELECT value FROM product_meta WHERE key='owner'").fetchone()
            if row is None:
                return None
            if not isinstance(row[0], str) or re.fullmatch(r"[0-9a-f]{64}", row[0]) is None:
                raise ValueError
            return row[0]
    except (OSError, sqlite3.Error, ValueError):
        raise ValueError("Existing product owner pin is not a private validated hash") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local mini-program auth gateway; not a production deployment or a K1 service",
        allow_abbrev=False,
    )
    parser.add_argument("--database", type=Path, default=Path(".data/mini-gateway/sessions.sqlite3"))
    parser.add_argument("--app-id", default=os.environ.get("AI_CAT_MINI_APP_ID", ""))
    parser.add_argument("--app-secret-file", type=Path, help="Private external UTF-8 file; never a command-line secret")
    parser.add_argument("--bridge-token-file", type=Path, help="Optional private token for the existing read-only local bridge")
    parser.add_argument("--status-pet-id", default="", help="Explicit single pet routed to the local read-only bridge")
    parser.add_argument("--status-device-serial", default="", help="Full device identity pinned by the server; never supplied by the mini-program")
    parser.add_argument("--motion-upstream", help="Optional explicit private device origin; not a client-selected URL")
    parser.add_argument("--motion-device-env-file", type=Path, help="Private environment file containing the existing device API key")
    parser.add_argument("--motion-database", type=Path, help="Optional dedicated intent ledger, distinct from the session database")
    parser.add_argument("--motion-product-session-file", type=Path,
                        help="Optional private JSON with a genuine webpage session_token and explicit pet_id; never creates a login or binding")
    parser.add_argument("--enable-motion", action="store_true", help="Operator opt-in after supervised hardware confirmation; NEVER enabled by default")
    parser.add_argument("--product-database", type=Path, help="Separate private interaction receipt ledger; never a device database")
    parser.add_argument("--enable-interactions", action="store_true", help="Explicitly allow four confirmed product interactions; independent of motor enablement")
    parser.add_argument("--enable-dialog-readonly", action="store_true", help="Require the dedicated persisted-dialog read-only upstream capability; never enables dialog controls")
    parser.add_argument("--dialog-database", type=Path, help="Separate private dialog command receipt ledger, outside the exported package")
    parser.add_argument("--enable-dialog-control", action="store_true", help="Explicitly allow fixed listen/interrupt/text intents; requires the dedicated read-only capability")
    parser.add_argument("--preferences-database", type=Path, help="Separate private ordinary-settings and feedback receipt ledger")
    parser.add_argument("--enable-preferences", action="store_true", help="Explicitly allow ordinary name, volume and follow-up changes; never enables destructive operations")
    parser.add_argument("--enable-feedback", action="store_true", help="Explicitly allow device-local text feedback, not a cloud/customer-service submission")
    parser.add_argument("--lifecycle-database", type=Path,
                        help="Optional independent lifecycle capability/receipt ledger; NEVER enables destructive execution")
    parser.add_argument("--port", type=int, choices=[8787], default=8787)
    return parser


def build_app(args: argparse.Namespace):
    status_options = (bool(args.bridge_token_file), bool(args.status_pet_id), bool(args.status_device_serial))
    if any(status_options) and not all(status_options):
        raise ValueError("Status pet, device identity and bridge token must be configured together")
    if any(part.lower() in {"miniprogram", "wechat-miniprogram"} for part in args.database.resolve().parts):
        raise ValueError("Gateway database must stay outside the mini-program package")
    app_secret = read_private_text(args.app_secret_file) if args.app_secret_file else ""
    bridge_token = read_private_text(args.bridge_token_file) if args.bridge_token_file else None
    config = MiniGatewayConfig(
        db_path=args.database,
        app_id=args.app_id,
        app_secret=app_secret,
        status_pet_id=args.status_pet_id,
    )
    reader = LocalStatusReader(bridge_token=bridge_token, expected_serial=args.status_device_serial) if bridge_token is not None else None
    motion_options = (bool(args.motion_upstream), bool(args.motion_device_env_file))
    motion_requested = (any(motion_options) or args.motion_database is not None
                        or args.motion_product_session_file is not None or args.enable_motion)
    controller = None
    product_service = None
    dialog_reader = None
    dialog_controller = None
    preferences_service = None
    lifecycle_service = None
    lifecycle_path = getattr(args, "lifecycle_database", None)
    lifecycle_requested = lifecycle_path is not None
    if lifecycle_requested and (args.motion_product_session_file is None or not all(status_options) or not all(motion_options)):
        raise ValueError("Lifecycle checks require all private device configuration and genuine webpage authorization together")
    preferences_requested = args.preferences_database is not None or args.enable_preferences or args.enable_feedback
    if preferences_requested and (args.motion_product_session_file is None or not all(status_options) or not all(motion_options)):
        raise ValueError("Preferences require all private device configuration and genuine webpage authorization together")
    dialog_requested = args.dialog_database is not None or args.enable_dialog_control
    if dialog_requested and (not args.enable_dialog_readonly or args.motion_product_session_file is None
                             or not all(status_options) or not all(motion_options)):
        raise ValueError("Dialog control requires the dedicated read-only reader and all private device configuration together")
    product_requested = (args.product_database is not None or args.enable_interactions
                         or args.motion_product_session_file is not None)
    if product_requested and args.motion_product_session_file is None:
        raise ValueError("Product data and private webpage authorization must be configured together")
    if args.enable_dialog_readonly and args.motion_product_session_file is None:
        raise ValueError("Read-only dialog and private webpage authorization must be configured together")
    product_ledger = args.product_database or args.database.parent / "product.sqlite3"
    motion_ledger = args.motion_database or args.database.parent / "motion.sqlite3"
    dialog_ledger = args.dialog_database or args.database.parent / "dialog.sqlite3"
    preferences_ledger = args.preferences_database or args.database.parent / "preferences.sqlite3"
    lifecycle_owner = None
    if lifecycle_requested:
        if (any(part.lower() in {"miniprogram", "wechat-miniprogram"} for part in lifecycle_path.resolve().parts) or
                any(lifecycle_path.resolve() == other.resolve() or
                    (lifecycle_path.exists() and other.exists() and os.path.samefile(lifecycle_path, other))
                    for other in (args.database, motion_ledger, product_ledger, dialog_ledger, preferences_ledger))):
            raise ValueError("Lifecycle checks require a separate private ledger outside the exported package")
        lifecycle_owner = read_existing_product_owner_pin(product_ledger)
    if preferences_requested:
        if (any(part.lower() in {"miniprogram", "wechat-miniprogram"} for part in preferences_ledger.resolve().parts) or
                any(preferences_ledger.resolve() == other.resolve() or
                    (preferences_ledger.exists() and other.exists() and os.path.samefile(preferences_ledger, other))
                    for other in (args.database, motion_ledger, product_ledger, dialog_ledger))):
            raise ValueError("Preferences require a separate private ledger outside the exported package")
    if dialog_requested:
        if (any(part.lower() in {"miniprogram", "wechat-miniprogram"} for part in dialog_ledger.resolve().parts) or
                any(dialog_ledger.resolve() == other.resolve() or
                    (dialog_ledger.exists() and other.exists() and os.path.samefile(dialog_ledger, other))
                    for other in (args.database, motion_ledger, product_ledger))):
            raise ValueError("Dialog commands require a separate private ledger outside the exported package")
    if product_requested:
        paths = (product_ledger, args.database, motion_ledger)
        if (any(part.lower() in {"miniprogram", "wechat-miniprogram"} for part in product_ledger.resolve().parts) or
                any(product_ledger.resolve() == other.resolve() or
                    (product_ledger.exists() and other.exists() and os.path.samefile(product_ledger, other))
                    for other in paths[1:])):
            raise ValueError("Product interactions require a separate private ledger outside the exported package")
    if motion_requested:
        if not all(status_options) or not all(motion_options):
            raise ValueError("Motion device, private credential and pinned status binding must be configured together")
        ledger = args.motion_database or args.database.parent / "motion.sqlite3"
        if (ledger.resolve() == args.database.resolve() or
                (ledger.exists() and args.database.exists() and os.path.samefile(ledger, args.database)) or
                any(part.lower() in {"miniprogram", "wechat-miniprogram"} for part in ledger.resolve().parts)):
            raise ValueError("Motion requires a separate private ledger outside the exported package")
        product_options = {}
        if args.motion_product_session_file is not None:
            product_session, product_pet_id = read_product_authorization(args.motion_product_session_file)
            product_options = {"product_session": product_session, "product_pet_id": product_pet_id}
        device_key = read_device_api_key(args.motion_device_env_file)
        controller = LocalMotionController(db_path=ledger, enabled=args.enable_motion,
                                           upstream_url=args.motion_upstream,
                                           api_key=device_key,
                                           expected_serial=args.status_device_serial,
                                           **product_options)
        if product_requested:
            try:
                product_service = LocalProductService(db_path=product_ledger, upstream_url=args.motion_upstream,
                    api_key=device_key, expected_serial=args.status_device_serial,
                    interactions_enabled=args.enable_interactions, **product_options)
            except Exception:
                controller.close()
                raise
        if args.enable_dialog_readonly:
            try:
                dialog_reader = LocalDialogReader(upstream_url=args.motion_upstream, api_key=device_key,
                    expected_serial=args.status_device_serial, **product_options)
            except Exception:
                if product_service is not None:
                    product_service.close()
                controller.close()
                raise
        if dialog_requested:
            try:
                dialog_controller = LocalDialogController(db_path=dialog_ledger, enabled=args.enable_dialog_control,
                    upstream_url=args.motion_upstream, api_key=device_key, expected_serial=args.status_device_serial,
                    reader=dialog_reader, **product_options)
            except Exception:
                if dialog_reader is not None:
                    dialog_reader.close()
                if product_service is not None:
                    product_service.close()
                controller.close()
                raise
        if preferences_requested:
            try:
                preferences_service = LocalPreferencesService(db_path=preferences_ledger,
                    settings_enabled=args.enable_preferences, feedback_enabled=args.enable_feedback,
                    upstream_url=args.motion_upstream, api_key=device_key, expected_serial=args.status_device_serial,
                    **product_options)
            except Exception:
                if dialog_controller is not None:
                    dialog_controller.close()
                if dialog_reader is not None:
                    dialog_reader.close()
                if product_service is not None:
                    product_service.close()
                controller.close()
                raise
        if lifecycle_requested:
            try:
                # No flag, JSON marker or ordinary owner session can inject a
                # production executor/operator. These checks remain fail-closed.
                lifecycle_service = LocalLifecycleService(db_path=lifecycle_path,
                    expected_serial=args.status_device_serial, product_pet_id=product_pet_id,
                    expected_owner_sha256=lifecycle_owner, enabled_kinds=(), executor=None)
            except Exception:
                if preferences_service is not None:
                    preferences_service.close()
                if dialog_controller is not None:
                    dialog_controller.close()
                if dialog_reader is not None:
                    dialog_reader.close()
                if product_service is not None:
                    product_service.close()
                controller.close()
                raise
    if controller is None:
        return create_app(config, status_reader=reader)
    return create_app(config, status_reader=reader, motion_controller=controller, product_service=product_service,
                      dialog_reader=dialog_reader, dialog_controller=dialog_controller, preferences_service=preferences_service,
                      **({"lifecycle_service": lifecycle_service} if lifecycle_service is not None else {}))


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        application = build_app(args)
    except (OSError, ValueError):
        # Do not print an exception carrying a credential, file path or DB data.
        parser.exit(2, "Local gateway configuration is invalid; check the private files and dedicated database.\n")
    uvicorn.run(application, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
