# SPDX-License-Identifier: Apache-2.0
"""Single sign-on (OpenID Connect) against a fake identity provider with real RS256 tokens."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from careerline.auth import oidc
from careerline.config import Settings
from tests.conftest import TEST_PASSWORD
from tests.fake_web import FakeWeb

ISSUER = "https://idp.example.test"
CLIENT = "careerline"
SECRET = "client-secret-for-tests-only-0123456789"


class FakeIdp(FakeWeb):
    """Discovery, JWKS, token and userinfo endpoints. Codes map to the claims to issue."""

    def __init__(self) -> None:
        super().__init__({})
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.codes: dict[str, dict[str, Any]] = {}
        self.overrides: dict[str, Any] = {}
        self.userinfo: dict[str, Any] | None = None
        self.token_requests: list[httpx.Request] = []
        self.discovery: dict[str, Any] = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks.json",
            "userinfo_endpoint": f"{ISSUER}/userinfo",
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        }

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def sign(self, claims: dict[str, Any], **header: Any) -> str:
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid, **header})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery)
        if path == "/jwks.json":
            return httpx.Response(200, json=self.jwks())
        if path == "/userinfo":
            if self.userinfo is None:
                return httpx.Response(401)
            return httpx.Response(200, json=self.userinfo)
        if path == "/token":
            self.token_requests.append(request)
            form = parse_qs(request.content.decode())
            code = form["code"][0]
            claims = self.codes.get(code)
            if claims is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form["code_verifier"][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            if challenge.rstrip(b"=").decode() != claims.pop("_challenge"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            token = self.overrides.pop("id_token", None) or self.sign(claims)
            return httpx.Response(
                200, json={"id_token": token, "access_token": "at-123", "token_type": "Bearer"}
            )
        return httpx.Response(404)

    def approve(self, authorize_url: str, code: str, **claims: Any) -> str:
        """What the provider does after the user signs in: returns the callback query."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        now = int(time.time())
        self.codes[code] = {
            "iss": ISSUER,
            "aud": CLIENT,
            "sub": "user-1",
            "iat": now,
            "exp": now + 300,
            "nonce": q["nonce"],
            "_challenge": q["code_challenge"],
            **claims,
        }
        return f"/api/v1/auth/oidc/callback?code={code}&state={q['state']}"


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    oidc.clear_cache()
    yield
    oidc.clear_cache()


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
def sso_settings(make_settings: Callable[..., Settings]) -> Callable[..., Settings]:
    def _make(**over: Any) -> Settings:
        return make_settings(
            oidc_issuer=ISSUER,
            oidc_client_id=CLIENT,
            oidc_client_secret=SECRET,
            public_url="https://careerline.example.com",
            **over,
        )

    return _make


async def _client(
    app_factory: Callable[[Settings], httpx.AsyncClient], s: Settings, idp: FakeIdp
) -> httpx.AsyncClient:
    c = app_factory(s)
    c._transport.app.state.oidc_fetcher_factory = lambda: idp.fetcher(allow_private=True)  # type: ignore[attr-defined]
    await c.get("/api/v1/auth/status")
    return c


async def _start(c: httpx.AsyncClient, next_path: str = "/") -> str:
    r = await c.get("/api/v1/auth/oidc/start", params={"next": next_path})
    assert r.status_code == 303, r.text
    return r.headers["location"]


async def _sign_in(
    c: httpx.AsyncClient, idp: FakeIdp, code: str = "c1", **claims: Any
) -> httpx.Response:
    url = await _start(c)
    return await c.get(idp.approve(url, code, **claims))


