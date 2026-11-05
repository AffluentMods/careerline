# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import pyotp
from sqlalchemy import select, text

from careerline import db as dbmod
from careerline.config import Settings
from careerline.models import AuditLog, AuthSession, User

PASSWORD = "correct horse battery staple"
OWNER = {"email": "Owner@Example.com", "password": PASSWORD, "display_name": "Sam Owner"}


async def _register(client: httpx.AsyncClient, **over: str) -> httpx.Response:
    return await client.post("/api/v1/auth/register", json={**OWNER, **over})


async def _login(client: httpx.AsyncClient, email: str = "owner@example.com") -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})


async def _enable_mfa(client: httpx.AsyncClient) -> tuple[str, list[str]]:
    r = await client.post("/api/v1/auth/mfa/setup", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/")
    r = await client.post("/api/v1/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def _next_code(secret: str) -> str:
    # The current step was used to enable MFA; the next step is still in the window.
    return pyotp.TOTP(secret).at(time.time() + 30)


async def _scalar(stmt):  # type: ignore[no-untyped-def]
    async for s in dbmod.get_db():
        return await s.scalar(stmt)


# --- registration & status -------------------------------------------------------


async def test_status_before_and_after_first_user(client: httpx.AsyncClient) -> None:
    extra = {
        "password_login": True,
        "sso": {"enabled": False, "label": "single sign-on"},
        "demo": False,
    }
    r = await client.get("/api/v1/auth/status")
    assert r.json() == {"has_users": False, "registration_open": True, "session": "none", **extra}
    assert (await _register(client)).status_code == 201
    r = await client.get("/api/v1/auth/status")
    assert r.json() == {"has_users": True, "registration_open": False, "session": "active", **extra}


async def test_first_user_is_owner_and_signed_in(client: httpx.AsyncClient) -> None:
    r = await _register(client)
    assert r.status_code == 201
    assert r.json()["is_owner"] is True
    assert r.json()["email"] == "owner@example.com"
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "Sam Owner"


async def test_registration_closes_after_first_user(
    app_factory: Callable[[Settings], httpx.AsyncClient], settings: Settings
) -> None:
    a = app_factory(settings)
    await a.get("/api/v1/auth/status")
    await _register(a)
    b = app_factory(settings)
    await b.get("/api/v1/auth/status")
    r = await _register(b, email="other@example.com")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "registration_closed"


async def test_weak_password_rejected(client: httpx.AsyncClient) -> None:
    r = await _register(client, password="short")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "weak_password"


async def test_validation_errors_do_not_echo_input(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": "hunter2-secret-value"}
    )
    assert r.status_code == 422
    assert "hunter2-secret-value" not in r.text


async def test_unknown_fields_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/auth/register", json={**OWNER, "is_owner": True})
    assert r.status_code == 422


# --- CSRF -------------------------------------------------------------------------


async def test_post_without_csrf_header_is_rejected(
    app_factory: Callable[[Settings], httpx.AsyncClient], settings: Settings
) -> None:
    c = app_factory(settings)
    c.event_hooks["request"].clear()
    await c.get("/api/v1/auth/status")
    r = await c.post("/api/v1/auth/register", json=OWNER)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


async def test_csrf_must_match_session_token(client: httpx.AsyncClient, settings: Settings) -> None:
    await _register(client)
    # The header matches the cookie, but not the token bound to the session.
    client.cookies.set(settings.csrf_cookie, "forged", domain="testserver.local")
    r = await client.post("/api/v1/auth/logout", headers={"x-csrf-token": "forged"})
    assert r.status_code == 403
    assert (await client.get("/api/v1/auth/me")).status_code == 200


# --- login & sessions -------------------------------------------------------------


async def test_login_failure_is_generic_and_audited(client: httpx.AsyncClient) -> None:
    await _register(client)
    await client.post("/api/v1/auth/logout")
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong password!!"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong password!!"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    count = await _scalar(
        select(text("count(*)")).select_from(AuditLog).where(AuditLog.event == "auth.login_failed")
    )
    assert count == 2


async def test_login_logout_cycle(client: httpx.AsyncClient) -> None:
    await _register(client)
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    r = await _login(client)
    assert r.json()["status"] == "ok"
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_session_token_is_hashed_at_rest(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    await _register(client)
    raw = client.cookies.get(settings.session_cookie)
    assert raw
    hashes = await _scalar(
        select(text("string_agg(encode(token_hash, 'hex'), ',')")).select_from(AuthSession)
    )
    assert raw not in hashes


async def test_session_cookie_flags(client: httpx.AsyncClient) -> None:
    r = await _register(client)
    cookie = next(v for k, v in r.headers.multi_items() if k == "set-cookie" and "cl_session" in v)
    assert cookie.startswith("__Host-cl_session=")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag.lower() in cookie.lower()


async def test_login_rate_limited_per_email(client: httpx.AsyncClient) -> None:
    await _register(client)
    await client.post("/api/v1/auth/logout")
    codes = []
    for _ in range(11):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "wrong password!!"},
        )
        codes.append(r.status_code)
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


async def test_sessions_list_and_cross_user_isolation(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
) -> None:
    s = make_settings(allow_registration=True)
    alice = app_factory(s)
    await alice.get("/api/v1/auth/status")
    await _register(alice)
    bob = app_factory(s)
    await bob.get("/api/v1/auth/status")
    assert (await _register(bob, email="bob@example.com")).json()["is_owner"] is False

    alice_sessions = (await alice.get("/api/v1/auth/sessions")).json()
    assert len(alice_sessions) == 1 and alice_sessions[0]["current"] is True
    r = await bob.delete(f"/api/v1/auth/sessions/{alice_sessions[0]['id']}")
    assert r.status_code == 404
    assert (await alice.get("/api/v1/auth/me")).status_code == 200


async def test_password_change_revokes_other_sessions(
    app_factory: Callable[[Settings], httpx.AsyncClient], settings: Settings
) -> None:
    a = app_factory(settings)
    await a.get("/api/v1/auth/status")
    await _register(a)
    b = app_factory(settings)
    await b.get("/api/v1/auth/status")
    await _login(b)
    r = await a.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
    )
    assert r.status_code == 204
    assert (await a.get("/api/v1/auth/me")).status_code == 200
    assert (await b.get("/api/v1/auth/me")).status_code == 401


