"""Temporary private delegation stores; never contacts hardware or real files."""
import hashlib
import sqlite3

import pytest

from ai_cat_controller.local_gateway.delegation import DelegationStore


def binding(**changes):
    return {"owner_user_id": "user_original", "pet_id": "pet_original",
            "serial_number": "K1-LOCAL-TEST-001", "bound_at": "2026-11-02T00:00:00+00:00", **changes}


def test_only_hash_persisted_and_restart_keeps_genuine_binding(tmp_path):
    path = tmp_path / 'private' / 'delegation.sqlite3'
    store = DelegationStore(path)
    token = store.issue(binding())
    assert token.startswith('lgd_') and token.encode() not in path.read_bytes()
    reopened = DelegationStore(path)
    principal = reopened.resolve(token, binding(), 'GET', '/api/v1/pets')
    assert principal.user_id == 'user_original' and principal.pet_id == 'pet_original'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT token_hash FROM lg_delegation').fetchone()[0] == hashlib.sha256(token.encode()).hexdigest()
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('patch', [{"owner_user_id": "user_new"}, {"pet_id": "pet_new"},
    {"serial_number": "K1-LOCAL-OTHER"}, {"bound_at": "2026-11-02T00:00:01+00:00"}, {"owner_user_id": None}])
def test_binding_change_invalidates_old_grant(tmp_path, patch):
    store = DelegationStore(tmp_path / 'private' / 'delegation.sqlite3')
    token = store.issue(binding())
    with pytest.raises(ValueError):
        store.resolve(token, binding(**patch), 'GET', '/api/v1/pets')


def test_explicit_reissue_rotates_old_token_and_revoke_is_durable(tmp_path):
    path = tmp_path / 'private' / 'delegation.sqlite3'
    store = DelegationStore(path)
    old = store.issue(binding())
    new = store.issue(binding())
    assert new != old
    with pytest.raises(ValueError): store.resolve(old, binding(), 'GET', '/api/v1/pets')
    store.revoke(binding())
    with pytest.raises(ValueError): DelegationStore(path).resolve(new, binding(), 'GET', '/api/v1/pets')


@pytest.mark.parametrize(('method','path'), [('POST','/api/v1/pets/bind'),
    ('POST','/api/v1/pets/pet_original/unbind'), ('POST','/api/v1/pets/pet_original/memory/clear'),
    ('POST','/api/v1/product/reset'), ('GET','/api/v1/pets/pet_other/dashboard'),
    ('POST','/api/v1/services/start'), ('POST','/api/v1/motion/head/nod'),
    ('GET','/api/v1/pets/pet_original/dashboard?x=y'), ('GET','/api/v1/pets/pet_original/../pet_original/dashboard'),
    ('GET','/api/v1/local-gateway/delegations'), ('PATCH','/api/v1/pets/pet_original/growth/debug')])
def test_no_dangerous_or_unpinned_routes(tmp_path, method, path):
    store = DelegationStore(tmp_path / 'private' / 'delegation.sqlite3')
    token = store.issue(binding())
    with pytest.raises(ValueError): store.resolve(token, binding(), method, path)


@pytest.mark.parametrize(('method','path'), [('GET','/api/v1/device/status'),
    ('GET','/api/v1/pets/pet_original/dashboard'), ('GET','/api/v1/pets/pet_original/intimacy'),
    ('GET','/api/v1/pets/pet_original/growth'), ('GET','/api/v1/pets/pet_original/actions'),
    ('GET','/api/v1/pets/pet_original/dialog-readonly/status'),
    ('POST','/api/v1/pets/pet_original/actions/head_nod/execute'),
    ('POST','/api/v1/pets/pet_original/interactions'), ('PATCH','/api/v1/pets/pet_original/settings'),
    ('POST','/api/v1/pets/pet_original/feedback'), ('POST','/api/v1/dialog/wake'),
    ('POST','/api/v1/dialog/interrupt'), ('POST','/api/v1/dialog/text'),
    ('POST','/api/v1/motion/stop'), ('PATCH','/api/v1/dialog/config')])
def test_exact_existing_business_bridge_routes(tmp_path, method, path):
    store = DelegationStore(tmp_path / 'private' / 'delegation.sqlite3')
    token = store.issue(binding())
    assert store.resolve(token, binding(), method, path).pet_id == 'pet_original'


def test_refuses_foreign_database_without_modifying_it(tmp_path):
    directory = tmp_path / 'private'; directory.mkdir(mode=0o700)
    path = directory / 'foreign.db'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE pets(id TEXT)')
    path.chmod(0o600); before = path.read_bytes()
    with pytest.raises(ValueError): DelegationStore(path)
    assert path.read_bytes() == before


def test_refuses_symlink_and_nonprivate_file(tmp_path):
    directory = tmp_path / 'private'; directory.mkdir(mode=0o700)
    path = directory / 'delegation.sqlite3'; DelegationStore(path)
    link = directory / 'link.sqlite3'; link.symlink_to(path)
    with pytest.raises(ValueError): DelegationStore(link)
    path.chmod(0o644)
    with pytest.raises(ValueError): DelegationStore(path)
