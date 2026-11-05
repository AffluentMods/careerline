# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-11-05 — CareerLine: AI agents over MCP, first public release

### Changed
- **Renamed from Waypoint to CareerLine** (the old name collided with HashiCorp Waypoint). Everything that carried the name changed with it: the Python package (`careerline`), settings (`CAREERLINE_*` instead of `WAYPOINT_*`), cookies (`cl_session`, `cl_csrf`, `cl_oidc`), backup files (`careerline-*.clbackup`, new file signature and key-derivation label), export file names, the Docker image and Compose project, and the two-factor issuer shown in authenticator apps. No release had shipped under the old name; backups and `.env` files made before the rename need their `WAYPOINT_` settings renamed and can't be restored by this version.

### Security
- The outbound-request guard now judges NAT64 addresses (`64:ff9b::/96`) by the IPv4 address they embed and refuses deprecated IPv4-compatible addresses (`::/96`), so a host resolving to, for example, `64:ff9b::a9fe:a9fe` can't reach `169.254.169.254` through a NAT64 gateway.

### Added
- **API access for AI agents.** Settings → API access creates personal API keys (shown once, stored hashed, scoped to read / add drafts / propose changes, with an expiry; creating one needs the password). Agents connect over MCP at `POST /api/mcp` (Claude Code, Claude Desktop, ChatGPT, and other MCP clients) and get 21 tools: read the vault, jobs, tracker, and agenda; add vault facts and STAR stories as unverified drafts marked "From API"; add jobs by link or text; and propose reminders, stage moves, notes, saving or tracking jobs, and profile changes, which wait for Confirm on the Assistant page. Keys can't reach the account, security settings, export, deletion, or anything outside the server. Rate-limited and audited (`api_token.created`, `api_token.revoked`, `api.tool`). See `docs/api-access.md`.
- The assistant can propose profile changes (name, headline, location, summary) with `update_profile`.
- GitHub Actions CI: backend lint, types, tests (against Postgres 16), and pip-audit; frontend lint, types, tests, build, license check, and runtime npm audit; Playwright end-to-end tests; and a Docker image build.
- `CAREERLINE_TRUSTED_PROXIES` (Docker Compose): the reverse proxies (IPs or CIDR ranges, such as Traefik's Docker network `172.18.0.0/16`) whose `X-Forwarded-For` / `X-Forwarded-Proto` headers are believed, so rate limits, the audit log, and the session list see real visitor IPs behind a proxy. The image no longer hard-codes `--forwarded-allow-ips 127.0.0.1`; it sets uvicorn's `FORWARDED_ALLOW_IPS=127.0.0.1` as the default instead.
- `CAREERLINE_BIND` (Docker Compose): the host address the app's port is published on. Defaults to `127.0.0.1`; set a LAN address to reach CareerLine from other devices without a reverse proxy.

### Fixed
- Confirming an assistant proposal locks it first, so two confirms at once (two tabs, a retry) apply it only once.

## [1.0.0] - 2026-11-04 — Phases 4–8: Discovery, AI, Materials, Assistant, Polish

### Added — Phase 4: Discovery
- Job sources: Greenhouse, Lever, and Ashby boards; Adzuna; USAJOBS; JSearch; RSS and Atom feeds. Each shows its terms of use. Keys are encrypted per source.
- A background worker (`python -m careerline.worker`, started by the local runner and Compose) with a Postgres job queue: scheduled fetches, retries with backoff, de-duplicated jobs, and daily maintenance.
- Normalization of pay (hourly and yearly), work mode, shifts, clearance, and dates; cross-source de-duplication by fingerprint, with every sighting kept.
- Filters: places and radius, work mode, minimum and target pay, shifts, clearance, required and excluded words, preferred and blocked companies. Matches / Filtered / Saved / Dismissed views with the reason for each.
- Add one job by link (robots.txt respected, LinkedIn, Indeed, and similar sites never fetched) or by pasting its text. Track a posting straight into the tracker.
- `SafeFetcher`: one outbound client that blocks private addresses, pins DNS, re-checks redirects, and caps response size. `CAREERLINE_OUTBOUND_ALLOW_PRIVATE`.

### Added — Phase 5: AI
- Providers: Claude (official SDK), any OpenAI-compatible API, and Ollama, configured per user in Settings → AI, with a connection test.
- Fit scores (0–100) with matched requirements, gaps, and red flags. Keyword scoring needs no AI; AI scoring runs on new matching postings and on demand. Scores go stale and are redone when the vault changes.
- Prompt-injection defenses: untrusted data blocks, standing rules, schema-validated output, and injection-looking text flagged as a red flag.
- PII redaction for hosted providers (on by default), per-call token and cost logging, a monthly budget, and a rate limit.

### Added — Phase 6: Materials
- Tailored resumes and cover letters per posting or application, by rules or AI, built only from usable vault facts.
- A fact check on every line: cited facts must exist and be verified, numbers must appear in them, and unsupported lines are flagged until edited, approved, or removed. Export is blocked while anything is flagged.
- Comparison with the master resume, PDF and Word export, and attaching the file to an application's timeline.

### Added — Phase 7: Assistant and interview prep
- An assistant that answers questions about your search with read tools and proposes changes (reminders, stage moves, notes, saving or tracking jobs) that run only after you confirm. It never sends anything outside the server.
- Interview prep: likely questions per job, matched to your STAR stories, with notes; AI-tailored questions; practice interviews with feedback and a rating per answer.

### Added — Phase 8: Polish
- Insights: response rate by source, stage funnel with conversion, time to first response, applications per week, and quiet applications, with table views.
- Notifications: a daily digest and new-match alerts by email (instance SMTP), ntfy, or Discord/Slack/JSON webhooks; encrypted addresses; test sends; a delivery log.
- Full export (JSON + files) and import into an empty account; password re-confirmation (`/auth/reauth`) for both.
- Scheduled encrypted backups of the whole database with retention, "Back up now" for the owner, and `--verify-backup` / `--restore` options on the start scripts (`python -m careerline.data` underneath).
- Single sign-on with OpenID Connect (Authelia, Authentik, Keycloak, …): PKCE, state, nonce, JWKS-verified ID tokens, account linking, optional auto-create and link-by-verified-email, and an SSO-only mode (`CAREERLINE_PASSWORD_LOGIN=false`). SSO-only accounts can set a password later.
- Demo mode (`start.ps1 -Demo`): a fictional job seeker in a separate database, with registration closed and no outbound requests.
- Encryption key rotation: `start.ps1 -RotateKey` adds a new key and re-encrypts every encrypted value with it; `-KeyStatus` shows which keys are still in use (`python -m careerline.data rekey` / `keys` underneath). Old keys can then be retired.
- Docs: user guide, operations guide (backups, restore, single sign-on with Authelia, notifications, demo, upgrades), and an MkDocs site config.
- Settings: `CAREERLINE_PUBLIC_URL`, `CAREERLINE_SMTP_*`, `CAREERLINE_IMPORT_ARCHIVE_MAX_BYTES`, `CAREERLINE_BACKUP_*`, `CAREERLINE_OIDC_*`, `CAREERLINE_PASSWORD_LOGIN`, `CAREERLINE_DEMO_MODE`.
- Audit events: `discovery.source_created`, `discovery.source_deleted`, `ai.settings_updated`, `document.generated`, `document.exported`, `assistant.action_confirmed`, `auth.reauth`, `auth.reauth_failed`, `auth.sso_linked`, `auth.sso_unlinked`, `auth.sso_failed`, `data.exported`, `data.imported`, `data.backup_requested`, `notifications.updated`, `security.rekey`. Sign-ins through single sign-on are `auth.login` / `auth.register` with `method: oidc`.
- Tests: 318 backend, 66 frontend unit, 5 Playwright end-to-end (one covering add job → score → interview prep → resume → track → insights → export → notifications).

### Changed
- `users.password_hash` may be empty (single-sign-on-only accounts); sessions record how they were created and when the user last re-authenticated.
- The first account can be created through single sign-on.
- Compose mounts the backups volume read-only into the app so the owner can see backups.
- Every encryption context now comes from one module (`careerline/security/contexts.py`); the stored values are unchanged.

### Security
- Every imported archive is validated like API input (types, vault schemas, http(s)-only links), gets fresh IDs, never carries pending assistant actions, and is size-limited before parsing.
- External links in the UI are rendered only for http(s) addresses.
- Sign-in endpoints for single sign-on are rate-limited per IP; ntfy tokens are limited to printable ASCII.
- Dependency scan at release: pip-audit (PyPI and OSV) found no known vulnerabilities in the locked Python packages; npm audit flagged only development-time lint tooling.

## [0.3.0] - 2026-11-04 — Phase 3: Tracker

### Added
- Applications with their own job details (title, company, location, work mode, pay range, currency, link, source, pasted description), priority, applied date, and apply-by deadline.
- Board view: five open stages plus a collapsed Closed column, native drag and drop, and a "Move to…" menu on every card for keyboard and touch. Sortable, filterable list view that stacks on phones.
- Timeline per application. Stage changes are recorded automatically and can't be edited. Notes, "sent" entries (which resume version), interviews, and calls or messages can be added, with file attachments.
- Attachments: PDF, DOCX, PNG, JPEG, and UTF-8 text, checked by content. Stored AES-GCM encrypted in Postgres and served download-only with a sandboxing CSP. 10 MB per file and 250 MB per user by default.
- Contacts shared across applications, with encrypted email and phone, linked to applications by role. Referrals with status and follow-up dates.
- Reminders, plus an agenda that merges reminders, apply-by dates, offer deadlines, and referral follow-ups. A follow-up reminder is offered (never created automatically) when a card moves to Applied.
- Offer comparison: yearly and hourly equivalents, bonus, equity, PTO, schedule, commute, and growth path. The best value in each row is highlighted, and offers are compared only within one currency.
- Overview: "Due soon" and "Pipeline" cards. Sidebar count of items due today or overdue.
- Audit events: `tracker.application_deleted`, `tracker.attachment_uploaded`, `tracker.attachment_deleted`, `tracker.contact_deleted`.
- Settings: `CAREERLINE_ATTACHMENT_MAX_BYTES` and `CAREERLINE_ATTACHMENT_QUOTA_BYTES`.
- Tests: 150 backend, 45 frontend unit, 4 Playwright end-to-end.

### Changed
- API responses' Content-Security-Policy now includes `sandbox`.
- Request-size overrides match route patterns, not only exact paths.
- `FieldCipher` gained `encrypt_bytes` / `decrypt_bytes` for files.

## [0.2.0] - 2026-11-04 — Phase 2: Career Vault

### Added
- Career Vault profile with encrypted email, phone, and address.
- Vault entries for roles, responsibilities, achievements (with optional metrics), projects, certifications (earned / in progress / expired), education, skills, and links. Each kind has a strict schema, and each entry has a verified flag and an evidence note.
- Verification flow: facts you type start verified, imported facts start as drafts, and bulk approve is available. An entry is *usable* by the AI only when it and every parent are verified. The sidebar and Overview show how many drafts need review.
- Resume import from PDF or DOCX into draft entries. The type is checked by content, DOCX files are screened for zip bombs, and parsing runs in a separate process with a timeout and no secrets. A rule-based parser reads sections, dates, and bullets. The original file is never stored, and the extracted text is encrypted. A review screen shows the source text next to the drafts, with edit, change kind, approve, discard, and profile suggestions.
- STAR story bank with tags, linked to vault entries.
- Cross-user isolation enforced by composite foreign keys in the database, as well as in queries.
- Audit events: `vault.import`, `vault.import_deleted`, `vault.verify`, `vault.profile_updated`.
- Per-route request size limit: `CAREERLINE_IMPORT_MAX_BYTES` (5 MB) for uploads, 1 MB elsewhere.
- Tests: 117 backend (unit + API, including isolation on every vault route and hostile-file cases), 28 frontend unit, and 3 Playwright end-to-end.

### Changed
- The local runner now lets real environment variables override `.env`.
- The local runner waits for PostgreSQL crash recovery after an unclean shutdown instead of failing to start.
- `start.ps1` checks exit codes instead of treating progress text on stderr as an error, so it also works with its output redirected to a file.

### Dependencies
- Added `python-docx` (MIT, with `lxml`, BSD-3-Clause) and `pypdf` (BSD-3-Clause).

## [0.1.0] - 2026-11-04 — Phase 1: Foundation

### Added
- FastAPI backend with async SQLAlchemy 2, Alembic migrations, and PostgreSQL 16.
- Local accounts: first-run owner setup, optional open registration, argon2id password hashing with automatic rehash.
- TOTP two-factor authentication: QR enrollment, ±30 s window, replay protection, 10 one-time recovery codes, disable/regenerate with password + code.
- Server-side sessions with opaque tokens (SHA-256 at rest), idle and absolute expiry, session list and revoke, revoke-others on password change and MFA enable.
- CSRF protection (double-submit token bound to the session), strict CSP and security headers, request-size limit, Postgres-backed rate limiting.
- AES-256-GCM field encryption with key versioning and per-field context binding.
- Audit log for registration, sign-in (success/failure), MFA changes, password changes, and session revokes.
- React + TypeScript UI: sign-in, MFA challenge, first-run setup, app shell with navigation, Security settings, light/dark/system themes, responsive layout.
- One-command local runner with embedded PostgreSQL (no Docker needed), plus Dockerfile and Docker Compose.
- Tests: 43 backend (unit + API against real Postgres), 8 frontend unit, 2 Playwright end-to-end.
- License allow-list check for npm dependencies.
