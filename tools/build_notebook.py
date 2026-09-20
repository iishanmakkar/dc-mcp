"""Build agentic_data_cleaner.ipynb from datacleaner/engine.py so the notebook can never drift
from the tested code.   python tools/build_notebook.py [out.ipynb]
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = (ROOT / "datacleaner" / "engine.py").read_text(encoding="utf-8")
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "agentic_data_cleaner.ipynb"


def sections():
    parts = re.split(r"^# %% SECTION: (\w+) \| (.+)$", ENGINE, flags=re.M)
    out = {}
    for i in range(1, len(parts), 3):
        marker = f"# %% SECTION: {parts[i]} | {parts[i + 1]}\n"
        out[parts[i]] = (parts[i + 1], marker + parts[i + 2].strip("\n") + "\n")
    return out


S = sections()
cells = []


def md(text):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)})


def code(text):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                  "source": text.strip("\n").splitlines(True)})


md("""
# Agentic AI Data Cleaner

**One notebook. Any dataset, any size, any file format. Bring your own model -- or none at all.**

This is a closed-loop cleaning agent, not a script of `dropna()` calls:

```
 ingest -> scan -> REPORT TO YOU -> plan -> ASK YOU -> act -> verify -> re-plan -> export
```

| Stage | What happens |
|---|---|
| **Ingest** | csv, tsv, xlsx, json, jsonl, parquet, feather, orc, sqlite, stata, sas, spss, xml, html, avro, a folder of files, a glob, a URL, or a DataFrame you already have. `NA` / `N/A` / `null` stay visible instead of being silently swallowed. Pickle/HDF5 need `allow_pickle=True` because they can execute code. |
| **Scan** | 20 deterministic checks produce **findings**: exact and near duplicates, disguised nulls (`N/A`, `?`, `-`), numbers and dates stored as text, **ambiguous categories** (`India` / `india` / ` India `), **ambiguous date order** (is `03/04` March 4th or April 3rd?), mixed types, mojibake, outliers, impossible values, zero-variance and duplicate columns, PII exposure. |
| **Report** | Everything found is printed first, grouped by category and severity, with evidence and a proposed fix. Nothing is changed yet. |
| **Plan** | Your model reasons over the findings and decides what to do. No model configured? Deterministic heuristics take over and everything still works. A model can never lower an action's risk, invent an action, or touch a column that does not exist. |
| **Review** | Every change is shown with its risk and rationale. Low/medium risk applies automatically; **high risk (deleting rows/columns, filling in values, masking PII) always needs a human yes.** |
| **Act & verify** | Actions run one at a time and are logged; a failing action never corrupts the frame and can never empty the dataset. Then the data is rescanned and the agent re-plans on what is left. |
| **Export** | Cleaned data in any format, a JSON audit report, and a standalone `.py` script that replays the exact run with no LLM and no package. |

**New in 2.0:** the `AgenticDataCleaner` class that was missing from the previous export is restored, JSON/JSONL export
works again, identifier-like columns (zip codes, phone numbers) are never turned into numbers, ambiguous dates can be
resolved with `date_order="dmy"|"mdy"`, and the same engine now powers a hosted **Muse connector** (see `server/`).

---

## Setup

```bash
pip install pandas numpy openpyxl pyarrow tabulate
```

Optional model -- create a `.env` next to this notebook (or export the variables):

```ini
LLM_PROVIDER=openai          # "openai" = ANY OpenAI-compatible API
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

| Provider | `LLM_PROVIDER` | `LLM_BASE_URL` |
|---|---|---|
| OpenAI | `openai` | `https://api.openai.com/v1` |
| Anthropic | `anthropic` | `https://api.anthropic.com/v1` |
| Groq | `openai` | `https://api.groq.com/openai/v1` |
| OpenRouter | `openai` | `https://openrouter.ai/api/v1` |
| Together / Fireworks / DeepSeek | `openai` | their `/v1` endpoint |
| Ollama (local) | `openai` | `http://localhost:11434/v1` |
| LM Studio (local) | `openai` | `http://localhost:1234/v1` |
| vLLM / llama.cpp (local) | `openai` | `http://localhost:8000/v1` |

Nothing is hard-coded and no key is ever stored in the notebook. Set `redact_llm_context=True` on the agent to keep raw
cell values out of what is sent to the model.
""")

code('''
# Project folders and local environment
from pathlib import Path

BASE_DIR = Path.cwd()
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
ENV_PATH = BASE_DIR / ".env"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

env_created = not ENV_PATH.exists()
if env_created:
    ENV_PATH.write_text(
        "# Agentic Data Cleaner configuration\\n"
        "# Uncomment and set these values to enable an LLM.\\n"
        "# LLM_PROVIDER=openai\\n"
        "# LLM_BASE_URL=https://api.openai.com/v1\\n"
        "# LLM_API_KEY=\\n"
        "# LLM_MODEL=gpt-4o-mini\\n",
        encoding="utf-8",
    )

print(f"Project directory: {BASE_DIR}")
print(f"Input folder:  {INPUT_DIR}")
print(f"Output folder: {OUTPUT_DIR}")
print(f"Environment:   {ENV_PATH} ({'created' if env_created else 'already present'})")
''')

md("""
## 1 - Install dependencies

