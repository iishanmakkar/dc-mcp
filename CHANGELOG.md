# Changelog

## 2.0.1 -- verification pass

* Verified end to end over real HTTP (uvicorn + official MCP client): auth (401 without key), 10 MCP tools,
  one-shot `clean_dataset` (1,012-row sample: safe fixes applied, 10 items routed to `needs_your_decision`),
  two-step flow (scan -> plan -> apply with `approve_ids` -> export -> download of the actual bytes),
  free-tier paywalls (5 files/day, csv-only export) returning plain-language upgrade messages, upload slots,
  ~1 h link TTL, tenant isolation between API keys, and clean tool errors for unknown dataset ids.
* The committed notebook was re-executed top to bottom in a fresh kernel (0 errors) and regenerated from
  `tools/build_notebook.py` so it stays byte-identical to the engine.
* `requirements.txt`: `fastapi>=0.121` (older FastAPI breaks against the Starlette that `mcp>=2` pulls in).
* Test deps (`nbconvert`, `ipykernel`, `mcp`, `markdown`) were missing from the environment during the first
  run -- `requirements-dev.txt` already lists them; install it before `pytest`.

## 2.0.0 -- Muse-ready rebuild

### Fixed (bugs found in the uploaded notebook)
* **The `AgenticDataCleaner` class was missing.** Cell "7 - The agent" held only an orphaned `make_plan` method (a syntax
  error); a fresh *Run all* failed with `NameError`. The class is restored: `ingest / scan / report / make_plan / review /
  apply / verify / run / act / instruct / audit_frame / export`, same log format and file names as before.
* **JSON / JSONL export crashed** (`to_json() got an unexpected keyword argument 'na_rep'`). Missing values are now written as `null`.
* **`unify_categories` could re-introduce whitespace**: the "canonical" spelling was chosen by frequency first, so a padded
  variant that was more common than the clean one won. Trimmed spellings now win.
* **`male`/`female` were treated as booleans** and a gender column was converted to True/False. Removed.
* **Zip codes, phone and account numbers were candidates for numeric casting / imputation**, which destroys leading zeros. Identifier-like columns are now never cast or filled.
* **Date columns were reported as phone-number PII**, and as "ambiguous categories". Fixed. Phone numbers starting with `(` are now detected.
* **Placeholder tokens were capped at six** in the fix (`N/A, ?, -, ...`); the fix now replaces every token found.
* **`N/A` / `NA` / `null` never reached the sentinel check** because pandas converts them while reading (and turns the country code `NA` into a missing value). CSV/Excel are now read with only blank cells as missing.
* **Counts were inflated**: `strip_whitespace` / `fix_encoding` counted every missing value as a "changed" value.
* **A model could downgrade risk**, e.g. label "delete duplicate rows" as `low` so it auto-applied. Risk now has a floor per action that a model cannot lower.
* **Unsalted hashing of PII** (phone numbers can be reversed with a lookup table). A random salt is used unless you pass one.
* Pickle/HDF5 (arbitrary code execution when loaded) now need `allow_pickle=True`.
* Notebook run cell no longer contains a personal Windows path in its saved output; `warnings.filterwarnings("ignore")` and `pd.set_option` are notebook-only, not import-time side effects.

### Changed (behaviour you may notice)
* Imputation (`fill_missing`) and non-flag outlier handling are **high risk** and no longer auto-apply (they invent or alter data). Outliers are *flagged* by default.
* Ambiguous dates can be resolved with `date_order="dmy" | "mdy"`.
* Plan entries the agent refuses now explain why (e.g. "Not imputed: filling text values would invent data").
* The CSV/text export default is still `NA` in the notebook; the hosted server exports blanks so files round-trip.

### Added
* `datacleaner/` package (single-file engine, also the source of the notebook), `tools/build_notebook.py`.
* `server/`: MCP + REST service, bearer-key auth, per-client isolation, TTL storage, SSRF-safe URL fetch, upload slots,
  free/paid tiers and usage metering, hardened file parsing.
* `redact_llm_context` to keep cell values out of model prompts; prompt-injection wording in the planner prompt.
* 89 automated tests, including a live MCP client test and a full top-to-bottom notebook execution in a fresh kernel.
