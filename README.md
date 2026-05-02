# Sands-sorter

Automated bill scanning, classification, and filing for household property bills.

Watches Gmail attachments and a Dropbox inbox of scanned snail mail, identifies
the **scope** (a Property, a Car, or the singleton **Life** bucket for general
expenses) and spend category, then files each document into Google Drive at
`Properties/<Name>/<Category>/<Year>/`, `Cars/<Name>/<Category>/<Year>/`, or
`Life/<Category>/<Year>/`. Anything low-confidence lands in
`Unsorted/<Year>/`. A daily or weekly digest email lets you spot-check and
reassign misfiles, and a small web UI lets you train classification rules.

## Status

This branch contains **M1 through M5** of the plan in
`/root/.claude/plans/1-i-have-subscription-piped-candle.md`:

**M1 (foundation):**
- FastAPI app skeleton with Google OAuth login (allowlist).
- Postgres schema + Alembic migrations covering scopes/categories/vendors/rules/sources/processed_documents/document_extractions/reassignments/summary_runs.
- Unified `scopes` table (kind = property | car | life), with a seeded Life singleton and a `country` flag (AU | US) per scope that drives the financial-year folder.
- Properties, Cars, Life, and Categories pages.
- Drive uploader (idempotent folder chain + upload), FY-aware path builder.
- Manual `/upload` that hashes, dedups, and files to Drive.

**M2 (bulk import workhorse):**
- `direction` (expense | income) on rules and documents.
- Document AI Invoice Parser wrapper → `document_extractions` (amount, currency, doc_date, due_date, counterparty).
- Rules engine + Anthropic Claude fallback (structured outputs + prompt caching on the scope/category catalog).
- Recursive importer with `LocalTreeSource` (path on disk) and `DriveTreeSource` (Drive folder).
- `/import` wizard — start a job, process pending items in batches, decide on awaiting items (with optional "save as rule"). Originals are copied; the source folder is left untouched.
- `/rules` CRUD, `/moves` recent-documents view.
- 56 unit tests covering hashing, FY math, kind-aware path building, rules engine, Document AI extraction parsing, LLM JSON parsing, classifier pipeline, source iterators, the wizard end-to-end, and FY-grouping for the per-scope view.

**M2.1 (scope kinds refactor) + M2.2 (FY out of Drive path):**
- `ScopeKind` is now `property | personal | entity`. Cars are folded into Property (e.g. Audi S5, Tesla S sit alongside houses); Life renames to Personal; Entity is a new multi-instance kind covering trusts and companies (SANDS, Farmout).
- Migration `202604290004` does the Postgres ENUM rename-and-replace dance, renames the singleton row to "Personal", and seeds the household's real-world inventory (9 properties + 2 entities).
- Drive layout flattens: `<Kind>/<Name?>/<Category>/<file>` (no more `FY<YYYY>/`). FY stays as metadata on `processed_documents.financial_year`.
- New `/scopes/<id>` page lists every filed document for a scope, grouped by financial year (descending). Each FY group has a `Download FY<YYYY>.zip` button that streams a memory-efficient zip of every file in that scope+FY (Drive bytes piped through `zipstream-ng`).
- `/cars` and `/life` removed; `/personal` and `/entities` added; nav updated.

**M2.3 (importer preview + rule tester):**
- `/import` form gets a "Preview only" checkbox. Preview jobs run the full pipeline (hash → OCR → classify) but **never auto-file**, even on high confidence — every item lands in the awaiting queue with its proposed Drive path visible. Approving an item still does the real upload, so a preview can be promoted to a real run incrementally.
- `/rules/test` lets you paste sample text (plus optional sender email + filename) and see which rules match, in priority order, with the winning rule highlighted. Disabled rules and lower-priority matches are shown for context.
- `evaluate_all()` and public `matches()` helpers in `app/classifier/rules.py`.

