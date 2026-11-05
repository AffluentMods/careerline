# SPDX-License-Identifier: Apache-2.0
"""Single sign-on with OpenID Connect: authorization code flow with PKCE.

Works with standard providers such as Authelia, Authentik and Keycloak. The provider is
configured by the server admin (``CAREERLINE_OIDC_*``), so its addresses may be on the LAN;
they must still use https (plain http only for localhost).

The flow state (state, nonce, PKCE verifier, where to return) lives in a short-lived,
encrypted, HttpOnly cookie, so a callback only works in the browser that started it. The
ID token is verified against the provider's published keys: signature, issuer, audience,
expiry and nonce. Provider error text is never shown; the browser gets a short code.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote, urlencode, urlsplit

import jwt
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from careerline import audit
from careerline.auth.sessions import (
    ACTIVE,
    DB,
    CipherDep,
    CurrentUser,
    OptionalSession,
    SettingsDep,
    csrf_protect,
    start_session,
    utcnow,
)
from careerline.config import Settings
from careerline.errors import ApiError
from careerline.models import OidcIdentity, User
from careerline.net import FetchError, SafeFetcher
from careerline.security import contexts
from careerline.security.crypto import CryptoError, FieldCipher
from careerline.security.ratelimit import check_and_hit

router = APIRouter(prefix="/api/v1/auth/oidc", tags=["auth"], dependencies=[Depends(csrf_protect)])

FLOW_SECONDS = 600
ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)
_FLOW_CONTEXT = contexts.OIDC_FLOW
_CACHE_SECONDS = 3600
_JWKS_REFRESH_SECONDS = 60
_LEEWAY_SECONDS = 60
# Same lock id as password registration, so the two can't both create the first owner.
_REGISTER_LOCK_ID = 7_240_001
_NEXT_RE = re.compile(r"^/(?![/\\])[A-Za-z0-9/_\-.?=&%]*$")

ERROR_CODES = frozenset(
    {
        "not_configured",
        "flow_expired",
        "state_mismatch",
        "provider_denied",
        "provider_unreachable",
        "provider_insecure",
        "provider_mismatch",
        "token_failed",
        "bad_token",
        "no_account",
        "email_required",
        "email_taken",
        "linked_elsewhere",
        "session_changed",
        "inactive",
    }
)


class OidcError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Provider:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str
    auth_methods: tuple[str, ...]


_cache: dict[str, tuple[float, Any]] = {}


def clear_cache() -> None:
    _cache.clear()


def flow_cookie(settings: Settings) -> str:
    return "__Host-cl_oidc" if settings.cookie_secure else "cl_oidc"


def _fetcher(request: Request) -> SafeFetcher:
    factory = getattr(request.app.state, "oidc_fetcher_factory", None)
    return factory() if factory else SafeFetcher(allow_private=True)


def safe_next(value: str | None) -> str:
    """Only same-site paths: no scheme, no host, no protocol-relative tricks."""
    if value and len(value) <= 300 and _NEXT_RE.match(value):
        return value
    return "/"


def _checked_url(url: object) -> str:
    if not isinstance(url, str) or not url:
        raise OidcError("provider_mismatch")
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme == "https" or (parts.scheme == "http" and local):
        return url
    raise OidcError("provider_insecure")


async def _get_json(fetcher: SafeFetcher, url: str, **kw: Any) -> Any:
    try:
        res = await fetcher.get(url, max_redirects=0, max_bytes=512_000, **kw)
    except FetchError:
        raise OidcError("provider_unreachable") from None
    if res.status != 200:
        raise OidcError("provider_unreachable")
    try:
        return res.json()
    except ValueError:
        raise OidcError("provider_unreachable") from None


async def provider(settings: Settings, fetcher: SafeFetcher) -> Provider:
    configured = settings.oidc_issuer.rstrip("/")
    key = f"discovery:{configured}"
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        cached: Provider = hit[1]
        return cached
    doc = await _get_json(fetcher, _checked_url(f"{configured}/.well-known/openid-configuration"))
    if not isinstance(doc, dict) or str(doc.get("issuer", "")).rstrip("/") != configured:
        raise OidcError("provider_mismatch")
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    prov = Provider(
        issuer=str(doc["issuer"]),
        authorization_endpoint=_checked_url(doc.get("authorization_endpoint")),
        token_endpoint=_checked_url(doc.get("token_endpoint")),
        jwks_uri=_checked_url(doc.get("jwks_uri")),
        userinfo_endpoint=_checked_url(doc["userinfo_endpoint"])
        if doc.get("userinfo_endpoint")
        else "",
        auth_methods=tuple(str(m) for m in methods if isinstance(m, str)),
    )
    _cache[key] = (time.monotonic() + _CACHE_SECONDS, prov)
    return prov


async def _jwks(fetcher: SafeFetcher, prov: Provider, *, refresh: bool) -> list[dict[str, Any]]:
    key = f"jwks:{prov.jwks_uri}"
    hit = _cache.get(key)
    fresh_enough = hit and (time.monotonic() - hit[1][0]) < _JWKS_REFRESH_SECONDS
    if hit and hit[0] > time.monotonic() and (not refresh or fresh_enough):
        keys: list[dict[str, Any]] = hit[1][1]
        return keys
    doc = await _get_json(fetcher, prov.jwks_uri)
    found = doc.get("keys") if isinstance(doc, dict) else None
    if not isinstance(found, list):
        raise OidcError("provider_unreachable")
    keys = [k for k in found if isinstance(k, dict)]
    _cache[key] = (time.monotonic() + _CACHE_SECONDS, (time.monotonic(), keys))
    return keys


def _pick_key(keys: list[dict[str, Any]], kid: str | None, alg: str) -> Any:
    candidates = [
        k
        for k in keys
        if k.get("use", "sig") == "sig"
        and k.get("alg", alg) == alg
        and (kid is None or k.get("kid") == kid)
    ]
    if kid is None and len(candidates) != 1:
        return None
    for jwk in candidates:
        try:
            return jwt.PyJWK(jwk, algorithm=alg).key
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            continue
    return None


async def verify_id_token(
    settings: Settings, fetcher: SafeFetcher, prov: Provider, token: str, nonce: str
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise OidcError("bad_token") from None
    alg, kid = header.get("alg"), header.get("kid")
    if alg not in ALGORITHMS or (kid is not None and not isinstance(kid, str)):
        raise OidcError("bad_token")
    key = _pick_key(await _jwks(fetcher, prov, refresh=False), kid, alg)
    if key is None:  # the provider may have rotated its keys
        key = _pick_key(await _jwks(fetcher, prov, refresh=True), kid, alg)
    if key is None:
        raise OidcError("bad_token")
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key,
            algorithms=[alg],
            audience=settings.oidc_client_id,
            issuer=prov.issuer,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError:
        raise OidcError("bad_token") from None
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("bad_token")
    aud = claims["aud"]
    azp = claims.get("azp")
    if (isinstance(aud, list) and len(aud) > 1 and azp is None) or (
        azp is not None and azp != settings.oidc_client_id
    ):
        raise OidcError("bad_token")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255:
        raise OidcError("bad_token")
    return claims


async def _exchange(
    settings: Settings, fetcher: SafeFetcher, prov: Provider, code: str, verifier: str
) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_callback_url,
        "code_verifier": verifier,
    }
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    secret = settings.oidc_client_secret.get_secret_value() if settings.oidc_client_secret else ""
    if secret and "client_secret_basic" in prov.auth_methods:
        # RFC 6749 2.3.1: both parts are form-encoded before base64.
        pair = f"{quote(settings.oidc_client_id, safe='')}:{quote(secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    else:
        form["client_id"] = settings.oidc_client_id
        if secret:
            form["client_secret"] = secret
    try:
        res = await fetcher.post(
            prov.token_endpoint,
            headers=headers,
            content=urlencode(form).encode(),
            max_redirects=0,
            max_bytes=512_000,
        )
    except FetchError:
        raise OidcError("provider_unreachable") from None
    if res.status != 200:
        raise OidcError("token_failed")
    try:
        data = res.json()
    except ValueError:
        raise OidcError("token_failed") from None
    if not isinstance(data, dict) or not isinstance(data.get("id_token"), str):
        raise OidcError("token_failed")
    return data


async def _profile(
    fetcher: SafeFetcher, prov: Provider, claims: dict[str, Any], access_token: object
) -> dict[str, Any]:
    """Email and name, from the ID token or else the userinfo endpoint (Authelia's default)."""
    info = dict(claims)
    if not claims.get("email") and prov.userinfo_endpoint and isinstance(access_token, str):
        try:
            extra = await _get_json(
                fetcher,
                prov.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except OidcError:
            extra = None  # best effort: the email is only needed to create an account
        if isinstance(extra, dict) and extra.get("sub") == claims["sub"]:
            for k in ("email", "email_verified", "name", "preferred_username"):
                if k in extra:
                    info[k] = extra[k]
    email = info.get("email")
    info["email"] = email.strip().lower() if isinstance(email, str) and "@" in email else ""
    info["email_verified"] = info.get("email_verified") is True
    return info


# --- flow cookie -------------------------------------------------------------------------------


def _write_flow(
    response: Response, settings: Settings, cipher: FieldCipher, flow: dict[str, Any]
) -> None:
    response.set_cookie(
        flow_cookie(settings),
        cipher.encrypt(json.dumps(flow), _FLOW_CONTEXT),
        max_age=FLOW_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        # Lax so the cookie comes back on the provider's top-level redirect to the callback.
        samesite="lax",
        path="/",
    )


def _read_flow(cipher: FieldCipher, raw: str | None) -> dict[str, Any]:
    if not raw:
        raise OidcError("flow_expired")
    try:
        flow = json.loads(cipher.decrypt(raw, _FLOW_CONTEXT))
    except (CryptoError, ValueError):
        raise OidcError("flow_expired") from None
    if not isinstance(flow, dict) or time.time() - float(flow.get("t", 0)) > FLOW_SECONDS:
        raise OidcError("flow_expired")
    return flow


def _clear_flow(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        flow_cookie(settings), path="/", secure=settings.cookie_secure, httponly=True
    )


async def _begin(
    request: Request,
    settings: Settings,
    cipher: FieldCipher,
    response: Response,
    *,
    next_path: str,
    bound_user: uuid.UUID | None,
) -> str:
    if not settings.oidc_enabled:
        raise ApiError(404, "not_found", "Single sign-on isn't set up on this server.")
    try:
        prov = await provider(settings, _fetcher(request))
    except OidcError:
        raise ApiError(
            502, "sso_unavailable", "The sign-in provider couldn't be reached. Try again later."
        ) from None
    verifier = secrets.token_urlsafe(64)
    flow = {
        "s": secrets.token_urlsafe(32),
        "n": secrets.token_urlsafe(32),
        "v": verifier,
        "next": safe_next(next_path),
        "u": str(bound_user) if bound_user else None,
        "t": time.time(),
    }
    _write_flow(response, settings, cipher, flow)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_callback_url,
        "scope": settings.oidc_scopes,
        "state": flow["s"],
        "nonce": flow["n"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if bound_user:
        params["prompt"] = "login"  # confirming identity: ask the provider to sign in again
    sep = "&" if "?" in prov.authorization_endpoint else "?"
    return f"{prov.authorization_endpoint}{sep}{urlencode(params)}"


# --- account resolution ---------------------------------------------------------------------


async def _resolve(
    db: AsyncSession,
    settings: Settings,
    issuer: str,
    info: dict[str, Any],
    bound_user: uuid.UUID | None,
) -> tuple[User, str]:
    """Find (or link, or create) the CareerLine user. Returns the user and the audit event."""
    sub, email = info["sub"], info["email"]
    identity = await db.scalar(
        select(OidcIdentity).where(OidcIdentity.issuer == issuer, OidcIdentity.subject == sub)
    )
    user: User | None = None
    event = "auth.login"
    if bound_user is not None:
        if identity is not None and identity.user_id != bound_user:
            raise OidcError("linked_elsewhere")
        user = await db.get(User, bound_user)
        event = "auth.reauth" if identity else "auth.sso_linked"
    elif identity is not None:
        user = await db.get(User, identity.user_id)
    else:
        if settings.oidc_link_by_email and email and info["email_verified"]:
            user = await db.scalar(select(User).where(User.email == email))
            event = "auth.sso_linked"
        if user is None:
            user = await _create_user(db, settings, info)
            event = "auth.register"
    if user is None or not user.is_active:
        raise OidcError("inactive")
    if identity is None:
        identity = OidcIdentity(user_id=user.id, issuer=issuer, subject=sub)
        db.add(identity)
    identity.email = email
    identity.last_login_at = utcnow()
    return user, event


async def _create_user(db: AsyncSession, settings: Settings, info: dict[str, Any]) -> User:
    await db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _REGISTER_LOCK_ID})
    has_users = bool(await db.scalar(select(exists().select_from(User))))
    allowed = (not has_users) or settings.allow_registration or settings.oidc_auto_create
    if settings.demo_mode or not allowed:
        raise OidcError("no_account")
    email = info["email"]
    if not email:
        raise OidcError("email_required")
    if await db.scalar(select(exists().where(User.email == email))):
        raise OidcError("email_taken")
    name = next(
        (
            str(info[k]).strip()[:100]
            for k in ("name", "preferred_username")
            if isinstance(info.get(k), str) and info[k].strip()
        ),
        email.split("@")[0][:100],
    )
    user = User(email=email, display_name=name, password_hash=None, is_owner=not has_users)
    db.add(user)
    await db.flush()
    return user


# --- routes ---------------------------------------------------------------------------------


@router.get("/start")
async def start(
    request: Request, db: DB, settings: SettingsDep, cipher: CipherDep, next: str = "/"
) -> RedirectResponse:
    await check_and_hit(db, f"oidc:ip:{audit.client_ip(request)}", 60, timedelta(minutes=5))
    await db.commit()
    response = RedirectResponse("/", status_code=303)
    url = await _begin(request, settings, cipher, response, next_path=next, bound_user=None)
    response.headers["location"] = url
    return response


class BoundStartIn(BaseModel):
    purpose: Literal["link", "reauth"]
    next: str = Field(default="/settings/security", max_length=300)


class StartOut(BaseModel):
    url: str


@router.post("/start")
async def start_bound(
    body: BoundStartIn,
    request: Request,
    response: Response,
    settings: SettingsDep,
    cipher: CipherDep,
    auth: CurrentUser,
) -> StartOut:
    """Link a provider account to the signed-in user, or confirm identity through it."""
    url = await _begin(
        request, settings, cipher, response, next_path=body.next, bound_user=auth.user.id
    )
    return StartOut(url=url)


def _with_error(path: str, code: str) -> str:
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}sso_error={code}"


