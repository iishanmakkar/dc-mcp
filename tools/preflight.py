"""Deployment preflight -- run this AFTER deploying, before submitting to Muse.

It exercises exactly what a Muse reviewer will: public pages, auth, the MCP handshake,
a real one-shot clean, a real download, and deletion.

    python tools/preflight.py https://your-domain.example  YOUR_API_KEY

Exit code 0 = everything a reviewer touches works; anything else = do not submit yet.
Needs only httpx (already in requirements.txt).
"""
from __future__ import annotations

import json
import sys

import httpx

ACCEPT = "application/json, text/event-stream"

CLEAN_ARGS = {
    "csv_text": ("name,joined,salary,email\n"
                 "\"  Alice \",2024-01-05,\"50000\",alice@example.com\n"
                 "\"Bob  \",N/A,\"62000\",bob@example.com\n"
                 "Carol,2024-03-11,\"58000\",carol@example.com\n"
                 "\"  Alice \",2024-01-05,\"50000\",alice@example.com\n"),
    "filename": "preflight.csv",
}

REQUIRED_TOOLS = {
    "clean_dataset", "scan_dataset", "get_cleaning_plan", "apply_cleaning_plan",
    "apply_cleaning_action", "preview_dataset", "export_dataset", "create_upload_url",
    "delete_dataset", "list_cleaning_actions",
}


class Check:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, label: str, condition: bool, detail: str = "") -> None:
        mark = "PASS" if condition else "FAIL"
        if not condition:
            self.failures += 1
        print(f"[{mark}] {label}" + (f" -- {detail}" if detail and not condition else ""))


def rpc(c: httpx.Client, base: str, key: str, method: str, params: dict | None = None, id_: int = 1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    r = c.post(f"{base}/mcp", json=body,
               headers={"Authorization": f"Bearer {key}", "Accept": ACCEPT},
               timeout=60)
    return r


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    base, key = sys.argv[1].rstrip("/"), sys.argv[2]
    chk = Check()

    with httpx.Client(follow_redirects=True) as c:
        # 1. public surfaces
        r = c.get(f"{base}/healthz", timeout=30)
        chk.ok("GET /healthz -> {\"ok\": true}", r.status_code == 200 and r.json().get("ok") is True, f"HTTP {r.status_code} {r.text[:80]}")
        for path, name in [("/", "landing page"), ("/privacy", "privacy policy"), ("/terms", "terms of service"), ("/support", "support page")]:
            r = c.get(f"{base}{path}", timeout=30)
            chk.ok(f"GET {path} ({name}) renders", r.status_code == 200 and "html" in r.headers.get("content-type", ""), f"HTTP {r.status_code}")
        r = c.get(f"{base}/v1/actions", timeout=30)
        chk.ok("API rejects requests without a key (401)", r.status_code == 401, f"HTTP {r.status_code}")
        r = c.get(f"{base}/v1/actions", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        chk.ok("API accepts the bearer key", r.status_code == 200, f"HTTP {r.status_code} {r.text[:80]}")

        # 2. MCP handshake
        r = rpc(c, base, key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "preflight", "version": "1.0"}})
        chk.ok("MCP /mcp initialize", r.status_code == 200 and "serverInfo" in r.text, f"HTTP {r.status_code} {r.text[:120]}")
        c.post(f"{base}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers={"Authorization": f"Bearer {key}", "Accept": ACCEPT}, timeout=30)
        r = rpc(c, base, key, "tools/list", {}, id_=2)
        names = {t["name"] for t in r.json().get("result", {}).get("tools", [])} if r.status_code == 200 else set()
        chk.ok(f"MCP tools/list -> {len(names)} tools, all expected", REQUIRED_TOOLS <= names, f"missing: {sorted(REQUIRED_TOOLS - names)}")

        # 3. the money shot: a real clean + real download
        r = rpc(c, base, key, "tools/call", {"name": "clean_dataset", "arguments": CLEAN_ARGS}, id_=3)
        data = {}
        if r.status_code == 200:
            content = r.json().get("result", {}).get("content", [])
            if content and content[0].get("type") == "text":
                try:
                    data = json.loads(content[0]["text"])
                except json.JSONDecodeError:
                    data = {}
        steps, files, decisions = data.get("steps", []), data.get("files", []), data.get("needs_your_decision", [])
        chk.ok("clean_dataset ran", bool(data), r.text[:150])
        chk.ok(f"safe fixes applied ({len(steps)} steps)", len(steps) > 0)
        chk.ok(f"risky items held for approval ({len(decisions)})", len(decisions) > 0)
        chk.ok(f"download links returned ({len(files)} files)", len(files) > 0)
        for f in files:
            dr = c.get(f["download_url"], timeout=60)
            chk.ok(f"download '{f['kind']}' works ({f['bytes']} bytes)", dr.status_code == 200 and len(dr.content) > 0, f"HTTP {dr.status_code}")
        if data.get("dataset_id"):
            r = rpc(c, base, key, "tools/call", {"name": "delete_dataset", "arguments": {"dataset_id": data["dataset_id"]}}, id_=4)
            chk.ok("delete_dataset cleans up", r.status_code == 200)

    print()
    if chk.failures:
        print(f"{chk.failures} check(s) FAILED -- fix these before submitting to Muse.")
        return 1
    print("ALL CHECKS PASSED -- this deployment is ready for the Muse submission form (muse/submission.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
