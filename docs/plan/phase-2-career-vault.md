# Phase 2 Plan: Career Vault

Status: **done (2026-11-04)**.

**What changed from this plan while building it:**
- **"Usable" instead of blocking verification.** You can verify a fact whose parent role is still a draft, but it isn't *usable* by the AI until the parent is verified too. The UI shows "Parent is a draft". This avoids order-dependent approvals during import review.
- **No import `status` column.** Failed files are rejected before anything is stored, so every saved import is a parsed one. Profile suggestions are stored encrypted (`suggestions_enc`) so the review screen survives a reload.
- **Role employer is optional.** The parser can't always find it, and a placeholder employer would be invented data. Title is required; a dated role with no title found gets the marked placeholder "(add job title)".
- **Delete with "discard drafts"** keeps any draft that has a verified entry under it, so nothing approved is lost.
- **Local runner fixes found while testing:** real environment variables now override `.env`, and startup waits for Postgres crash recovery after an unclean shutdown instead of failing.

**Goal:** a single, trustworthy record of your career that every later phase draws from. You can:
- enter facts by hand,
- import them from an existing resume as drafts,
- review and verify each fact,
- build a STAR story bank linked to those facts.

From Phase 5 on, the AI may use only **verified** facts.

**Spec scope (section 9, phase 2):** CRUD, resume import into draft entries, verification flow, story bank.

---

## 1. Decisions (defaults in bold; tell me to change any)

| Topic | Choice | Why |
|---|---|---|
| Data shape | **One `vault_entries` table** with a `kind` column and a JSONB `data` column. Each kind has its own strict Pydantic schema, selected by a discriminated union. | Phase 6 must link every generated resume bullet to the entry it came from. One table gives that link a single stable ID to point at. A new kind needs no migration. Pydantic still rejects bad fields. |
| Certifications | **A kind of vault entry**, not a separate table. The spec lists `Certification` on its own. | Certifications are facts like any other. They need the same verified and evidence handling, and resumes cite them the same way. |
| What "verified" means | **One flag per entry and per story.** The AI uses an item only if it is verified. Unverified items are drafts. | This keeps the spec's "verified flag" and "draft, then approve" as one idea. Two separate states (approved *and* verified) would be confusing. |
| Entries you type yourself | **Start verified.** The form has an "I confirm this is accurate" box, ticked by default. | You wrote the entry, so you are vouching for it. Untick the box to keep it as a draft. |
| Imported entries | **Always start unverified,** tagged with the import they came from. | The parser guesses. Nothing it produces becomes usable until you approve it. |
| Editing a verified entry | **Stays verified.** `verified_at` is updated. | You made the change. Phase 6 records the exact text it used at generation time, so earlier documents stay accurate. |
| Resume parsing | **Rule-based parser in Phase 2.** It finds section headings, date ranges, and bullet points. An optional AI-assisted import comes in Phase 5. | The AI layer doesn't exist yet. The rule-based parser is deterministic and testable, and works offline. Expect rough drafts that need review. |
| Upload format | **Raw request body** (`Content-Type: application/pdf` or the DOCX type), not multipart. | Avoids adding a multipart parser, which is one less dependency and one less code path that handles untrusted input. |
| Original file | **Not stored.** Only the extracted text is kept, encrypted, so you can view it next to the drafts. Deleting the import deletes that text. Drafts you already approved are kept. | Resumes contain your address and phone number. Keeping the smallest amount of data is the safer default. |
| Profile contact details | **Encrypted at rest:** email, phone, address. Name, headline, and summary are stored as plain text. | The spec asks for contact details to be encrypted. Vault facts themselves stay plain text, so later phases can search and match against them. The database is private to you. |
| Deleting entries | **Hard delete,** cascading to child entries and removing links to stories. | Keeps Phase 2 simple. Phase 6 stores a copy of the text it used, so deleting an entry later won't break documents already generated. |

### New dependencies (both permissive, both need your OK)

| Package | License | Used for | Notes |
|---|---|---|---|
| `python-docx` | MIT | Reading DOCX text and paragraph styles | Pulls in `lxml` (BSD-3). I'll confirm its XML parser has entity resolution switched off. |
| `pypdf` | BSD-3-Clause | Reading PDF text | Pure Python. **PyMuPDF is AGPL and is excluded.** |

No new frontend dependencies.

## 2. Data model (migration `0002`)

**`vault_profiles`**: one row per user.
- `full_name`, `headline`, `location_text` (city/region only), `summary`.
- Encrypted: `email_enc`, `phone_enc`, `address_enc`. Each uses the encryption context `user:{id}:profile:{field}`.

