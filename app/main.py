from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.config import get_settings
from app.utils.logging import configure_logging
from app.web.routes import router as web_router

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(title="Sands-sorter", version="0.1.0")
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")

    static_dir = BASE_DIR / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(web_router)

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
