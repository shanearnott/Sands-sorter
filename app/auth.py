from __future__ import annotations

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import HTTPException, Request, status
from starlette.responses import RedirectResponse

from app.config import get_settings, is_local_mode, runtime_value

LOCAL_USER_EMAIL = "local@local"

oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def _allowed_emails() -> list[str]:
    raw = runtime_value("allowed_emails")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def _client():
    client_id = runtime_value("google_oauth_client_id")
    client_secret = runtime_value("google_oauth_client_secret")
    if not (client_id and client_secret):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Google OAuth not configured"
        )
    google = oauth.create_client("google")
    google.client_id = client_id
    google.client_secret = client_secret
    return google


async def login(request: Request) -> RedirectResponse:
    if is_local_mode():
        # No Google OAuth configured — there is no login flow to start.
        return RedirectResponse(url="/settings", status_code=302)
    redirect_url = runtime_value("google_oauth_redirect_url") or get_settings().google_oauth_redirect_url
    return await _client().authorize_redirect(request, redirect_url)


async def callback(request: Request) -> RedirectResponse:
    try:
        token = await _client().authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"OAuth error: {exc.error}") from exc

    userinfo = token.get("userinfo") or {}
    email = (userinfo.get("email") or "").lower()
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No email in OIDC response")

    allowlist = _allowed_emails()
    if allowlist and email not in allowlist:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{email} is not authorised")

    request.session["user_email"] = email
    request.session["user_name"] = userinfo.get("name", email)
    return RedirectResponse(url="/", status_code=302)


def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


def current_user(request: Request) -> str:
    if is_local_mode():
        return LOCAL_USER_EMAIL
    email = request.session.get("user_email")
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return email


def optional_user(request: Request) -> str | None:
    if is_local_mode():
        return LOCAL_USER_EMAIL
    return request.session.get("user_email")
