"""Short-lived, in-memory storage for working datasets and download artifacts (TTL, then gone)."""
from __future__ import annotations

import secrets, threading, time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Dataset:
    id: str
    owner: str
    agent: Any
    name: str
    tier: str
    expires: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    artifact_tokens: List[str] = field(default_factory=list)


@dataclass
class Artifact:
    token: str
    owner: str
    payload: bytes
    mime: str
    filename: str
    expires: float


@dataclass
class UploadSlot:
    token: str
    owner: str
    expires: float
    filename: str = "upload"
    payload: Optional[bytes] = None


class NotFound(Exception):
    pass


class Store:
    def __init__(self, ttl: int, max_per_client: int):
        self.ttl, self.max_per_client = ttl, max_per_client
        self._lock = threading.Lock()
        self.datasets: Dict[str, Dataset] = {}
        self.artifacts: Dict[str, Artifact] = {}
        self.slots: Dict[str, UploadSlot] = {}

    def _now(self) -> float:
        return time.time()

    def sweep(self) -> int:
        now, n = self._now(), 0
        with self._lock:
            for k in [k for k, v in self.datasets.items() if v.expires <= now]:
                ds = self.datasets.pop(k)      # expired datasets take their exports with them
                for token in ds.artifact_tokens:
                    self.artifacts.pop(token, None)
                n += 1
            for table in (self.artifacts, self.slots):
                for k in [k for k, v in table.items() if v.expires <= now]:
                    del table[k]
                    n += 1
        return n

    # -- datasets
    def add_dataset(self, owner: str, agent: Any, name: str, tier: str) -> Dataset:
        self.sweep()
        with self._lock:
            if sum(1 for d in self.datasets.values() if d.owner == owner) >= self.max_per_client:
                raise NotFound(f"You already have {self.max_per_client} datasets open. Delete one first.")
            ds = Dataset(secrets.token_urlsafe(18), owner, agent, name, tier, self._now() + self.ttl)
            self.datasets[ds.id] = ds
            return ds

    def get_dataset(self, owner: str, dataset_id: str) -> Dataset:
        self.sweep()
        with self._lock:
            ds = self.datasets.get(dataset_id)
        if ds is None or ds.owner != owner:      # same answer for "missing" and "not yours"
            raise NotFound("Dataset not found or expired. Datasets are deleted after a short retention period.")
        return ds

    def delete_dataset(self, owner: str, dataset_id: str) -> bool:
        # Lock order is always ds.lock -> _lock (never the reverse: export holds
        # ds.lock while adding artifacts), so take ds.lock first to avoid deadlock.
        with self._lock:
            ds = self.datasets.get(dataset_id)
            if ds is None or ds.owner != owner:
                return False
        with ds.lock:
            with self._lock:
                if self.datasets.get(dataset_id) is not ds:
                    return False                # raced with another delete
                del self.datasets[dataset_id]
                for token in ds.artifact_tokens:          # the exports go with the dataset
                    self.artifacts.pop(token, None)
                return True

    # -- artifacts
    def add_artifact(self, owner: str, payload: bytes, mime: str, filename: str) -> Artifact:
        art = Artifact(secrets.token_urlsafe(24), owner, payload, mime, filename, self._now() + self.ttl)
        with self._lock:
            self.artifacts[art.token] = art
        return art

    def get_artifact(self, token: str) -> Artifact:
        self.sweep()
        with self._lock:
            art = self.artifacts.get(token)
        if art is None:
            raise NotFound("Download link not found or expired.")
        return art

    # -- upload slots
    def add_slot(self, owner: str) -> UploadSlot:
        slot = UploadSlot(secrets.token_urlsafe(24), owner, self._now() + min(self.ttl, 900))
        with self._lock:
            self.slots[slot.token] = slot
        return slot

    def take_slot(self, token: str) -> UploadSlot:
        self.sweep()
        with self._lock:
            slot = self.slots.get(token)
        if slot is None:
            raise NotFound("Upload link not found or expired.")
        return slot

    def pop_slot(self, owner: str, token: str) -> UploadSlot:
        with self._lock:
            slot = self.slots.get(token)
            if slot is None or slot.owner != owner or slot.payload is None:
                raise NotFound("Upload not found or not yet received.")
            del self.slots[token]
            return slot
