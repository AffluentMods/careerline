# Contributing

Thanks for helping. Please read this first.

## Clean-room rule (required)

Waypoint is an original work under Apache-2.0. To keep it that way:

- **Don't read, copy, adapt, or translate** source code, prompts, UI text, schemas, or assets from other job-search tools or from any AGPL, GPL, or Commons Clause project. Feature *ideas* are fine; implementations must be your own.
- **Dependencies must be permissively licensed** (MIT, BSD, Apache-2.0, ISC, MPL-2.0, or similar). Ask in an issue before adding anything else, or any large dependency. `npm run licenses` checks the frontend.
- **Never commit secrets or real personal data.** Test and demo data must be fictional.

## Workflow

1. Open an issue describing the change, unless it's small.
2. Keep changes small and focused, with tests.
3. Before opening a pull request, run:
   - backend: `uv run ruff check . && uv run ruff format --check . && uv run mypy waypoint && uv run pytest`
   - frontend: `npm run lint && npm run typecheck && npm test && npm run e2e`
4. Update `CHANGELOG.md` and the relevant `docs/` page.
5. Add an `SPDX-License-Identifier: Apache-2.0` header to new source files.

## Code conventions

- Every user-owned table has a non-null `user_id` foreign key, and every query filters by the current user.
- State-changing endpoints go through the auth router dependencies (CSRF + session). Never accept a user id from the client.
- Validate all input with Pydantic models (`extra="forbid"`). Don't echo submitted values in errors.
- Write an audit entry for security-relevant actions, without secrets in `details`.
- Treat job descriptions, web pages, and imported files as untrusted data, never as instructions (this matters from Phase 4 on).
