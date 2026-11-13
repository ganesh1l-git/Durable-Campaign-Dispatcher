"""Telephony client communicating with the mock telephony provider."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CallDispatchResult:
    status: str  # "accepted", "not_placed", "ambiguous", "error"
    call_sid: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error_detail: Optional[str] = None
    retryable: bool = False


class TelephonyError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Telephony HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class TelephonyClient:
    """Robust HTTP client for the telephony provider API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8099", timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def place_call(
        self,
        campaign_id: str,
        lead_id: str,
        phone: str,
        request_id: str,
        callback_url: str,
    ) -> CallDispatchResult:
        """
        Place a call via POST /v1/calls.
        Handles idempotency, 503s, and ambiguous 504s via request_id verification.
        """
        client = await self.get_client()
        payload = {
            "request_id": request_id,
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "phone": phone,
            "callback_url": callback_url,
        }

        try:
            resp = await client.post("/v1/calls", json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("Network timeout or error on POST /v1/calls for %s: %s", request_id, exc)
            return await self._resolve_ambiguous_request(request_id)

        if resp.status_code in (201, 200):
            data = resp.json()
            return CallDispatchResult(
                status="accepted",
                call_sid=data.get("call_sid"),
                data=data,
            )

        if resp.status_code == 503:
            logger.info("Provider 503 for %s - service unavailable, not placed", request_id)
            return CallDispatchResult(
                status="not_placed",
                retryable=True,
                error_detail="503_service_unavailable",
            )

        if resp.status_code == 504:
            logger.warning("Provider 504 for %s - ambiguous outcome, resolving...", request_id)
            return await self._resolve_ambiguous_request(request_id)

        # Handle 4xx violations
        error_detail = resp.text
        try:
            err_json = resp.json()
            error_detail = err_json.get("detail", err_json.get("error", resp.text))
        except Exception:
            pass
        logger.error("Provider rejected call with %s: %s", resp.status_code, error_detail)
        raise TelephonyError(resp.status_code, str(error_detail))

    async def _resolve_ambiguous_request(self, request_id: str) -> CallDispatchResult:
        """Query GET /v1/calls?request_id=... to verify whether a call was accepted."""
        for attempt in range(4):
            if attempt > 0:
                await asyncio.sleep(0.5 * attempt)
            call = await self.get_call_by_request_id(request_id)
            if call is not None:
                # The call WAS placed and accepted!
                return CallDispatchResult(
                    status="accepted",
                    call_sid=call.get("call_sid"),
                    data=call,
                )
            if attempt == 3:
                # After 4 attempts if it still 404s, it was definitely never placed.
                return CallDispatchResult(
                    status="not_placed",
                    retryable=True,
                    error_detail="timeout_resolved_not_placed",
                )

        return CallDispatchResult(status="ambiguous", retryable=False, error_detail="unresolved_timeout")

    async def get_call_by_request_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        """GET /v1/calls?request_id=... returns call details if exists, None if 404."""
        client = await self.get_client()
        try:
            resp = await client.get("/v1/calls", params={"request_id": request_id})
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
        except httpx.HTTPError:
            pass
        return None

    async def get_call_by_sid(self, call_sid: str) -> Optional[Dict[str, Any]]:
        """GET /v1/calls/{call_sid} returns call details if exists, None if 404."""
        client = await self.get_client()
        try:
            resp = await client.get(f"/v1/calls/{call_sid}")
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
        except httpx.HTTPError:
            pass
        return None

