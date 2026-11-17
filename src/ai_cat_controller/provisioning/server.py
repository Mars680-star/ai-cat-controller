"""Fixed SoftAP TCP listener for authenticated, encrypted, bounded frames.

No AP/network changes occur on import or construction. Only an explicit
start() binds 10.47.0.1:8766. It is intentionally unrelated to the web API key,
cloud product ownership and the localhost development gateway.
"""
import asyncio
import ipaddress

from .protocol import AP_ADDRESS, TCP_PORT, MAX_FRAME, ProvisioningError, encode_frame, parse_json, require


class ProvisioningTCPServer:
    def __init__(self, window):
        self.window, self._server = window, None
        self._connections = set()

    async def start(self):
        require(self._server is None)
        self._server = await asyncio.start_server(self.handle_connection, host=AP_ADDRESS, port=TCP_PORT, limit=MAX_FRAME + 4)
        return self._server

    async def handle_connection(self, reader, writer):
        if len(self._connections) >= 4:
            writer.close()
            return
        self._connections.add(writer)
        pending_id = None
        try:
            peer, local = writer.get_extra_info("peername"), writer.get_extra_info("sockname")
            require(type(peer) is tuple and type(local) is tuple and local[0] == AP_ADDRESS and local[1] == TCP_PORT)
            address = ipaddress.ip_address(peer[0])
            require(address in ipaddress.ip_network("10.47.0.0/24") and
                    str(address) not in {"10.47.0.0", AP_ADDRESS, "10.47.0.255"})
            # At most four packets per socket; client explicitly opens a fresh
            # status connection instead of holding an unbounded credential pipe.
            for _ in range(4):
                header = await asyncio.wait_for(reader.readexactly(4), 5)
                length = int.from_bytes(header, "big")
                require(1 <= length <= MAX_FRAME)
                body = await asyncio.wait_for(reader.readexactly(length), 5)
                packet = parse_json(body)
                response = await self.window.process_packet(packet)
                pending_id = packet["requestId"] if packet["kind"] == "configure" else None
                writer.write(encode_frame(response))
                await asyncio.wait_for(writer.drain(), 5)
                if pending_id is not None:
                    self.window.start_pending(pending_id)
                    pending_id = None
        except (Exception, asyncio.CancelledError):
            if pending_id is not None:
                self.window.abort_pending(pending_id)
            # No raw error, QR, key, SSID, password, frame or peer is logged.
        finally:
            self._connections.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def close(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._connections):
            writer.close()
        await self.window.close()
