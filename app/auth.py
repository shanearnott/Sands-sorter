from __future__ import annotations

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import HTTPException, Request, status
from starlette.responses import RedirectResponse

from app.config import get_settings

oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def _client():
    settings = get_settings()
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Google OAuth not configured"
        )
    google = oauth.create_client("google")
    google.client_id = settings.google_oauth_client_id
    google.client_secret = settings.google_oauth_client_secret
    return google


async def login(request: Request) -> RedirectResponse:
    settings = get_settings()
    return await _client().authorize_redirect(request, settings.google_oauth_redirect_url)


async def callback(request: Request) -> RedirectResponse:
    try:
        token = await _client().authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"OAuth error: {exc.error}") from exc

    userinfo = token.get("userinfo") or {}
    email = (userinfo.get("email") or "").lower()
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No email in OIDC response")

    allowlist = get_settings().allowed_emails_list
    if allowlist and email not in allowlist:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{email} is not authorised")

    request.session["user_email"] = email
    request.session["user_name"] = userinfo.get("name", email)
    return RedirectResponse(url="/", status_code=302)


def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


def current_user(request: Request) -> str:
    email = request.session.get("user_email")
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return email


def optional_user(request: Request) -> str | None:
    return request.session.get("user_email")
