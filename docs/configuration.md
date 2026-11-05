# Configuration

All settings come from environment variables prefixed with `CAREERLINE_` (or from `.env` in the repo root). A real environment variable wins over the same setting in `.env`. Any secret can instead come from a file via `CAREERLINE_<NAME>_FILE`, which suits Docker secrets.

| Variable | Default | Description |
|---|---|---|
| `CAREERLINE_DATABASE_URL` | *(required)* | `postgresql://user:pass@host:5432/db`. The local runner and Compose set this for you. |
| `CAREERLINE_ENCRYPTION_KEYS` | *(required)* | Comma-separated `version:key` pairs (base64url, 32 bytes). The first one encrypts; all of them decrypt. **Back it up.** |
| `CAREERLINE_ENV` | `production` | `development` enables the API docs at `/api/docs`. |
| `CAREERLINE_COOKIE_SECURE` | `true` | Marks cookies `Secure` and uses `__Host-` names. Set `false` only for plain `http://localhost`. |
| `CAREERLINE_HSTS` | `false` | Send `Strict-Transport-Security`. Turn it on only when served over HTTPS. |
| `CAREERLINE_ALLOW_REGISTRATION` | `false` | Lets more accounts be created after the first (owner) account. |
| `CAREERLINE_SESSION_TTL_HOURS` | `168` | Absolute session lifetime. |
| `CAREERLINE_SESSION_IDLE_MINUTES` | `720` | Sign out after this much inactivity. |
| `CAREERLINE_MFA_PENDING_MINUTES` | `5` | Time allowed to enter the second factor after the password. |
| `CAREERLINE_MAX_BODY_BYTES` | `1000000` | Largest accepted request body. |
| `CAREERLINE_IMPORT_MAX_BYTES` | `5000000` | Largest resume upload. Applies to `POST /api/v1/vault/imports` only. |
| `CAREERLINE_ATTACHMENT_MAX_BYTES` | `10000000` | Largest tracker attachment. Applies to the attachment upload route only. |
| `CAREERLINE_ATTACHMENT_QUOTA_BYTES` | `250000000` | Total attachment storage per user. |
| `CAREERLINE_STATIC_DIR` | *(unset)* | Folder with the built UI. When set, the API also serves the UI from the same origin. |
| `CAREERLINE_PUBLIC_URL` | *(unset)* | Where CareerLine is reached from outside, e.g. `https://careerline.example.com`. Used for links in notifications and the default single sign-on redirect address. |
| `CAREERLINE_OUTBOUND_ALLOW_PRIVATE` | `false` | Let job sources, AI endpoints, ntfy, and webhooks reach private or LAN addresses for **every** user. The owner can always use private addresses for AI and notifications (a LAN Ollama or home-lab ntfy); everyone else is blocked unless this is on. |
| `CAREERLINE_DEMO_MODE` | `false` | Fictional seed data on first start, registration closed, no outbound requests, AI providers and notifications off. Use `start.ps1 -Demo`, which also uses a separate database. |

### Email notifications (one SMTP server for the instance)

| Variable | Default | Description |
|---|---|---|
| `CAREERLINE_SMTP_HOST` | *(unset)* | Mail server. Email notifications are offered only when this and `SMTP_FROM` are set. |
| `CAREERLINE_SMTP_PORT` | `587` | |
| `CAREERLINE_SMTP_SECURITY` | `starttls` | `starttls`, `tls` (implicit TLS, usually port 465), or `none` (only for a relay on localhost). Certificates are always verified. |
| `CAREERLINE_SMTP_USERNAME` | *(unset)* | |
| `CAREERLINE_SMTP_PASSWORD` | *(unset)* | Secret; `CAREERLINE_SMTP_PASSWORD_FILE` also works. |
| `CAREERLINE_SMTP_FROM` | *(unset)* | Sender address, e.g. `CareerLine <careerline@example.com>`. |

### Export, import, and backups

| Variable | Default | Description |
|---|---|---|
| `CAREERLINE_IMPORT_ARCHIVE_MAX_BYTES` | `300000000` | Largest export archive accepted by *Import from a CareerLine export*. |
| `CAREERLINE_BACKUP_DIR` | *(unset)* | Folder for scheduled encrypted backups. Backups are off when unset. The local runner sets `.localdata/backups`; the Docker image sets `/backups` (the `backups` volume). |
| `CAREERLINE_BACKUP_KEY` | *(unset)* | base64url 32-byte key for backups. When unset, a key derived from the first encryption key is used, so `.env` alone is enough to restore. `_FILE` works. |
| `CAREERLINE_BACKUP_INTERVAL_HOURS` | `24` | How often the worker writes a backup. |
| `CAREERLINE_BACKUP_KEEP` | `14` | How many backups to keep; older ones are deleted. |

### Single sign-on (OpenID Connect)

