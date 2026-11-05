# CareerLine

A self-hosted, AI-assisted job search app. It helps you find jobs, decide which are worth it, prepare truthful application materials from your own verified facts, track every application, and prepare for interviews. Your data lives in your own database.

> **Status: 1.1.0.** Sign in (with two-factor or single sign-on), build your Career Vault, pull jobs from the sources you choose, score them, write fact-checked resumes and cover letters, track applications, practice interviews, ask the assistant, and see what's working. Try it with fictional data first: `.\start.ps1 -Demo`.

## Principles

- **Private by default.** Nothing leaves your server except calls you configure: job sources, an AI provider, and notification channels.
- **Truthful by design.** The AI may only use facts you've verified in your Career Vault. Every generated line links to the fact it came from, and lines that can't be traced are flagged and block export.
- **You stay in charge.** Anything the AI would change waits for your Confirm. Nothing is ever sent to an employer or contact on your behalf.
- **Security first.** argon2id, TOTP MFA, OIDC single sign-on, server-side sessions, CSRF protection, strict CSP, field encryption, SSRF-guarded outbound requests, prompt-injection defenses, encrypted backups, and an audit log. See the [threat model](docs/threat-model.md).
- **Open source.** Apache-2.0, with only permissively licensed dependencies.

## What it does

**Career Vault.** Your single source of truth: profile, roles, responsibilities, achievements (with metrics), projects, certifications, education, skills, links, and STAR stories. Facts are *verified* or *drafts*; only verified facts (whose parents are verified too) are ever used. Import a PDF or Word resume into drafts and approve them on a side-by-side review screen. Contact details are encrypted.

**Jobs.** Add sources (Greenhouse, Lever, Ashby boards, Adzuna, USAJOBS, JSearch, RSS/Atom feeds) and CareerLine fetches them on a schedule, respecting each site's terms and robots.txt. Duplicates across sources are merged. Hard filters (location and radius, work mode, minimum pay, shifts, clearance, keywords, companies) sort postings into Matches and Filtered, with the reason for each. Add a single job by link or by pasting its text. LinkedIn, Indeed, and similar sites are never fetched.

**Fit scores.** Each posting gets a 0–100 score with matched requirements, gaps, and red flags (scam patterns, injected instructions). Keyword scoring works with no AI at all; AI scoring uses Claude, any OpenAI-compatible API, or a local Ollama. Scores go stale and are redone when your vault changes. The Settings → AI page shows calls, tokens, and estimated cost, with a monthly budget.

**Materials.** Tailored resumes and cover letters per job, built only from verified facts. Every line cites its facts, numbers must appear in those facts, and anything unsupported is flagged until you edit, approve, or remove it. Compare with your master resume, then export to PDF or Word (simple single-column layouts that applicant tracking systems read well) or attach the file to the application.

**Tracker.** Board and list views from Saved to Offer, a timeline per application (stage changes recorded automatically), encrypted attachments, contacts and referrals, reminders and an agenda, and side-by-side offer comparison.

**Interview prep.** Likely questions for a job (behavioral, technical, situational, company), each matched to your STAR stories, with your notes. With an AI provider, practice interviews ask one question at a time and give feedback on each answer.

**Assistant.** Ask about your search ("What should I follow up on this week?"). It reads your CareerLine data through tools; anything that would change it (a reminder, a stage move, a note) shows up as a proposed change with Confirm and Reject buttons.

