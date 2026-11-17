import copy
import json
import sqlite3
from pathlib import Path

import pytest

from ai_cat_controller.local_gateway.protocol import (
    Card, ChallengeBroker, LocalProtocolError, open_packet, seal_packet, validate_address,
)
from ai_cat_controller.local_gateway.store import LocalPairingStore


@pytest.fixture
def card():
    return Card('dev_' + 'a' * 32, 'b' * 32, '12' * 32)


@pytest.fixture
def store(tmp_path, card):
    value = LocalPairingStore(tmp_path / 'local' / 'sessions.sqlite3', card=card, serial='original_serial', initialize=True)
    yield value
    value.close()


def test_card_is_distinct_and_secret_safe(card):
    assert Card.parse(card.encode()) == card
    assert card.key_hex not in repr(card)
    for invalid in [card.encode() + ' ', card.encode().replace('LOCAL', 'PROVISION'), 'http://example.com']:
        with pytest.raises(LocalProtocolError):
            Card.parse(invalid)


@pytest.mark.parametrize('value', ['127.0.0.1', 'localhost', '169.254.169.254', '10.0.0.1:8000',
    'https://192.168.1.1', '192.168.001.2', '192.168.1.255', '8.8.8.8', '172.32.1.2', '::1'])
def test_address_rejects_aliases_and_non_lan(value):
    with pytest.raises(LocalProtocolError):
        validate_address(value)


@pytest.mark.parametrize('value', ['192.168.1.152', '10.47.0.1', '172.16.5.2'])
def test_canonical_lan_addresses(value):
    assert validate_address(value) == value


def test_packet_roundtrip_and_one_use_challenge(store, card):
    broker = ChallengeBroker(store)
    challenge = broker.issue('192.168.1.109', 'c'*32)
    body = {'method': 'POST', 'path': '/local/pair', 'body': {'confirmation': 'pair-local-device'}, 'accessToken': None}
    packet = seal_packet(card, challenge, 'c' * 32, body, direction='request')
    assert broker.consume(packet) == body
    response = broker.reply(packet, {'status': 200, 'body': {'success': True}})
    assert response['nonce'] == '01' + challenge['requestNonce'][2:]
    assert open_packet(card, response, direction='response') == {'status': 200, 'body': {'success': True}}
    with pytest.raises(LocalProtocolError):
        broker.consume(packet)
    with pytest.raises(LocalProtocolError):
        broker.reply(packet, {'status': 200})


@pytest.mark.parametrize('field,value', [('deviceId', 'dev_' + 'f' * 32), ('cardId', 'd' * 32),
    ('challengeId', 'e' * 32), ('requestId', 'f' * 32), ('nonce', '01' + '00' * 11)])
def test_aead_binds_every_identity(store, card, field, value):
    challenge = ChallengeBroker(store).issue('192.168.1.109', 'c'*32)
    packet = seal_packet(card, challenge, 'c' * 32, {'secret': 'not in transport'}, direction='request')
    bad = copy.deepcopy(packet)
    bad[field] = value
    with pytest.raises(LocalProtocolError):
        open_packet(card, bad, direction='request')


def test_challenge_expiry_does_not_dispatch(store, card):
    now = [1.0]
    broker = ChallengeBroker(store, clock=lambda: now[0])
    challenge = broker.issue('192.168.1.109', 'c'*32)
    packet = seal_packet(card, challenge, 'c' * 32, {}, direction='request')
    now[0] += 31
    with pytest.raises(LocalProtocolError):
        broker.consume(packet)


def test_restart_does_not_reuse_nonces_or_accept_old_challenges(tmp_path, card):
    path = tmp_path / 'private' / 'sessions.sqlite3'
    first = LocalPairingStore(path, card=card, serial='original_serial', initialize=True)
    broker = ChallengeBroker(first)
    challenge = broker.issue('192.168.1.109', 'c'*32)
    old = seal_packet(card, challenge, 'c' * 32, {}, direction='request')
    first.close()
    second = LocalPairingStore(path, card=card, serial='original_serial')
    try:
        restarted = ChallengeBroker(second)
        assert restarted.issue('192.168.1.109', 'c'*32)['requestNonce'] != challenge['requestNonce']
        with pytest.raises(LocalProtocolError):
            restarted.consume(old)
    finally:
        second.close()


