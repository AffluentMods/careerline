// SPDX-License-Identifier: Apache-2.0
import { useState, type ChangeEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button, Card, Checkbox, FullPageLoader, Notice } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  KIND_LABEL,
  useEntries,
  useImport,
  useImports,
  useProfile,
  useVaultMutations,
  type Entry,
  type ImportDetail,
  type ImportSummary,
  type Kind,
  type Profile,
} from "../../lib/vault";
import { EntryItem } from "./entries";

const ALL_KINDS = Object.keys(KIND_LABEL) as Kind[];

const SUGGESTION_LABELS: Partial<Record<keyof Profile, string>> = {
  full_name: "Full name",
  headline: "Headline",
  location_text: "Location",
  email: "Email",
  phone: "Phone",
  summary: "Summary",
};

// --- pure helpers (unit tested) ----------------------------------------------------------

/** Order import entries parent-first and give each a nesting depth within the import. */
export function reviewOrder(entries: Entry[]): { entry: Entry; depth: number }[] {
  const ids = new Set(entries.map((e) => e.id));
  const kids = new Map<string, Entry[]>();
  const roots: Entry[] = [];
  for (const e of entries) {
    if (e.parent_id && ids.has(e.parent_id)) kids.set(e.parent_id, [...(kids.get(e.parent_id) ?? []), e]);
    else roots.push(e);
  }
  const out: { entry: Entry; depth: number }[] = [];
  const walk = (e: Entry, depth: number) => {
    out.push({ entry: e, depth });
    for (const c of kids.get(e.id) ?? []) walk(c, depth + 1);
  };
  roots.forEach((r) => walk(r, 0));
  return out;
}

/** Keep only selections that are still drafts in this import. */
export function liveSelection(selected: Set<string>, entries: Entry[]): string[] {
  const drafts = new Set(entries.filter((e) => !e.verified).map((e) => e.id));
  return [...selected].filter((id) => drafts.has(id));
}

/** Suggestions worth offering: non-empty and different from the current profile. */
export function usefulSuggestions(
  suggestions: Partial<Profile>,
  profile: Profile,
): { key: keyof Profile; value: string; replaces: string }[] {
  return (Object.keys(SUGGESTION_LABELS) as (keyof Profile)[])
    .map((key) => ({ key, value: (suggestions[key] ?? "").trim(), replaces: profile[key] }))
    .filter((s) => s.value && s.value !== s.replaces);
}

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}

// --- list + upload --------------------------------------------------------------------------