**M3 (live ingestion + demo mode):**
- Pollers package: `gmail.py` and `dropbox.py` for the live path; `local_mailbox.py` and `local_dropbox.py` for demo. Demo pollers walk a local folder shaped like the real source (`.eml` files for mailbox, any supported file for Dropbox).
- `FakeDriveUploader` writes to `DEMO_DRIVE_ROOT/<path>` so the full pipeline works without Google Drive credentials.
- Fallback OCR via `pdfplumber` + `pytesseract` so demo runs without Document AI.
- Shared `app/pipeline.py` (`process_raw_doc`) runs: PDF password vault → HEIC normalisation → body-PDF render → SHA dedup → OCR → classifier (rules → LLM) → Drive upload → `ProcessedDocument` row + source-cleanup ack.
- **PDF password vault**: `/passwords` CRUD, with sender + filename matchers; `pikepdf`-based decrypt; encrypted PDFs that can't be unlocked land in Unsorted with a clear error.
- **HEIC normalisation** via `pillow-heif` (iPhone screenshots).
- **Body-PDF rendering** via WeasyPrint for senders on the source's `body_allowlist` — only fires when there's no attachment.
- **Summary digest**: builds a per-scope HTML (Income / Expenses / Net) with a "Please review" pin for Unsorted items. Sends via Gmail when configured, otherwise writes to `DEMO_DIGEST_DIR/digest-<timestamp>.html`.
- Worker CLI: `python -m app.worker poll` and `python -m app.worker summarize` (and `backfill-extractions`).
- HTTP endpoints: `POST /internal/poll`, `POST /internal/summary` for Cloud Scheduler.

**M4 (training UX polish):**
- `/vendors` CRUD with default scope/category/direction.
- `/sources` health page: Gmail/Dropbox connection status, body-PDF allowlist editor, enable/disable toggle, last-polled timestamp + last-error display.
- `/unsorted` queue: every Unsorted-status doc with an inline reassign form (scope + category + direction + "save as rule").
- `POST /moves/<doc_id>/reassign` updates a filed doc and optionally saves a learned rule.

**M5 (graphical overview):**
- `/scopes/<id>` gets four Chart.js charts (monthly income/expense/net bars, category donut, top-vendors horizontal bar, per-vendor monthly trend) above the FY-grouped tables.
- `/overview` cross-scope page with FY filter and per-scope monthly net lines.
- JSON endpoints under `/reports/...` drive the charts (cacheable, JSON-friendly shapes).
- CSV + XLSX export per scope+FY at `/scopes/<id>/export.csv` and `.xlsx`.
- `python -m app.worker backfill-extractions` re-OCRs filed docs that are missing `document_extractions`.

**Demo mode env vars (no Google auth required):**
```
DEMO_DROPBOX_ROOT=/path/to/local/dropbox     # poller watches inbox/, moves to processed/
DEMO_MAILBOX_ROOT=/path/to/local/mailbox     # poller reads inbox/*.eml
DEMO_DRIVE_ROOT=/path/to/local/drive          # FakeDriveUploader writes here
DEMO_DIGEST_DIR=/path/to/digests              # digest HTML lands here
DEMO_OCR=1                                    # forces local OCR even if Document AI is set
ANTHROPIC_API_KEY=...                          # optional — enables LLM fallback
```
First poll auto-seeds demo `Source` rows; or add real ones via `/sources` once you have credentials.

Later milestones (Dropbox poller, OCR, rules + LLM classifier, Gmail pollers,
daily digest, full training UI) live as stubs alongside the M1 code.

## Local dev

```bash
cp .env.example .env   # fill in OAuth + Drive folder IDs as needed
docker compose up -d db
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

Visit <http://localhost:8000>. Sign in with a Google account whose email is in
`ALLOWED_EMAILS`. Add a property and a category, then drop a file in `/upload`.

## Tests

```bash
pytest
```

## Layout

```
app/
  main.py             FastAPI app factory
  config.py           Settings (pydantic-settings)
  db.py               SQLAlchemy session
  models.py           ORM models
  auth.py             Google OAuth (single-user/small allowlist)
  drive/              Drive uploader + FY-aware path helpers
  extraction/         Document AI Invoice Parser wrapper
  classifier/         Rules engine + Claude fallback + pipeline
  importer/           TreeSource iterators + ImportJob orchestration
  pollers/            Gmail + Dropbox source pollers (M3)
  summary/            Daily/weekly digest (M3)
  worker/             CLI for Cloud Scheduler (M3)
  web/                Routes + Jinja templates
migrations/           Alembic
tests/                pytest
```