| Variable | Default | Description |
|---|---|---|
| `CAREERLINE_OIDC_ISSUER` | *(unset)* | The provider's issuer URL, e.g. `https://auth.example.com` for Authelia. Must be https (http only for localhost). Single sign-on is on when this, the client ID, and a redirect address are set. |
| `CAREERLINE_OIDC_CLIENT_ID` | *(unset)* | |
| `CAREERLINE_OIDC_CLIENT_SECRET` | *(unset)* | Secret; `_FILE` works. Leave unset for a public client (PKCE only). |
| `CAREERLINE_OIDC_REDIRECT_URL` | `<PUBLIC_URL>/api/v1/auth/oidc/callback` | Must match the redirect URI registered with the provider exactly. |
| `CAREERLINE_OIDC_LABEL` | `single sign-on` | Button text: "Sign in with *label*". |
| `CAREERLINE_OIDC_SCOPES` | `openid email profile` | |
| `CAREERLINE_OIDC_AUTO_CREATE` | `false` | Create a CareerLine account the first time someone signs in through the provider, even when registration is closed. |
| `CAREERLINE_OIDC_LINK_BY_EMAIL` | `false` | Sign an existing account in when the provider vouches for the same **verified** email. Off by default: link accounts from Settings → Security instead. |
| `CAREERLINE_PASSWORD_LOGIN` | `true` | Set `false` for single sign-on only. Refused at startup unless single sign-on is configured. |

## Fixed limits (not configurable)

| Limit | Value |
|---|---|
| Resume imports per user | 20 per hour |
| Resume parsing time | 15 seconds, then the parser process is killed |
| Resume text read | first 10 PDF pages, 100,000 characters |
| DOCX archive | 200 files, 20 MB uncompressed, 100:1 compression ratio |
| Attachment uploads per user | 60 per hour |
| Attachment types | PDF, DOCX, PNG, JPEG, UTF-8 text |
| Job source "fetch now" | 60 per hour per user; scheduled fetches every 1–168 hours per source |
| Single-job imports (link or pasted text) | 30 per hour per user |
| Fetched pages and API responses | 5 MB, 3 redirects, standard ports only (unless private targets are allowed) |
| Old postings | Removed after 60 days unless saved or tracked |
| AI calls | 60 per 10 minutes per user, plus the user's monthly budget |
| Assistant | 6 tool steps per message; read tools run at once, changes wait for Confirm |
| Notification test sends | 10 per hour per user |
| Digest | Sent within 3 hours after the chosen hour, at most once a day, skipped when nothing is due |
| Match alerts | Postings first seen in the last 7 days; up to 10 per alert |
| Exports | 10 per hour per user, within 10 minutes of confirming your password |
| Imports | 5 per hour per user, into an empty account only; 1 GB unpacked, 200 MB per table, 50 MB per file |
| "Back up now" | 6 per hour (owner only) |
| Password re-confirmation | 10 tries per 15 minutes per user |
| Single sign-on | 60 sign-in requests per 5 minutes per IP; a sign-in must finish within 10 minutes |

## Generating secrets

```bash
python -c "import secrets,base64; print('1:'+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))"
```

## Rotating the encryption key

Rotate when a key may have leaked, or as routine hygiene. Stop CareerLine first: an app still running with the old key list can't read values written with the new key.

**Local (`start.ps1` / `start.sh`):**

```powershell
.\start.ps1 -RotateKey     # adds a new key first in .env (old keys kept), re-encrypts everything
.\start.ps1                # start again, then Settings → Data → Back up now
.\start.ps1 -KeyStatus     # shows which key versions are still in use
```

**Anywhere else** (Docker, or keys set as environment variables):

1. Generate a new key (below) and put it **first**: `CAREERLINE_ENCRYPTION_KEYS=2:<new>,1:<old>`.
2. Stop the app and worker, then re-encrypt: `python -m careerline.data rekey` (Compose: `docker compose run --rm worker python -m careerline.data rekey`).
3. Start again and make a fresh backup.

Every encrypted value (two-factor secrets, profile and contact details, imported resume text, attachments, AI and job-source keys, notification addresses) is decrypted with its old key and written again with the new one, keeping its binding to its row and column. The command can be stopped and run again, and it never overwrites a value that changed meanwhile.

**Removing the old key.** When `-KeyStatus` (or `python -m careerline.data keys`) shows the old version is unused, you can remove it from `CAREERLINE_ENCRYPTION_KEYS`. Backups written before you re-encrypted still need it (unless you set `CAREERLINE_BACKUP_KEY`), so keep a copy somewhere safe until those backups have expired. Restores try every key in the list, newest first.

## Exposing CareerLine beyond localhost

Both the local runner and Compose bind to `127.0.0.1`. To expose CareerLine:

1. Put a TLS-terminating reverse proxy in front (Caddy, Traefik, or nginx; Authelia works too).
2. Set `CAREERLINE_COOKIE_SECURE=true`, `CAREERLINE_HSTS=true`, and `CAREERLINE_PUBLIC_URL`.
3. Pass the proxy's address to uvicorn's `--forwarded-allow-ips` so rate limits and the audit log see real client IPs.
