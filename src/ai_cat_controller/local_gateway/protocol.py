"""Per-device physical card and bounded, challenge-bound AES-256-GCM RPC.

No shared WeChat secret or device API key belongs in this protocol. A physical
card is a local possession credential, not cloud ownership or a network QR.
Nonce counters are durably allocated before a challenge leaves the device.
"""
from dataclasses import dataclass, field
import ipaddress
import json
import re
import secrets
import time

PROTOCOL = 'aicat-local-v1'
PORT = 8789
MAX_REQUEST = 8192
MAX_RESPONSE = 2 * 1024 * 1024
MAX_PACKET = 20 * 1024
DEVICE = re.compile(r'dev_[0-9a-f]{32}')
HEX_ID = re.compile(r'[0-9a-f]{32}')
_CARD = re.compile(r'AICAT-LOCAL:1:(dev_[0-9a-f]{32}):([0-9a-f]{32}):([0-9a-f]{64})')
_NETWORKS = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


class LocalProtocolError(ValueError):
    def __init__(self):
        super().__init__('Local request was not verified')


def require(value):
    if not value:
        raise LocalProtocolError()


def exact(value, fields):
    require(type(value) is dict and set(value) == set(fields))


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        raise LocalProtocolError() from None


def parse_json(raw, *, maximum=MAX_PACKET):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    try:
        require(type(raw) is bytes and 0 < len(raw) <= maximum)
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(LocalProtocolError()))
        require(type(value) is dict)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise LocalProtocolError() from None


def validate_address(value):
    try:
        require(type(value) is str)
        address = ipaddress.IPv4Address(value)
        require(str(address) == value and any(address in network for network in _NETWORKS))
        require(value.rsplit('.', 1)[1] not in {'0', '255'})
        return value
    except (ValueError, TypeError):
        raise LocalProtocolError() from None


@dataclass(frozen=True)
class Card:
    device_id: str
    card_id: str
    key_hex: str = field(repr=False)

    def __post_init__(self):
        require(type(self.device_id) is str and DEVICE.fullmatch(self.device_id))
        require(type(self.card_id) is str and HEX_ID.fullmatch(self.card_id))
        require(type(self.key_hex) is str and re.fullmatch(r'[0-9a-f]{64}', self.key_hex))

    @classmethod
    def parse(cls, value):
        require(type(value) is str and len(value) <= 192)
        match = _CARD.fullmatch(value)
        require(match is not None)
        return cls(*match.groups())

    def encode(self):
        return f'AICAT-LOCAL:1:{self.device_id}:{self.card_id}:{self.key_hex}'


def validate_challenge(value, card):
    exact(value, ['v', 'deviceId', 'cardId', 'challengeId', 'requestId', 'requestNonce', 'ttlSeconds', 'proof'])
    require(type(value['v']) is int and value['v'] == 1 and value['deviceId'] == card.device_id and value['cardId'] == card.card_id)
    require(type(value['challengeId']) is str and HEX_ID.fullmatch(value['challengeId']))
    require(type(value['requestId']) is str and HEX_ID.fullmatch(value['requestId']))
    require(type(value['requestNonce']) is str and re.fullmatch(r'00000000[0-9a-f]{16}', value['requestNonce']))
    require(type(value['ttlSeconds']) is int and value['ttlSeconds'] == 30)
    require(type(value['proof']) is str and re.fullmatch(r'[0-9a-f]{32}', value['proof']))
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        proof_nonce = bytes.fromhex('02' + value['requestNonce'][2:])
        require(AESGCM(bytes.fromhex(card.key_hex)).decrypt(proof_nonce,
            bytes.fromhex(value['proof']), challenge_aad(value)) == b'')
    except Exception:
        raise LocalProtocolError() from None
    return value


def challenge_aad(value):
    return canonical([PROTOCOL, 'challenge', value['deviceId'], value['cardId'],
        value['challengeId'], value['requestId'], value['requestNonce'], value['ttlSeconds']])


def validate_packet(packet, *, direction):
    exact(packet, ['v', 'deviceId', 'cardId', 'challengeId', 'requestId', 'nonce', 'ciphertext'])
    require(type(packet['v']) is int and packet['v'] == 1)
    require(type(packet['deviceId']) is str and DEVICE.fullmatch(packet['deviceId']))
    for key in ('cardId', 'challengeId', 'requestId'):
        require(type(packet[key]) is str and HEX_ID.fullmatch(packet[key]))
    require(direction in {'request', 'response'})
    prefix = '00000000' if direction == 'request' else '01000000'
    require(type(packet['nonce']) is str and re.fullmatch(prefix + r'[0-9a-f]{16}', packet['nonce']))
    maximum = MAX_REQUEST if direction == 'request' else MAX_RESPONSE
    value = packet['ciphertext']
    require(type(value) is str and 32 <= len(value) <= 2 * (maximum + 16) and len(value) % 2 == 0)
    require(re.fullmatch(r'[0-9a-f]+', value))
    if direction == 'request':
        require(len(canonical(packet)) <= MAX_PACKET)
    return packet