Only `pandas` and `numpy` are required. The rest are optional format readers/writers -- install the ones your data
actually needs. Run this cell once, then restart the kernel if anything was newly installed.
""")
code('''
# Required
%pip install -q "pandas>=2.0" "numpy>=1.24"

# Readers/writers for the common formats.
%pip install -q openpyxl pyarrow tabulate

# Optional readers for less common formats.
# %pip install -q xlrd pyxlsb odfpy lxml
# %pip install -q tables pyreadstat pandavro

# The LLM client uses only the standard library -- no openai/anthropic/langchain SDK needed.
print("dependencies ready")
''')

md("## 2 - Configuration and the BYO-model client")
code(S["core"][1])
code('''
import warnings
warnings.filterwarnings("ignore")
pd.set_option("display.max_columns", 250)
pd.set_option("display.width", 200)

load_dotenv(str(ENV_PATH))
LLM_CLIENT = default_llm()
print(LLM_CLIENT.describe())
LLM_CLIENT.test_connection()
''')

md("## 3 - Universal I/O: read anything, write anything")
code(S["io"][1])

md("""
## 4 - Diagnostics engine

Each check returns `Finding` objects carrying severity, evidence and a proposed fix. Add your own domain check by writing a
function and decorating it with `@check` -- the agent picks it up automatically.
""")
code(S["diagnostics"][1])

md("""
## 5 - Action registry

These are the only operations the agent may perform. The catalog is serialised and handed to the model, so the model can only
ever choose a real action with real parameters -- it cannot execute arbitrary code. Register new ones with `@action`.
""")
code(S["actions"][1] + '\nprint(f"{len(ACTIONS)} cleaning actions registered | {len(CHECKS)} diagnostic checks registered")\n')

md("""
## 6 - Planner and human-in-the-loop review

`build_plan` asks the model for a reasoned decision per finding and falls back to the deterministic rule wherever the model is
unavailable, unparseable, or proposes something outside the catalog. Guard rails: a model can raise an action's risk but never
lower it; imputation and deletion are always `high` risk; identifier columns are never cast or filled.
""")
code(S["planner"][1])

md("## 7 - The agent")
code(S["agent"][1])

md("""
## 8 - Run it on your files

Drop files into `input/` and run the next cell. Every cleaned file, audit report and replay script is written to `output/`.
`input/` ships with a small synthetic sample so the notebook runs end to end out of the box -- delete it and add your own.
""")
code('''
# Process every supported data file in input/.
SUPPORTED_INPUT_EXTS = (
    TEXT_EXT | EXCEL_EXT | SQLITE_EXT
    | {".json", ".jsonl", ".ndjson", ".parquet", ".feather", ".ft", ".orc",
       ".html", ".htm", ".xml", ".dta", ".sas7bdat", ".xpt", ".sav", ".avro"}
)                                      # pickle/HDF5 are excluded on purpose (they can run code); see ALLOW_PICKLE
input_files = sorted(
    path for path in INPUT_DIR.iterdir()
    if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_EXTS
)
if not input_files:
    raise FileNotFoundError(f"No supported data files found in {INPUT_DIR}. Copy a file there and run this cell again.")

INTERACTIVE = False                    # True -> review every decision for every file
AUTO_RISK   = "medium"                 # low/medium auto-apply; high-risk changes always wait for you
DATE_ORDER  = None                     # "dmy" or "mdy" if you know the date convention of ambiguous columns
ALLOW_PICKLE = False                   # True only for pickle/HDF5 files you created yourself

agents = {}
cleaned_frames = {}
paths = {}
for input_path in input_files:
    output_path = OUTPUT_DIR / f"{input_path.stem}_cleaned{input_path.suffix}"
    print(f"\\n{'=' * 92}\\nProcessing: {input_path.name}\\nOutput:    {output_path.name}\\n{'=' * 92}")
    current_agent = AgenticDataCleaner(
        str(input_path),
        goal="Inspect this dataset first, then choose only evidence-backed, dataset-specific cleaning actions. "
             "Preserve legitimate records and never invent identifier or contact values.",
        date_order=DATE_ORDER,
        allow_pickle=ALLOW_PICKLE,
        name=input_path.name,
    )
    cleaned_frames[input_path.name] = current_agent.run(
        interactive=INTERACTIVE,
        auto_approve_max_risk=AUTO_RISK,
        max_iterations=2,
    )
    paths[input_path.name] = current_agent.export(str(output_path))
    agents[input_path.name] = current_agent

agent = current_agent
clean = cleaned_frames
print(f"\\nProcessed {len(input_files)} input file(s) from {INPUT_DIR}")
print(f"Wrote cleaned files and audit artifacts to {OUTPUT_DIR}")
''')

md("### Audit trail -- every change, in order")
code('''
audit_tables = []
for input_name, current_agent in agents.items():
    audit = current_agent.audit_frame().copy()
    audit.insert(0, "input_file", input_name)
    audit_tables.append(audit)
audit_all = pd.concat(audit_tables, ignore_index=True) if audit_tables else pd.DataFrame()
audit_all
''')
md("### What is left after cleaning\n\nAnything still listed here needs a decision from you (high-risk changes, ambiguous dates, missing values the agent refuses to invent).")
code('''
finding_tables = []
for input_name, current_agent in agents.items():
    findings = findings_frame(current_agent.findings).copy()
    findings.insert(0, "input_file", input_name)
    finding_tables.append(findings)
findings_all = pd.concat(finding_tables, ignore_index=True) if finding_tables else pd.DataFrame()
findings_all
''')
md("### Export: data + audit report + replayable script")
code("paths")

md("""
## 9 - Driving the agent yourself