**`vault_entries`**
- Identity: `id`, `user_id`, `kind`, `parent_id` (nullable).
- Content: `data` (JSONB), `evidence_note`.
- Verification: `verified` (bool), `verified_at`.
- Provenance: `source` (`manual` or `import`), `import_id`.
- Ordering and timestamps: `sort_date` (taken from the entry's dates, for ordering), `created_at`, `updated_at`.
- Isolation is enforced by the database, not only by app code. `UNIQUE (user_id, id)` lets `(user_id, parent_id)` be a **composite foreign key** to `(user_id, id)`, so an entry can never have another user's entry as its parent.
- `kind` is limited to the allowed values by a CHECK constraint.

**`stories`**
- Content: `title`, `situation`, `task`, `action`, `result`, `reflection`, `tags` (text[]).
- Verification: `verified`, `verified_at`.

**`story_entries`**: links stories to the vault entries they draw on.
- Columns: `(user_id, story_id, entry_id)`.
- Composite foreign keys on both sides make a cross-user link impossible.

**`resume_imports`**
- File details: `filename` (sanitized), `mime`, `size_bytes`, `sha256`.
- `text_enc`: the extracted text, encrypted.
- `status`: `parsed`, `failed`, or `done`.
- `stats` (JSONB): counts of drafts found per kind.

### Entry kinds and their fields

Dates are partial ISO strings (`YYYY` or `YYYY-MM`). An `end` of `null` means "current".

| Kind | Fields | Allowed parent |
|---|---|---|
| `role` | `title` (official), `employer`, `location`, `employment_type`, `start`, `end`, `summary` | none |
| `responsibility` | `text` | role |
| `achievement` | `text`, `metric` (optional: `value`, `unit`, `context`) | role, project, education, or none |
| `project` | `name`, `your_role`, `start`, `end`, `description`, `url` | role or none |
| `certification` | `name`, `issuer`, `status` (`earned`, `in_progress`, `expired`), `earned_on`, `expires_on`, `target_date`, `credential_id`, `url` | none |
| `education` | `institution`, `credential`, `field`, `status` (`completed`, `in_progress`), `start`, `end` | none |
| `skill` | `name`, `category`, `level` (optional: `familiar`, `proficient`, `expert`) | none |
| `link` | `label`, `url` | none |

Rules shared by all kinds:
- Every string has a maximum length.
- URLs must be `https`, or `http` for local addresses only.
- Text is stored as you typed it and escaped by React when shown. No HTML or Markdown rendering in Phase 2.

## 3. API (`/api/v1/vault`; every route needs an active session and CSRF)

| Method & path | What it does |
|---|---|
| `GET/PUT /profile` | Read or replace your profile. Contact fields are decrypted on read. |
| `GET /entries?kind=&verified=&q=` | List entries, filtered by kind, verified state, or text. |
| `POST /entries`, `GET/PATCH/DELETE /entries/{id}` | Create, read, update, delete. The parent's kind is checked against the table above. |
| `POST /entries/verify` | Bulk `{ids, verified}`, used for "approve selected". Writes an audit entry. |
| `GET /summary` | Counts per kind, plus how many entries need review (for the sidebar badge). |
| `POST /imports` | Upload a DOCX or PDF. The response is the import record plus the drafts it created. |
| `GET /imports`, `GET /imports/{id}` | List imports, or show one with its extracted text and remaining drafts. |
| `DELETE /imports/{id}?discard_drafts=true` | Delete the extracted text. Optionally also delete drafts you haven't verified. |
| `GET/POST /stories`, `GET/PATCH/DELETE /stories/{id}` | Story CRUD. `entry_ids` sets the links; each ID must belong to you. |

**Isolation:** every query goes through one helper that always filters by `user_id`. Another user's ID returns **404, not 403**, so the API never reveals that the record exists. The tests check this for every route.

**Audit log events:** `vault.import`, `vault.import_deleted`, `vault.verify` (with counts), `vault.profile_updated`.

## 4. Resume import pipeline (handling an untrusted file)

1. **Size cap: 5 MB for this route only.** The 1 MB limit stays in force everywhere else. The body-size middleware gains a per-route limit.
2. **Rate limit:** 20 imports per hour per user.
3. **File type is checked by its first bytes,** not only by the declared type:
   - PDF must start with `%PDF-`.
   - DOCX must start with `PK\x03\x04` and contain `word/document.xml`.
   - Anything else returns `415`.
4. **DOCX zip-bomb guard,** checked before the file is opened:
   - at most 200 files inside the archive,
   - at most 20 MB total uncompressed,
   - a compression ratio no higher than 100:1,
   - no absolute paths or `..` in file names.
5. **Parsing runs in a separate process with a 15-second timeout,** and the process is killed on timeout. A malformed or hostile PDF can't hang or crash the server. The parser also stops after 10 pages or 100 000 characters of text.
6. **Text is extracted only.** No macros, embedded objects, images, or links are followed or kept.
7. **The parser turns text into drafts:**
   - It recognises section headings (Experience, Education, Certifications, Skills, Projects, and common variants).
   - Within a section, it splits on date ranges and bullet points:
     - a role line becomes a `role` draft,
     - its bullets become `achievement` drafts if they contain a number, otherwise `responsibility` drafts,
     - a skills line is split on commas.
   - A contact block at the top fills profile *suggestions*. These are shown for you to accept and never applied automatically.
8. **Everything it creates is unverified,** with `source=import`.
9. **Failures are generic:** "We couldn't read this file." The details go to the server log only, never to the client.

Tests use **fictional resumes** that are built inside the tests: DOCX made with python-docx, and a small PDF written by hand. No real personal data goes in the repo.

## 5. Frontend

The **Vault** page replaces the placeholder. It has these tabs:
- **Profile**
- **Experience:** roles, with their responsibilities and achievements nested under each one
- **Projects**
- **Certifications**
- **Education**
- **Skills:** grouped by category
- **Links**
- **Stories**
- **Imports**

The "needs review" count shows as a badge in the sidebar.

**Each entry** shows a **Verified** or **Draft** badge and has inline edit, an evidence note, and delete. Draft entries also carry the line "Drafts are never used by the AI."

**Import review screen:**
- The extracted text is on the left and the drafts are on the right, in a single column on phones.
- For each draft you can edit it, change its kind, set its parent role, approve it, or discard it.
- Checkboxes plus an **Approve selected** button let you approve several at once.
- Profile suggestions show as a separate card with an **Apply** button.

**Story editor:**
- Fields for Situation, Task, Action, Result, and Reflection.
- A picker to link vault entries.
- Tags.
- The verified toggle.

**Empty states** offer two first steps: "Import a resume" and "Add your first role."

Everything works with the keyboard. The same CSS tokens are used, so light and dark mode both work.

## 6. Tests

- **Unit tests:**
  - every kind's schema, with valid and invalid examples;
  - partial-date and URL validation;
  - the parser against several fictional resumes: a classic layout, a two-column DOCX, a PDF, and a file with no recognisable sections;
  - the file-type and zip-bomb guards;
  - timeout handling.
- **API tests:**
  - CRUD for every kind;
  - parent-kind rules;
  - bulk verify;
  - profile encryption at rest;
  - import with a good DOCX, a good PDF, a wrong type (415), more than 5 MB (413), a zip bomb, and a corrupt file;
  - rate limit;
  - stories with links;
  - **cross-user isolation on every route** (including trying to link another user's entry to your story, or using it as a parent);
  - audit events.
- **Frontend unit tests:** the entry form, the verified badge logic, and the import review selection.
- **Playwright end-to-end:**
  1. Add a role and an achievement by hand.
  2. Import a fictional DOCX.
  3. Approve two drafts and discard one.
  4. Create a story linked to the achievement.
  5. Check the review badge count at each step.

## 7. Docs to update

- **README:** a Vault section.
- **CHANGELOG:** release 0.2.0.
- **`docs/architecture.md`:** add the vault tables and the import flow.
- **`docs/threat-model.md`:** add rows for uploaded files, meaning parser DoS, zip bombs, and malicious content, plus how each is handled.
- **`docs/configuration.md`:** the import limits, if they are made configurable.

## 8. Build order (small, reviewable steps)

1. Migration, models, and Pydantic schemas for each kind, with unit tests.
2. Entry, profile, and verify API, with isolation tests.
3. Story API.
4. Vault UI: profile, entry lists and forms, badges.
5. Story UI.
6. Import guards and the process-isolated extractor, with tests.
7. The text-to-drafts parser, with fixture tests.
8. Import API and review UI.
9. End-to-end test, docs, CHANGELOG.

## 9. Done when

- All backend, frontend, and Playwright tests pass. ruff, mypy strict, ESLint, and tsc are clean. The license check passes with the two new packages.
- Locally with `.\start.ps1`, you can:
  - build a vault by hand,
  - import a resume,
  - approve drafts,
  - write a linked story.

  Restarting the app keeps all of it.
- Docker Compose is still untested on this machine (no Docker), as in Phase 1.

## 10. Not in this phase

- AI-assisted import (Phase 5).
- Generating resumes from the vault (Phase 6).
- Mapping interview questions to stories (Phase 7).
- Full JSON export and import of the vault (Phase 8).
- Attachments or evidence files. Evidence is a text note for now.
