# Operations

Running Waypoint day to day: backups and restores, single sign-on, notifications, demo mode, and upgrades. Every setting mentioned here is listed in [configuration](configuration.md).

## Backups

Waypoint writes an encrypted backup of the **whole database** (every account) on a schedule.

| | Local (`start.ps1` / `start.sh`) | Docker Compose |
|---|---|---|
| Where | `.localdata/backups/` | the `backups` volume (`/backups` in the containers) |
| When | every 24 hours (`WAYPOINT_BACKUP_INTERVAL_HOURS`) | same |
| How many | newest 14 (`WAYPOINT_BACKUP_KEEP`) | same |

The background worker writes them, so it must be running (the start scripts and Compose start it). The owner sees the backups, the last result, and a **Back up now** button in Settings → Data.

**What's inside.** Every table, values exactly as stored: encrypted fields stay encrypted, passwords stay hashed. Sign-in sessions, rate-limit counters, and the job queue are left out. The file is a zip encrypted with AES-256-GCM in 1 MiB chunks, so a damaged, truncated, or reordered file is detected rather than half-restored.

**The key.** Backups use `WAYPOINT_BACKUP_KEY` if you set one, otherwise a key derived from your first `WAYPOINT_ENCRYPTION_KEYS` entry. Either way, **a backup is useless without your `.env`**. Keep a copy of `.env` somewhere other than the machine (a password manager works well), and copy the backups to another disk or machine too: a backup on the same disk doesn't survive that disk failing.

**Check a backup opens:**

```powershell
.\start.ps1 -VerifyBackup .localdata\backups\waypoint-20261104-020000.wpbackup
```

### Restoring (local)

Restores go into an **empty** database, made by the same Waypoint version that wrote the backup.