def test_sessions_are_not_wechat_and_database_has_no_plain_token(store, card):
    issued = store.issue_local_session()
    assert issued.user_id.startswith('loc_') and len(issued.user_id) <= 104
    assert store.resolve_session(issued.access_token, '').user_id == issued.user_id
    assert store.resolve_session(issued.access_token, 'wx' + '0' * 16) is None
    assert store.authorize(issued.user_id, store.pet_id)
    assert not store.authorize('usr_forged', store.pet_id)
    assert store.registered_device_matches(store.pet_id, 'original_serial')
    assert not store.registered_device_matches(store.pet_id, 'other_serial')
    assert issued.access_token.encode() not in store.path.read_bytes()
    assert card.key_hex.encode() not in store.path.read_bytes()
    store.revoke_session(issued.access_token, '')
    assert store.resolve_session(issued.access_token, '') is None


def test_exclusive_store_and_card_pin(tmp_path, card):
    path = tmp_path / 'private' / 'sessions.sqlite3'
    first = LocalPairingStore(path, card=card, serial='original_serial', initialize=True)
    with pytest.raises((ValueError, OSError)):
        LocalPairingStore(path, card=card, serial='original_serial')
    first.close()
    with pytest.raises(ValueError):
        LocalPairingStore(path, card=card, serial='other_serial')
    with pytest.raises(ValueError):
        LocalPairingStore(path, card=Card(card.device_id, card.card_id, '34' * 32), serial='original_serial')


def test_challenge_budget_is_bounded(store):
    broker = ChallengeBroker(store)
    for index in range(8):
        broker.issue('192.168.1.109', f'{index:032x}')
    with pytest.raises(LocalProtocolError):
        broker.issue('192.168.1.109', 'e'*32)


def test_runtime_never_resets_nonce_store_for_an_existing_card(tmp_path, card):
    path = tmp_path / 'private' / 'sessions.sqlite3'
    with pytest.raises(ValueError):
        LocalPairingStore(path, card=card, serial='original_serial')
    assert not path.exists()
    first = LocalPairingStore(path, card=card, serial='original_serial', initialize=True)
    first.next_counter()
    first.close()
    with pytest.raises(ValueError):
        LocalPairingStore(path, card=card, serial='original_serial', initialize=True)


def test_private_store_escapes_uri_metacharacters(tmp_path, card):
    path = tmp_path / 'private' / 'sessions?#.sqlite3'
    store = LocalPairingStore(path, card=card, serial='original_serial', initialize=True)
    try:
        assert store.next_counter() == 1
        assert store.path == path
    finally:
        store.close()


def test_existing_missing_nonce_row_is_never_repaired(tmp_path, card):
    path = tmp_path / 'private' / 'sessions.sqlite3'
    store = LocalPairingStore(path, card=card, serial='original_serial', initialize=True)
    store.next_counter()
    store.close()
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM lg_counter')
    with pytest.raises(ValueError):
        LocalPairingStore(path, card=card, serial='original_serial')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM lg_counter').fetchone() == (0,)


def test_untrusted_or_replayed_challenge_cannot_reuse_nonce_on_a_new_client(store, card):
    broker = ChallengeBroker(store)
    old = broker.issue('192.168.1.109', 'c'*32)
    # A new phone visit must choose a fresh request ID before it receives any
    # server challenge. Replaying a real old proof cannot cause an encryption.
    with pytest.raises(LocalProtocolError):
        seal_packet(card, old, 'd'*32, {'private':'new data'}, direction='request')
    forged = copy.deepcopy(old)
    forged['requestId'] = 'd'*32
    with pytest.raises(LocalProtocolError):
        seal_packet(card, forged, 'd'*32, {'private':'new data'}, direction='request')
    fresh = broker.issue('192.168.1.109', 'd'*32)
    fresh['requestNonce'] = old['requestNonce']
    with pytest.raises(LocalProtocolError):
        seal_packet(card, fresh, 'd'*32, {'private':'new data'}, direction='request')


def test_shared_synthetic_cross_language_vector():
    fixture = Path(__file__).resolve().parents[2]/'miniprogram/scripts/fixtures/local-gateway-aesgcm-v1.json'
    value = json.loads(fixture.read_text())
    assert value['fixture'] == 'synthetic-only-not-a-real-device-card'
    card = Card.parse(value['card'])
    assert seal_packet(card,value['challenge'],value['request']['requestId'],value['requestPlain'],direction='request') == value['request']
    assert open_packet(card,value['request'],direction='request') == value['requestPlain']
    assert open_packet(card,value['response'],direction='response') == value['responsePlain']