# --- MFA --------------------------------------------------------------------------


async def test_mfa_full_flow(client: httpx.AsyncClient) -> None:
    await _register(client)
    secret, codes = await _enable_mfa(client)
    assert len(codes) == 10
    assert (await client.get("/api/v1/auth/me")).json()["mfa_enabled"] is True

    await client.post("/api/v1/auth/logout")
    r = await _login(client)
    assert r.json() == {"status": "mfa_required", "user": None}
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "mfa_required"

    bad = await client.post("/api/v1/auth/mfa/verify", json={"code": "000000"})
    assert bad.status_code == 401
    good = await client.post("/api/v1/auth/mfa/verify", json={"code": _next_code(secret)})
    assert good.status_code == 200, good.text
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_totp_secret_encrypted_at_rest(client: httpx.AsyncClient) -> None:
    await _register(client)
    secret, _ = await _enable_mfa(client)
    stored = await _scalar(select(User.mfa_secret_enc))
    assert stored.startswith("v1:")
    assert secret not in stored


async def test_totp_code_cannot_be_replayed(client: httpx.AsyncClient) -> None:
    await _register(client)
    secret, _ = await _enable_mfa(client)
    code = _next_code(secret)
    await client.post("/api/v1/auth/logout")
    await _login(client)
    assert (await client.post("/api/v1/auth/mfa/verify", json={"code": code})).status_code == 200
    await client.post("/api/v1/auth/logout")
    await _login(client)
    assert (await client.post("/api/v1/auth/mfa/verify", json={"code": code})).status_code == 401


async def test_recovery_code_works_once(client: httpx.AsyncClient) -> None:
    await _register(client)
    _, codes = await _enable_mfa(client)
    for expected in (200, 401):
        await client.post("/api/v1/auth/logout")
        await _login(client)
        r = await client.post("/api/v1/auth/mfa/verify", json={"code": codes[0]})
        assert r.status_code == expected


async def test_pending_session_locks_after_five_failures(client: httpx.AsyncClient) -> None:
    await _register(client)
    secret, _ = await _enable_mfa(client)
    await client.post("/api/v1/auth/logout")
    await _login(client)
    for _ in range(4):
        r = await client.post("/api/v1/auth/mfa/verify", json={"code": "000000"})
        assert r.json()["error"]["code"] == "invalid_code"
    r = await client.post("/api/v1/auth/mfa/verify", json={"code": "000000"})
    assert r.json()["error"]["code"] == "mfa_locked"
    r = await client.post("/api/v1/auth/mfa/verify", json={"code": _next_code(secret)})
    assert r.status_code == 401
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_pending_session_cannot_reach_protected_routes(client: httpx.AsyncClient) -> None:
    await _register(client)
    await _enable_mfa(client)
    await client.post("/api/v1/auth/logout")
    await _login(client)
    assert (await client.get("/api/v1/auth/sessions")).status_code == 401
    r = await client.post("/api/v1/auth/mfa/setup", json={"password": PASSWORD})
    assert r.status_code == 401


async def test_disable_mfa_requires_password_and_code(client: httpx.AsyncClient) -> None:
    await _register(client)
    secret, _ = await _enable_mfa(client)
    r = await client.post(
        "/api/v1/auth/mfa/disable",
        json={"password": "wrong password!!", "code": _next_code(secret)},
    )
    assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/mfa/disable", json={"password": PASSWORD, "code": _next_code(secret)}
    )
    assert r.status_code == 204
    assert (await client.get("/api/v1/auth/me")).json()["mfa_enabled"] is False


# --- HTTP hardening ----------------------------------------------------------------


async def test_security_headers(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/healthz")
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert "max-age=" in r.headers["strict-transport-security"]


async def test_oversized_body_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login", content=b"x" * 1_100_000, headers={"content-type": "application/json"}
    )
    assert r.status_code == 413


async def test_readyz(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/readyz")).json() == {"status": "ok"}


async def test_docs_disabled_outside_development(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/docs")).status_code == 404


async def test_spa_served_with_app_csp(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>t</title>")
    c = app_factory(make_settings(static_dir=tmp_path))
    for path in ("/", "/settings/security"):
        r = await c.get(path)
        assert r.status_code == 200
        assert "script-src 'self'" in r.headers["content-security-policy"]
    assert (await c.head("/")).status_code == 200
    assert (await c.get("/api/nope")).status_code == 404
    assert (await c.get("/../pyproject.toml")).status_code in (200, 404)
    assert "hatchling" not in (await c.get("/..%2Fpyproject.toml")).text
