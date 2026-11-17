"""Bounded, fixed-origin code2session without high-level request URL logging."""

import asyncio
from dataclasses import dataclass, field
import json
import math
import re
from typing import Protocol

import httpx


class IdentityProvider(Protocol):
    async def exchange(self, code: str) -> str: ...


class WeChatUnavailable(Exception):
    pass


@dataclass(repr=False)
class WeChatCodeProvider:
    app_id: str = field(repr=False)
    app_secret: str = field(repr=False)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 3.0

    def __repr__(self) -> str:
        return "WeChatCodeProvider(credentials=<redacted>)"

    async def exchange(self, code: str) -> str:
        async def read() -> str:
            transport = self.transport if self.transport is not None else httpx.AsyncHTTPTransport(trust_env=False, retries=0)
            # Direct transport avoids httpx.AsyncClient's INFO URL logging. It has
            # no redirect machinery; a 3xx response is rejected, never followed.
            async with transport:
                request = httpx.Request("GET", "https://api.weixin.qq.com/sns/jscode2session", headers={"Accept-Encoding": "identity"}, params={
                    "appid": self.app_id, "secret": self.app_secret, "js_code": code,
                    "grant_type": "authorization_code",
                })
                response = await transport.handle_async_request(request)
                try:
                    if response.status_code != 200:
                        raise ValueError("Identity provider unavailable")
                    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                        raise ValueError("Identity provider unavailable")
                    length = response.headers.get("content-length")
                    if length is not None and not 0 <= int(length) <= 32768:
                        raise ValueError("Identity provider unavailable")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 32768:
                            raise ValueError("Identity provider unavailable")
                        raw.extend(chunk)
                    data = json.loads(raw)
                    if not isinstance(data, dict) or data.get("errcode", 0) != 0:
                        raise ValueError("Identity provider unavailable")
                    openid = data.get("openid")
                    if not isinstance(openid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", openid):
                        raise ValueError("Identity provider unavailable")
                    return openid
                finally:
                    await response.aclose()

        try:
            if isinstance(self.timeout_seconds, bool) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 10:
                raise ValueError("Invalid deadline")
            return await asyncio.wait_for(read(), timeout=self.timeout_seconds)
        except Exception:
            pass
        # Outside the except block: no raw provider exception/request retained as
        # chained context, and callers receive no codes, URLs or provider bodies.
        raise WeChatUnavailable("WeChat login is temporarily unavailable")