@router.get("/callback")
async def callback(
    request: Request,
    db: DB,
    settings: SettingsDep,
    cipher: CipherDep,
    session: OptionalSession,
    code: str = "",
    state: str = "",
    error: str = "",
) -> RedirectResponse:
    failure_path = "/login"
    await check_and_hit(db, f"oidc:ip:{audit.client_ip(request)}", 60, timedelta(minutes=5))
    await db.commit()
    try:
        if not settings.oidc_enabled:
            raise OidcError("not_configured")
        flow = _read_flow(cipher, request.cookies.get(flow_cookie(settings)))
        bound = uuid.UUID(flow["u"]) if flow.get("u") else None
        if bound is not None:
            failure_path = flow["next"]
            if session is None or session.user_id != bound or session.state != ACTIVE:
                raise OidcError("session_changed")
        if error:
            raise OidcError("provider_denied")
        if not state or len(state) > 200 or not hmac.compare_digest(state, flow["s"]):
            raise OidcError("state_mismatch")
        if not code or len(code) > 2000:
            raise OidcError("token_failed")
        fetcher = _fetcher(request)
        prov = await provider(settings, fetcher)
        tokens = await _exchange(settings, fetcher, prov, code, flow["v"])
        claims = await verify_id_token(settings, fetcher, prov, tokens["id_token"], flow["n"])
        info = await _profile(fetcher, prov, claims, tokens.get("access_token"))
        user, event = await _resolve(db, settings, prov.issuer, info, bound)
    except OidcError as exc:
        await db.rollback()
        audit.record(db, request, "auth.sso_failed", None, reason=exc.code)
        await db.commit()
        response = RedirectResponse(_with_error(failure_path, exc.code), status_code=303)
        _clear_flow(response, settings)
        return response

    response = RedirectResponse(flow["next"], status_code=303)
    # Never keep a session from before sign-in (prevents session fixation).
    if session is not None:
        await db.delete(session)
    await start_session(db, request, response, settings, user, ACTIVE, method="oidc")
    audit.record(db, request, event, user.id, method="oidc", issuer=prov.issuer)
    await db.commit()
    _clear_flow(response, settings)
    return response


class IdentityOut(BaseModel):
    id: uuid.UUID
    issuer: str
    email: str
    created_at: datetime
    last_login_at: datetime | None


@router.get("/identities")
async def identities(auth: CurrentUser, db: DB) -> list[IdentityOut]:
    rows = await db.scalars(
        select(OidcIdentity)
        .where(OidcIdentity.user_id == auth.user.id)
        .order_by(OidcIdentity.created_at)
    )
    return [IdentityOut.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/identities/{identity_id}", status_code=204)
async def unlink(identity_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB) -> Response:
    uid = auth.user.id
    row = await db.scalar(
        select(OidcIdentity).where(OidcIdentity.user_id == uid, OidcIdentity.id == identity_id)
    )
    if row is None:
        raise ApiError(404, "not_found", "Not found.")
    others = await db.scalar(
        select(exists().where(OidcIdentity.user_id == uid, OidcIdentity.id != row.id))
    )
    if not auth.user.has_password and not others:
        raise ApiError(
            409, "last_sign_in_method", "Set a password first, or you won't be able to sign in."
        )
    await db.delete(row)
    audit.record(db, request, "auth.sso_unlinked", uid, issuer=row.issuer)
    await db.commit()
    return Response(status_code=204)
