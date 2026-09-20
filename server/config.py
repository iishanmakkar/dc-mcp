"""Runtime configuration, read once from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _b(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _l(name: str, default: str = "") -> List[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"))
    allowed_hosts: List[str] = field(default_factory=lambda: _l("ALLOWED_HOSTS", "localhost:*,127.0.0.1:*"))
    # --- auth: comma-separated bearer keys. Empty = OPEN (development only!).
    api_keys: List[str] = field(default_factory=lambda: _l("API_KEYS"))
    # AUTH_MODE selects which credential is accepted: "key" (default), "oauth", or "either".
    # - key: static bearer keys only (works with Muse, Claude, ChatGPT, any MCP client).
    # - oauth: OAuth 2.1 / OIDC JWT access tokens only (for platforms that require OAuth).
    # - either: accept both (recommended during review: Muse + generic clients keep working).
    auth_mode: str = field(default_factory=lambda: os.environ.get("AUTH_MODE", "key").strip().lower() or "key")
    oauth_enabled: bool = field(default_factory=lambda: _b("OAUTH_ENABLED", False))
    oauth_issuer: str = field(default_factory=lambda: os.environ.get("OAUTH_ISSUER", "").rstrip("/"))
    oauth_audience: List[str] = field(default_factory=lambda: _l("OAUTH_AUDIENCE"))
    oauth_jwks_url: str = field(default_factory=lambda: os.environ.get("OAUTH_JWKS_URL", ""))
    oauth_tier_claim: str = field(default_factory=lambda: os.environ.get("OAUTH_TIER_CLAIM", "tier"))
    oauth_required_scopes: List[str] = field(default_factory=lambda: _l("OAUTH_REQUIRED_SCOPES"))
    # Empty API_KEYS + no OAuth = fail CLOSED unless explicitly opened for local dev.
    allow_anonymous: bool = field(default_factory=lambda: _b("ALLOW_ANONYMOUS", False))
    # --- limits for the top tier (also the only tier when billing is off)
    max_upload_mb: int = field(default_factory=lambda: _i("MAX_UPLOAD_MB", 25))
    max_rows: int = field(default_factory=lambda: _i("MAX_ROWS", 200_000))
    max_cols: int = field(default_factory=lambda: _i("MAX_COLS", 500))
    ttl_seconds: int = field(default_factory=lambda: _i("TTL_SECONDS", 3600))
    max_datasets_per_client: int = field(default_factory=lambda: _i("MAX_DATASETS_PER_CLIENT", 10))
    rate_limit_per_minute: int = field(default_factory=lambda: _i("RATE_LIMIT_PER_MINUTE", 120))
    allow_url_fetch: bool = field(default_factory=lambda: _b("ALLOW_URL_FETCH", True))
    # --- optional model (OFF by default: no data ever leaves the server)
    llm_enabled: bool = field(default_factory=lambda: _b("LLM_ENABLED", False))
    # --- monetisation (see docs/monetization.md)
    billing_enabled: bool = field(default_factory=lambda: _b("BILLING_ENABLED", False))
    paid_clients: List[str] = field(default_factory=lambda: _l("PAID_CLIENTS"))   # ids of paying clients
    free_max_rows: int = field(default_factory=lambda: _i("FREE_MAX_ROWS", 5_000))
    free_max_upload_mb: int = field(default_factory=lambda: _i("FREE_MAX_UPLOAD_MB", 2))
    free_files_per_day: int = field(default_factory=lambda: _i("FREE_FILES_PER_DAY", 5))


def get_settings() -> Settings:
    return Settings()
