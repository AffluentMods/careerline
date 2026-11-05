# Phase 3 Plan: Application Tracker

Status: **done (2026-11-04)**.

**What changed from this plan while building it:**
- **Optional links use app-level clearing.** A composite foreign key can't `SET NULL` a single column through SQLAlchemy, so reminder → contact and attachment → event use the default rule and the service clears them before deleting. Deleting a timeline entry keeps its files on the application.
- **The agenda takes the browser's date** (`?today=`), so "overdue" and "today" match your calendar without a server-side time zone.
- **Referrals auto-link** the contact to the application as a referrer.
- **Every API response's CSP now includes `sandbox`.** The security-headers middleware sets the API policy for all `/api/` responses, so adding `sandbox` there covers attachment downloads too.
- **The body-size override matches route patterns** (the attachment path contains an ID).

**Goal:** track every application from Saved to a final outcome in one place. That covers:
- a board and a list,
- a timeline of what you sent and what happened,
- the people involved and who referred you,
- reminders and deadlines that show up on your Overview,
- a side-by-side comparison of offers.

**Spec scope:**
- Section 4.5.
- Section 9, phase 3: applications, kanban, timeline, contacts and referrals, reminders.
- Offer comparison is also in 4.5, so I've included it here (see decision table).

---

## 1. Decisions (defaults in bold; tell me to change any)

| Topic | Choice | Why |
|---|---|---|
| Job details | **Each application stores its own job details:** title, company, location, work mode, link, pay range, source, and the pasted job description. In Phase 4, a nullable `job_posting_id` will be added to point at discovered postings. | Discovery doesn't exist yet. You need to track jobs you found anywhere, and the copy stays even if a posting disappears. |
| Stages | **Fixed list:** Saved → Applied → Screening → Interviewing → Offer, then Accepted / Rejected / Withdrawn. The last three appear as a collapsed **Closed** column. | Matches the spec. A fixed list keeps the board and later analytics (Phase 8) simple. |
| Stage history | **Every stage change adds a timeline event automatically.** Stage events can't be edited or deleted; notes can. | Gives an honest history. Phase 8's funnel analytics (time-to-response, conversion rates) is built on these events. |
| Moving cards | **Built-in browser drag and drop on desktop, plus a "Move to…" menu on every card** for keyboard, touch, and screen-reader users. **No drag-and-drop library.** | Avoids a new dependency. The menu makes the board fully usable without dragging. The alternative is `@dnd-kit` (MIT, ~40 kB), which I can add if you'd rather. |
| Card order | **Sorted automatically** within each column: next due item first, then most recently updated. Optional priority (low / normal / high). No manual reordering. | Manual ordering needs ranking logic and rarely beats "what's due next". |
| Attachments | **Stored in Postgres, encrypted** with the existing field cipher. **10 MB per file, 250 MB per user** (configurable). **Allowed types:** PDF, DOCX, PNG, JPEG, plain text, checked by content. **Downloads only:** files are never shown inline, and are served with `Content-Disposition: attachment` and a sandboxing CSP. | One thing to back up and move between computers, with no file-system volume needed. Offer letters and resumes are sensitive, so encryption at rest is worth it. Files are small, so storing them in the database is fine. |
| Contacts | **One contacts list per user, reused across applications.** Name, company, and title are plain text, for search. **Email and phone are encrypted** (the spec asks for this). Each contact is linked to an application with a role: recruiter, hiring manager, interviewer, referrer, or other. | One person often matters for several applications. |
| Referrals | **A separate record per (application, contact):** status (to ask, asked, agreed, submitted, declined), asked on, referred on, follow-up date, notes. | Covers "who referred you, when, follow-up status" from the spec. |
| Reminders | **In-app only in this phase.** You see them on an agenda in the tracker and as a "Due soon" card on Overview, with a count in the sidebar. Email, ntfy, and webhook delivery come in Phase 8. | Nothing leaves the server without a configured channel, and notification channels are Phase 8 scope. |
| Reminder dates | **A local date with an optional time, no time zone.** Phase 8 will add a time-zone setting when the server starts sending notifications. | The browser shows them as entered. No time-zone bugs while only you are looking at them. |
| Deadlines | **The agenda also includes application deadlines ("apply by") and offer deadlines**, without you creating reminders for them. | "Deadlines" without duplicate data entry. |
| Follow-up nudge | **When you move a card to Applied, Waypoint offers a follow-up reminder** one week out. One click to accept. Nothing is created automatically. | Useful, and still your decision. |
| Offers | **Included.** One offer per application. Comparison table: base pay, **annual and hourly equivalents** (from hours per week, default 40), bonus, stock or equity (as notes), PTO days, schedule, one-way commute minutes, growth path, benefits notes, and deadline. Money is stored as integer cents with a currency, and offers are only compared within the same currency. | It's in the spec's tracker section, and it's small. |
| Deleting | **Hard delete with confirmation.** Deleting an application also deletes its events, attachments, links, referrals, offer, and reminders. Contacts are kept. Deletions are audited. | Keeps the database tidy. The audit log records that it happened, not what was deleted. |

