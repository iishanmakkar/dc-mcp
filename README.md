# Agentic Data Cleaner

*Your AI assistant is great at reasoning. It is terrible at spreadsheets. This fixes that.*

[![MIT license](https://img.shields.io/badge/license-MIT-111111?style=flat-square)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-111111?style=flat-square)](requirements.txt)
[![MCP Streamable HTTP](https://img.shields.io/badge/MCP-streamable--http-111111?style=flat-square)](https://dc-mcp-26zy.onrender.com/mcp)
[![Live on Render](https://img.shields.io/badge/live-render-111111?style=flat-square)](https://dc-mcp-26zy.onrender.com/healthz)
[![89 tests](https://img.shields.io/badge/tests-89_passing-111111?style=flat-square)](#development)

**Live now, no install:** site — https://dc-mcp-self.vercel.app · connector — `https://dc-mcp-26zy.onrender.com/mcp` · install guide — https://dc-mcp-26zy.onrender.com/install

---

You know the file. Exported from some system, opened once, never cleaned: `"  Alice "`, `N/A` where a date should be, two identical rows, a cabin column that is 77% empty. You paste it to your AI and it confidently invents the missing ages.

Agentic Data Cleaner puts a deterministic cleaning engine inside your AI agent: 10 MCP tools that scan, fix what's safe, hold what isn't, and hand back the file plus an audit report and a replay script. The model reasons. The engine cleans. Neither improvises.

## Before / after

You paste this:

```csv
name,joined,salary
"  Alice ",2024-01-05,50000
Bob,N/A,62000
"  Alice ",2024-01-05,50000
```

You get back this (`team_cleaned.csv`), plus the report and the replay script:

```csv
name,joined,salary
Alice,2024-01-05,50000
Bob,,62000
Alice,2024-01-05,50000
```

Whitespace trimmed, `N/A` nulled, and the duplicate row held under `needs_your_decision` until *you* approve its removal — the engine never deletes or invents data on its own. More real runs in [examples](#numbers).

## Numbers

Honest measurements from this repo's own verification runs (consumer laptop, single process):

| Dataset | Rows × cols | Missing cells | Safe steps | Time |
|---|---|---|---|---|
| Titanic (Kaggle train.csv) | 891 × 12 | 866 | 4 | ~2 s |
| Ames housing (mirror) | 2,051 × 81 | 11,040 | 36 | ~12 s |
| IBM HR attrition (Kaggle) | 1,470 × 35 | 0 | 6 | ~4 s |
| Iris | 150 × 5 | 0 | 0 held 2* | <1 s |

\* Iris genuinely contains one exact-duplicate pair (rows 101/142) — correctly held for approval, not auto-deleted.

Coverage, all green: **89-test suite** · **54 input→output format combos** (csv, tsv, txt, psv, json, jsonl, xlsx, xlsm, parquet in; csv, tsv, xlsx, json, jsonl, parquet out) · **5 real datasets** · replay script reproduces the MCP result cell-for-cell. Memory is the real constraint (~10× file size while cleaning); size caps are enforced before parsing.

## How it works

The agent stops guessing and starts calling:

```
1. SCAN    -> what is wrong? (findings with severity + evidence, changes nothing)
2. PLAN    -> what is the fix? (per-problem action, risk level, auto or needs-you)
3. APPLY   -> safe fixes run; deleting rows/columns, filling values, masking PII wait for approve_ids
4. EXPORT  -> cleaned file + audit report + replay script, time-limited links, auto-deleted in ~1 h
```

Originals are never modified. Working copies die after about an hour or on demand.

## Install

Zero setup — point any MCP client at the hosted server. No API key needed.

### Muse

Add a connector with URL `https://dc-mcp-26zy.onrender.com/mcp`. Example prompts that work immediately:

- *"Clean this customer spreadsheet: fix the whitespace, the N/A placeholders and the date formats, and give me the cleaned file."*
- *"Scan this CSV and tell me everything that's wrong with it before you change anything."*
- *"Remove the duplicate rows from this export, and show me which ones you'd remove first."*

### Claude Desktop / Cursor / VS Code

```json
{ "mcpServers": { "agentic-data-cleaner": { "url": "https://dc-mcp-26zy.onrender.com/mcp" } } }
```

Claude Desktop: same shape in `claude_desktop_config.json`. Cursor: `.cursor/mcp.json`. VS Code: MCP extension config.

### Opencode

`opencode.json`:

```json
{ "mcp": { "agentic-data-cleaner": { "type": "remote", "url": "https://dc-mcp-26zy.onrender.com/mcp" } } }
```

Restart opencode afterwards (config loads once at startup).

### Antigravity

`~/.gemini/config/mcp_config.json` (global) or `.agents/mcp_config.json` (workspace):

```json
{ "mcpServers": { "agentic-data-cleaner": { "serverUrl": "https://dc-mcp-26zy.onrender.com/mcp" } } }
```

Then MCP Servers → Refresh. (Note: Antigravity wants `serverUrl`; everyone else wants `url`. Yes, really.)

### ChatGPT / raw HTTP

Any client that speaks Streamable HTTP JSON-RPC:

```bash
curl -X POST https://dc-mcp-26zy.onrender.com/mcp \
  -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Full per-client guide with troubleshooting: https://dc-mcp-26zy.onrender.com/install

### Uninstall

| Client | Removal |
|---|---|
| Muse | Remove the connector |
| Claude Desktop / Cursor / VS Code / Antigravity | Delete the `agentic-data-cleaner` entry from the config file |
| Opencode | Delete the entry from `opencode.json`, restart |

No local files are created by the hosted server; your data expires server-side within about an hour anyway.

## The 10 tools

| Tool | What it does |
|---|---|
| `clean_dataset` | One call: scan → apply every safe fix → export links. Risky items return under `needs_your_decision` |
| `scan_dataset` | Load a file, list every problem, change nothing |
| `get_cleaning_plan` | Proposed fix per problem, with risk and "applies automatically?" |
| `apply_cleaning_plan` | Low/medium auto-apply; high-risk only via `approve_ids` (your explicit yes) |
| `apply_cleaning_action` | One specific action; risky ones need `confirm=true` |
| `preview_dataset` / `export_dataset` | Look at rows / time-limited download links (csv, tsv, xlsx, json, jsonl, parquet) |
| `create_upload_url` | One-time URL for files too big to pass inline |
| `delete_dataset` / `list_cleaning_actions` | Delete now / list every action with risks |

## Notebook (fully local, no server)

```bash
git clone https://github.com/iishanmakkar/dc-mcp.git && cd dc-mcp
pip install -r requirements.txt jupyter
jupyter lab agentic_data_cleaner.ipynb     # Run All -- replace input/uncleaned_data.csv, results land in output/
```

Or as a library: `from datacleaner import AgenticDataCleaner; AgenticDataCleaner("data.csv").run(interactive=False)`.
Nothing leaves your machine. The notebook is generated from the engine (`python tools/build_notebook.py`) so it can't drift.

## Self-host

```bash
pip install -r requirements.txt
cp .env.example .env            # set API_KEYS, PUBLIC_BASE_URL, ALLOWED_HOSTS
python -m uvicorn server.app:app --port 8000 --env-file .env   # Windows; Linux/macOS: source .env first
```

No credentials configured = the server **refuses to start** (set `ALLOW_ANONYMOUS=true` for local dev only).
Deploy configs included: `render.yaml` (Blueprint), `fly.toml`, `Dockerfile`, `vercel.json` (static site only --
serverless can't host the stateful MCP; keep the backend on Render/Fly).
Verify any deployment with `python tools/preflight.py https://your-domain <key>` (17 checks, must print ALL CHECKS PASSED).

## Let any AI install it

Paste `AI_PROMPT.md` to any assistant: it connects to the hosted server, proves the handshake, runs a real clean,
and teaches the approval loop. No git, no install.

## Security model

Enforced and tested, not promised: optional key/OAuth/auth-off modes with per-IP isolation for anonymous users;
per-client dataset isolation; unguessable expiring tokens with cascade delete; allow-listed formats only
(no pickle/HDF5/SQLite/HTML/legacy `.xls`); ragged rows and column-count caps rejected *before* pandas parses;
zip-bomb and Parquet-expansion guards; SSRF-pinned URL fetching (https/443, public IPs only); fixed action registry
(no code execution); generic errors; id-and-count logs only; DNS-rebinding protection; strict CSP.

## Development

```bash
pip install pytest httpx
pytest                # full suite: engine, planner rails, server, SSRF, tiers, live MCP client, notebook
```

When changing the engine, regenerate the notebook (`python tools/build_notebook.py agentic_data_cleaner.ipynb`) --
a test fails if it drifts.

## FAQ

**Do I need an account or key?** No. Point and clean.

**Will it delete my data?** It can't delete anything without your explicit per-item approval, and it never touches your original file.

**What if my file is huge?** 25 MB / 200,000 rows per file on the hosted server. Past that, self-host and raise the caps (memory is ~10× file size).

**Does it call an AI model?** No. Deterministic rules do everything; `LLM_*` stays off unless you turn it on.

**Why not just let the chatbot clean it?** Chat can't hold a 17 MB file, produces no audit trail, and invents missing values with total confidence. This does none of that.

## License

[MIT](LICENSE). Built by **Ishan Makkar** -- https://github.com/iishanmakkar/dc-mcp
