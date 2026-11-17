"""Lifecycle CLI only exposes unready capabilities; all files below are synthetic."""
import json
import sqlite3

import pytest

from ai_cat_controller.mini_gateway import cli


def arguments(tmp_path):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    for name, value in [('app.txt', 'a' * 32), ('bridge.txt', 'y' * 43),
                        ('device.env', 'AI_CAT_API_KEY=synthetic-private-key'),
                        ('web.json', json.dumps({'session_token': 'z' * 32, 'pet_id': 'pet_web'}))]:
        (directory / name).write_text(value)
        (directory / name).chmod(0o600)
    return ['--database', str(directory / 'sessions.sqlite3'), '--app-id', 'wx1234567890abcdef',
            '--app-secret-file', str(directory / 'app.txt'), '--bridge-token-file', str(directory / 'bridge.txt'),
            '--status-pet-id', 'pet_mini', '--status-device-serial', 'K1-SYNTHETIC-CLI',
            '--motion-upstream', 'http://192.168.1.2:8000', '--motion-device-env-file', str(directory / 'device.env'),
            '--motion-product-session-file', str(directory / 'web.json')]


def test_cli_lifecycle_absent_by_default_has_no_production_enable_switch():
    parser = cli.build_parser()
    assert parser.parse_args([]).lifecycle_database is None
    for option in ['--enable-lifecycle', '--enable-product-reset', '--lifecycle-executor', '--operator-token']:
        with pytest.raises(SystemExit):
            parser.parse_args([option])


def test_lifecycle_requires_existing_explicit_device_and_product_configuration(tmp_path):
    destination = tmp_path / 'private' / 'lifecycle.sqlite3'
    args = cli.build_parser().parse_args(['--lifecycle-database', str(destination)])
    with pytest.raises(ValueError, match='together'):
        cli.build_app(args)
    assert not destination.exists()


@pytest.mark.parametrize('name', ['sessions.sqlite3', 'product.sqlite3', 'motion.sqlite3',
                                 'dialog.sqlite3', 'preferences.sqlite3', 'miniprogram/export.sqlite3'])
def test_lifecycle_store_aliases_rejected_before_other_resources_open(tmp_path, name):
    args = cli.build_parser().parse_args(arguments(tmp_path) + ['--lifecycle-database', str(tmp_path / 'private' / name)])
    with pytest.raises(ValueError, match='separate'):
        cli.build_app(args)
    assert not (tmp_path / 'private' / 'motion.sqlite3').exists()


def test_only_hash_of_existing_owner_is_read_and_missing_pin_is_not_manufactured(tmp_path):
    missing = tmp_path / 'missing.sqlite3'
    assert cli.read_existing_product_owner_pin(missing) is None
    assert not missing.exists()
    path = tmp_path / 'private.sqlite3'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE product_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute('CREATE TABLE product_receipts(receipt_id TEXT PRIMARY KEY)')
        c.execute('INSERT INTO product_meta VALUES(?,?)', ('schema', 'mini_product_v1'))
    path.chmod(0o600)
    before = path.read_bytes()
    assert cli.read_existing_product_owner_pin(path) is None
    assert path.read_bytes() == before
    with sqlite3.connect(path) as c:
        c.execute('INSERT INTO product_meta VALUES(?,?)', ('owner', 'a' * 64))
    before = path.read_bytes()
    assert cli.read_existing_product_owner_pin(path) == 'a' * 64
    assert path.read_bytes() == before


@pytest.mark.parametrize('unsafe', ['wal', 'view', 'schema', 'hardlink', 'symlink', 'public'])
def test_owner_pin_reader_rejects_unverified_store_without_writes(tmp_path, unsafe):
    path = tmp_path / 'product.sqlite3'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE product_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute('CREATE TABLE product_receipts(receipt_id TEXT PRIMARY KEY)')
        c.execute('INSERT INTO product_meta VALUES(?,?)', ('schema', 'mini_product_v1'))
        c.execute('INSERT INTO product_meta VALUES(?,?)', ('owner', 'a' * 64))
        if unsafe == 'view':
            c.execute('CREATE VIEW hidden AS SELECT * FROM product_meta')
        elif unsafe == 'schema':
            c.execute('UPDATE product_meta SET value=? WHERE key=?', ('other_v1', 'schema'))
    path.chmod(0o600)
    if unsafe == 'wal':
        path.with_name(path.name + '-wal').write_bytes(b'')
    elif unsafe == 'hardlink':
        import os
        os.link(path, path.with_name('alias.sqlite3'))
    elif unsafe == 'symlink':
        original = path.with_name('original.sqlite3')
        path.rename(original)
        path.symlink_to(original)
    elif unsafe == 'public':
        path.chmod(0o644)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        cli.read_existing_product_owner_pin(path)
    assert path.read_bytes() == before


def test_configured_lifecycle_only_initializes_disabled_unready_adapter(tmp_path, monkeypatch):
    captured = {}
    values = arguments(tmp_path)
    class Resource:
        def __init__(self, **kwargs):
            pass
        def close(self):
            pass
    monkeypatch.setattr(cli, 'LocalMotionController', Resource)
    monkeypatch.setattr(cli, 'LocalProductService', Resource)
    monkeypatch.setattr(cli, 'create_app', lambda config, **kwargs: captured.update(kwargs) or 'app')
    destination = tmp_path / 'private' / 'lifecycle.sqlite3'
    args = cli.build_parser().parse_args(values + ['--lifecycle-database', str(destination)])
    assert cli.build_app(args) == 'app'
    service = captured['lifecycle_service']
    assert service.enabled_kinds == frozenset() and service._executor is None and service._owner is None
    assert service.expected_serial == 'K1-SYNTHETIC-CLI'
    service.close()


def test_lifecycle_failure_releases_previous_resources_without_starting_server(tmp_path, monkeypatch):
    closed = []
    def resource(name):
        class Resource:
            def __init__(self, **_):
                pass
            def close(self):
                closed.append(name)
        return Resource
    monkeypatch.setattr(cli, 'LocalMotionController', resource('motion'))
    monkeypatch.setattr(cli, 'LocalProductService', resource('product'))
    def fail(**_):
        raise ValueError('private-lifecycle-error')
    monkeypatch.setattr(cli, 'LocalLifecycleService', fail)
    args = cli.build_parser().parse_args(arguments(tmp_path) + ['--lifecycle-database', str(tmp_path / 'private' / 'lifecycle.sqlite3')])
    with pytest.raises(ValueError):
        cli.build_app(args)
    assert closed == ['product', 'motion']
