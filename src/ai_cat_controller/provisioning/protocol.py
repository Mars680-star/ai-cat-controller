"""Bounded AES-256-GCM packets for a locally authorized provisioning window.

The ephemeral QR key is a possession credential. It must only be displayed by
the local operator/physical-presence workflow, never a public discovery route.
This protocol does not grant cloud ownership or select a caller-supplied URL.
"""
from dataclasses import dataclass, field
import json
import re
import secrets
import unicodedata
import base64

PROTOCOL = "aicat-provision-v1"
AP_ADDRESS = "10.47.0.1"
TCP_PORT = 8766
MAX_FRAME = 4096
DEVICE_ID = re.compile(r"dev_[0-9a-f]{32}")
SESSION_ID = re.compile(r"[0-9a-f]{32}")
REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
QR_PATTERN = re.compile(r"AICAT-PROVISION:1:(dev_[0-9a-f]{32}):([0-9a-f]{32}):([0-9a-f]{64})")
RECEIPT_QR_PREFIX = "AICAT-PROVISION-RECEIPT:1:"
STATUSES = {"accepted", "connecting", "connected", "failed", "unknown", "not_sent"}


class ProvisioningError(ValueError):
    def __init__(self):
        super().__init__("Provisioning request was not verified")


def require(condition):
    if not condition:
        raise ProvisioningError()


def exact(value, fields):
    require(type(value) is dict and set(value) == set(fields))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def parse_json(data):
    require(type(data) is bytes and 1 <= len(data) <= MAX_FRAME)
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ProvisioningError()))
        require(type(value) is dict)
        return value
    except Exception:
        raise ProvisioningError() from None


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class Invitation:
    device_id: str
    session_id: str
    key_hex: str = field(repr=False)

    def __post_init__(self):
        require(type(self.device_id) is str and DEVICE_ID.fullmatch(self.device_id) is not None)
        require(type(self.session_id) is str and SESSION_ID.fullmatch(self.session_id) is not None)
        require(type(self.key_hex) is str and re.fullmatch("[0-9a-f]{64}", self.key_hex) is not None)

    @classmethod
    def parse(cls, value):
        require(type(value) is str and len(value) <= 192)
        match = QR_PATTERN.fullmatch(value)
        require(match is not None)
        return cls(*match.groups())

    def encode(self):
        return "AICAT-PROVISION:1:" + self.device_id + ":" + self.session_id + ":" + self.key_hex


def _plain_text(value):
    require(type(value) is str and not any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value))
    return value.encode("utf-8")


@dataclass(frozen=True)
class NetworkCredentials:
    ssid: str = field(repr=False)
    password: str = field(repr=False)
    security: str = "wpa2-personal"

    @classmethod
    def validate(cls, value):
        exact(value, ["ssid", "password", "security"])
        require(value["security"] == "wpa2-personal")
        ssid, password = _plain_text(value["ssid"]), _plain_text(value["password"])
        require(1 <= len(ssid) <= 32)
        require(8 <= len(password) <= 63 or
                len(password) == 64 and re.fullmatch("[0-9a-fA-F]{64}", value["password"]) is not None)
        return cls(value["ssid"], value["password"], value["security"])

    def payload(self):
        return {"ssid": self.ssid, "password": self.password, "security": self.security}


def packet_header(invitation, request_id, kind, direction):
    require(type(request_id) is str and REQUEST_ID.fullmatch(request_id) is not None)
    require(kind in {"configure", "status"} and direction in {"request", "response"})
    return [PROTOCOL, direction, invitation.device_id, invitation.session_id, request_id, kind]


def validate_packet(value, *, direction):
    exact(value, ["v", "deviceId", "sessionId", "requestId", "kind", "nonce", "ciphertext"])
    require(type(value["v"]) is int and value["v"] == 1)
    require(type(value["deviceId"]) is str and DEVICE_ID.fullmatch(value["deviceId"]) is not None)
    require(type(value["sessionId"]) is str and SESSION_ID.fullmatch(value["sessionId"]) is not None)
    require(type(value["requestId"]) is str and REQUEST_ID.fullmatch(value["requestId"]) is not None)
    require(value["kind"] in {"configure", "status"})
    require(type(value["nonce"]) is str and re.fullmatch("[0-9a-f]{24}", value["nonce"]) is not None)
    require(value["nonce"].startswith("00" if direction == "request" else "01"))
    require(type(value["ciphertext"]) is str and len(value["ciphertext"]) % 2 == 0 and
            re.fullmatch("[0-9a-f]{32,3072}", value["ciphertext"]) is not None)
    require(len(canonical(value)) <= MAX_FRAME)
    return value


def encode_packet(invitation, request_id, kind, body, *, direction, nonce=None):
    # Separate nonce domains prevent reflection and cross-direction collisions.
    header = packet_header(invitation, request_id, kind, direction)
    if nonce is None:
        nonce = (b"\0" if direction == "request" else b"\1") + secrets.token_bytes(11)
    require(type(nonce) is bytes and len(nonce) == 12 and nonce[0] == (0 if direction == "request" else 1))
    plaintext = canonical(body)
    require(len(plaintext) <= 1520)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ciphertext = AESGCM(bytes.fromhex(invitation.key_hex)).encrypt(nonce, plaintext, canonical(header))
    return validate_packet({"v": 1, "deviceId": invitation.device_id, "sessionId": invitation.session_id,
                            "requestId": request_id, "kind": kind, "nonce": nonce.hex(),
                            "ciphertext": ciphertext.hex()}, direction=direction)


def decode_packet(invitation, packet, *, direction):
    try:
        validate_packet(packet, direction=direction)
        require(packet["deviceId"] == invitation.device_id and packet["sessionId"] == invitation.session_id)
        header = packet_header(invitation, packet["requestId"], packet["kind"], direction)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        body = AESGCM(bytes.fromhex(invitation.key_hex)).decrypt(
            bytes.fromhex(packet["nonce"]), bytes.fromhex(packet["ciphertext"]), canonical(header))
        return packet, parse_json(body)
    except Exception:
        raise ProvisioningError() from None


def encode_frame(packet):
    data = canonical(packet)
    require(1 <= len(data) <= MAX_FRAME)
    return len(data).to_bytes(4, "big") + data


def encode_receipt_qr(packet):
    validate_packet(packet, direction="response")
    require(packet["kind"] == "configure")
    encoded = base64.urlsafe_b64encode(canonical(packet)).decode("ascii").rstrip("=")
    require(len(encoded) <= 2000)
    return RECEIPT_QR_PREFIX + encoded