1. Stop Waypoint.
2. Move the current database aside (don't delete it until the restore is checked): rename `.localdata\pg` to `.localdata\pg-before-restore`.
3. Restore:

   ```powershell
   .\start.ps1 -Restore .localdata\backups\waypoint-20261104-020000.wpbackup
   ```

   This creates a fresh database, applies migrations, loads the backup, and exits. It refuses to touch a database that already has accounts.
4. Start Waypoint normally (`.\start.ps1`) and sign in with your usual password.

On macOS or Linux, use `./start.sh --restore <file>` and `--verify-backup <file>`.

### Restoring (Docker Compose)

```bash
docker compose down
docker volume ls                          # find the database volume, e.g. <project>_pgdata
docker volume rm <project>_pgdata         # only after copying it, or rename the volume in compose.yaml instead
docker compose up -d db
docker compose run --rm migrate
docker compose run --rm worker python -m waypoint.data restore /backups/waypoint-20261104-020000.wpbackup
docker compose up -d
```

If the backup came from an older Waypoint version, check out that version, restore, then upgrade normally.

### Export and import (per account)

Settings → Data → **Download export** gives one person's data as a zip of JSON files plus their uploaded files, with encrypted fields decrypted. It's for moving between servers or keeping your own copy, not a substitute for backups. Exports need your password again (or a fresh single sign-on) within the last 10 minutes.

**Import from a Waypoint export** loads that zip into the signed-in account, which must be empty. Every record gets a new ID; job sources come in turned off; AI keys, job-source keys, and ntfy/webhook addresses are never in an export, so enter them again. The archive is treated as untrusted: unknown fields, invalid values, links that aren't http(s), and references to records that aren't in the archive are refused, and nothing is saved unless everything imports.

## Rotating the encryption key

Stop Waypoint, run `.\start.ps1 -RotateKey` (or `./start.sh --rotate-key`), start it again, and make a fresh backup. Full steps, including Docker, are in [configuration](configuration.md#rotating-the-encryption-key).

## Single sign-on (OpenID Connect)

Waypoint works with any standard OpenID Connect provider. It uses the authorization code flow with PKCE, checks `state` and `nonce`, and verifies the ID token's signature (RS/PS/ES/EdDSA), issuer, audience, and expiry against the provider's published keys.

### With Authelia

1. Pick a client secret and hash it for Authelia:

   ```bash
   docker run --rm authelia/authelia:latest authelia crypto hash generate pbkdf2 --random --random.length 64
   ```

   The command prints a random secret and its hash. The secret goes to Waypoint, the hash to Authelia.

2. Add Waypoint as a client in Authelia's `configuration.yml`:

   ```yaml
   identity_providers:
     oidc:
       clients:
         - client_id: 'waypoint'
           client_name: 'Waypoint'
           client_secret: '$pbkdf2-sha512$310000$...'   # the hash from step 1
           public: false
           authorization_policy: 'two_factor'
           redirect_uris:
             - 'https://waypoint.example.com/api/v1/auth/oidc/callback'
           scopes: ['openid', 'email', 'profile']
           response_types: ['code']
           grant_types: ['authorization_code']
           require_pkce: true
           pkce_challenge_method: 'S256'
           token_endpoint_auth_method: 'client_secret_basic'
   ```

3. Configure Waypoint (in `.env`):

   ```ini
   WAYPOINT_PUBLIC_URL=https://waypoint.example.com
   WAYPOINT_OIDC_ISSUER=https://auth.example.com
   WAYPOINT_OIDC_CLIENT_ID=waypoint
   WAYPOINT_OIDC_CLIENT_SECRET=<the random secret from step 1>
   WAYPOINT_OIDC_LABEL=Authelia
   WAYPOINT_COOKIE_SECURE=true
   WAYPOINT_HSTS=true
   ```

4. Restart Waypoint. The sign-in page shows **Sign in with Authelia**.

Authelia returns email and name from its userinfo endpoint rather than in the ID token by default; Waypoint asks for them there when the token has none.

### Accounts

- **First sign-in on a new server** (no accounts yet) creates the owner account, just like the setup page.
- **Existing password account:** sign in with your password, then Settings → Security → **Link a … account**. From then on either works. You can unlink later as long as you still have a password.
- **New people:** blocked unless registration is open (`WAYPOINT_ALLOW_REGISTRATION=true`) or `WAYPOINT_OIDC_AUTO_CREATE=true`.
- `WAYPOINT_OIDC_LINK_BY_EMAIL=true` signs an existing account in when the provider vouches for the same **verified** email. Leave it off unless you control the provider's user list.
- Accounts created through single sign-on have no password. They can set one in Settings → Security within 10 minutes of signing in.
- Signing in through the provider skips Waypoint's own two-factor step: the provider is expected to do that (`authorization_policy: two_factor` above).
- `WAYPOINT_PASSWORD_LOGIN=false` turns off email and password sign-in entirely.

If something goes wrong, the sign-in page shows a short explanation (for example "No Waypoint account is linked to that sign-in"); the provider's own error text is never shown, and the audit log records `auth.sso_failed` with the reason.

## Notifications

Each person chooses what to get and where in Settings → Notifications:

- **Daily digest:** overdue and due-today reminders, follow-ups, and deadlines, at the hour they pick, in their time zone. Skipped on days with nothing due.
- **New job matches:** postings first seen in the last week that pass their filters and reach the score they choose. Checked every few minutes; each posting is announced once.

Channels:

| Channel | Setup |
|---|---|
| Email | The server owner configures one SMTP server (`WAYPOINT_SMTP_*`). Each person enters their address. |
| ntfy | A topic address such as `https://ntfy.sh/<long-random-topic>`, or a self-hosted ntfy server, plus an optional access token. Anyone who knows a public topic name can read it, so make it long and random. |
| Discord | Channel settings → Integrations → Webhooks → copy the webhook URL. |
| Slack | An Incoming Webhook URL from a Slack app. |
| Other | Any URL that accepts a JSON POST: `{"source", "kind", "title", "items", "link"}`. |

Use **Send a test** after saving. Addresses and tokens are encrypted at rest. Webhook and ntfy requests go through the same guard as job sources: only http(s), no redirects, and **no private or LAN addresses** unless the person is the server owner or `WAYPOINT_OUTBOUND_ALLOW_PRIVATE=true`. Job titles in messages are escaped for each service, so a posting can't add links, formatting, or `@everyone` mentions.

Set `WAYPOINT_PUBLIC_URL` so messages link back to Waypoint.

## Demo mode

```powershell
.\start.ps1 -Demo
```

Starts Waypoint with a fictional job seeker (Riley Morgan) in a **separate database** (`.localdata/pg-demo`), so it never touches your real data. Sign in as `demo@example.com` / `explore-waypoint`. In demo mode registration is closed, nothing is sent outside the server (no job-source fetches, link imports, AI providers, or notifications), and backups are off. Everything in it (people, companies, email addresses, phone numbers) is made up.

To start the demo over, stop it and delete `.localdata/pg-demo`.

## Upgrading

1. Make a backup (Settings → Data → **Back up now**, or wait for the scheduled one) and copy it and `.env` somewhere safe.
2. Replace the code with the new version (keep `.env` and `.localdata/`).
3. Start Waypoint. Migrations run automatically, locally and in Compose (`migrate` service).

Read the [changelog](../CHANGELOG.md) first for anything that needs attention.

## Logs and the audit log

The app and worker log to the console (the local runner's window, or `docker compose logs app worker`). Security events go to the `audit_log` table: sign-ins (password and single sign-on), failures, two-factor changes, password confirmations, exports and imports, backup requests, AI and notification setting changes, job sources added or removed, generated and exported documents, assistant actions you confirmed, and deletions of applications, contacts, and attachments. Every AI call (feature, model, tokens, cost, outcome) is also logged in `ai_runs`, visible in Settings → AI.
