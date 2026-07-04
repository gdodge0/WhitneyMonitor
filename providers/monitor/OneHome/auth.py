"""
OneHome authentication.

The durable credential is the **share/email token** (the base64 blob from a
OneHome portal share link). The GraphQL API instead wants a short-lived Bearer
**JWT** (``sessionToken``) that we mint from the share token via::

    POST https://services.onehome.com/api/authentication/checkToken
    {"emailToken": "<share token>"}

The returned JWT has no ``exp`` claim and a short server-side lifetime, so we
cannot inspect its expiry. ``OneHomeAuth`` therefore caches it with a proactive
soft TTL and also supports a reactive :meth:`refresh` (called by the fetch layer
on an HTTP 401).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import httpx

from .http import DEFAULT_HEADERS

DEFAULT_CHECK_TOKEN_URL = "https://services.onehome.com/api/authentication/checkToken"


class OneHomeAuth:
    def __init__(
        self,
        share_token: str,
        check_token_url: str = DEFAULT_CHECK_TOKEN_URL,
        *,
        token_ttl: int = 1500,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 15.0,
    ) -> None:
        self.share_token = share_token
        self.check_token_url = check_token_url
        self.token_ttl = token_ttl
        self.timeout = timeout
        # checkToken takes no Authorization header.
        self._headers = {k: v for k, v in (headers or DEFAULT_HEADERS).items() if k != "Authorization"}

        self._jwt: Optional[str] = None
        self._obtained_at: float = 0.0
        self.metadata: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def _is_stale(self) -> bool:
        return self._jwt is None or (time.monotonic() - self._obtained_at) > self.token_ttl

    async def get_token(self) -> str:
        """Return a cached JWT, minting a fresh one if missing or past the soft TTL."""
        if self._is_stale():
            return await self.refresh(after=self._jwt)
        return self._jwt  # type: ignore[return-value]

    async def refresh(self, *, after: Optional[str] = None) -> str:
        """Mint a fresh JWT from the share token via ``checkToken``.

        ``after`` is the token the caller found unsatisfactory (stale or 401'd).
        If, once we hold the lock, the cached token has already been replaced by
        a peer, we return that newer token instead of minting redundantly. Called
        with ``after=None`` it always mints.

        Raises ``RuntimeError`` on failure (never echoing the token itself).
        """
        async with self._lock:
            # A peer may have refreshed past the token we were unhappy with.
            if after is not None and self._jwt is not None and self._jwt != after:
                return self._jwt

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        self.check_token_url,
                        json={"emailToken": self.share_token},
                        headers=self._headers,
                    )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"checkToken request failed: {exc}") from exc

            if resp.status_code >= 400:
                raise RuntimeError(f"checkToken returned HTTP {resp.status_code}: {resp.text[:200]}")

            try:
                body = resp.json()
            except ValueError as exc:
                raise RuntimeError("checkToken returned a non-JSON body") from exc

            jwt = body.get("sessionToken")
            if not jwt:
                raise RuntimeError("checkToken response missing 'sessionToken'")

            self._jwt = jwt
            self._obtained_at = time.monotonic()
            # Keep non-secret metadata around for potential future use.
            self.metadata = {
                k: body.get(k)
                for k in ("groupID", "savedSearchID", "mlsID", "agentID")
            }
            return jwt
