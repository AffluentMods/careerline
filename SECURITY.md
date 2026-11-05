# Security Policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems.

Report privately through GitHub's "Report a vulnerability" (Security → Advisories) on this repository. Include:

- what you found and where (file, endpoint, or screen),
- steps to reproduce or a proof of concept,
- the impact you expect.

You'll get an acknowledgement within 5 days. Fixes for confirmed issues are released as soon as practical, and reporters are credited unless they ask not to be.

## Supported versions

Only the latest release receives security fixes.

## Scope

In scope: this repository's code, default configuration, Docker image, and Compose file.
Out of scope: vulnerabilities in third-party dependencies with no demonstrated impact on Waypoint (please report those upstream), and issues that need an already-compromised host.

## Hardening already in place

See [docs/threat-model.md](docs/threat-model.md) for the full list. Highlights: argon2id passwords, TOTP MFA with replay protection and one-time recovery codes, opaque server-side sessions (hashed at rest), CSRF tokens bound to the session, strict Content-Security-Policy, AES-256-GCM field encryption, rate limiting, and an audit log.