async def test_status_and_authorize_request(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    c = await _client(app_factory, sso_settings(), idp)
    status = (await c.get("/api/v1/auth/status")).json()
    assert status["sso"] == {"enabled": True, "label": "single sign-on"}
    url = await _start(c)
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{ISSUER}/authorize"
    assert q["client_id"] == [CLIENT] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://careerline.example.com/api/v1/auth/oidc/callback"]
    assert q["code_challenge_method"] == ["S256"] and len(q["state"][0]) > 30
    cookie = c.cookies.get("__Host-cl_oidc")
    assert cookie and q["state"][0] not in cookie  # the flow state is encrypted


async def test_first_sign_in_creates_the_owner(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    c = await _client(app_factory, sso_settings(), idp)
    r = await _sign_in(
        c, idp, email="Jordan@Example.com", email_verified=True, name="Jordan Rivera"
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    me = (await c.get("/api/v1/auth/me")).json()
    assert me["email"] == "jordan@example.com" and me["is_owner"] is True
    assert me["display_name"] == "Jordan Rivera" and me["has_password"] is False
    # Client authentication used HTTP Basic with the secret.
    auth = idp.token_requests[0].headers["authorization"]
    assert base64.b64decode(auth.split()[1]).decode() == f"{CLIENT}:{SECRET}"
    # Signing in again finds the same account.
    await c.post("/api/v1/auth/logout")
    r = await _sign_in(c, idp, code="c2", email="jordan@example.com")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert (await c.get("/api/v1/auth/me")).json()["id"] == me["id"]
    identities = (await c.get("/api/v1/auth/oidc/identities")).json()
    assert len(identities) == 1 and identities[0]["issuer"] == ISSUER


@pytest.mark.parametrize(
    ("claims", "header", "code"),
    [
        ({"aud": "someone-else"}, {}, "bad_token"),
        ({"iss": "https://evil.example"}, {}, "bad_token"),
        ({"exp": 1}, {}, "bad_token"),
        ({"nonce": "replayed"}, {}, "bad_token"),
        ({"aud": [CLIENT, "other"]}, {}, "bad_token"),  # several audiences and no azp
        ({"azp": "other"}, {}, "bad_token"),
        ({}, {"kid": "unknown"}, "bad_token"),
    ],
)
async def test_rejects_bad_id_tokens(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
    claims: dict[str, Any],
    header: dict[str, Any],
    code: str,
) -> None:
    c = await _client(app_factory, sso_settings(), idp)
    url = await _start(c)
    callback = idp.approve(url, "c1", email="jordan@example.com", **claims)
    if header:
        stored = dict(idp.codes["c1"])
        stored.pop("_challenge")
        idp.overrides["id_token"] = idp.sign(stored, **header)
    r = await c.get(callback)
    assert r.status_code == 303
    assert r.headers["location"] == f"/login?sso_error={code}"
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_rejects_unsigned_and_forged_tokens(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    c = await _client(app_factory, sso_settings(), idp)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for i, forge in enumerate(
        [
            lambda claims: jwt.encode(claims, None, algorithm="none"),  # type: ignore[arg-type]
            lambda claims: jwt.encode(claims, SECRET, algorithm="HS256"),
            lambda claims: jwt.encode(
                claims, other_key, algorithm="RS256", headers={"kid": idp.kid}
            ),
        ]
    ):
        url = await _start(c)
        callback = idp.approve(url, f"f{i}", email="jordan@example.com")
        claims = {k: v for k, v in idp.codes[f"f{i}"].items() if k != "_challenge"}
        idp.overrides["id_token"] = forge(claims)
        r = await c.get(callback)
        assert r.headers["location"] == "/login?sso_error=bad_token", i
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_state_and_flow_checks(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    c = await _client(app_factory, sso_settings(), idp)
    url = await _start(c)
    callback = idp.approve(url, "c1", email="jordan@example.com")
    r = await c.get(callback.replace("state=", "state=x"))
    assert r.headers["location"] == "/login?sso_error=state_mismatch"
    # The flow cookie is single-use: the real callback now fails too.
    r = await c.get(callback)
    assert r.headers["location"] == "/login?sso_error=flow_expired"
    # A callback in a different browser (no flow cookie) fails.
    other = await _client(app_factory, sso_settings(), idp)
    url = await _start(c)
    r = await other.get(idp.approve(url, "c2", email="jordan@example.com"))
    assert r.headers["location"] == "/login?sso_error=flow_expired"
    # The provider reporting an error.
    url = await _start(c)
    state = parse_qs(urlsplit(url).query)["state"][0]
    r = await c.get(f"/api/v1/auth/oidc/callback?error=access_denied&state={state}")
    assert r.headers["location"] == "/login?sso_error=provider_denied"


async def test_new_accounts_need_registration_or_auto_create(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    s = sso_settings()
    owner = await _client(app_factory, s, idp)
    await owner.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": TEST_PASSWORD, "display_name": "Owner"},
    )
    stranger = await _client(app_factory, s, idp)
    r = await _sign_in(stranger, idp, sub="user-2", email="casey@example.com", email_verified=True)
    assert r.headers["location"] == "/login?sso_error=no_account"

    auto = await _client(app_factory, sso_settings(oidc_auto_create=True), idp)
    r = await _sign_in(auto, idp, code="c2", sub="user-2", email="casey@example.com")
    assert r.headers["location"] == "/"
    me = (await auto.get("/api/v1/auth/me")).json()
    assert me["email"] == "casey@example.com" and me["is_owner"] is False

    # Same email as an existing password account: never silently merged.
    r = await _sign_in(auto, idp, code="c3", sub="user-3", email="owner@example.com")
    assert r.headers["location"] == "/login?sso_error=email_taken"


async def test_link_by_verified_email_only(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    s = sso_settings(oidc_link_by_email=True)
    owner = await _client(app_factory, s, idp)
    await owner.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": TEST_PASSWORD, "display_name": "Owner"},
    )
    owner_id = (await owner.get("/api/v1/auth/me")).json()["id"]
    c = await _client(app_factory, s, idp)
    r = await _sign_in(c, idp, email="owner@example.com", email_verified=False)
    assert r.headers["location"] == "/login?sso_error=no_account"
    r = await _sign_in(c, idp, code="c2", email="owner@example.com", email_verified=True)
    assert r.headers["location"] == "/"
    assert (await c.get("/api/v1/auth/me")).json()["id"] == owner_id


async def test_userinfo_fallback_for_email(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    c = await _client(app_factory, sso_settings(), idp)
    idp.userinfo = {"sub": "someone-else", "email": "attacker@example.com"}
    r = await _sign_in(c, idp)
    assert r.headers["location"] == "/login?sso_error=email_required"  # sub mismatch ignored
    idp.userinfo = {"sub": "user-1", "email": "jordan@example.com", "email_verified": True}
    r = await _sign_in(c, idp, code="c2")
    assert r.headers["location"] == "/"
    assert (await c.get("/api/v1/auth/me")).json()["email"] == "jordan@example.com"


async def test_link_unlink_and_reauth(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    c = await _client(app_factory, sso_settings(), idp)
    await c.post(
        "/api/v1/auth/register",
        json={"email": "jordan@example.com", "password": TEST_PASSWORD, "display_name": "J"},
    )
    r = await c.post(
        "/api/v1/auth/oidc/start", json={"purpose": "link", "next": "/settings/security"}
    )
    assert r.status_code == 200
    url = r.json()["url"]
    assert parse_qs(urlsplit(url).query)["prompt"] == ["login"]
    r = await c.get(idp.approve(url, "c1", sub="idp-jordan", email="j@corp.example"))
    assert r.headers["location"] == "/settings/security"
    identities = (await c.get("/api/v1/auth/oidc/identities")).json()
    assert [i["email"] for i in identities] == ["j@corp.example"]

    # A second account can't take over the same provider account.
    other = await _client(app_factory, sso_settings(allow_registration=True), idp)
    await other.post(
        "/api/v1/auth/register",
        json={"email": "casey@example.com", "password": TEST_PASSWORD, "display_name": "C"},
    )
    url = (await other.post("/api/v1/auth/oidc/start", json={"purpose": "link"})).json()["url"]
    r = await other.get(idp.approve(url, "c2", sub="idp-jordan"))
    assert r.headers["location"] == "/settings/security?sso_error=linked_elsewhere"

    # Password accounts can unlink.
    r = await c.delete(f"/api/v1/auth/oidc/identities/{identities[0]['id']}")
    assert r.status_code == 204


async def test_sso_only_account_rules(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    c = await _client(app_factory, sso_settings(), idp)
    await _sign_in(c, idp, email="jordan@example.com")
    r = await c.post("/api/v1/auth/reauth", json={"password": "anything at all"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "sso_reauth"
    # A fresh SSO sign-in counts as re-authentication, so export works right away.
    assert (await c.post("/api/v1/data/export")).status_code == 200
    # Can't remove the only way to sign in.
    ident = (await c.get("/api/v1/auth/oidc/identities")).json()[0]
    r = await c.delete(f"/api/v1/auth/oidc/identities/{ident['id']}")
    assert r.status_code == 409
    # Setting a first password needs no current password, only a recent sign-in.
    r = await c.post("/api/v1/auth/password", json={"new_password": TEST_PASSWORD})
    assert r.status_code == 204
    assert (await c.get("/api/v1/auth/me")).json()["has_password"] is True
    r = await c.post("/api/v1/auth/password", json={"new_password": "another long password"})
    assert r.status_code == 401  # now the current password is required


async def test_password_login_can_be_turned_off(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    make_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    c = await _client(app_factory, sso_settings(password_login=False), idp)
    status = (await c.get("/api/v1/auth/status")).json()
    assert status["password_login"] is False and status["registration_open"] is False
    r = await c.post(
        "/api/v1/auth/login", json={"email": "jordan@example.com", "password": TEST_PASSWORD}
    )
    assert r.status_code == 403
    with pytest.raises(ValueError, match="needs single sign-on"):
        make_settings(password_login=False)


async def test_discovery_must_match_issuer(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    sso_settings: Callable[..., Settings],
    idp: FakeIdp,
) -> None:
    idp.discovery["issuer"] = "https://evil.example"
    c = await _client(app_factory, sso_settings(), idp)
    r = await c.get("/api/v1/auth/oidc/start")
    assert r.status_code == 502
    oidc.clear_cache()
    idp.discovery["issuer"] = ISSUER
    idp.discovery["token_endpoint"] = "http://idp.example.test/token"  # not https
    r = await c.get("/api/v1/auth/oidc/start")
    assert r.status_code == 502


def test_safe_next() -> None:
    assert oidc.safe_next("/settings/data") == "/settings/data"
    assert oidc.safe_next("/jobs?tab=saved") == "/jobs?tab=saved"
    for bad in (
        "//evil.example",
        "/\\evil.example",
        "https://evil.example",
        "javascript:x",
        "",
        None,
    ):
        assert oidc.safe_next(bad) == "/"


async def test_disabled_when_not_configured(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/oidc/start")).status_code == 404
    r = await client.get("/api/v1/auth/oidc/callback?code=x&state=y")
    assert r.headers["location"] == "/login?sso_error=not_configured"