export function ImportsTab() {
  const imports = useImports();
  const m = useVaultMutations();
  const navigate = useNavigate();

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const result = await m.upload.mutateAsync(file);
      navigate(`/vault/imports/${result.id}`);
    } catch {
      // shown below
    }
  }

  return (
    <section className="stack" aria-label="Imports">
      <Card
        title="Import a resume"
        description="We read the text and turn it into drafts for you to check. Nothing is used until you approve it."
      >
        <div className="upload">
          <label className={`btn btn-primary${m.upload.isPending ? " is-busy" : ""}`}>
            {m.upload.isPending ? <span className="spinner" aria-hidden="true" /> : null}
            {m.upload.isPending ? "Reading your resume…" : "Choose a PDF or Word file"}
            <input
              type="file"
              className="sr-only"
              accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              disabled={m.upload.isPending}
              onChange={(e) => void onFile(e)}
            />
          </label>
          <p className="muted small">
            Up to 5 MB. The file itself isn't kept: only the text we read from it, encrypted, until you delete the
            import.
          </p>
        </div>
        {m.upload.error ? <Notice>{errorMessage(m.upload.error)}</Notice> : null}
      </Card>

      {imports.isPending ? <FullPageLoader /> : null}
      {imports.isError ? <Notice>{errorMessage(imports.error)}</Notice> : null}
      {imports.data?.length ? (
        <>
          <h2 className="section-title">Past imports</h2>
          <ul className="entry-list">
            {imports.data.map((imp) => (
              <ImportRow key={imp.id} imp={imp} />
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

function ImportRow({ imp }: { imp: ImportSummary }) {
  return (
    <li className="entry">
      <div className="entry-main">
        <div className="entry-text">
          <div className="entry-title-row">
            <span className="entry-title">{imp.filename}</span>
            {imp.drafts_remaining ? (
              <span className="badge badge-warn">{imp.drafts_remaining} to review</span>
            ) : (
              <span className="badge badge-ok">Reviewed</span>
            )}
          </div>
          <p className="muted small">
            {new Date(imp.created_at).toLocaleString()} · {formatSize(imp.size_bytes)} · {imp.stats.total ?? 0} drafts
            found
          </p>
        </div>
        <div className="entry-actions">
          <Link className="btn btn-secondary" to={`/vault/imports/${imp.id}`}>
            {imp.drafts_remaining ? "Review" : "Open"}
          </Link>
        </div>
      </div>
    </li>
  );
}

// --- review --------------------------------------------------------------------------------

export function ImportReview({ id }: { id: string }) {
  const detail = useImport(id);
  const entries = useEntries();
  const profile = useProfile();

  if (detail.isPending || entries.isPending || profile.isPending) return <FullPageLoader />;
  if (detail.isError) {
    return (
      <div className="stack">
        <Notice>{errorMessage(detail.error)}</Notice>
        <Link to="/vault/imports">Back to imports</Link>
      </div>
    );
  }
  if (entries.isError) return <Notice>{errorMessage(entries.error)}</Notice>;
  if (profile.isError) return <Notice>{errorMessage(profile.error)}</Notice>;
  return <Review imp={detail.data} all={entries.data} profile={profile.data} />;
}

function Review({ imp, all, profile }: { imp: ImportDetail; all: Entry[]; profile: Profile }) {
  const m = useVaultMutations();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [discard, setDiscard] = useState(true);

  const ordered = reviewOrder(imp.entries);
  const drafts = imp.entries.filter((e) => !e.verified);
  const live = liveSelection(selected, imp.entries);
  const allSelected = drafts.length > 0 && live.length === drafts.length;

  function toggle(entryId: string, on: boolean) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (on) next.add(entryId);
      else next.delete(entryId);
      return next;
    });
  }

  async function approveSelected() {
    setError(null);
    setBulkBusy(true);
    try {
      await m.verify.mutateAsync({ ids: live, verified: true });
      setSelected(new Set());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBulkBusy(false);
    }
  }

  async function discardSelected() {
    setError(null);
    setBulkBusy(true);
    try {
      for (const entryId of live) {
        // Deleting a parent removes its children, so later ids may already be gone.
        await m.remove.mutateAsync(entryId).catch(() => undefined);
      }
      setSelected(new Set());
    } finally {
      setBulkBusy(false);
    }
  }

  async function finish() {
    setError(null);
    try {
      await m.removeImport.mutateAsync({ id: imp.id, discardDrafts: discard });
      navigate("/vault/experience");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const sections = imp.stats.sections ?? [];

  return (
    <section className="stack" aria-label="Review import">
      <div className="row-between">
        <div>
          <Link to="/vault/imports" className="small">
            ← All imports
          </Link>
          <h2>Review {imp.filename}</h2>
          <p className="muted small">
            {imp.stats.total ?? 0} drafts found
            {sections.length ? ` in ${sections.join(", ")}` : ""}. {drafts.length} left to review.
          </p>
        </div>
      </div>

      {(imp.stats.total ?? 0) === 0 ? (
        <Notice tone="info">
          We couldn't find sections like Experience or Education in this file. You can still copy facts from the
          text on the left into your vault by hand.
        </Notice>
      ) : (
        <Notice tone="info">
          These drafts were guessed from your resume&apos;s layout. Check each one against your records, fix anything
          that&apos;s off, then approve it.
        </Notice>
      )}

      <div className="review-grid">
        <aside className="review-source" aria-label="Text read from the file">
          <h3 className="section-title">What we read</h3>
          <pre tabIndex={0}>{imp.text || "(no text found)"}</pre>
        </aside>

        <div className="stack">
          <Suggestions suggestions={imp.suggestions} profile={profile} />

          {drafts.length ? (
            <div className="bulk-bar" role="toolbar" aria-label="Bulk actions">
              <Checkbox
                label={allSelected ? "Clear selection" : `Select all ${drafts.length} drafts`}
                checked={allSelected}
                onChange={(e) => setSelected(e.target.checked ? new Set(drafts.map((d) => d.id)) : new Set())}
              />
              <div className="row">
                <Button disabled={!live.length} busy={bulkBusy} onClick={() => void approveSelected()}>
                  Approve selected ({live.length})
                </Button>
                <Button
                  variant="secondary"
                  disabled={!live.length || bulkBusy}
                  onClick={() => void discardSelected()}
                >
                  Discard selected
                </Button>
              </div>
            </div>
          ) : null}
          {error ? <Notice>{error}</Notice> : null}

          <ul className="entry-list">
            {ordered.map(({ entry, depth }) => (
              <EntryItem
                key={entry.id}
                entry={entry}
                entries={all}
                depth={depth}
                showKind
                kindChoices={ALL_KINDS}
                select={
                  entry.verified ? undefined : { checked: selected.has(entry.id), onChange: (on) => toggle(entry.id, on) }
                }
              />
            ))}
          </ul>

          <Card
            title="Finish"
            description="Delete this import when you're done. Approved entries stay in your vault either way."
          >
            {finishing ? (
              <div className="stack-sm">
                <Checkbox
                  checked={discard}
                  onChange={(e) => setDiscard(e.target.checked)}
                  label={`Also discard the ${drafts.length} draft${drafts.length === 1 ? "" : "s"} I haven't approved`}
                />
                <div className="row">
                  <Button variant="danger" busy={m.removeImport.isPending} onClick={() => void finish()}>
                    Delete import
                  </Button>
                  <Button variant="ghost" onClick={() => setFinishing(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <Button variant="secondary" onClick={() => setFinishing(true)}>
                Delete import…
              </Button>
            )}
          </Card>
        </div>
      </div>
    </section>
  );
}

function Suggestions({ suggestions, profile }: { suggestions: Partial<Profile>; profile: Profile }) {
  const m = useVaultMutations();
  const offers = usefulSuggestions(suggestions, profile);
  const [chosen, setChosen] = useState<Set<keyof Profile>>(
    () => new Set(offers.filter((o) => !o.replaces).map((o) => o.key)),
  );
  const [done, setDone] = useState(false);
  if (!offers.length) return done ? <Notice tone="success">Profile updated.</Notice> : null;

  async function apply() {
    const next: Profile = { ...profile };
    for (const o of offers) if (chosen.has(o.key)) next[o.key] = o.value;
    await m.saveProfile.mutateAsync(next);
    setDone(true);
  }

  return (
    <Card title="Profile details found" description="Pick the ones to copy into your profile.">
      <ul className="suggestions">
        {offers.map((o) => (
          <li key={o.key}>
            <Checkbox
              checked={chosen.has(o.key)}
              onChange={(e) =>
                setChosen((cur) => {
                  const next = new Set(cur);
                  if (e.target.checked) next.add(o.key);
                  else next.delete(o.key);
                  return next;
                })
              }
              label={
                <>
                  <strong>{SUGGESTION_LABELS[o.key]}:</strong> {o.value}
                </>
              }
              hint={o.replaces ? `Replaces “${o.replaces}”` : undefined}
            />
          </li>
        ))}
      </ul>
      {m.saveProfile.error ? <Notice>{errorMessage(m.saveProfile.error)}</Notice> : null}
      <Button variant="secondary" disabled={!chosen.size} busy={m.saveProfile.isPending} onClick={() => void apply()}>
        Apply to profile
      </Button>
    </Card>
  );
}
