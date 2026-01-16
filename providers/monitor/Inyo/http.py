"""
range_fetcher.py

Specialised helpers for retrieving payloads across date ranges from the
ACME analytics API.

This module *extends* the generic utilities in ``async_http_utils.py`` by
importing its :pyfunc:`fire_and_forget` helper and exposing a high-level
:pyfunc:`fetch_ranges_concurrently` coroutine that supports **multiple**
endpoints, each with its own list of date ranges (``start_date``/``end_date``
query params, ``payload`` envelope, optional ``error`` field).

🚨 **Breaking change — v2.1.0 (2025-07-03)**

*   Legacy single-endpoint signature was removed in v2.0.0.
*   **Header behaviour changed in v2.1.0:** headers are **not merged**.  If
    *headers* is ``None`` the default browser-style headers are used; otherwise
    the caller-supplied dictionary is sent verbatim.
"""
from __future__ import annotations

import asyncio
import random
import traceback
from collections import defaultdict
from json import JSONDecodeError
from typing import Any, Dict, List, Optional, Tuple

import httpx

# Re-export for convenience; callers can import *either* from the shared
# module or directly from here.
from lib.http_utils import fire_and_forget  # noqa: F401  (re-export)

__all__ = [
    "fetch_ranges_concurrently",
    "fire_and_forget",
    "DEFAULT_HEADERS",
]

# ---------------------------------------------------------------------------
# Default request headers (imitating a modern Firefox browser)
# ---------------------------------------------------------------------------

DEFAULT_HEADERS: Dict[str, str] = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Referer": "https://www.recreation.gov/permits/445860/registration/detailed-availability",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "TE": "trailers",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _fetch(
        ac: httpx.AsyncClient,
        endpoint: str,
        start: str,
        end: str,
        headers: Dict[str, str],
        timeout: float,
) -> Tuple[bool, Any]:
    """Issue a GET for a single date range and return ``(success, result)``."""
    try:
        resp = await ac.get(
            endpoint,
            params={
                "start_date": start,
                "end_date": end,
                "rid": random.randint(0, 9_999_999),
            },
            headers=headers,
            timeout=timeout,
        )

        # Attempt JSON decode first
        try:
            resp_json = resp.json()
        except JSONDecodeError:
            return (
                False,
                f"[post-request/json] error on {start}->{end}. "
                f"Body: {resp.text} Status: {resp.status_code}",
            )

        # API-level error handling
        if "error" in resp_json:
            return (
                False,
                f"[post-request/response] error on {start}->{end}. "
                f"Body: {resp.text} Status: {resp.status_code}",
            )

        return True, resp_json["payload"]

    except Exception as exc:  # noqa: BLE001
        return (
            False,
            f"[request] error on {start}->{end}. "
            f"Trace: {''.join(traceback.format_exception(exc))}",
        )


async def _fetch_tagged(
        ac: httpx.AsyncClient,
        endpoint: str,
        start: str,
        end: str,
        headers: Dict[str, str],
        timeout: float,
) -> Tuple[str, bool, Any]:
    """Wrap :func:`_fetch` to include the *endpoint* in the result tuple."""
    success, result = await _fetch(ac, endpoint, start, end, headers, timeout)
    return endpoint, success, result


# ---------------------------------------------------------------------------
# Public coroutine
# ---------------------------------------------------------------------------

async def fetch_ranges_concurrently(
        endpoints_ranges: Dict[str, List[List[str]]],
        headers: Optional[Dict[str, str]] = None,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Fetch *all* date ranges concurrently across **multiple** endpoints.

    Parameters
    ----------
    endpoints_ranges:
        Mapping ``{endpoint_url: [[start, end], ...], ...}`` where the ranges
        use ISO-8601 strings (``YYYY-MM-DD``).
    headers:
        When ``None`` (default) the :data:`DEFAULT_HEADERS` constant is used.
        Otherwise *headers* is sent **as-is** — no merging occurs.
    timeout:
        Per-request timeout (seconds).
    client:
        Optional shared :class:`httpx.AsyncClient` instance. When ``None`` a
        disposable client is created and closed for the call.

    Returns
    -------
    data:
        ``Dict[str, Dict[str, Any]]`` keyed first by endpoint URL then by date.
    errors:
        List of human-readable error strings (one per failed range), each
        prefixed with its endpoint URL.
    """

    # Choose headers -----------------------------------------------------------
    headers_to_use: Dict[str, str] = DEFAULT_HEADERS if headers is None else headers

    # Decide whether to spin up a disposable client or use the caller's. --------
    own_client = client is None
    if own_client:
        async with httpx.AsyncClient(timeout=timeout) as ac:
            tasks = [
                _fetch_tagged(ac, endpoint, rng[0], rng[1], headers_to_use, timeout)
                for endpoint, ranges in endpoints_ranges.items()
                for rng in ranges
            ]
            results = await asyncio.gather(*tasks)
    else:
        tasks = [
            _fetch_tagged(client, endpoint, rng[0], rng[1], headers_to_use, timeout)
            for endpoint, ranges in endpoints_ranges.items()
            for rng in ranges
        ]
        results = await asyncio.gather(*tasks)

    # Collate results -----------------------------------------------------------
    data: Dict[str, Dict[str, Any]] = defaultdict(dict)
    errors: List[str] = []

    for endpoint, success, result in results:
        if success:
            if not isinstance(result, dict):
                errors.append(
                    f"{endpoint}: [unexpected payload] Expected dict, "
                    f"got {type(result).__name__}: {result}"
                )
                continue
            data[endpoint].update(result)
        else:
            errors.append(f"{endpoint}: {result}")

    return data, errors
