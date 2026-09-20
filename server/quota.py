"""Tiers, rate limiting and usage metering.

The free/paid split follows docs/monetization.md: the free tier is enough to try the connector on
a small file; what people pay for is what Muse cannot do by itself in chat -- large files, the
audit report and the replayable pipeline script.

`tier_for()` is the single place to connect real entitlement (Stripe Link / Meta's payment flow).
Until BILLING_ENABLED=true everyone gets the full tier.
"""
from __future__ import annotations

import json, logging, threading, time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict

from .config import Settings

log = logging.getLogger("cleaner.usage")


class QuotaError(Exception):
    def __init__(self, message: str, status: int = 402):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Tier:
    name: str
    max_rows: int
    max_bytes: int
    files_per_day: int          # 0 = unlimited
    audit_report: bool
    replay_script: bool
    formats: frozenset


ALL_FORMATS = frozenset({"csv", "tsv", "xlsx", "json", "jsonl", "parquet"})


def tier_for(client_id: str, s: Settings, tier_hint: str | None = None) -> Tier:
    """Pick the tier for a client.

    Free-launch mode (default): with BILLING_ENABLED=false everyone gets the full
    paid tier -- no payment plumbing needed, works on localhost and any host.

    Paid mode (BILLING_ENABLED=true):
      1. tier_hint == "paid" (from a verified OAuth JWT claim, e.g. Stripe Link /
         Meta entitlement mapped into the token) -> paid.
      2. client_id in PAID_CLIENTS -> paid (manual allow-list / Stripe webhook).
      3. otherwise -> free tier.
    """
    paid = Tier("paid", s.max_rows, s.max_upload_mb * 1024 * 1024, 0, True, True, ALL_FORMATS)
    if not s.billing_enabled:
        return paid
    if tier_hint and tier_hint.strip().lower() in ("paid", "pro", "premium", "subscribed"):
        return paid
    if client_id in s.paid_clients:
        return paid
    return Tier("free", min(s.free_max_rows, s.max_rows), s.free_max_upload_mb * 1024 * 1024,
                s.free_files_per_day, False, False, frozenset({"csv"}))


class Meter:
    """In-memory counters. Swap for Redis/Postgres when you run more than one instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: Dict[str, Deque[float]] = defaultdict(deque)
        self._files: Dict[tuple, int] = defaultdict(int)

    def rate_limit(self, client_id: str, per_minute: int) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._calls[client_id]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= per_minute:
                raise QuotaError("Too many requests -- slow down and retry in a minute.", 429)
            q.append(now)

    def start_file(self, client_id: str, tier: Tier) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock:
            # Prune other days so the counter map cannot grow without bound.
            for key in [k for k in self._files if k[1] != day]:
                del self._files[key]
            if tier.files_per_day and self._files[(client_id, day)] >= tier.files_per_day:
                raise QuotaError(f"The free plan includes {tier.files_per_day} files per day. "
                                 f"Upgrade for unlimited files, larger files, audit reports and replay scripts.")
            self._files[(client_id, day)] += 1

    @staticmethod
    def record(client_id: str, event: str, rows: int, cols: int, tier: str) -> None:
        """One structured line per billable event: ids and counts only, never data."""
        log.info(json.dumps({"event": event, "client": client_id, "rows": rows, "cols": cols, "tier": tier}))
