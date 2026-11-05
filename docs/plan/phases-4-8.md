# Phases 4–8: Discovery, AI, Materials, Assistant, Polish (as built)

Phases 1–3 each had a plan written before the work started. At the owner's request, Phases 4–8 were built straight through, so this page records what was built against the spec, how it was verified, and what was deliberately left out.

## Phase 4 — Discovery

| Spec item | Built | Verified by |
|---|---|---|
| Job source adapters | Greenhouse, Lever, Ashby, Adzuna, USAJOBS, JSearch, RSS/Atom (`discovery/adapters.py`), each with its terms link | Adapter unit tests against fictional fixtures; source API tests |
| Respect terms and robots.txt | APIs and feeds only; robots.txt for link import; big job boards never fetched | `test_discovery_units`, `test_discovery_api` (blocked domains, robots 4xx/5xx) |
| Scheduler | Postgres job queue and worker; per-source interval; retries with backoff; stale-job recovery | Queue tests (dedupe, retry, stale), scheduler test |
| De-duplication | Fingerprint of normalized company/title/location; sightings per source | Cross-source fetch test |
| Filters | Location radius, work mode, pay, shifts, clearance, keywords, companies, with reasons | Filter unit and API tests |
| SSRF safety | `SafeFetcher` with pinned DNS and per-hop checks | Bad-URL parametrized tests (private, loopback, mapped IPv6, ports, credentials, redirects) |

## Phase 5 — AI

| Spec item | Built | Verified by |
|---|---|---|
| Provider abstraction | Claude (SDK), OpenAI-compatible, Ollama, test fake | Provider tests with mock transports, including a hostile `ANTHROPIC_BASE_URL` |
| Fit scoring | Rule scorer always; AI scorer validated against a schema; stale on vault change | Scoring unit and API tests |
| Injection defenses | Untrusted blocks, rules in every system prompt, schema validation, red flags | Prompting tests (tag look-alikes, schema strictness) |
| Cost tracking | Per-call tokens and cost, monthly budget, rate limit | Usage and budget API tests |

## Phase 6 — Materials

| Spec item | Built | Verified by |
|---|---|---|
| Tailored resume / cover letter | Rules and AI builders from usable facts only | `test_materials` |
| Fact-check linking | Per-line citations, number check, flags, approval, export lock | Fact-check tests including invented numbers and unknown fact IDs |
| DOCX / PDF export | python-docx and ReportLab, ATS-friendly single column | Export tests (content, escaping, audit) |

## Phase 7 — Assistant and interview prep

| Spec item | Built | Verified by |
|---|---|---|
| Agent tools with confirmations | Read tools run; write tools become pending actions; no external sends | `test_assistant` (confirm, reject, double-confirm, isolation) |
| Interview prep | Question sets matched to stories, notes, AI questions, mock interviews | Interview tests; Playwright `search.spec` |

## Phase 8 — Polish

| Spec item | Built | Verified by |
|---|---|---|
| Analytics | Response rate by source, funnel, time to response, weekly volume, quiet applications | `test_insights_demo`, `phase8.test.tsx`, Playwright |
| Notifications | Email (SMTP), ntfy, Discord/Slack/JSON webhooks; digest and match alerts | `test_notify` (encryption, escaping, SSRF, digest once a day, alerts once per posting) |
| Export, import | Zip of JSON + files; validated, re-keyed import into an empty account | `test_data_portability` (round trip, hostile archives, isolation) |
| Encrypted backups | Chunked AES-GCM, retention, owner UI, CLI and start-script restore | `test_backup` (tamper, truncation, rotation, full restore into a new database), manual restore of the e2e database |
| OIDC login | PKCE, state, nonce, JWKS verification, linking, SSO-only mode | `test_oidc` against a fake provider with real RS256 tokens |
| Docs site | MkDocs config over `docs/`, user guide, operations guide | — |
| Demo mode | Fictional seed data, separate database, outbound blocked | `test_insights_demo`, browser check |

## Deliberately not built

- **Scraping** sites that don't offer an API or feed.
- **Sending** anything to employers or contacts. The assistant drafts; you send.
- **CI** (gitleaks, pip-audit, SBOM). Deferred while the project runs locally without git.
