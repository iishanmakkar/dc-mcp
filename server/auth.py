"""Pluggable authentication: bearer API keys and/or OAuth 2.1 access tokens (JWT).

Muse's connector spec was not verifiable from the development environment (Meta's developer
portal is login-walled), so both mechanisms are supported at once and chosen by configuration:

* ``API_KEYS``      -- static bearer keys (the original behaviour, kept as the default).
* ``OAUTH_ENABLED`` -- validate an OAuth 2.1 / OIDC access token as an RS256 JWT against the
  issuer's JWKS. This is what a platform that authenticates connectors via OAuth will send.

Switching between them is configuration, not a rewrite: ``AUTH_MODE`` selects ``key``, ``oauth``
or ``either``. Both paths produce the same thing -- an opaque, non-reversible client id -- so
nothing downstream (store, quota, metering) knows or cares which one was used.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("cleaner.auth")

# Access tokens are cached by (issuer, audience) for this many seconds, independent of the
# shorter token lifetime -- see JwksCache.
_JWKS_TTL = 3600


class AuthError(Exception):
    """Raised when a credential is present but not acceptable."""


@dataclass(frozen=True)
class Principal:
    """The outcome of authenticating a request."""

    client_id: str               # opaque, stable per credential; never the raw secret
    scheme: str                  # "key" | "oauth" | "anonymous"
    subject: Optional[str] = None    # OAuth 'sub' claim, when available
    tier_hint: Optional[str] = None  # optional claim naming a plan ("paid"/"free")


def _key_id(key: str) -> str:
    return "k_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _subject_id(issuer: str, subject: str) -> str:
    """A stable id for an OAuth subject, scoped to the issuer so two issuers can't collide."""
    digest = hashlib.sha256(f"{issuer}\x00{subject}".encode()).hexdigest()[:16]
    return "o_" + digest


class KeyAuthenticator:
    """Constant-time bearer-key comparison against a configured list."""

    def __init__(self, keys: List[str]) -> None:
        self.keys = [k for k in keys if k]

    @property
    def enabled(self) -> bool:
        return bool(self.keys)

    def authenticate(self, header: str) -> Optional[Principal]:
        token = _bearer(header)
        if not token:
            return None
        for key in self.keys:               # constant-time compare against every key
            if hmac.compare_digest(token.encode(), key.encode()):
                return Principal(_key_id(key), "key")
        return None


class JwksCache:
    """Fetches and caches a JWKS document, refreshing on rotation or expiry."""

    def __init__(self, url: str, ttl: int = _JWKS_TTL) -> None:
        self.url = url
        self.ttl = ttl
        self._lock = threading.Lock()
        self._keys: Dict[str, Any] = {}
        self._fetched = 0.0

    def _fetch(self) -> Dict[str, Any]:
        # Operator-configured URL only (never user input). Still: https except
        # http://localhost for tests, 10 s timeout, 64 KB cap.
        if not (self.url.startswith("https://") or self.url.startswith("http://localhost")
                or self.url.startswith("http://127.0.0.1")):
            raise ValueError(f"Refusing to fetch JWKS from non-https URL: {self.url!r}")
        with urllib.request.urlopen(self.url, timeout=10) as resp:   # noqa: S310 -- https, configured
            raw = resp.read(65536 + 1)
        if len(raw) > 65536:
            raise ValueError("JWKS document too large.")
        doc = json.loads(raw.decode("utf-8"))
        keys = {}
        for jwk in doc.get("keys", []):
            kid = jwk.get("kid")
            if kid:
                keys[kid] = jwk
        return keys

    def get(self, kid: Optional[str], *, force: bool = False) -> Optional[Any]:
        # Fast path without I/O under lock; the fetch itself happens outside it
        # so a slow/hung JWKS endpoint cannot stall every other authentication.
        with self._lock:
            fresh = (time.monotonic() - self._fetched) <= self.ttl
            recent = (time.monotonic() - self._fetched) <= 5
            if self._keys and (not force and fresh or force and recent):
                keys = self._keys
            else:
                keys = None
        if keys is None:
            fetched = self._fetch()
            with self._lock:
                self._keys = fetched
                self._fetched = time.monotonic()
                keys = self._keys
        if kid is None and len(keys) == 1:
            return next(iter(keys.values()))
        return keys.get(kid)


