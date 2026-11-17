"""Provisioning vectors and state tests: synthetic keys, no device/network calls."""
import asyncio
import base64
import json
from pathlib import Path
import pytest

from ai_cat_controller.provisioning.protocol import (
    ProvisioningError, Invitation, NetworkCredentials, PROTOCOL,
    encode_packet, decode_packet, encode_frame, parse_json, MAX_FRAME, RECEIPT_QR_PREFIX,
)
from ai_cat_controller.provisioning.window import ProvisioningWindow
from ai_cat_controller.provisioning.server import ProvisioningTCPServer


DEVICE = "dev_" + "1" * 32
KEY = "2" * 64
SESSION = "3" * 32
QR = "AICAT-PROVISION:1:" + DEVICE + ":" + SESSION + ":" + KEY


def invitation():
    return Invitation.parse(QR)


def request(invite, kind="configure", request_id="wifi-test", nonce=1, **body):
    content = {"ssid": "测试 Wi-Fi ", "password": "synthetic-pass", "security": "wpa2-personal"} if kind == "configure" else {}
    content.update(body)
    return encode_packet(invite, request_id, kind, content, direction="request", nonce=b"\0" + nonce.to_bytes(11, "big"))


def unpack(invite, packet):
    return decode_packet(invite, packet, direction="response")[1]


def test_qr_is_strict_fixed_protocol_and_never_accepts_url_or_extra_fields():
    value = invitation()
    assert value.device_id == DEVICE and value.session_id == SESSION
    assert KEY not in repr(value)
    assert value.encode() == QR
    for invalid in [QR + ":extra", QR + "\n", QR.replace(":1:", ":2:"), "http://10.47.0.1/" + QR, QR.replace(DEVICE, "7c2b63fd4a128"), QR.lower(), QR + "?url=http://evil"]:
        with pytest.raises(ProvisioningError):
            Invitation.parse(invalid)


@pytest.mark.parametrize("ssid,password", [("家庭网络", "密码示例123"), ("a" * 32, "a" * 63), ("末尾空格 ", " trailing "), ("猫" * 10, "a" * 64)])
def test_supported_wifi_values_are_bounded_preserved_and_hidden_in_repr(ssid, password):
    value = NetworkCredentials.validate({"ssid": ssid, "password": password, "security": "wpa2-personal"})
    assert value.ssid == ssid and value.password == password
    assert ssid not in repr(value) and password not in repr(value)


@pytest.mark.parametrize("patch", [
    {"ssid": ""}, {"ssid": "猫" * 11}, {"ssid": "a\nb"}, {"ssid": "\u202eabc"},
    {"password": "short"}, {"password": "z" * 64}, {"password": "a\0bcccccc"},
    {"security": "open"}, {"url": "http://evil"}, {"password": 123},
])
def test_invalid_wifi_credentials_never_reach_backend(patch):
    raw = {"ssid": "home", "password": "synthetic-pass", "security": "wpa2-personal", **patch}
    with pytest.raises(ProvisioningError):
        NetworkCredentials.validate(raw)


def test_aead_authenticates_kind_direction_session_request_id_and_all_ciphertext():
    invite = invitation()
    packet = request(invite)
    original, body = decode_packet(invite, packet, direction="request")
    assert original == packet and body["ssid"] == "测试 Wi-Fi "
    assert "synthetic-pass" not in json.dumps(packet) and "测试" not in json.dumps(packet)
    for field, value in [("kind", "status"), ("requestId", "another"), ("sessionId", "a" * 32),
                         ("deviceId", "dev_" + "a" * 32), ("nonce", "01" + packet["nonce"][2:]),
                         ("ciphertext", packet["ciphertext"][:-2] + "00"), ("v", True)]:
        with pytest.raises(ProvisioningError):
            decode_packet(invite, {**packet, field: value}, direction="request")
    with pytest.raises(ProvisioningError):
        decode_packet(invite, packet, direction="response")


def test_python_aesgcm_matches_shared_node_noble_reference_vector():
    vector = json.loads((Path(__file__).parents[2] / "miniprogram/scripts/fixtures/provisioning-aesgcm-v1.json").read_text())
    invite = Invitation(vector["deviceId"], vector["sessionId"], vector["keyHex"])
    packet = encode_packet(invite, vector["requestId"], vector["kind"], vector["body"],
                           direction="request", nonce=bytes.fromhex(vector["nonceHex"]))
    assert packet["ciphertext"] == vector["ciphertextHex"]
    assert decode_packet(invite, packet, direction="request")[1] == vector["body"]


def test_frames_and_json_reject_oversize_duplicate_keys_and_nonobjects():
    packet = request(invitation())
    frame = encode_frame(packet)
    assert int.from_bytes(frame[:4], "big") == len(frame) - 4 <= MAX_FRAME
    assert parse_json(frame[4:]) == packet
    for value in [b'{"v":1,"v":1}', b'[]', b'null', b'{"v":NaN}', b'x' * (MAX_FRAME + 1), b"\xff"]:
        with pytest.raises(ProvisioningError):
            parse_json(value)


