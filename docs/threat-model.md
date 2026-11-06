# Threat Model

Covers all eight phases of CareerLine 1.0: authentication and platform, Career Vault and resume import, tracker, job discovery, AI, tailored materials, the assistant and interview prep, and the Phase 8 additions (insights, notifications, export/import, encrypted backups, single sign-on, demo mode), plus API keys and the MCP endpoint for external agents. Last updated 2026-11-05.

## Assets

| Asset | Why it matters |
|---|---|
| Career Vault contents | A full work history and personal details: high value for identity fraud and social engineering. |
| Uploaded resumes | Contain contact details. Treated as untrusted input *and* as sensitive data. |
| Tracker attachments | Offer letters, resumes, and notes: sensitive, and untrusted when downloaded again. |
| Contacts | Other people's names, emails, and phone numbers. |
| Account credentials, TOTP secrets, recovery codes | Give full access to everything else. |
| Session tokens | Equivalent to a signed-in user. |
| API keys for the MCP endpoint | Let an agent read the account's job search and add drafts or proposals, within the key's permissions. |
| Encryption key (`CAREERLINE_ENCRYPTION_KEYS`) | Decrypts every encrypted field. |
| API keys for job sources and AI providers | Cost the owner money and leak data if stolen. |
| Notification addresses and tokens (webhook URLs, ntfy topics) | Anyone holding them can post to, or read, the user's channel. |
| Exports and backups | A complete copy of one account (export, decrypted) or of every account (backup, encrypted). |
| Audit log | Needed to investigate incidents, so it must be trustworthy. |

## Trust boundaries

