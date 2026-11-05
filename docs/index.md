# Waypoint

A self-hosted, AI-assisted job search app. It helps you find jobs, decide which are worth it, prepare truthful application materials from your own verified facts, track every application, and prepare for interviews. Your data lives in your own database.

## Start here

- **Try it:** `.\start.ps1 -Demo` (Windows) or `./start.sh --demo`, then sign in as `demo@example.com` / `explore-waypoint`. Everything in the demo is fictional and nothing leaves your computer.
- **Use it for real:** `.\start.ps1` or `./start.sh`, open http://localhost:8080, and create the owner account. Back up the `.env` file it creates.
- **Host it:** Docker Compose behind a TLS reverse proxy. See [configuration](configuration.md) and [operations](operations.md).

## Pages

| Page | For |
|---|---|
| [User guide](guide.md) | What each part of Waypoint does, in the order you'd use it. |
| [Operations](operations.md) | Backups and restores, single sign-on (Authelia), notifications, demo mode, upgrades. |
| [Configuration](configuration.md) | Every setting and fixed limit. |
| [Architecture](architecture.md) | How it's built, and why. |
| [Threat model](threat-model.md) | What's protected, from what, and how. |

## Principles

- **Private by default.** Nothing leaves your server except calls you configure.
- **Truthful by design.** The AI may only use facts you've verified, every generated line cites its facts, and unsupported lines block export.
- **You stay in charge.** The assistant proposes changes; you confirm them. Nothing is sent on your behalf.
- **Security first, open source.** Apache-2.0, permissive dependencies only.
