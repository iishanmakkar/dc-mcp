# Auto-install prompt — paste this to any AI assistant

Copy everything below the line and paste it into Claude, ChatGPT, Cursor,
VS Code, Opencode or any other AI coding assistant. It will download, install,
verify and connect this MCP automatically.

---

I want to install and run the Agentic Data Cleaner MCP server from
https://github.com/iishanmakkar/dc-mcp. Do all of this yourself, step by step,
and stop only when everything is verified working:

1. **Download:** `git clone https://github.com/iishanmakkar/dc-mcp.git`
   (if git is missing, download the ZIP from that URL and extract it).
   `cd` into the cloned folder for every command below.

2. **Install:** you need Python 3.11+. Run `pip install -r requirements.txt`
   and confirm `python -c "import fastapi, mcp, pandas; print('deps ok')"`.

3. **Configure:** copy `.env.example` to `.env`. Generate a key with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"` and set:
   `API_KEYS=<that key>`, `PUBLIC_BASE_URL=http://localhost:8000`,
   `ALLOWED_HOSTS=localhost:*,127.0.0.1:*`, `AUTH_MODE=key`,
   `BILLING_ENABLED=false`. Never print or commit the key.

4. **Start:** run `python -m uvicorn server.app:app --port 8000 --env-file .env`
   (keep it running). Confirm `GET http://localhost:8000/healthz` returns
   `{"ok": true}` and `GET http://localhost:8000/` renders the homepage.

5. **Verify the MCP:** `POST http://localhost:8000/mcp` with headers
   `Authorization: Bearer <key>` and `Accept: application/json, text/event-stream`,
   body `{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}` must return
   all 10 tools. Then call `clean_dataset` with
   `{"csv_text":"name,salary\n\"  Alice \",50000\nBob,N/A\n","filename":"t.csv"}`
   and prove the cleaned download contains `Alice,50000` and `Bob,` (null kept).

6. **Run the checks:** `python tools/preflight.py http://localhost:8000 <key>`
   must print ALL CHECKS PASSED (17 checks: pages, auth, MCP handshake, a real
   clean + download). If any check fails, diagnose and fix it before continuing.

7. **Connect my client:** write the matching MCP client config for the AI
   assistant I am using right now (remote server
   `http://localhost:8000/mcp` + the bearer key), show it to me, and tell me
   exactly where to save it and that I must restart the assistant afterwards.

Rules: never skip verification; every check must genuinely pass (show me the
evidence, not claims). If a step fails twice, stop and show me the exact error
and what you tried. Prefer the hosted option when local setup is impossible:
remote server `https://dc-mcp-26zy.onrender.com/mcp` needs no install, only
the endpoint + key in the client config.

---

*Repo owner: Ishan Makkar — https://github.com/iishanmakkar/dc-mcp*
