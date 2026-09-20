# Will this earn money?

## Whether it will earn money

A data cleaner is a real need, but the value is in cleaning a file the user has, and most of that happens in chat with no
connector. To justify payment you'd want something Muse can't do by itself: large files, audit reports, reusable replay
scripts, or scheduled cleaning of recurring exports. Charging per file or per row through Stripe Link fits that.

That is exactly how this project is split. Nothing below is a forecast -- treat the numbers as hypotheses to test.

## What is free, what is paid

| | Free plan | Paid plan | Why people would pay |
|---|---|---|---|
| Scan + list every problem | yes | yes | Hook: shows value in one call |
| Safe automatic fixes (whitespace, `N/A`, types, dates, spelling variants) | yes | yes | Muse can *sort of* do this in chat on a small paste |
| File size | 5,000 rows / 2 MB | up to 200,000 rows / 25 MB (configurable) | **Chat can't hold a big file** |
| Files per day | 5 | unlimited | Recurring exports |
| Export formats | CSV | CSV, TSV, XLSX, JSON, JSONL, Parquet | Hand-off to BI / data-lake tools |
| **Audit report** (every change, in order, with evidence) | -- | yes | Compliance, handing data to a client or auditor |
| **Replay script** (standalone `.py`, no LLM, no package) | -- | yes | Run the *same* cleaning on next month's file |
| Scheduled cleaning of a recurring export | -- | **not built yet** | The strongest recurring-revenue feature -- see roadmap |

These limits live in `server/quota.py` and are controlled by environment variables (`FREE_MAX_ROWS`, `FREE_FILES_PER_DAY`,
...). With `BILLING_ENABLED=false` (the default) everyone gets the paid plan, so you can launch first and turn the gates on
later.

## Charging model to test

* **Per file** -- simplest to explain in chat ("clean this file: $X"). Fits people who clean a file once in a while.
* **Per row** (or per 10k rows, rounded up) -- fairer for big files and maps to your real cost (CPU/RAM per row).
* **Subscription** -- only once scheduled cleaning exists; otherwise there is nothing recurring to subscribe to.

Start with per-file pricing on the paid features, watch how many free users hit the size cap or ask for the report, and
adjust. Every billable event is written as one structured log line (`cleaner.usage`: client id hash, event, row and column
counts, tier -- never data), so you can reconcile against whatever Meta / Stripe reports.

## What I could not verify (read this before counting on income)

* **Meta has not published a revenue-share or fee schedule for connector developers** that I could find. Assume you keep what
  you charge minus payment-processor fees until the terms are public.
* **Payments run through Stripe Link.** How Muse tells *your server* that a user has paid is Meta's contract, not something
  this code can guess. `tier_for()` in `server/quota.py` is the single function to connect to it -- until then, paid clients
  can be listed in `PAID_CLIENTS`.
* **Stripe / payout availability for an individual in India**, and any tax/GST or business-registration requirement, must be
  checked directly with Stripe and Meta before you tick "my connector accepts payments".
* **Muse is US-only and 18+ at launch**, so your paying users are US users; price in USD.

## Honest unit economics

Measured on one run in the development sandbox (synthetic 6-column customer file, one-shot `clean_dataset`, single process --
your hardware will differ, so re-measure on your host):

| Rows | File size | Time | Peak process memory |
|---|---|---|---|
| 50,000 | 4.2 MB | 3.4 s | ~206 MB |
| 200,000 | 16.8 MB | 7.7 s | ~397 MB |

Memory is the real constraint: on this benchmark a 17 MB file needed roughly 10x its size in RAM while being cleaned, and
each open dataset stays in memory for the retention period. A small container handles light use; size it for
(concurrent large files) x (~10x file size). Free users cost you the same as paid users, so keep `MAX_DATASETS_PER_CLIENT` and
the free limits tight. Long requests (several seconds) are also a risk if the calling platform has a short tool timeout -- test
this against Muse before promising 200k-row files.

## Roadmap items that would make it worth paying for

1. **Scheduled cleaning of recurring exports** (weekly CSV from a shared drive -> cleaned file). Needs persistent storage,
   a scheduler and a connection to the user's file source (e.g. Google Drive) -- not built.
2. **Saved cleaning recipes** -- the replay script already contains the recipe; a hosted "run recipe X on this file" tool is a
   small addition and the natural bridge to scheduling.
3. **Team audit history** -- keep reports for a longer, user-chosen retention period.
