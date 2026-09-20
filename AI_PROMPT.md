# Auto-connect prompt — paste this to any AI assistant

Copy everything below the line and paste it into Claude, ChatGPT, Cursor,
VS Code, Opencode, Antigravity or any other AI assistant. No downloads, no
install, no git: it connects the assistant to the hosted MCP server and
proves it works.

---

Connect me to the Agentic Data Cleaner MCP server. Do everything yourself and
stop only when a real file has been cleaned through the server:

1. **Configure my client:** add a remote MCP server entry pointing at
   `https://dc-mcp-26zy.onrender.com/mcp` (Streamable HTTP, no Authorization
   header needed — the server admits keyless visitors). Use the exact config
   shape my assistant expects (Antigravity: `serverUrl`; Claude/Cursor/opencode:
   `url`). Show me the snippet, tell me exactly which file to save it in for
   the assistant I am using right now, and remind me to restart/reload the
   assistant afterwards if that is required for MCP changes to take effect.

2. **Prove the handshake:** `POST https://dc-mcp-26zy.onrender.com/mcp` with
   header `Accept: application/json, text/event-stream` and body
   `{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}` must return
   all 10 tools: clean_dataset, scan_dataset, get_cleaning_plan,
   apply_cleaning_plan, apply_cleaning_action, preview_dataset, export_dataset,
   create_upload_url, delete_dataset, list_cleaning_actions.

3. **Prove a real clean:** call `clean_dataset` with
   `{"csv_text":"name,salary\n\"  Alice \",50000\nBob,N/A\n","filename":"t.csv"}`.
   The result must show safe fixes applied (whitespace trimmed, N/A nulled),
   risky items held under `needs_your_decision`, and a `cleaned_data` download
   link. Download it and show me the cleaned content: `Alice,50000` and `Bob,`
   with the salary empty (null preserved, nothing invented).

4. **Teach me the loop:** in one short paragraph explain that I paste or point
   at messy data, the assistant calls `clean_dataset`, safe fixes apply
   automatically, and anything destructive (deleting rows/columns, filling
   values, masking personal data) comes back for my explicit approval via
   `apply_cleaning_plan(approve_ids=[...])`. Originals are never modified.

Rules: every check must genuinely pass against the live server — show me the
evidence, not claims. If a call fails twice (note: the free Render host sleeps
when idle, so a first slow call is normal — retry), stop and show me the exact
error and what you tried. Docs: https://dc-mcp-26zy.onrender.com/install

---

*Agentic Data Cleaner by Ishan Makkar — https://github.com/iishanmakkar/dc-mcp*
