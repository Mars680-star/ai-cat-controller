"""One short locally opened window; no networking, passwords on disk, or shell.

The caller owns real physical/operator authorization, AP setup and a verified
network backend. This module never treats a boolean "secure" flag as evidence.
Only start_pending() (after the encrypted acceptance has actually flushed) can
call that backend. Queries and replayed configure IDs never start it.
"""
import asyncio
import hashlib
import hmac
import secrets
import time

from .protocol import (
    DEVICE_ID, Invitation, NetworkCredentials, ProvisioningError, canonical,
    decode_packet, encode_packet, encode_receipt_qr, exact, require,
)


class ProvisioningWindow:
    def __init__(self, device_id, backend, *, ttl_seconds=180, clock=time.monotonic):
        require(type(device_id) is str and DEVICE_ID.fullmatch(device_id) is not None)
        require(callable(backend) and type(ttl_seconds) is int and 30 <= ttl_seconds <= 300)
        self.device_id, self._backend, self._ttl, self._clock = device_id, backend, ttl_seconds, clock
        self._invitation = None
        self._deadline, self._receipt_deadline = 0.0, 0.0
        self._request_id, self._digest, self._credentials, self._status = None, None, None, "closed"
        self._nonces, self._response_nonces = set(), set()
        self._task = None

    def open(self):
        """Local operator only: return private QR contents, not a public route."""
        require(self._status not in {"accepted", "connecting", "unknown"})
        require(self._task is None or self._task.done())
        self._invitation = Invitation(self.device_id, secrets.token_hex(16), secrets.token_hex(32))
        self._deadline = self._clock() + self._ttl
        self._receipt_deadline = self._deadline + 600
        self._request_id, self._digest, self._credentials, self._status = None, None, None, "open"
        self._nonces.clear()
        self._response_nonces.clear()
        return self._invitation.encode()

    async def process_packet(self, packet):
        require(self._invitation is not None and self._clock() < self._receipt_deadline)
        _, body = decode_packet(self._invitation, packet, direction="request")
        require(packet["nonce"] not in self._nonces and len(self._nonces) < 64)
        self._nonces.add(packet["nonce"])
        request_id, kind = packet["requestId"], packet["kind"]
        if kind == "configure":
            credentials = NetworkCredentials.validate(body)
            digest = hmac.new(bytes.fromhex(self._invitation.key_hex), canonical(credentials.payload()), hashlib.sha256).digest()
            if self._request_id is not None:
                require(request_id == self._request_id and hmac.compare_digest(digest, self._digest))
            else:
                require(self._status == "open" and self._clock() < self._deadline)
                self._request_id, self._digest, self._credentials, self._status = request_id, digest, credentials, "accepted"
        else:
            exact(body, [])
            require(self._request_id is not None and request_id == self._request_id)
        if self._status == "accepted" and self._clock() >= self._deadline:
            self.abort_pending(request_id)
        return self._response_packet(request_id, kind)

    def _response_packet(self, request_id, kind):
        # TCP and operator-displayed receipt QR share a nonce domain, generator
        # and budget. Never duplicate this logic to mint a second response path.
        require(self._invitation is not None and self._request_id == request_id)
        require(self._clock() < self._receipt_deadline and len(self._response_nonces) < 64)
        for _ in range(8):
            nonce = b"\1" + secrets.token_bytes(11)
            if nonce not in self._response_nonces:
                self._response_nonces.add(nonce)
                return encode_packet(self._invitation, request_id, kind, {"status": self._status}, direction="response", nonce=nonce)
        raise ProvisioningError()

    def receipt_packet(self):
        """Local operator only; the original private key is never returned."""
        require(self._request_id is not None and self._status != "closed")
        if self._status == "accepted" and self._clock() >= self._deadline:
            self.abort_pending(self._request_id)
        return self._response_packet(self._request_id, "configure")

    def receipt_qr(self):
        """Encrypted original receipt, useful after station/AP address changes."""
        return encode_receipt_qr(self.receipt_packet())

    def start_pending(self, request_id):
        """The TCP writer calls this only after drain() succeeds, never on GET."""
        if self._request_id != request_id or self._status != "accepted":
            return None
        if self._clock() >= self._deadline:
            self.abort_pending(request_id)
            return None
        credentials, self._credentials = self._credentials, None
        self._status = "connecting"
        async def apply():
            try:
                result = await self._backend(credentials, request_id)
                self._status = result if result in {"connected", "failed", "unknown", "not_sent"} else "unknown"
            except BaseException:
                # A backend may have changed Wi-Fi before failing/cancellation.
                # Never claim failure proves nothing happened or call it twice.
                self._status = "unknown"
        self._task = asyncio.create_task(apply())
        return self._task

    def abort_pending(self, request_id):
        if self._request_id == request_id and self._status == "accepted":
            self._credentials, self._status = None, "not_sent"

    async def settle(self):
        if self._task is not None:
            await asyncio.shield(self._task)

    async def close(self):
        await self.settle()
        self._invitation, self._credentials = None, None
        self._nonces.clear()
        self._response_nonces.clear()
        self._status = "closed"