class OAuthAuthenticator:
    """Validates an RS256 JWT access token against an issuer's JWKS.

    Deliberately strict: signature, expiry, issuer and audience are all checked, and only
    asymmetric algorithms are accepted (``alg: none`` and HMAC-confusion are rejected).
    """

    def __init__(self, issuer: str, audience: List[str], jwks_url: str,
                 tiers_claim: str = "tier", required_scopes: Optional[List[str]] = None) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = [a for a in audience if a]
        self.tiers_claim = tiers_claim
        self.required_scopes = [s for s in (required_scopes or []) if s]
        self.jwks = JwksCache(jwks_url)
        try:
            import jwt as _jwt                     # PyJWT
        except ImportError as exc:                 # pragma: no cover - config error
            raise AuthError("OAUTH_ENABLED=true requires the 'PyJWT[crypto]' package.") from exc
        self._jwt = _jwt

    @property
    def enabled(self) -> bool:
        return True

    def authenticate(self, header: str) -> Optional[Principal]:
        token = _bearer(header)
        if not token:
            return None
        try:
            header_alg = self._jwt.get_unverified_header(token)
        except Exception:
            return None                            # not a JWT at all: not our credential
        alg = header_alg.get("alg", "")
        if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256"):
            log.warning("rejected token with unsupported alg=%r", alg)
            return None
        kid = header_alg.get("kid")
        key = self._lookup_key(kid, token)
        if key is None:
            return None
        options = {"require": ["exp", "iss"]}
        if self.audience:
            options["require"].append("aud")
        try:
            claims = self._jwt.decode(
                token, key=key, algorithms=[alg], audience=self.audience or None,
                issuer=self.issuer, options=options,
            )
        except Exception as exc:
            log.info("token rejected: %s", exc)
            return None
        if self.required_scopes and not _has_scopes(claims, self.required_scopes):
            log.info("token rejected: missing required scope(s) %s", self.required_scopes)
            return None
        sub = str(claims.get("sub") or "")
        if not sub:
            return None
        issuer = str(claims.get("iss") or self.issuer)
        tier = claims.get(self.tiers_claim)
        return Principal(_subject_id(issuer, sub), "oauth", subject=sub,
                         tier_hint=str(tier) if tier else None)

    def _lookup_key(self, kid: Optional[str], token: str) -> Optional[Any]:
        """Return a usable public key, refreshing the JWKS once if the kid is unknown."""
        for force in (False, True):
            try:
                jwk = self.jwks.get(kid, force=force)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("JWKS fetch failed: %s", exc)
                return None
            if jwk is None:
                continue
            try:
                return self._jwt.PyJWK(jwk).key
            except Exception as exc:                # pragma: no cover - malformed JWKS
                log.warning("JWKS entry unusable: %s", exc)
                return None
        return None


def _bearer(header: str) -> str:
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def _has_scopes(claims: Dict[str, Any], required: List[str]) -> bool:
    raw = claims.get("scope") or claims.get("scp") or ""
    granted = set(raw.split()) if isinstance(raw, str) else set(raw or [])
    return all(scope in granted for scope in required)


class Authenticator:
    """Front door: tries the configured schemes in order and returns the first Principal."""

    def __init__(self, mode: str, keys: List[str], oauth: Optional[OAuthAuthenticator] = None) -> None:
        self.mode = mode
        self.key_auth = KeyAuthenticator(keys)
        self.oauth = oauth
        if mode == "oauth" and oauth is None:
            raise AuthError("AUTH_MODE=oauth requires OAUTH_ISSUER and OAUTH_JWKS_URL.")
        if mode in ("key", "either") and not self.key_auth.enabled and oauth is None:
            log.warning("No credentials configured: the API is OPEN. Set API_KEYS before exposing it.")

    @property
    def open(self) -> bool:
        """True when nothing is configured -- development only."""
        return not self.key_auth.enabled and self.oauth is None

    def authenticate(self, header: str) -> Optional[Principal]:
        """Return a Principal, or None if the credential is missing/invalid."""
        if self.open:
            return Principal("anonymous", "anonymous")
        if self.mode in ("key", "either") and self.key_auth.enabled:
            p = self.key_auth.authenticate(header)
            if p is not None:
                return p
        if self.mode in ("oauth", "either") and self.oauth is not None:
            p = self.oauth.authenticate(header)
            if p is not None:
                return p
        return None


def build_authenticator(settings) -> Authenticator:
    """Construct the Authenticator described by Settings."""
    oauth = None
    if settings.oauth_enabled or settings.auth_mode == "oauth":
        if not settings.oauth_issuer or not settings.oauth_jwks_url:
            if settings.auth_mode == "oauth":
                raise AuthError("AUTH_MODE=oauth requires OAUTH_ISSUER and OAUTH_JWKS_URL.")
            log.warning("OAUTH_ENABLED ignored: OAUTH_ISSUER / OAUTH_JWKS_URL not set.")
        else:
            jwt = settings.oauth_jwks_url
            if not jwt.startswith("https://") and not jwt.startswith("http://localhost") \
                    and not jwt.startswith("http://127.0.0.1"):
                raise AuthError("OAUTH_JWKS_URL must be https (or http://localhost for tests).")
            if not settings.oauth_audience:
                log.warning("OAUTH_AUDIENCE is empty: tokens for any audience of this issuer are accepted. "
                            "Set OAUTH_AUDIENCE in production.")
            oauth = OAuthAuthenticator(
                issuer=settings.oauth_issuer, audience=settings.oauth_audience, jwks_url=jwt,
                tiers_claim=settings.oauth_tier_claim, required_scopes=settings.oauth_required_scopes,
            )
    return Authenticator(settings.auth_mode, settings.api_keys, oauth)
