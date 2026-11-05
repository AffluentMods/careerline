// SPDX-License-Identifier: Apache-2.0
/** Entry form and entry display shared by every vault tab and the import review. */
import { useState, type FormEvent } from "react";
import { Button, Checkbox, Field, Notice, SelectField, TextAreaField } from "../../components/ui";
import { errorMessage, fieldErrors } from "../../lib/api";
import {
  ALLOWED_PARENTS,
  FIELDS,
  KIND_LABEL,
  convertForm,
  entryDates,
  entryState,
  entrySubtitle,
  entryTitle,
  fromForm,
  parentOptions,
  toForm,
  useVaultMutations,
  type Entry,
  type EntryInput,
  type FormValues,
  type Kind,
} from "../../lib/vault";

const DATE_PATTERN = "(19|20)\\d{2}(-(0[1-9]|1[0-2]))?";

export interface EntryFormProps {
  kind: Kind;
  /** Kinds the user may switch to (import review). Omit to lock the kind. */
  kindChoices?: Kind[];
  initial?: Entry;
  parentId?: string | null;
  entries: Entry[];
  submitLabel: string;
  onSubmit: (input: EntryInput) => Promise<unknown>;
  onCancel: () => void;
}

export function EntryForm({
  kind: startKind,
  kindChoices,
  initial,
  parentId = null,
  entries,
  submitLabel,
  onSubmit,
  onCancel,
}: EntryFormProps) {
  const [kind, setKind] = useState<Kind>(startKind);
  const [values, setValues] = useState<FormValues>(() => toForm(startKind, initial?.data ?? {}));
  const [parent, setParent] = useState<string>(initial ? (initial.parent_id ?? "") : (parentId ?? ""));
  const [evidence, setEvidence] = useState(initial?.evidence_note ?? "");
  const [verified, setVerified] = useState(initial ? initial.verified : true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const parentsAllowed = ALLOWED_PARENTS[kind];
  const canHaveParent = parentsAllowed.some((k) => k !== null);
  const needsParent = !parentsAllowed.includes(null);
  const parentChoices = parentOptions(entries, kind, initial?.id);
  const errs = fieldErrors(error);

  function changeKind(next: Kind) {
    setValues((v) => convertForm(kind, next, v));
    setKind(next);
    if (!ALLOWED_PARENTS[next].some((k) => k !== null)) setParent("");
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        kind,
        parent_id: canHaveParent && parent ? parent : null,
        data: fromForm(kind, values),
        evidence_note: evidence.trim(),
        verified,
      });
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  const set = (name: string) => (v: string) => setValues((cur) => ({ ...cur, [name]: v }));

  return (
    <form className="entry-form" onSubmit={submit} noValidate={false}>
      {kindChoices ? (
        <SelectField
          label="Kind"
          value={kind}
          onChange={(e) => changeKind(e.target.value as Kind)}
          options={kindChoices.map((k) => ({ value: k, label: KIND_LABEL[k] }))}
        />
      ) : null}
      <div className="form-grid">
        {FIELDS[kind].map((f) => {
          const common = {
            label: f.label + (f.required ? "" : " (optional)"),
            value: values[f.name] ?? "",
            required: f.required,
            maxLength: f.maxLength,
          };
          const hint = errs[f.name] ? <span className="field-error">{errs[f.name]}</span> : f.hint;
          const wide = f.type === "textarea" ? " span-2" : "";
          if (f.type === "textarea") {
            return (
              <div key={f.name} className={wide}>
                <TextAreaField {...common} hint={hint} onChange={(e) => set(f.name)(e.target.value)} />
              </div>
            );
          }
          if (f.type === "select") {
            return (
              <SelectField
                key={f.name}
                {...common}
                hint={hint}
                options={f.options ?? []}
                placeholder={f.required ? undefined : "Not set"}
                onChange={(e) => set(f.name)(e.target.value)}
              />
            );
          }
          return (
            <Field
              key={f.name}
              {...common}
              hint={hint}
              type={f.type === "url" ? "url" : "text"}
              inputMode={f.type === "date" ? "numeric" : undefined}
              pattern={f.type === "date" ? DATE_PATTERN : undefined}
              placeholder={f.type === "date" ? "2024-05" : undefined}
              onChange={(e) => set(f.name)(e.target.value)}
            />
          );
        })}
        {canHaveParent ? (
          <SelectField
            label={needsParent ? "Belongs to" : "Belongs to (optional)"}
            value={parent}
            required={needsParent}
            onChange={(e) => setParent(e.target.value)}
            placeholder={needsParent ? "Choose…" : "Nothing (stands alone)"}
            options={parentChoices.map((p) => ({
              value: p.id,
              label: `${KIND_LABEL[p.kind]}: ${entryTitle(p)}${entrySubtitle(p) ? ` — ${entrySubtitle(p)}` : ""}`,
            }))}
            hint={errs.parent_id}
          />
        ) : null}
        <div className="span-2">
          <TextAreaField
            label="Evidence (optional)"
            rows={2}
            value={evidence}
            maxLength={2000}
            onChange={(e) => setEvidence(e.target.value)}
            hint="Where this can be checked, such as an offer letter, certificate ID, or review."
          />
        </div>
      </div>
      <Checkbox
        checked={verified}
        onChange={(e) => setVerified(e.target.checked)}
        label="I confirm this is accurate"
        hint="Unticked entries stay drafts. Drafts are never used by the AI."
      />
      {error ? <Notice>{errorMessage(error)}</Notice> : null}
      <div className="row">
        <Button type="submit" busy={busy}>
          {submitLabel}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function StateBadge({ entry }: { entry: Pick<Entry, "verified" | "usable"> }) {
  const state = entryState(entry);
  if (state === "verified") return <span className="badge badge-ok">Verified</span>;
  if (state === "blocked")
    return (
      <span className="badge badge-warn" title="Verify the item this belongs to before the AI can use it">
        Parent is a draft
      </span>
    );
  return (
    <span className="badge badge-warn" title="Drafts are never used by the AI">
      Draft
    </span>
  );
}

interface EntryItemProps {
  entry: Entry;
  entries: Entry[];
  childEntries?: Entry[];
  childKinds?: Kind[];
  showKind?: boolean;
  kindChoices?: Kind[];
  select?: { checked: boolean; onChange: (checked: boolean) => void };
  /** Indent level, used by the import review's flat list. */
  depth?: number;
}

export function EntryItem({
  entry,
  entries,
  childEntries = [],
  childKinds = [],
  showKind,
  kindChoices,
  select,
  depth = 0,
}: EntryItemProps) {
  const m = useVaultMutations();
  const [mode, setMode] = useState<"view" | "edit" | "confirm-delete">("view");
  const [adding, setAdding] = useState<Kind | null>(null);
  const [error, setError] = useState<string | null>(null);

  const title = entryTitle(entry);
  const subtitle = entrySubtitle(entry);
  const dates = entryDates(entry);
  const body =
    (entry.kind === "role" && (entry.data.summary as string)) ||
    (entry.kind === "project" && (entry.data.description as string)) ||
    "";
  const metric = entry.kind === "achievement" ? (entry.data.metric as Record<string, string> | null) : null;
  const descendants = entries.filter((e) => e.parent_id === entry.id).length;

  async function toggleVerified() {
    setError(null);
    try {
      await m.verify.mutateAsync({ ids: [entry.id], verified: !entry.verified });
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function remove() {
    setError(null);
    try {
      await m.remove.mutateAsync(entry.id);
    } catch (err) {
      setError(errorMessage(err));
      setMode("view");
    }
  }

  if (mode === "edit") {
    return (
      <li className={`entry entry-editing${depth ? " indent" : ""}`}>
        <EntryForm
          kind={entry.kind}
          kindChoices={kindChoices && descendants === 0 ? kindChoices : undefined}
          initial={entry}
          entries={entries}
          submitLabel="Save"
          onCancel={() => setMode("view")}
          onSubmit={async (input) => {
            const body: Partial<EntryInput> = {
              data: input.data,
              parent_id: input.parent_id,
              evidence_note: input.evidence_note,
              verified: input.verified,
            };
            if (input.kind !== entry.kind) body.kind = input.kind;
            await m.update.mutateAsync({ id: entry.id, body });
            setMode("view");
          }}
        />
      </li>
    );
  }

  return (
    <li className={`entry${entry.verified ? "" : " entry-draft"}${depth ? " indent" : ""}`} data-kind={entry.kind}>
      <div className="entry-main">
        {select ? (
          <input
            type="checkbox"
            className="entry-check"
            aria-label={`Select ${title}`}
            checked={select.checked}
            onChange={(e) => select.onChange(e.target.checked)}
          />
        ) : null}
        <div className="entry-text">
          <div className="entry-title-row">
            {showKind ? <span className="kind-tag">{KIND_LABEL[entry.kind]}</span> : null}
            <span className="entry-title">{title}</span>
            <StateBadge entry={entry} />
            {entry.source === "api" ? (
              <span className="badge" title="Added by an AI agent with one of your API keys">
                From API
              </span>
            ) : null}
          </div>
          {subtitle || dates ? (
            <p className="muted small">{[subtitle, dates].filter(Boolean).join(" · ")}</p>
          ) : null}
          {metric?.value ? (
            <p className="small">
              Metric: {metric.value}
              {metric.unit ? ` ${metric.unit}` : ""}
              {metric.context ? ` (${metric.context})` : ""}
            </p>
          ) : null}
          {body ? <p className="small entry-body">{body}</p> : null}
          {entry.evidence_note ? <p className="muted small">Evidence: {entry.evidence_note}</p> : null}
        </div>
        <div className="entry-actions">
          <Button variant="ghost" onClick={() => void toggleVerified()} busy={m.verify.isPending}>
            {entry.verified ? "Mark as draft" : "Verify"}
          </Button>
          <Button variant="ghost" onClick={() => setMode("edit")}>
            Edit
          </Button>
          <Button variant="ghost" onClick={() => setMode("confirm-delete")}>
            Delete
          </Button>
        </div>
      </div>
      {mode === "confirm-delete" ? (
        <div className="confirm-row" role="group" aria-label="Confirm delete">
          <span className="small">
            Delete this {KIND_LABEL[entry.kind].toLowerCase()}
            {descendants ? ` and the ${descendants} item${descendants === 1 ? "" : "s"} under it` : ""}?
          </span>
          <Button variant="danger" onClick={() => void remove()} busy={m.remove.isPending}>
            Delete
          </Button>
          <Button variant="ghost" onClick={() => setMode("view")}>
            Keep
          </Button>
        </div>
      ) : null}
      {error ? <Notice>{error}</Notice> : null}

      {childEntries.length || childKinds.length ? (
        <div className="entry-children">
          {childEntries.length ? (
            <ul className="entry-list">
              {childEntries.map((c) => (
                <EntryItem key={c.id} entry={c} entries={entries} showKind />
              ))}
            </ul>
          ) : null}
          {adding ? (
            <div className="entry entry-editing">
              <EntryForm
                kind={adding}
                parentId={entry.id}
                entries={entries}
                submitLabel={`Add ${KIND_LABEL[adding].toLowerCase()}`}
                onCancel={() => setAdding(null)}
                onSubmit={async (input) => {
                  await m.create.mutateAsync(input);
                  setAdding(null);
                }}
              />
            </div>
          ) : (
            <div className="row add-row">
              {childKinds.map((k) => (
                <Button key={k} variant="secondary" onClick={() => setAdding(k)}>
                  Add {KIND_LABEL[k].toLowerCase()}
                </Button>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </li>
  );
}
