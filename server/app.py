"""FastAPI app: REST API + MCP endpoint (/mcp) + downloads + legal pages.

    uvicorn server.app:app --host 0.0.0.0 --port 8000

Works anywhere, not just Muse: the MCP endpoint at /mcp is plain Streamable HTTP
and any MCP client (Muse, Claude Desktop, ChatGPT developer mode, Cursor,
VS Code, mcp-inspector, ...) can connect with `Authorization: Bearer <key>`.
Auth is pluggable (see server/auth.py): AUTH_MODE=key (default), oauth, or either.
With ALLOW_ANONYMOUS=true keyless visitors are admitted too, each isolated by a
hashed id of their source IP (a wrong key is still rejected). Billing gates the
free tier (see docs/monetization.md); with BILLING_ENABLED=false everyone gets
the full tier.
"""
from __future__ import annotations

import hashlib
import logging
import pathlib
import secrets
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import markdown
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field

from .auth import Principal, build_authenticator
from .config import Settings, get_settings
from .mcp_tools import build_mcp, current_client, current_principal
from .quota import QuotaError
from .service import Service, ServiceError
from .store import NotFound

log = logging.getLogger("cleaner")
DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"
PUBLIC_PREFIXES = ("/healthz", "/v1/download/", "/v1/uploads/", "/privacy", "/terms", "/support",
                   "/install", "/about",
                   "/openapi.json", "/docs", "/.well-known/", "/mcp.json", "/manifest.json")
