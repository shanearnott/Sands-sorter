from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import select

from app import auth
from app.config import get_settings, set_runtime_overrides
from app.db import SessionLocal
from app.models import AppConfig
from app.utils.logging import configure_logging
from app.web.import_routes import router as import_router
from app.web.internal_routes import router as internal_router
from app.web.m4_routes import router as m4_router
from app.web.m5_routes import router as m5_router
from app.web.password_routes import router as password_router
from app.web.routes import router as web_router
from app.web.scope_routes import router as scope_router
from app.web.settings_routes import router as settings_router

BASE_DIR = Path(__file__).resolve().parent


def _load_runtime_overrides() -> None:
    try:
        with SessionLocal() as db:
            rows = db.scalars(select(AppConfig)).all()
            set_runtime_overrides({r.key: r.value for r in rows})
    except Exception:
        # DB might not be migrated yet on first boot — leave overrides empty.
        set_runtime_overrides({})


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    _load_runtime_overrides()

    app = FastAPI(title="Sands-sorter", version="0.1.0")
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")

    static_dir = BASE_DIR / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(web_router)
    app.include_router(import_router)
    app.include_router(scope_router)
    app.include_router(password_router)
    app.include_router(m4_router)
    app.include_router(m5_router)
    app.include_router(internal_router)
    app.include_router(settings_router)

    @app.get("/auth/login")
    async def login(request: Request):
        return await auth.login(request)

    @app.get("/auth/callback")
    async def callback(request: Request):
        return await auth.callback(request)

    @app.get("/auth/logout")
    async def logout(request: Request):
        return auth.logout(request)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