**New dependencies: none** (backend or frontend).

## 2. Data model (migration `0003`)

All tables have `user_id`, plus `UNIQUE (user_id, id)` where other tables reference them. Every cross-table reference is a **composite foreign key on `(user_id, …)`**, as in Phase 2, so the database itself blocks cross-user links.

| Table | Key columns |
|---|---|
| `applications` | `title`, `company`, `location`, `work_mode` (onsite / hybrid / remote / null), `job_url`, `source`, `pay_min_cents`, `pay_max_cents`, `pay_period` (hour / year), `currency`, `description` (pasted text, up to 50k characters), `stage`, `stage_changed_at`, `applied_on`, `deadline`, `priority` |
| `application_events` | `application_id`, `kind` (`stage_change`, `note`, `sent`, `interview`, `contact`, `other`), `occurred_at`, `title`, `body`, `data` (JSONB: stage from/to, which materials were sent, interview type, and so on) |
| `attachments` | `application_id`, `event_id` (nullable), `filename`, `mime`, `size_bytes`, `sha256`, `content_enc` (bytea, AES-GCM) |
| `contacts` | `name`, `company`, `title`, `email_enc`, `phone_enc`, `linkedin_url`, `notes` |
| `application_contacts` | `application_id`, `contact_id`, `role` |
| `referrals` | `application_id`, `contact_id`, `status`, `asked_on`, `referred_on`, `follow_up_on`, `notes` |
| `reminders` | `title`, `kind` (follow-up / deadline / interview / task), `due_on`, `due_time`, `application_id` (nullable), `contact_id` (nullable), `done_at`, `notes` |
| `offers` | `application_id` (unique), `currency`, `base_cents`, `base_period`, `hours_per_week`, `bonus_cents`, `bonus_notes`, `equity_notes`, `pto_days`, `schedule`, `commute_minutes`, `growth_notes`, `benefits_notes`, `deadline` |

"What was sent / which resume version": a **Sent** event records a free-text description of what you sent (for example "Resume v3, security-analyst version, plus cover letter"), with the files optionally attached. In Phase 6 it will also link to the generated document.

## 3. API (`/api/v1/tracker`; every route needs an active session and CSRF)

| Method & path | What it does |
|---|---|
| `GET/POST /applications`, `GET/PATCH/DELETE /applications/{id}` | CRUD. The list can be filtered by stage and search text. It returns board-ready summaries: next due item, contact count, whether there's an offer. |
| `POST /applications/{id}/stage` | `{stage, note?}`: change the stage and write the stage event. Moving to Applied fills in `applied_on` if it's empty. |
| `GET/POST /applications/{id}/events`, `PATCH/DELETE /events/{id}` | Timeline. Stage events are read-only. |
| `POST /applications/{id}/attachments` | Raw-body upload (like resume import), optionally tied to an event. |
| `GET /attachments/{id}/download`, `DELETE /attachments/{id}` | Download (served as an attachment) or delete. |
| `GET/POST /contacts`, `GET/PATCH/DELETE /contacts/{id}` | Contacts. Email and phone are encrypted at rest. |
| `PUT/DELETE /applications/{id}/contacts/{contact_id}` | Link or unlink a contact with a role. |
| `GET/POST /applications/{id}/referrals`, `PATCH/DELETE /referrals/{id}` | Referrals. |
| `GET/POST /reminders`, `PATCH/DELETE /reminders/{id}` | Reminders. Mark done or undo. |
| `GET /agenda?days=14` | Overdue and upcoming items, merged from reminders, application deadlines, and offer deadlines. Used by the Overview page and the sidebar count. |
| `PUT/DELETE /applications/{id}/offer`, `GET /offers` | Offer for one application, and every offer with computed annual and hourly figures for comparison. |
| `GET /summary` | Counts per stage, plus overdue and due-today counts. |

**Isolation:** the same per-user helper pattern as the vault. Another user's IDs return **404**. Tests cover every route, plus the database constraints.

