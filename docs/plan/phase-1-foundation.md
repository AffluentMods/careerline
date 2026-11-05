# Phase 1 Plan: Foundation

Status: **done (2026-11-04)**, built to run locally first, with no git repo yet.

**What changed from this plan while building it:**
- **No Caddy.** FastAPI serves the built UI itself, so there's still one origin and the same strict CSP, with one fewer container. TLS is left to whatever reverse proxy you already run.
- **Local mode without Docker.** `start.ps1` / `start.sh` run an embedded PostgreSQL 16 (`pgserver`, Apache-2.0, dev-only).
- **No CI yet.** It's deferred until the project goes into git. The npm license allow-list check exists (`npm run licenses`). Semgrep, gitleaks, Trivy, SBOM, and Dependabot come with CI.
- **Docker Compose is written but not run here,** because Docker isn't installed on this machine.

Goal: a repo you could make public today. It has a license, CI with security scans, local accounts with argon2id and TOTP MFA, Postgres with migrations, a base UI shell with dark mode, and `docker compose up` working end to end.

---

## 1. Decisions (defaults in bold; tell me to change any)

| Topic | Choice | Why |
|---|---|---|
| License | **Apache-2.0** + `NOTICE` + SPDX headers | Permissive, includes a patent grant, matches the spec's recommendation |
| Python | **3.12** in the container image (3.12+ supported) | 3.14 is installed locally, but 3.12 has the widest wheel support |
| Python tooling | **uv** (lockfile), ruff, mypy (strict), pytest | One fast tool for venv and lock; all MIT/Apache |
| Postgres driver | **asyncpg** (Apache-2.0) | psycopg 3 is **LGPL-3.0**, so it is flagged under the clean-room rules and left out |
| Sessions | **Server-side sessions in Postgres**, opaque random token in an `HttpOnly; Secure; SameSite=Lax` cookie. Only a SHA-256 hash of the token is stored | Can be revoked, nothing sensitive goes to the browser, no JWT pitfalls |
| CSRF | **Double-submit token** (cookie + `X-CSRF-Token` header) on every state-changing request | Works with a same-origin SPA |
| Serving | **Caddy** (Apache-2.0) serves the built SPA and proxies `/api` → FastAPI, all on **one origin** | Keeps the CSP tight, CSRF simple, and TLS automatic when exposed |
| Frontend | **Vite + React + TS**, React Router, TanStack Query, **hand-written CSS with design tokens** (no UI kit) | Small, all MIT, and the look is fully original |
| Background worker | **Not in Phase 1.** Phase 4 adds a Postgres-backed queue (`SELECT … FOR UPDATE SKIP LOCKED`, no Redis) | Keeps it simple: one fewer service |
| Field encryption | **AES-256-GCM** (`cryptography`) with a versioned key read from an env var or file | Used right away for TOTP secrets; later for API keys and contacts |

## 2. Repo layout

```
.
├── LICENSE  NOTICE  README.md  CHANGELOG.md  SECURITY.md  CONTRIBUTING.md
├── compose.yaml              # db, migrate (one-shot), api, web
├── .env.example
├── backend/
│   ├── pyproject.toml  uv.lock  Dockerfile
│   ├── alembic/              # migrations
│   ├── waypoint/
│   │   ├── main.py           # app factory, middleware
│   │   ├── config.py         # pydantic-settings; secrets from env or *_FILE
│   │   ├── db.py             # async engine/session
│   │   ├── security/         # passwords, totp, crypto, csrf, headers, ratelimit
│   │   ├── auth/             # routes, service, models (User, Session, RecoveryCode)
│   │   └── audit/            # AuditLog model + writer
│   └── tests/                # unit + API tests against a real Postgres
├── frontend/
│   ├── package.json  package-lock.json  vite.config.ts  Dockerfile (build → Caddy)
│   ├── src/  (app shell, auth pages, settings/security, theme tokens)
│   └── e2e/                  # Playwright
├── deploy/Caddyfile
├── docs/  architecture.md  threat-model.md  clean-room.md  configuration.md  plan/
└── .github/  workflows/ci.yml  workflows/release.yml  dependabot.yml
```

## 3. Backend scope

**Data (first Alembic migration):** `user`, `auth_session`, `mfa_recovery_code`, `audit_log`. Every future user-owned table gets a non-null `user_id`, and every query goes through a per-user repository helper. Tests cover cross-user access from day one.