Three levels of control, from most to least autonomous.
""")
md("**a) Free-text instruction** (needs a model) -- the agent maps your sentence onto real actions:")
code('# agent.instruct("hash every email column, drop anything more than 60% empty, and clip salary outliers")')
md("**b) Direct action call** (no model needed) -- full manual control:")
code('''
# agent.act("fill_missing", columns=["age"], strategy="median")
# agent.act("mask_pii", columns=["email_address"], mode="partial")
# agent.act("drop_duplicate_rows", subset=["employee_id"], keep="first")

pd.DataFrame([{"action": a.name, "destructive": a.destructive, "params": ", ".join(a.params),
               "description": a.description} for a in ACTIONS.values()])
''')
md("**c) Step by step**, if you want to inspect between stages, and approve high-risk items by id:")
code('''
# ag = AgenticDataCleaner("your_file.parquet")
# ag.ingest()
# ag.scan()
# ag.report()                                   # read what is wrong before deciding anything
# ag.make_plan()
# ag.review(interactive=True)                   # approve / skip / edit each change
#   ...or non-interactively, approving one high-risk finding by id:
# ag.review(interactive=False, auto_approve_max_risk="medium", approve_ids=["duprow-d41d8c"])
# ag.apply()
# ag.verify()
# ag.export("clean.xlsx")
''')

md("""
## 10 - Extending it

**A new check** -- returns findings the agent will plan against:

```python
@check
def chk_email_domain(ctx):
    out = []
    for c in ctx.obj_cols:
        vals = ctx.strs(c)
        if vals.str.contains("@").mean() > 0.8:
            bad = int((~vals.str.contains(r"@[\\w.-]+\\.\\w{2,}$", regex=True)).sum())
            if bad:
                out.append(Finding(_fid("mail", c), "validity", "high", f"Malformed emails in '{c}'",
                                   f"{bad} value(s) are not valid addresses.", columns=[c],
                                   suggested_action="none"))
    return out
```

**A new action** -- immediately available to both you and the model:

```python
@action("winsorize_all", "Clip every numeric column to its 1st-99th percentile.", {})
def a_wins(df, **_):
    out = df.copy()
    for c in out.select_dtypes("number"):
        out[c] = out[c].clip(out[c].quantile(.01), out[c].quantile(.99))
    return out, "winsorised all numeric columns"
```

### Handling genuinely huge files

`load_any(path, nrows=200_000)` profiles a slice, then replay the generated `*_pipeline.py` over the full file in chunks:

```python
plan_agent = AgenticDataCleaner("huge.csv", nrows=200_000)
plan_agent.run(interactive=True)

steps = [(s.action, s.params) for s in plan_agent.log if s.ok]
with pd.read_csv(INPUT_DIR / "huge.csv", chunksize=500_000) as reader:
    for i, chunk in enumerate(reader):
        for name, params in steps:
            chunk, _ = ACTIONS[name].fn(chunk, **params)
        chunk.to_parquet(OUTPUT_DIR / f"clean_part_{i:04d}.parquet", index=False)
```

Row-local actions (casting, trimming, category unification, range enforcement, masking) are chunk-safe. Global ones
(`drop_duplicate_rows`, median imputation) need a final pass over the concatenated parts.

## 11 - Use it from Muse (or any MCP client)

The same engine ships as a hosted connector in `server/` (MCP + REST). Muse does the reasoning; the connector exposes
deterministic tools (`clean_dataset`, `scan_dataset`, `get_cleaning_plan`, `apply_cleaning_plan`, `export_dataset`, ...).
See `README.md` and `docs/monetization.md`.

---

MIT licensed. Built with pandas, numpy and the standard library -- no agent framework, no vendor lock-in.
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                   "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
OUT.write_text(json.dumps(nb, indent=1), encoding="utf-8")
print(f"wrote {OUT} ({len(cells)} cells)")