**Limits:**
- Attachment uploads use the same per-route body limit as resume import. The override will match the route pattern, since the path contains an ID.
- 60 uploads per hour per user.
- The per-user storage quota is checked before saving.

**Audit log events:** `tracker.application_deleted`, `tracker.attachment_uploaded`, `tracker.attachment_deleted`, `tracker.contact_deleted`.

## 4. Frontend

The **Tracker** page replaces the placeholder. It has tabs for **Board · List · Agenda · Contacts · Offers**.

- **Board:**
  - Five open columns plus a collapsed **Closed** column.
  - Each card shows title, company, pay range, days in stage, and next due item.
  - Drag a card to move it, or use the card's **Move to…** menu.
  - Moving a card to Applied shows "Remind me to follow up on (date)?"
- **List:** a sortable table (company, title, stage, applied, last activity, next due) with stage filter and search. Works on phones as stacked rows.
- **Application page** (`/tracker/:id`):
  - Header with title, company, stage selector, and priority.
  - Sections:
    - **Details:** the job fields, plus the pasted description shown as plain text.
    - **Timeline:** add a note, Sent, Interview, or Contact event, and attach files.
    - **People:** linked contacts and referral status.
    - **Reminders**
    - **Offer**
- **Agenda:** Overdue, Today, This week, and Later groups. Tick an item done, or snooze it by a day or a week.
- **Contacts:** a searchable list. Each contact shows the applications they're linked to.
- **Offers:** a side-by-side comparison table. The best value in each row is highlighted, and only offers in the same currency are compared.
- **Overview:** new **Due soon** and **Pipeline** cards. The sidebar shows the overdue + due-today count on Tracker.

Everything uses the existing design tokens, light and dark themes, and keyboard access.

## 5. Tests

**Backend unit tests:**
- Pay maths (annual ↔ hourly, rounding, currency guard).
- Agenda merge and ordering.
- Attachment type sniffing.
- Stage-transition side effects.

**Backend API tests:**
- CRUD for every resource.
- A stage change writes an event, and stage events can't be edited or deleted.
- Attachments: encryption at rest, download headers, wrong type returns 415, oversize returns 413, quota enforced.
- Contacts' email and phone are encrypted at rest.
- Deleting an application cascades and is audited.
- **Cross-user isolation on every route** and at the database level.

**Frontend unit tests:**
- Board grouping and sorting.
- Money formatting and the hourly/annual helpers.
- Agenda grouping (overdue / today / this week).
- The Move to… flow.

**Playwright end-to-end:**
1. Create an application.
2. Move it Saved → Applied and accept the follow-up reminder.
3. Add a Sent event with an attachment, then download it.
4. Add a contact as referrer with referral status.
5. Check that the reminder shows on Overview.
6. Move the application to Offer, enter the offer, and compare it with a second offer.

## 6. Docs to update

- **README:** a Tracker section.
- **CHANGELOG:** release 0.3.0.
- **`docs/architecture.md`:** tracker tables, agenda, and how attachments are stored.
- **`docs/threat-model.md`:** attachment upload and download (stored XSS through downloads, type confusion, quota exhaustion), plus encryption of contact details.
- **`docs/configuration.md`:** attachment size and quota settings.

## 7. Build order (small, reviewable steps)

1. Cipher support for binary data, migration, and models.
2. Applications, stages, and events API with tests.
3. Contacts, links, and referrals API.
4. Reminders and agenda API.
5. Attachments (upload, download, quota) with tests.
6. Offers and comparison maths.
7. UI: Board and List, then the Application page.
8. UI: Agenda, Contacts, Offers, and the Overview cards.
9. End-to-end test, docs, CHANGELOG, version 0.3.0.

## 8. Done when

- All backend, frontend, and Playwright tests pass. ruff, mypy strict, ESLint, tsc, and the license check are clean.
- Locally, with `.\start.ps1`, you can:
  - track an application from Saved to Offer with a timeline and an attachment,
  - see a reminder on Overview,
  - compare two offers.
- Docker Compose is still untested on this machine (no Docker).

## 9. Not in this phase

- Linking to discovered job postings (Phase 4).
- Generated resumes and cover letters in Sent events (Phase 6).
- Email, ntfy, and webhook notifications, a time-zone setting, and funnel analytics (Phase 8).
- Importing applications from a spreadsheet (could come with Phase 8's import/export).
- Calendar (ICS) export of reminders. This is easy to add later if you want it.