1. **Browser ↔ server.** Everything from the client is untrusted.
2. **Server ↔ database.** On a private network in Compose (`internal: true`). Local mode binds to 127.0.0.1 only.
3. **Server ↔ external services:** job APIs and feeds, pages fetched by link import, AI providers, notification endpoints, and the single sign-on provider. Their responses are untrusted (the sign-on provider is trusted only after its token's signature checks out).
4. **Untrusted content inside the app:** imported resumes, job postings and fetched pages, and model output. These are treated as **data, never instructions**.
5. **Between users** on a multi-user instance: each user's data must be isolated from every other user.
6. **API process and extraction worker.** Resume files are parsed in a separate, short-lived process that gets no secrets.
7. **Model and tools.** The assistant's model can call read tools freely; every tool that changes data needs the user's Confirm.
8. **Archives from elsewhere.** An imported export may have been made or edited by someone else.
9. **External agents with API keys** (`/api/mcp`, and the REST API for full-access keys). The agent is a model the user chose, but it may be confused or prompt-injected by what it reads elsewhere. With narrow scopes it's treated like the built-in assistant's model: it can read within the key's permissions, add only unverified drafts, and propose changes the user confirms. With full access the user trusts it to act as them, except on account security.

## Threats and mitigations (STRIDE)

| # | Threat | Mitigation | Status |
|---|---|---|---|
| S1 | Password guessing / credential stuffing | argon2id (t=3, m=64 MiB, p=4). Rate limits: 30 logins / 5 min per IP and 10 / 15 min per email. 12-character minimum. Optional TOTP. | ✅ |
| S2 | Account enumeration | Same error for unknown email and wrong password. Unknown emails still run a dummy argon2 verify so timing matches. *Registration does reveal taken emails, but it's closed by default.* | ✅ / accepted |
| S3 | TOTP brute force | 5 wrong codes per pending session (then the session is destroyed), 10 per user per 15 min, and the pending session expires after 5 min. | ✅ |
| S4 | TOTP replay | The last accepted time step is stored, and codes at or before it are rejected. | ✅ |
| S5 | Session fixation | Every login and MFA step issues a fresh token, and any session from before login is deleted. | ✅ |
| S6 | Session theft via XSS | Session cookie is `HttpOnly`. Strict CSP with no inline script. React escaping, with `dangerouslySetInnerHTML` banned by lint. | ✅ |
| S7 | Stolen session reuse | Idle timeout (12 h) and absolute timeout (7 d). Users can see and revoke sessions. Password change and MFA enable revoke all other sessions. | ✅ |
| T1 | CSRF | `SameSite=Lax` session cookie, plus a CSRF token on every state-changing request that must match the cookie *and* the token bound to the session. JSON-only bodies. | ✅ |
| T2 | Mass assignment (e.g. `is_owner`) | Pydantic models with `extra="forbid"`. Ownership is set only on the server. | ✅ |
| T3 | Tampering with encrypted fields | AES-256-GCM (authenticated), with associated data binding each value to user and field. | ✅ |
| I15 | A leaked encryption key | Key rotation: a new key goes first, `rekey` re-encrypts every value with it (same contexts, compare-and-swap, resumable), and `keys` shows when the old one is unused and can be removed. Rotation is audited (`security.rekey`). Coverage is enforced by a test that fails if any `*_enc` column is missing from the re-encryption list. | ✅ |
| R1 | Denying security actions | Audit log covers register, login success/failure, MFA challenge, enable, disable and failures, recovery-code use, password change, session revoke, and logout. | ✅ |
| I1 | Database leak exposes secrets | Passwords are argon2id hashes. Session tokens and recovery codes are SHA-256. TOTP secrets are AES-GCM encrypted with a key that is *not* stored in the DB. | ✅ |
| I2 | Error messages leak data | One generic error format. Validation errors report field locations, never submitted values. API docs are off in production. | ✅ |
| I3 | Clickjacking, MIME sniffing, referrer leaks | `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, COOP/CORP same-origin. | ✅ |
| I4 | Cross-user data access | Every query filters by `user_id` from the session, never from the client. Tested (one user can't revoke another's session). | ✅ (enforced again each phase) |
| I5 | Secrets committed to git | `.gitignore` covers `.env` and `.localdata`. Only `.env.example` ships. | ✅ (gitleaks in CI: planned) |
| D1 | Oversized requests | 1 MB body limit (checked from Content-Length and while streaming). | ✅ |
| D2 | Login lockout of a real user by an attacker | Per-email limit is temporary (15 min). Accepted for a self-hosted, single-owner app. | accepted |
| D3 | argon2 CPU exhaustion | Login rate limits. Hashing runs in a thread pool. | ✅ partial |
| E1 | Container escape / privilege escalation | Non-root UID 10001, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`. DB on an internal-only network. | ✅ (unverified on this machine: no Docker) |
| I6 | Cross-user vault access | All vault queries go through per-user helpers, and other users' IDs return 404, not 403. Parent links and story links are composite foreign keys on `(user_id, id)`, so the **database** rejects cross-user references too. Tests cover every vault route and the database constraints. | ✅ |
| I7 | Contact details or resume text exposed by a DB leak | Profile email, phone, and address are AES-GCM encrypted (context `user:{id}:profile:{field}`). Imported text and profile suggestions are encrypted per import. The original file is never stored. | ✅ |
| T4 | Unverified or invented facts reaching generated documents | Imported entries always start as drafts. `usable` is true only when an entry and all its ancestors are verified, and later phases may use only usable entries. The parser emits only text found in the file, plus one clearly marked "(add job title)" placeholder. | ✅ (enforced again in Phases 5–6) |
| T5 | Malicious upload disguised by name or MIME type | The type is decided by magic bytes and must match the declared `Content-Type`, otherwise 415. Filenames are display-only and sanitized. | ✅ |
| D4 | Zip bomb / decompression DoS (DOCX) | Before parsing: at most 200 members, 20 MB total uncompressed, a 100:1 ratio per large member, no absolute or `..` paths, and `word/document.xml` must exist. | ✅ |
| D5 | Parser hang or crash on a hostile PDF/DOCX | Parsing runs in a separate process with a 15 s timeout (killed on expiry), at most 10 pages and 100k characters, a 512 MB memory limit on Linux, and a minimal environment. python-docx parses XML with entity resolution off. Failures return a generic message, and details go to the server log only. | ✅ (no memory limit on Windows) |
| D6 | Upload flooding | 5 MB body limit on the import route only (1 MB elsewhere), and 20 imports per hour per user. | ✅ |
| E3 | Code execution via uploaded documents | Only text is extracted. Macros, embedded objects, images, and links are never executed, followed, or kept. | ✅ |
| I8 | Cross-user tracker access | Same per-user helpers and composite foreign keys as the vault. Every tracker route answers 404 for another user's IDs, including linking another user's contact to your application and attaching to another user's event. Tests cover each route and the database constraints. | ✅ |
| I9 | Contact details or attachments exposed by a DB leak | Contact email and phone are AES-GCM encrypted per field. Attachment bytes are AES-GCM encrypted with a per-file context. | ✅ |
| T6 | Rewriting application history | Stage-change events are written by the server only; the API refuses to create, edit, or delete them. | ✅ |
| E4 | Stored XSS through an attachment | Only PDF, DOCX, PNG, JPEG, and UTF-8 text are accepted, decided by content and required to match the declared type (HTML, SVG, and anything else get 415). Downloads are served with `Content-Disposition: attachment`, the allow-listed type, `nosniff`, and a CSP with `sandbox` and `default-src 'none'`. The UI never renders files inline. | ✅ |
| E5 | Header injection via file names | Stored names are sanitized; the download header uses an ASCII fallback plus RFC 5987 `filename*` encoding. | ✅ |
| D7 | Storage exhaustion | 10 MB per attachment (route-specific body limit), 250 MB per user (configurable), 60 uploads per hour per user. | ✅ |
| I10 | Pasted job descriptions as an injection vector | Stored and shown as plain text only (React escaping, `white-space: pre-wrap`); never rendered as HTML or Markdown. They will be treated as untrusted data in AI prompts (Phase 5). | ✅ |
| T7 | SSRF through job sources, link import, AI endpoints, or webhooks | One outbound client (`SafeFetcher`): http/https only, no credentials in URLs, standard ports, DNS resolved and **every** address required to be public (IPv4-mapped IPv6 handled), the connection pinned to the checked address (no DNS-rebinding window) with SNI and Host preserved, redirects followed manually and re-checked (max 3; none for webhooks and sign-in), 5 MB response cap, no proxy settings from the environment. Private targets only for the owner's AI and notification endpoints, or for everyone with `CAREERLINE_OUTBOUND_ALLOW_PRIVATE`. | ✅ |
| T8 | Scraping sites that forbid it | Only documented APIs and feeds; robots.txt checked for link import (RFC 9309: 4xx allows, 5xx disallows); LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, SimplyHired, Dice, and CareerBuilder are never fetched; each source shows its terms. | ✅ |
| T9 | Prompt injection from postings, pages, or resumes | Untrusted text goes into prompts only inside delimited data blocks (tag look-alikes neutralized), with standing rules that data never carries instructions. Model output must match a JSON schema (one retry, then an error). Postings can't trigger tools. Injection-looking text is a red flag in the score. | ✅ |
| T10 | The AI inventing experience, titles, dates, or numbers | Only usable (verified) facts are sent. Every generated line must cite fact IDs that exist; numbers must appear in the cited facts; uncited cover-letter paragraphs may not contain claims. Anything that fails is flagged, and export is refused until the user edits, approves, or removes it. Structure (titles, employers, dates) always comes from the vault, not the model. | ✅ |
| E6 | The assistant changing data on its own | Read tools run directly; write tools only create a pending action shown with Confirm and Reject. No tool can send anything outside the server. New messages are refused while an action is pending. Confirmed actions are audited. At most 6 tool steps per message. | ✅ |
| D8 | AI cost runaway | 60 AI calls per 10 minutes per user, a monthly budget per user, token and cost logging per call, and AI scoring only for new postings that pass the filters (50 per run). | ✅ |
| I11 | Personal data sent to a hosted AI provider | Only usable vault facts and the posting are sent; emails, phone numbers, and street addresses are removed before hosted calls (on by default). Ollama keeps everything local. API keys are encrypted and never returned by the API. | ✅ |
| I12 | Generated documents carrying markup into PDF or Word | Text is escaped for ReportLab; DOCX is built from python-docx runs (no HTML). | ✅ |
| S8 | Single sign-on: forged or replayed tokens, login CSRF, code interception | Authorization code + PKCE (S256), random `state` and `nonce` in an encrypted, HttpOnly, 10-minute flow cookie bound to the browser, compared in constant time; the ID token's signature is checked against the provider's JWKS (no `none`, no HMAC), with issuer, audience/`azp`, expiry, and nonce; provider endpoints must be https; provider errors are shown only as fixed codes; 60 sign-in requests per 5 minutes per IP. | ✅ |
| S9 | Account takeover through single sign-on | Identities are keyed by issuer + subject, never by email. Linking an existing account needs either a signed-in session (whose user must still be the same at the callback) or `OIDC_LINK_BY_EMAIL` with a **verified** email. A provider account can't be linked to two CareerLine accounts. New accounts only when registration is open or `OIDC_AUTO_CREATE` is on. | ✅ |
| S10 | Open redirect after sign-in | The return path must be a same-site path (`/…`, no `//`, no backslash, limited characters); anything else becomes `/`. | ✅ |
| S11 | A stolen session exporting everything | Export and import need the password (or a fresh single sign-on) within the last 10 minutes; 10 exports and 5 imports per hour; both audited. | ✅ |
| T11 | Hostile import archive | Only known tables and columns; values checked against each column's type; vault data validated by the same schemas as the API; links must be http(s) (and the UI only links http(s) addresses anyway); every ID is replaced and references outside the archive are refused, so an archive can't touch other users' rows; pending assistant actions are never carried over; job sources arrive disabled; zip limits (1 GB unpacked, 200 MB per table, 50 MB per file, 100,000 members, never extracted to disk); all in one transaction. | ✅ |
| I13 | Backups exposing data | Backups are AES-256-GCM encrypted in authenticated 1 MiB chunks (counter + final-chunk flag in the nonce) with a per-file HKDF key, so truncation, reordering, and tampering are detected. Encrypted fields stay encrypted inside. The plaintext staging zip lives only in a private temp file and is deleted afterwards. | ✅ |
| T12 | Notification content abuse | Posting text in messages is escaped per service (Discord Markdown, Slack `<>&`), Discord `allowed_mentions` is empty, control characters are stripped, and email headers reject line breaks. | ✅ |
| I14 | Notification secrets leaking | Webhook URLs, ntfy topics and tokens, and the email address are encrypted; the API returns only a masked hint for webhook URLs; they're never exported. | ✅ |
| E7 | Demo mode exposed to the internet | Demo mode closes registration, blocks every outbound request, and uses a separate database; the documentation says it's for trying CareerLine, not for real data. | ✅ / accepted |
| S12 | A stolen or leaked API key | Keys are 256-bit random values stored only as SHA-256 hashes; shown once; scoped (read / add drafts / propose / full); expire after 7–365 days unless the user chooses never; revocable at once; creating a key or changing its scopes needs the password within the last 10 minutes and is audited. Narrow keys work only on `/api/mcp`. Full-access keys also work on the REST API, but never on sign-in and security (`/api/v1/auth`), key management (`/api/v1/api-tokens`), or full export/import, and they never count as recently re-authenticated, so a leaked key can't lock the owner out, mint more keys, or take everything in one archive. Last use is shown per key. | ✅ |
| E8 | An agent with a key overreaching (confused or prompt-injected) | With narrow scopes: vault facts and stories it adds are unverified drafts, marked as from the API, and drafts are never used for documents or scores; everything else it can change is a proposal the user confirms, re-validated at apply time; there are no delete, edit-in-place, or send tools; contact details are never returned. With full access the user has chosen to drop these checks: facts are saved verified and changes, including deletes and settings, apply at once. What remains: the UI warns before granting it, the account-security boundary in S12, nightly encrypted backups to recover from mistakes, and the audit log (`api.tool` / `api.request` with the key's id). In both cases untrusted text is wrapped and the server's instructions say not to follow it, and a key may make 300 changes per hour. | ✅ / accepted for full access |
| E9 | CSRF against REST requests made with a key | A key is sent in the `Authorization` header, which browsers never add on their own; a request carrying a session cookie is always treated as a browser request (session and CSRF checks), even if it also has a bearer header. | ✅ |
| D9 | Flooding through the MCP endpoint | 600 requests per key per 10 minutes; 30 failed key attempts per IP per 15 minutes; the normal body-size limit; no server-sent streams or sessions to hold open. | ✅ |
| S13 | Cross-site requests to `/api/mcp` from a browser (DNS rebinding) | The endpoint needs a bearer key (no cookies), and requests whose `Origin` isn't the server's own address (or `CAREERLINE_PUBLIC_URL`) are refused. | ✅ |
| E2 | Supply-chain compromise | Lockfiles (`uv.lock`, `package-lock.json`), pinned versions, license allow-list; CI runs pip-audit and `npm audit` on every push. | ✅ partial — SBOM, Semgrep, Trivy, Dependabot not yet |

## Known gaps

- The local runner's embedded Postgres trusts connections from 127.0.0.1 without a password. That's acceptable on a single-user desktop. Use Docker Compose (password-protected, private network) on shared machines.
- Vault facts, postings, and generated documents are stored in plain text so they can be searched, matched, and scored. The database is private to the owner, and backups are encrypted.
- Signing in through single sign-on skips CareerLine's own TOTP step; the provider is expected to require two factors.
- Exports are deliberately decrypted (they're the user's own data, for moving servers). The UI says so; keep them safe.
- CI (GitHub Actions) runs pip-audit on the locked Python packages and `npm audit` on the UI's runtime packages on every push. Development-only npm tooling isn't gated.
- `CAREERLINE_COOKIE_SECURE=false` is needed for plain `http://` (localhost or a home LAN). Over plain http, session cookies and API keys cross the network unencrypted, so anyone able to watch that network could take them. Use it only on a network you trust; otherwise put a TLS reverse proxy (or a VPN such as Tailscale) in front and set it to `true`.
- API keys survive a password change (like most personal access tokens). After a suspected compromise, change the password **and** revoke keys in Settings → API access.