**Bring your own AI agent.** Create an API key in Settings → API access and connect Claude, ChatGPT, or any [MCP](https://modelcontextprotocol.io) client. Your agent can read your search, fill in your Career Vault from your resume, add jobs, and propose changes. Facts it adds stay drafts until you verify them, and changes wait for your Confirm. See [API access](docs/api-access.md).

**Insights.** Response rate by source, the stage funnel with conversion between stages, time to first response, applications per week, and applications that have gone quiet.

**Notifications.** A daily digest of what's due and alerts for strong new matches, by email (SMTP), [ntfy](https://ntfy.sh) push, or a Discord, Slack, or JSON webhook. Addresses and tokens are encrypted.

**Your data.** Export everything (JSON plus your files, in a zip) after confirming your password, import it into an empty account on any CareerLine server, and keep scheduled encrypted backups of the whole database.

## Run it locally (no Docker)

Requirements: **Python 3.12+ via [uv](https://docs.astral.sh/uv/)** and **Node.js 20+**. PostgreSQL is bundled and started for you.

**Windows (PowerShell)**

```powershell
.\start.ps1
```

**macOS / Linux**

```bash
./start.sh
```

Then open **http://localhost:8080** and create the owner account. The first run:

1. installs backend and frontend dependencies,
2. creates `.env` with a new encryption key (**back this file up**),
3. starts a private PostgreSQL 16 with data in `.localdata/pg`,
4. applies migrations, starts the background worker (job fetches, scoring, notifications, backups), builds the UI, and serves everything on port 8080 (localhost only).

Encrypted backups are written to `.localdata/backups` once a day, keeping the newest 14.

Options:

| Windows | macOS / Linux | What it does |
|---|---|---|
| `-Demo` | `--demo` | Demo mode: a fictional job seeker in a separate database. Sign in as `demo@example.com` / `explore-careerline`. Nothing is sent outside the server. |
| `-Dev` | `--dev` | Hot-reloading dev servers (UI on :5173, API docs on :8000/api/docs). |
| `-Rebuild` | `--rebuild` | Rebuild the UI first. |
| `-VerifyBackup <file>` | `--verify-backup <file>` | Check that a backup opens. |
| `-Restore <file>` | `--restore <file>` | Restore a backup into an empty database ([how](docs/operations.md#restoring-local)). |
| `-RotateKey` | `--rotate-key` | Add a new encryption key and re-encrypt everything with it (CareerLine stopped). |
| `-KeyStatus` | `--key-status` | Show which encryption keys are still in use. |

## Run it with Docker Compose

```bash
cp .env.example .env      # then fill in POSTGRES_PASSWORD and CAREERLINE_ENCRYPTION_KEYS
docker compose up -d
```

Open http://localhost:8080. Compose runs the app, the worker, and Postgres; backups go to the `backups` volume. The app is bound to `127.0.0.1`. To reach it from other devices, put a TLS reverse proxy (Caddy, Traefik, nginx) in front and set `CAREERLINE_COOKIE_SECURE=true`, `CAREERLINE_HSTS=true`, `CAREERLINE_PUBLIC_URL`, and `CAREERLINE_TRUSTED_PROXIES` (the proxy's address or network range, so CareerLine sees real visitor IPs).

## Moving to another computer

Either copy the folder, or export and import.

**Copy the folder.** Copy everything **except** `node_modules/`, `backend/.venv/`, and `frontend/dist/`, then run the start script on the new machine. Stop CareerLine first.

- Keep **`.env`**: it holds the encryption key. Without it, two-factor secrets, contact details, API keys, attachments, and backups can't be decrypted.
- Keep **`.localdata/`** for your account and data (and your backups). If CareerLine was closed abruptly, the next start may pause for about 30 seconds while the database recovers. That's expected.

**Export and import.** Settings → Data → *Download export* on the old server; create an account on the new one and use *Import from a CareerLine export*. You'll re-enter API keys and notification addresses afterwards (they're never exported).

## Documentation

[User guide](docs/guide.md) · [API access for AI agents](docs/api-access.md) · [Operations: backups, single sign-on, notifications, demo](docs/operations.md) · [Configuration](docs/configuration.md) · [Architecture](docs/architecture.md) · [Threat model](docs/threat-model.md) · [Security policy](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)

The docs also build into a small site with [MkDocs](https://www.mkdocs.org/) (optional, not a project dependency): `uvx mkdocs serve` from the repo root.

## Development

```bash
# backend (from backend/)
uv sync
uv run pytest              # API + unit tests against a real (embedded) Postgres
uv run ruff check . && uv run ruff format --check . && uv run mypy careerline

# frontend (from frontend/)
npm ci
npm test                   # unit tests (Vitest)
npm run lint && npm run typecheck
npm run e2e                # Playwright, with a throwaway database (uses Edge on Windows)
npm run licenses           # fails if any dependency isn't permissively licensed
npm audit                  # known vulnerabilities in frontend packages

# known vulnerabilities in the locked Python packages (from backend/)
uv export --frozen --format requirements-txt --no-emit-project -o requirements-locked.txt
uvx pip-audit -r requirements-locked.txt --disable-pip --require-hashes
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1. Foundation | Auth with MFA, Postgres + migrations, UI shell, local runner, Docker Compose | **Done** |
| 2. Career Vault | Vault CRUD, resume import into drafts, verification, story bank | **Done** |
| 3. Tracker | Applications, kanban, timeline, contacts, reminders, offer comparison | **Done** |
| 4. Discovery | Job source adapters, scheduler and worker, de-duplication, filters, link import | **Done** |
| 5. AI | Provider abstraction (Claude, OpenAI-compatible, Ollama), fit scoring, injection defenses, cost tracking | **Done** |
| 6. Materials | Tailored resume and cover letter, fact-check linking, DOCX/PDF export | **Done** |
| 7. Assistant | Agent tools with confirmations, interview prep, practice interviews | **Done** |
| 8. Polish | Insights, notifications, export/import, encrypted backups, OIDC, demo mode, docs | **Done** |

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE).
