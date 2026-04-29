# Sands-sorter

Automated bill scanning, classification, and filing for household property bills.

Watches Gmail attachments and a Dropbox inbox of scanned snail mail, identifies
the property and spend category, files each document into Google Drive at
`Properties/<Property>/<Category>/<Year>/`, and emails a daily or weekly digest
so you can spot-check and reassign anything misfiled. A small web UI lets you
train classification rules.

## Status

This branch contains **M1** of the plan in
`/root/.claude/plans/1-i-have-subscription-piped-candle.md`:

- FastAPI app skeleton with Google OAuth login (allowlist)
- Postgres schema + Alembic migration covering all M2-M4 tables
- Properties and Categories CRUD
- Drive uploader (idempotent folder chain + upload)
- Manual `/upload` endpoint that hashes, dedups, and files to Drive
- Unit tests for hashing, path building, and the rules engine

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
  drive/              Google Drive uploader + path helpers
  pollers/            Gmail + Dropbox source pollers (M2/M3)
  ocr/                Document AI (M2)
  classifier/         Rules engine + LLM fallback + pipeline (M2/M3)
  summary/            Daily/weekly digest (M3)
  worker/             CLI for Cloud Scheduler (M2/M3)
  web/                Routes + Jinja templates
migrations/           Alembic
tests/                pytest
```