@pytest.mark.asyncio
async def test_configure_is_only_registered_until_response_flush_then_exactly_once():
    calls = []
    async def backend(credentials, request_id):
        calls.append((credentials, request_id))
        return "connected"
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    packet = request(invite)
    reply = await window.process_packet(packet)
    assert unpack(invite, reply) == {"status": "accepted"}
    assert calls == []
    task = window.start_pending("wifi-test")
    assert task is not None
    assert window.start_pending("wifi-test") is None
    await task
    assert len(calls) == 1
    status = await window.process_packet(request(invite, kind="status", nonce=2))
    assert unpack(invite, status) == {"status": "connected"}
    assert len(calls) == 1
    # New nonce, same configure ID returns its receipt, never starts a new task.
    same = await window.process_packet(request(invite, nonce=3))
    assert unpack(invite, same) == {"status": "connected"}
    assert window.start_pending("wifi-test") is None


@pytest.mark.asyncio
async def test_operator_receipt_qr_after_ap_disconnect_is_encrypted_bound_and_readonly():
    calls = []
    async def backend(*_):
        calls.append(1)
        return "connected"
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    with pytest.raises(ProvisioningError):
        window.receipt_qr()
    accepted = await window.process_packet(request(invite))
    first = window.receipt_packet()
    assert unpack(invite, first)["status"] == "accepted" and calls == []
    assert first["nonce"] != accepted["nonce"]
    await window.start_pending("wifi-test")
    qr = window.receipt_qr()
    assert qr.startswith(RECEIPT_QR_PREFIX)
    assert invite.key_hex not in qr and "测试" not in qr and "synthetic-pass" not in qr
    value = qr[len(RECEIPT_QR_PREFIX):]
    packet = parse_json(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    assert packet["kind"] == "configure" and packet["requestId"] == "wifi-test"
    assert unpack(invite, packet) == {"status": "connected"} and calls == [1]
    assert packet["nonce"] not in {accepted["nonce"], first["nonce"]}
    assert window.start_pending("wifi-test") is None and calls == [1]


@pytest.mark.asyncio
async def test_operator_receipts_share_tcp_nonce_budget_and_cannot_mint_after_close():
    async def backend(*_):
        return "unknown"
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    response = await window.process_packet(request(invite))
    seen = {response["nonce"]}
    for _ in range(63):
        packet = window.receipt_packet()
        assert packet["nonce"] not in seen
        seen.add(packet["nonce"])
    assert len(seen) == 64
    with pytest.raises(ProvisioningError):
        window.receipt_packet()
    with pytest.raises(ProvisioningError):
        window.receipt_qr()
    await window.close()
    with pytest.raises(ProvisioningError):
        window.receipt_qr()


@pytest.mark.asyncio
async def test_nonce_replay_changed_payload_and_new_request_are_rejected():
    async def backend(*_):
        return "connected"
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    packet = request(invite)
    await window.process_packet(packet)
    with pytest.raises(ProvisioningError):
        await window.process_packet(packet)
    with pytest.raises(ProvisioningError):
        await window.process_packet(request(invite, nonce=2, password="different"))
    with pytest.raises(ProvisioningError):
        await window.process_packet(request(invite, request_id="second", nonce=3))
    with pytest.raises(ProvisioningError):
        window.open()


@pytest.mark.asyncio
async def test_expired_windows_fail_closed_and_status_is_never_a_second_configuration():
    now = [0.0]
    calls = []
    async def backend(*_):
        calls.append(1)
        return "connected"
    window = ProvisioningWindow(DEVICE, backend, ttl_seconds=30, clock=lambda: now[0])
    invite = Invitation.parse(window.open())
    now[0] = 31
    with pytest.raises(ProvisioningError):
        await window.process_packet(request(invite))
    assert calls == []
    invite = Invitation.parse(window.open())
    with pytest.raises(ProvisioningError):
        await window.process_packet(request(invite, kind="status"))
    assert calls == []


@pytest.mark.asyncio
async def test_backend_failure_or_cancellation_is_unknown_and_cannot_reopen_or_repeat():
    async def backend(*_):
        raise RuntimeError("private ssid/password details")
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    await window.process_packet(request(invite))
    await window.start_pending("wifi-test")
    result = await window.process_packet(request(invite, kind="status", nonce=2))
    assert unpack(invite, result) == {"status": "unknown"}
    assert window.start_pending("wifi-test") is None
    with pytest.raises(ProvisioningError):
        window.open()
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
async def test_status_payload_nonce_budget_and_window_binding_cannot_bypass_one_configuration():
    calls = []
    async def backend(*_):
        calls.append(1)
        return "connected"
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    await window.process_packet(request(invite))
    with pytest.raises(ProvisioningError):
        await window.process_packet(request(invite, kind="status", nonce=2, ssid="hidden write"))
    # A malformed authenticated query consumes its nonce, but never the intent
    # again. A fixed session budget bounds the in-memory replay set.
    for nonce in range(3, 65):
        await window.process_packet(request(invite, kind="status", nonce=nonce))
    with pytest.raises(ProvisioningError):
        await window.process_packet(request(invite, kind="status", nonce=65))
    assert calls == []
    with pytest.raises(ProvisioningError):
        window.open()


@pytest.mark.asyncio
async def test_cancelled_actual_backend_remains_unknown_and_expired_unstarted_intent_is_not_sent():
    entered = asyncio.Event()
    async def backend(*_):
        entered.set()
        await asyncio.Event().wait()
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    await window.process_packet(request(invite))
    task = window.start_pending("wifi-test")
    await entered.wait()
    task.cancel()
    await task
    result = await window.process_packet(request(invite, kind="status", nonce=2))
    assert unpack(invite, result)["status"] == "unknown"
    with pytest.raises(ProvisioningError):
        window.open()
    now = [0]
    fresh = ProvisioningWindow(DEVICE, backend, ttl_seconds=30, clock=lambda: now[0])
    invite = Invitation.parse(fresh.open())
    await fresh.process_packet(request(invite))
    now[0] = 31
    assert fresh.start_pending("wifi-test") is None
    result = await fresh.process_packet(request(invite, kind="status", nonce=2))
    assert unpack(invite, result)["status"] == "not_sent"


class Writer:
    def __init__(self, *, local="10.47.0.1", peer="10.47.0.2", drain_error=False):
        self.local, self.peer, self.drain_error = local, peer, drain_error
        self.writes, self.drains, self.closed = [], 0, False
    def get_extra_info(self, name):
        return (self.peer, 10000) if name == "peername" else (self.local, 8766) if name == "sockname" else None
    def write(self, value):
        self.writes.append(value)
    async def drain(self):
        self.drains += 1
        if self.drain_error:
            raise ConnectionError("private socket failure")
    def close(self):
        self.closed = True
    async def wait_closed(self):
        return None


@pytest.mark.asyncio
async def test_tcp_server_flushes_acceptance_before_backend_and_only_binds_fixed_ap():
    trace = []
    async def backend(*_):
        trace.append(writer.drains)
        return "connected"
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    reader, writer = asyncio.StreamReader(), Writer()
    reader.feed_data(encode_frame(request(invite))); reader.feed_eof()
    server = ProvisioningTCPServer(window)
    await server.handle_connection(reader, writer)
    await window.settle()
    assert trace == [1]
    assert writer.closed
    assert unpack(invite, parse_json(writer.writes[0][4:]))["status"] == "accepted"


@pytest.mark.asyncio
async def test_flush_failure_proves_not_sent_and_wrong_source_cannot_configure():
    calls = []
    async def backend(*_):
        calls.append(1)
        return "connected"
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    reader, writer = asyncio.StreamReader(), Writer(drain_error=True)
    reader.feed_data(encode_frame(request(invite))); reader.feed_eof()
    await ProvisioningTCPServer(window).handle_connection(reader, writer)
    assert calls == []
    response = await window.process_packet(request(invite, kind="status", nonce=2))
    assert unpack(invite, response) == {"status": "not_sent"}
    for local, peer in [("127.0.0.1", "10.47.0.2"), ("10.47.0.1", "192.168.1.2"), ("10.47.0.1", "10.47.0.1")]:
        reader, writer = asyncio.StreamReader(), Writer(local=local, peer=peer)
        reader.feed_data(encode_frame(request(invite, nonce=3))); reader.feed_eof()
        await ProvisioningTCPServer(window).handle_connection(reader, writer)
        assert writer.writes == [] and writer.closed and calls == []


@pytest.mark.asyncio
async def test_tcp_server_bounds_frames_and_length_before_reading_body():
    calls = []
    async def backend(*_):
        calls.append(1)
        return "connected"
    window = ProvisioningWindow(DEVICE, backend)
    invite = Invitation.parse(window.open())
    reader, writer = asyncio.StreamReader(), Writer()
    for nonce in range(1, 6):
        reader.feed_data(encode_frame(request(invite, nonce=nonce)))
    reader.feed_eof()
    await ProvisioningTCPServer(window).handle_connection(reader, writer)
    await window.settle()
    assert len(writer.writes) == 4 and len(calls) == 1 and writer.closed
    reader, writer = asyncio.StreamReader(), Writer()
    reader.feed_data((MAX_FRAME + 1).to_bytes(4, "big"))
    reader.feed_eof()
    await ProvisioningTCPServer(window).handle_connection(reader, writer)
    assert writer.writes == [] and writer.closed
