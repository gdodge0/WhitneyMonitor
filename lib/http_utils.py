"""
http_utils.py (shared HTTP helpers)

General-purpose async HTTP utilities that can be imported by higher‑level
modules.  Provides the fire_and_forget helper for one‑off requests.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

__all__ = ["fire_and_forget"]


async def _request(ac: httpx.AsyncClient, **kwargs) -> None:  # pragma: no cover
    """Internal generic request wrapper with exception logging."""
    try:
        await ac.request(**kwargs)
    except Exception:
        logging.exception("[fire-and-forget] Request failed")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def fire_and_forget(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[Any] = None,
    json: Optional[Any] = None,
    params: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    client: httpx.AsyncClient | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> asyncio.Task:
    """Schedule an HTTP request on the current event loop and return the *Task*.

    The returned *asyncio.Task* can be ignored for fire‑and‑forget semantics or
    retained for later inspection/awaiting/cancellation.

    If *client* is omitted, a short‑lived :class:`httpx.AsyncClient` is created
    and closed automatically inside the task.  When issuing many requests, pass
    a shared client for connection reuse.
    """

    loop = loop or asyncio.get_running_loop()

    if client is None:
        async def _wrapper() -> None:  # pragma: no cover
            async with httpx.AsyncClient(timeout=timeout) as ac:
                await _request(
                    ac,
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    json=json,
                    params=params,
                    timeout=timeout,
                )

        return loop.create_task(_wrapper())

    return loop.create_task(
        _request(
            client,
            method=method,
            url=url,
            headers=headers,
            data=data,
            json=json,
            params=params,
            timeout=timeout,
        )
    )