def aad(packet, direction):
    return canonical([PROTOCOL, direction, packet['deviceId'], packet['cardId'], packet['challengeId'], packet['requestId']])


def seal_packet(card, challenge, request_id, body, *, direction):
    validate_challenge(challenge, card)
    require(type(request_id) is str and HEX_ID.fullmatch(request_id))
    require(request_id == challenge['requestId'])
    require(direction in {'request', 'response'} and type(body) is dict)
    packet = dict(v=1, deviceId=card.device_id, cardId=card.card_id,
        challengeId=challenge['challengeId'], requestId=request_id,
        nonce=('00' if direction == 'request' else '01') + challenge['requestNonce'][2:])
    plain = canonical(body)
    require(len(plain) <= (MAX_REQUEST if direction == 'request' else MAX_RESPONSE))
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    packet['ciphertext'] = AESGCM(bytes.fromhex(card.key_hex)).encrypt(
        bytes.fromhex(packet['nonce']), plain, aad(packet, direction)).hex()
    return validate_packet(packet, direction=direction)


def open_packet(card, packet, *, direction):
    try:
        validate_packet(packet, direction=direction)
        require(packet['deviceId'] == card.device_id and packet['cardId'] == card.card_id)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        plain = AESGCM(bytes.fromhex(card.key_hex)).decrypt(
            bytes.fromhex(packet['nonce']), bytes.fromhex(packet['ciphertext']), aad(packet, direction))
        return parse_json(plain, maximum=MAX_REQUEST if direction == 'request' else MAX_RESPONSE)
    except Exception:
        raise LocalProtocolError() from None


class ChallengeBroker:
    def __init__(self, store, *, clock=time.monotonic):
        self.store, self.card, self.clock = store, store.card, clock
        self._active, self._consumed, self._limits = {}, {}, {}

    def _prune(self):
        now = self.clock()
        self._active = {k: v for k, v in self._active.items() if v[1] > now}
        self._consumed = {k: v for k, v in self._consumed.items() if v[1] > now}
        self._limits = {k: v for k, v in self._limits.items() if v[0] + 1 > now}

    def issue(self, peer, request_id):
        self._prune()
        require(type(peer) is str and len(peer) <= 64 and len(self._active) < 64)
        require(len(self._active) + len(self._consumed) < 256)
        require(type(request_id) is str and HEX_ID.fullmatch(request_id))
        require(not any(v[2] == request_id for v in self._active.values()))
        require(not any(v[0] == request_id for v in self._consumed.values()))
        now = self.clock()
        require(peer in self._limits or len(self._limits) < 64)
        start, count = self._limits.get(peer, (now, 0))
        require(count < 8)
        self._limits[peer] = (start, count + 1)
        counter = self.store.next_counter()
        nonce = (b'\0' * 4 + counter.to_bytes(8, 'big')).hex()
        challenge_id = secrets.token_hex(16)
        challenge = dict(v=1, deviceId=self.card.device_id, cardId=self.card.card_id,
            challengeId=challenge_id, requestId=request_id, requestNonce=nonce, ttlSeconds=30)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        challenge['proof'] = AESGCM(bytes.fromhex(self.card.key_hex)).encrypt(
            bytes.fromhex('02' + nonce[2:]), b'', challenge_aad(challenge)).hex()
        self._active[challenge_id] = (nonce, now + 30, request_id, challenge)
        return challenge

    def consume(self, packet):
        self._prune()
        validate_packet(packet, direction='request')
        value = self._active.get(packet['challengeId'])
        require(value is not None and value[0] == packet['nonce'] and value[2] == packet['requestId'])
        body = open_packet(self.card, packet, direction='request')
        del self._active[packet['challengeId']]
        self._consumed[packet['challengeId']] = (packet['requestId'], value[1], value[0], False, value[3])
        return body

    def reply(self, packet, body):
        value = self._consumed.get(packet['challengeId'])
        require(value is not None and value[0] == packet['requestId'] and not value[3])
        self._consumed[packet['challengeId']] = (*value[:3], True, value[4])
        challenge = value[4]
        return seal_packet(self.card, challenge, packet['requestId'], body, direction='response')