**Endpoints (`/api/v1`):**
- `POST /auth/register`: the first user becomes the owner. After that, registration is closed unless `WAYPOINT_ALLOW_REGISTRATION=true`.
- `POST /auth/login`: on success, returns either a full session or, when MFA is on, an `mfa_pending` session that can only call `/auth/mfa/verify`.
- `POST /auth/mfa/verify`: accepts a TOTP code or a one-time recovery code.
- `POST /auth/logout`, `GET /auth/me`, `GET /auth/sessions`, `DELETE /auth/sessions/{id}`.
- `POST /auth/mfa/setup` → secret + otpauth URI (the QR code is drawn in the browser); `POST /auth/mfa/enable` (needs a valid code, returns 10 recovery codes, shown once); `POST /auth/mfa/disable` (needs password + code).
- `POST /auth/password`: change password, revoke other sessions.
- `GET /healthz` and `GET /readyz` (DB check).

**Hardening:**
- argon2id with OWASP-recommended parameters, rehashed on login when parameters change.
- Constant-time handling for unknown users, so response timing doesn't reveal which accounts exist.
- TOTP: ±1 step window; a used step can't be replayed. Secrets are encrypted at rest.
- Rate limits on login, MFA, and register: per IP and per account, kept in Postgres (no Redis).
- Security headers: strict CSP (`default-src 'self'`, no inline script), `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: no-referrer`, HSTS when TLS is on.
- Pydantic validation on every input, size limits on request bodies, generic error messages.
- Audit log entries for register, login success and failure, MFA enable/disable, recovery-code use, password change, and session revoke.

## 4. Frontend scope

- Pages: Login, MFA challenge, First-run registration, App shell (sidebar: Vault, Jobs, Tracker, Interview, Assistant, Insights as placeholders), Settings → Security (MFA enroll with QR, recovery codes, active sessions, change password).
- Light, dark, and system theme using CSS custom properties; responsive down to phone width; keyboard accessible.
- An API client that attaches the CSRF header and handles 401 → login and the `mfa_pending` → challenge redirect.

## 5. Docker Compose

`db` (postgres:16-alpine, healthcheck, named volume) → `migrate` (one-shot `alembic upgrade head`) → `api` (non-root, read-only filesystem) → `web` (Caddy + static build, port 8080). Secrets come from `.env` (copied from `.env.example`); there is a `make`-free `scripts/gen-secrets` helper to fill them in.

## 6. CI (GitHub Actions)

| Job | Tools |
|---|---|
| backend | ruff (lint + format check), mypy, pytest with a Postgres service, coverage |
| frontend | eslint, `tsc --noEmit`, vitest |
| e2e | `docker compose up`, then Playwright: register → enable MFA → logout → login with TOTP |
| security | Semgrep SAST, gitleaks (secrets), pip-audit + `npm audit`, **license gate** (fails on anything not MIT/BSD/Apache/ISC/MPL) |
| images | build both images, Trivy scan (fail on HIGH/CRITICAL), Syft SBOM; push to GHCR on `main` and tags |

Plus Dependabot for pip, npm, Docker, and Actions. All third-party Actions are pinned by commit SHA.

## 7. Docs delivered in Phase 1

README (quick start, screenshots placeholder), SECURITY.md, CHANGELOG.md, CONTRIBUTING.md (including the clean-room rule), `docs/architecture.md` (Mermaid diagram), `docs/threat-model.md` (assets, trust boundaries, STRIDE table: auth-focused now, extended each phase), `docs/configuration.md`.

## 8. Done when

- `pytest`, `vitest`, and Playwright all pass locally and in CI.
- `docker compose up` on a clean machine → open `http://localhost:8080` → register → enable MFA → log in again with TOTP.
- Semgrep, gitleaks, Trivy, and the license gate are green.

## 9. Environment notes (this machine)

- **Docker is not installed**, so I can't run `docker compose up` here until Docker Desktop is installed. Everything else (pytest against a Postgres, vitest, lint) needs either Docker or a local Postgres.
- **uv is not installed.** I'd install it with `py -m pip install uv` (user-level), or you can use `winget install astral-sh.uv`.
- Node 24 / npm 11 and Git are present. The folder is empty and not yet a git repo.
