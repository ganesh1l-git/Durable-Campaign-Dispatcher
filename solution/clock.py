"""Simulated clock interface and synchronization."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

IST = timezone(timedelta(hours=5, minutes=30))


class Clock:
    """Base interface for clock."""

    def now(self) -> datetime:
        raise NotImplementedError

    def sim_seconds_to_real(self, sim_seconds: float) -> float:
        raise NotImplementedError

    def real_seconds_to_sim(self, real_seconds: float) -> float:
        raise NotImplementedError

    async def sleep_sim(self, sim_seconds: float) -> None:
        raise NotImplementedError


class SimClock(Clock):
    """Simulated clock matching the mock telephony provider's clock formula."""

    def __init__(self, sim_epoch: datetime, real_epoch: float, speed: float = 600.0):
        if sim_epoch.tzinfo is None:
            self.sim_epoch = sim_epoch.replace(tzinfo=IST)
        else:
            self.sim_epoch = sim_epoch.astimezone(IST)
        self.real_epoch = real_epoch
        self.speed = float(speed)

    def now(self) -> datetime:
        elapsed_real = time.time() - self.real_epoch
        return self.sim_epoch + timedelta(seconds=elapsed_real * self.speed)

    def sim_seconds_to_real(self, sim_seconds: float) -> float:
        if self.speed <= 0:
            return 0.0
        return max(0.0, sim_seconds / self.speed)

    def real_seconds_to_sim(self, real_seconds: float) -> float:
        return max(0.0, real_seconds * self.speed)

    async def sleep_sim(self, sim_seconds: float) -> None:
        real_sec = self.sim_seconds_to_real(sim_seconds)
        if real_sec > 0:
            await asyncio.sleep(real_sec)

    def update(self, sim_epoch: datetime, real_epoch: float, speed: float) -> None:
        if sim_epoch.tzinfo is None:
            self.sim_epoch = sim_epoch.replace(tzinfo=IST)
        else:
            self.sim_epoch = sim_epoch.astimezone(IST)
        self.real_epoch = real_epoch
        self.speed = float(speed)

    @classmethod
    async def create_from_provider(cls, provider_url: str, client: Optional[httpx.AsyncClient] = None) -> SimClock:
        """Fetch current clock state from provider GET /v1/clock."""
        url = f"{provider_url.rstrip('/')}/v1/clock"
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=10.0)
            close_client = True
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            sim_epoch = datetime.fromisoformat(data["sim_epoch"])
            real_epoch = float(data["real_epoch"])
            speed = float(data.get("speed", 600.0))
            return cls(sim_epoch=sim_epoch, real_epoch=real_epoch, speed=speed)
        finally:
            if close_client:
                await client.aclose()


class FakeClock(Clock):
    """Deterministic in-memory clock for unit tests."""

    def __init__(self, start_time: datetime, speed: float = 600.0):
        if start_time.tzinfo is None:
            self.current_time = start_time.replace(tzinfo=IST)
        else:
            self.current_time = start_time.astimezone(IST)
        self.speed = float(speed)

    def now(self) -> datetime:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += timedelta(seconds=seconds)

    def advance_minutes(self, minutes: float) -> None:
        self.advance(minutes * 60)

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self.current_time = new_time.replace(tzinfo=IST)
        else:
            self.current_time = new_time.astimezone(IST)

    def sim_seconds_to_real(self, sim_seconds: float) -> float:
        return max(0.0, sim_seconds / self.speed)

    def real_seconds_to_sim(self, real_seconds: float) -> float:
        return max(0.0, real_seconds * self.speed)

    async def sleep_sim(self, sim_seconds: float) -> None:
        self.advance(sim_seconds)

