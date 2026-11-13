"""Data models and enums for the Durable Campaign Dispatcher."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class LeadState(str, Enum):
    FRESH = "fresh"
    PENDING_DIAL = "pending_dial"
    ACTIVE = "active"
    RETRY_SCHEDULED = "retry_scheduled"
    CALLBACK_SCHEDULED = "callback_scheduled"
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    DND_BLOCKED = "dnd_blocked"


class FinalState(str, Enum):
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    DND_BLOCKED = "dnd_blocked"


class CallState(str, Enum):
    DISPATCHING = "dispatching"
    ACTIVE = "active"
    ENDED = "ended"
    NOT_PLACED = "not_placed"


TERMINAL_DISPOSITIONS = {"interested", "not_interested", "wrong_number", "dnd"}
RETRYABLE_STATUSES = {"no_answer", "busy", "failed"}


@dataclass
class WindowConfig:
    start: str
    end: str
    days: Optional[List[str]] = None


@dataclass
class TenantConfig:
    tenant_id: str
    max_concurrent_calls: int


@dataclass
class CampaignConfig:
    campaign_id: str
    tenant_id: str
    leads_file: str
    window: WindowConfig
    max_attempts: int


@dataclass
class RetryPolicyConfig:
    no_answer_minutes: int = 60
    busy_minutes: int = 60
    failed_minutes: int = 15


@dataclass
class SystemConfig:
    sim_start: datetime
    timezone: str
    regulatory_window: WindowConfig
    window_grace_seconds: int
    retry_policy: RetryPolicyConfig
    webhook_secret: str
    tenants: Dict[str, TenantConfig]
    campaigns: Dict[str, CampaignConfig]


@dataclass
class LeadRecord:
    campaign_id: str
    lead_id: str
    phone: str
    state: str
    attempts: int = 0
    next_eligible_at: Optional[datetime] = None
    callback_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_disposition: Optional[str] = None
    last_call_sid: Optional[str] = None
    last_request_id: Optional[str] = None
    last_ended_at: Optional[datetime] = None


@dataclass
class CallRecord:
    request_id: str
    campaign_id: str
    lead_id: str
    phone: str
    tenant_id: str
    attempt_no: int
    state: str
    call_sid: Optional[str] = None
    created_at_sim: Optional[datetime] = None
    started_at_sim: Optional[datetime] = None
    ended_at_sim: Optional[datetime] = None
    status: Optional[str] = None
    disposition: Optional[str] = None
    callback_at: Optional[datetime] = None
    duration_sec: int = 0
    raw_result: Optional[str] = None