# CSP for the designed marketing pages (Tailwind/Iconify CDNs + Google Fonts).
# Legal pages keep the strict policy below.
SITE_CSP = ("default-src 'none'; script-src 'unsafe-inline' https://cdn.tailwindcss.com https://code.iconify.design; "
            "style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "img-src 'self' data:; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'")


class SourceIn(BaseModel):
    file_base64: Optional[str] = Field(None, description="Base64 file content (small files)")
    csv_text: Optional[str] = Field(None, description="Pasted CSV text")
    file_url: Optional[str] = Field(None, description="Public https link")
    upload_id: Optional[str] = Field(None, description="From POST /v1/uploads")
    filename: Optional[str] = None
    sheet: Optional[str] = None
    date_order: Optional[str] = Field(None, pattern="^(dmy|mdy)$")


class CleanIn(SourceIn):
    output_format: str = "csv"
    auto_approve_max_risk: str = "medium"
    approve_ids: List[str] = []


class PlanIn(BaseModel):
    date_order: Optional[str] = Field(None, pattern="^(dmy|mdy)$")


class ApplyIn(PlanIn):
    auto_approve_max_risk: str = "medium"
    approve_ids: List[str] = []


class ActionIn(BaseModel):
    action: str
    params: Dict[str, Any] = {}
    confirm: bool = False


class ExportIn(BaseModel):
    output_format: str = "csv"
    include_report: bool = True
    include_script: bool = True


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    s = settings or get_settings()
    # Normalise AUTH_MODE early so a typo fails fast instead of silently opening up.
    if s.auth_mode not in ("key", "oauth", "either"):
        raise ValueError(f"AUTH_MODE must be 'key', 'oauth' or 'either', got {s.auth_mode!r}")
    svc = Service(s)
    auth = build_authenticator(s)
    # Salt for per-visitor anonymous ids. Random per process: on restart all
    # anonymous buckets (and their in-memory datasets) are forgotten together.
    anon_salt = secrets.token_hex(8)
    if auth.open and not s.allow_anonymous:
        # Fail closed at SERVE time (see lifespan below): importing this module
        # with no credentials (tests, tooling) stays safe; serving refuses.
        log.warning("No credentials configured and ALLOW_ANONYMOUS is not set: the server will refuse to start. "
                    "Set API_KEYS (or OAuth) or ALLOW_ANONYMOUS=true for local development only.")
    elif auth.open:
        log.warning("ALLOW_ANONYMOUS=true: the API is OPEN. Never expose this to a network.")
    mcp = build_mcp(svc)
    max_body = int(s.max_upload_mb * 1024 * 1024 * 1.4) + 1024 * 1024
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", json_response=True, stateless_http=True, max_request_body_size=max_body,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=s.allowed_hosts))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if auth.open and not s.allow_anonymous:
            raise RuntimeError("Refusing to serve with no credentials: set API_KEYS (or OAUTH_ISSUER / "
                               "OAUTH_JWKS_URL), or ALLOW_ANONYMOUS=true for local development only.")
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(title="Agentic Data Cleaner", version="2.0.0", lifespan=lifespan,
                  description="Clean messy tabular data. Also available as an MCP server at /mcp.")
    app.state.service = svc
    # Browser-based MCP clients (ChatGPT, Claude web, mcp-inspector) need CORS.
    # Authorization header is allow-listed explicitly so bearer keys pass preflight.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id", "mcp-protocol-version"],
        max_age=86400,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        # GET /mcp is a human-readable info page (browsers, link previews); the
        # actual MCP calls are POST and always require a credential.
        info_page = path == "/mcp" and request.method == "GET"
        if path != "/" and not path.startswith(PUBLIC_PREFIXES) and not info_page and request.method != "OPTIONS":
            header = request.headers.get("authorization", "")
            principal = auth.authenticate(header)
            if principal is None and s.allow_anonymous and not header.strip():
                # Public mode: no credential sent -> admit as an anonymous visitor
                # isolated by source IP (hashed; raw IPs never logged or stored).
                # A WRONG key is still rejected below -- only a missing one is let in.
                ip = request.client.host if request.client else "unknown"
                cid = "anon_" + hashlib.sha256(f"{anon_salt}:{ip}".encode()).hexdigest()[:16]
                principal = Principal(cid, "anonymous")
            if principal is None:
                scheme = "Bearer"
                if s.auth_mode == "oauth" and s.oauth_issuer:
                    scheme = f'Bearer realm="data-cleaner", resource="{s.public_base_url}/mcp"'
                return JSONResponse({"error": "Missing or invalid credential. Provide "
                                     "Authorization: Bearer <key> (or a valid OAuth access token)."},
                                    status_code=401,
                                    headers={"WWW-Authenticate": scheme})
            current_client.set(principal.client_id)
            current_principal.set(principal)
            svc.note_tier_hint(principal.client_id, principal.tier_hint)
            request.state.client = principal.client_id
            request.state.principal = principal
        else:
            # Public path: still reset context so a reused task never leaks the last client.
            current_client.set("anonymous")
            current_principal.set(None)
            request.state.client = "anonymous"
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_body and not path.startswith("/v1/uploads/"):
            return JSONResponse({"error": "Request body too large."}, status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if path.startswith("/v1/") or path.startswith("/mcp"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(QuotaError)
    async def _quota_error(_: Request, exc: QuotaError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")           # stack trace stays server-side; no data in the message
        return JSONResponse({"error": "Something went wrong on our side. Please try again."}, status_code=500)

    def me(request: Request) -> str:
        return getattr(request.state, "client", None) or current_client.get()

    # ------------------------------------------------------------------ REST
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True, "service": "agentic-data-cleaner", "mcp": "/mcp", "auth_mode": s.auth_mode,
                "billing": "on" if s.billing_enabled else "off"}

    @app.get("/v1/actions")
    def list_actions():
        return svc.actions()

    @app.post("/v1/datasets", summary="Scan a file")
    def scan(body: SourceIn, request: Request):
        return svc.scan(me(request), **body.model_dump())

    @app.post("/v1/clean", summary="Scan, fix everything safe, and export")
    def clean(body: CleanIn, request: Request):
        d = body.model_dump()
        return svc.clean(me(request), fmt=d.pop("output_format"), auto_approve_max_risk=d.pop("auto_approve_max_risk"),
                         approve_ids=d.pop("approve_ids"), **d)

    def _ip(request: Request) -> str:
        return "ip:" + (request.client.host if request.client else "unknown")

    @app.post("/v1/uploads", summary="Get a one-time upload URL")
    def create_upload(request: Request):
        return svc.create_upload_slot(me(request))

    @app.put("/v1/uploads/{token}", summary="Upload raw file bytes")
    async def put_upload(token: str, request: Request, filename: Optional[str] = None):
        # Public capability URL: no account, so throttle by source IP instead.
        try:
            svc.meter.rate_limit(_ip(request), s.rate_limit_per_minute)
        except QuotaError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        try:
            slot = svc.store.take_slot(token)
        except NotFound:
            return JSONResponse({"error": "Upload link not found or expired."}, status_code=404)
        slot_owner_tier = svc.tier(slot.owner)
        cap = slot_owner_tier.max_bytes
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > cap:
                return JSONResponse({"error": "File too large for this plan."}, status_code=413)
        return svc.receive_upload(token, bytes(buf), filename)

    @app.get("/v1/datasets/{dataset_id}/plan")
    def get_plan(dataset_id: str, request: Request, date_order: Optional[str] = None):
        return svc.plan(me(request), dataset_id, date_order)

    @app.post("/v1/datasets/{dataset_id}/apply-plan")
    def apply_plan(dataset_id: str, body: ApplyIn, request: Request):
        return svc.apply_plan(me(request), dataset_id, body.auto_approve_max_risk, body.approve_ids, body.date_order)

    @app.post("/v1/datasets/{dataset_id}/actions")
    def apply_action(dataset_id: str, body: ActionIn, request: Request):
        return svc.apply_action(me(request), dataset_id, body.action, body.params, body.confirm)

    @app.get("/v1/datasets/{dataset_id}/preview")
    def preview(dataset_id: str, request: Request, rows: int = 20):
        return svc.preview(me(request), dataset_id, rows)

    @app.post("/v1/datasets/{dataset_id}/export")
    def export(dataset_id: str, body: ExportIn, request: Request):
        return svc.export(me(request), dataset_id, body.output_format, body.include_report, body.include_script)

    @app.delete("/v1/datasets/{dataset_id}")
    def delete(dataset_id: str, request: Request):
        return svc.delete(me(request), dataset_id)

    @app.get("/v1/download/{token}/{filename}", include_in_schema=False)
    def download(token: str, filename: str, request: Request):
        try:
            svc.meter.rate_limit(_ip(request), s.rate_limit_per_minute)
        except QuotaError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        art = svc.download(token)
        safe = art.filename.replace('"', "")
        return Response(art.payload, media_type=art.mime,
                        headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store"})

    # ----------------------------------------------------------- public pages
    def _page(name: str, title: str) -> HTMLResponse:
        path = DOCS / name
        body = markdown.markdown(path.read_text(encoding="utf-8"), extensions=["tables"]) if path.exists() else "<p>Coming soon.</p>"
        html = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>{title}</title><style>body{{font:16px/1.6 system-ui;max-width:760px;margin:40px auto;padding:0 16px;"
                f"color:#1a1a1a}}table{{border-collapse:collapse}}td,th{{border:1px solid #ddd;padding:6px 10px}}</style>{body}")
        return HTMLResponse(html, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @app.get("/privacy", include_in_schema=False)
    def privacy():
        return _page("privacy-policy.md", "Privacy Policy")

    @app.get("/terms", include_in_schema=False)
    def terms():
        return _page("terms-of-service.md", "Terms of Service")

    @app.get("/support", include_in_schema=False)
    def support():
        return _page("support.md", "Support")

    SITE = DOCS / "site"

    def _site(name: str) -> HTMLResponse:
        path = SITE / name
        if not path.exists():
            return HTMLResponse("<p>Coming soon.</p>", status_code=404)
        return HTMLResponse(path.read_text(encoding="utf-8"),
                            headers={"Content-Security-Policy": SITE_CSP})

    @app.get("/", include_in_schema=False)
    def home():
        return _site("home.html")

    @app.get("/install", include_in_schema=False)
    def install():
        return _site("install.html")

    @app.get("/about", include_in_schema=False)
    def about():
        return _site("about.html")

    @app.get("/mcp", include_in_schema=False)
    def mcp_info():
        # Browsers GET this URL when someone "visits" the endpoint. It is not a
        # web page to use -- it explains how an MCP client connects instead.
        return JSONResponse({
            "service": "Agentic Data Cleaner",
            "mcp_endpoint": f"{s.public_base_url}/mcp",
            "transport": "Streamable HTTP (MCP JSON-RPC via POST)",
            "how_to_connect": [
                "1. Copy your API key from the server operator.",
                "2. In your AI client (Muse, Claude Desktop, ChatGPT, Cursor, VS Code, Opencode), "
                "add a remote MCP server with the mcp_endpoint URL above.",
                f"3. Send header 'Authorization: Bearer <your key>' with every request. Full guide: {s.public_base_url}/install",
            ],
            "try_it": f"POST {s.public_base_url}/mcp with method 'tools/list'",
        })

    # --------------------------------------- universal MCP discovery (any client)
    def _manifest() -> Dict[str, Any]:
        tools = [{"name": t} for t in
                 ("clean_dataset", "scan_dataset", "get_cleaning_plan", "apply_cleaning_plan",
                  "apply_cleaning_action", "preview_dataset", "export_dataset",
                  "create_upload_url", "delete_dataset", "list_cleaning_actions")]
        auth_desc: Dict[str, Any] = {"mode": s.auth_mode}
        if s.auth_mode in ("key", "either"):
            auth_desc["bearer"] = {"header": "Authorization: Bearer <API_KEY>", "obtain": "from the server operator"}
        if s.oauth_issuer:
            auth_desc["oauth"] = {"issuer": s.oauth_issuer,
                                  "authorization_servers": [f"{s.public_base_url}/.well-known/oauth-authorization-server"],
                                  "scopes": s.oauth_required_scopes}
        return {"name": "Agentic Data Cleaner", "version": "2.0.0",
                "description": "Clean messy tabular data. Deterministic tools; safe fixes auto-apply, risky ones need approval.",
                "website": s.public_base_url,
                "mcp": {"transport": "streamable-http", "endpoint": f"{s.public_base_url}/mcp",
                        "clients": ["Muse", "Claude Desktop", "ChatGPT", "Cursor", "VS Code", "mcp-inspector",
                                    "any MCP Streamable-HTTP client"]},
                "rest": {"docs": f"{s.public_base_url}/docs", "actions": f"{s.public_base_url}/v1/actions"},
                "auth": auth_desc, "tools": tools}

    @app.get("/mcp.json", include_in_schema=False)
    @app.get("/manifest.json", include_in_schema=False)
    @app.get("/.well-known/mcp.json", include_in_schema=False)
    def manifest():
        return JSONResponse(_manifest())

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    def oauth_protected_resource():
        # RFC 9728: tells OAuth-capable MCP clients where to get a token.
        # Bearer-key clients ignore this and just send Authorization: Bearer <key>.
        body: Dict[str, Any] = {"resource": f"{s.public_base_url}/mcp",
                                "authorization_servers": ([s.oauth_issuer] if s.oauth_issuer
                                                          else [f"{s.public_base_url}/.well-known/oauth-authorization-server"]),
                                "bearer_methods_supported": ["header"],
                                "scopes_supported": s.oauth_required_scopes or []}
        if s.auth_mode in ("key", "either") and not s.oauth_issuer:
            body["note"] = "This server also accepts static bearer keys (AUTH_MODE=key/either). No OAuth round-trip needed."
        return JSONResponse(body)

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    def oauth_authorization_server():
        # Only meaningful when OAuth is configured; otherwise 404 so clients fall back to bearer keys.
        if not s.oauth_issuer:
            return JSONResponse({"error": "OAuth is not enabled on this server. Use Authorization: Bearer <API_KEY>."},
                                status_code=404)
        iss = s.oauth_issuer
        return JSONResponse({"issuer": iss, "authorization_endpoint": f"{iss}/authorize",
                             "token_endpoint": f"{iss}/token", "jwks_uri": s.oauth_jwks_url,
                             "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"]})

    app.mount("/", mcp_app)                      # /mcp -- must be last
    return app


app = create_app()
