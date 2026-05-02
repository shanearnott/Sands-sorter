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

This branch contains **M1, M2, M2.1, M2.2** of the plan in
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
