// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Checkbox, Field, FullPageLoader, Notice, SelectField } from "../../components/ui";
import { errorMessage, fieldErrors } from "../../lib/api";
import { useAdapters, useDiscoveryMutations, useSources, type Adapter, type FieldSpec, type Source } from "../../lib/discovery";

type Values = Record<string, string | boolean>;

/** Form values -> config/secret objects (numbers parsed, blanks left out). */
export function toPayload(fields: FieldSpec[], values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = values[f.name];
    if (f.type === "checkbox") {
      out[f.name] = Boolean(v);
    } else if (typeof v === "string" && v.trim() !== "") {
      out[f.name] = f.type === "number" ? Number(v) : v.trim();
    }
  }
  return out;
}

function fromConfig(fields: FieldSpec[], config: Record<string, unknown>): Values {
  const out: Values = {};
  for (const f of fields) {
    const v = config[f.name];
    out[f.name] = f.type === "checkbox" ? Boolean(v) : v === undefined || v === null ? "" : String(v);
  }
  return out;
}

function SpecFields({
  fields,
  values,
  onChange,
  errors,
  keepSecretHint,
}: {
  fields: FieldSpec[];
  values: Values;
  onChange: (name: string, value: string | boolean) => void;
  errors: Record<string, string>;
  keepSecretHint?: boolean;
}) {
  return (
    <>
      {fields.map((f) => {
        if (f.type === "checkbox") {
          return (
            <Checkbox
              key={f.name}
              label={f.label}
              checked={Boolean(values[f.name])}
              onChange={(e) => onChange(f.name, e.target.checked)}
            />
          );
        }
        if (f.type === "select") {
          return (
            <SelectField
              key={f.name}
              label={f.label}
              value={String(values[f.name] ?? "")}
              options={(f.options ?? []).map((o) => ({ value: o, label: o }))}
              onChange={(e) => onChange(f.name, e.target.value)}
            />
          );
        }
        return (
          <Field
            key={f.name}
            label={f.label}
            type={f.secret ? "password" : f.type === "number" ? "number" : "text"}
            autoComplete={f.secret ? "off" : undefined}
            required={f.required && !keepSecretHint}
            value={String(values[f.name] ?? "")}
            hint={errors[f.name] ?? (keepSecretHint && f.secret ? "Leave blank to keep the saved key" : f.hint)}
            onChange={(e) => onChange(f.name, e.target.value)}
          />
        );
      })}
    </>
  );
}

function SourceForm({ adapter, source, onDone }: { adapter: Adapter; source?: Source; onDone: () => void }) {
  const m = useDiscoveryMutations();
  const [name, setName] = useState(source?.name ?? adapter.label);
  const [interval, setInterval] = useState(String(source?.interval_hours ?? 12));
  const [config, setConfig] = useState<Values>(() => fromConfig(adapter.config_fields, source?.config ?? {}));
  const [secret, setSecret] = useState<Values>({});
  const [error, setError] = useState<unknown>(null);
  const errs = fieldErrors(error);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const secretPayload = toPayload(adapter.secret_fields, secret);
    const body: Record<string, unknown> = {
      name: name.trim(),
      interval_hours: Number(interval) || 12,
      config: toPayload(adapter.config_fields, config),
    };
    if (!source || Object.keys(secretPayload).length) body.secret = adapter.secret_fields.length ? secretPayload : null;
    try {
      if (source) await m.updateSource.mutateAsync({ id: source.id, body });
      else await m.createSource.mutateAsync({ kind: adapter.kind, ...body } as never);
      onDone();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form className="entry-form" onSubmit={submit}>
      <p className="muted small">
        {adapter.description}{" "}
        <a href={adapter.terms_url} target="_blank" rel="noopener noreferrer">
          Terms and docs
        </a>
      </p>
      <div className="form-grid">
        <Field label="Name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        <SelectField
          label="Check every"
          value={interval}
          options={[1, 3, 6, 12, 24, 48, 168].map((h) => ({ value: String(h), label: h < 24 ? `${h} hours` : h === 168 ? "week" : `${h / 24} days` }))}
          onChange={(e) => setInterval(e.target.value)}
        />
        <SpecFields
          fields={adapter.config_fields}
          values={config}
          errors={errs}
          onChange={(k, v) => setConfig((cur) => ({ ...cur, [k]: v }))}
        />
        <SpecFields
          fields={adapter.secret_fields}
          values={secret}
          errors={errs}
          keepSecretHint={Boolean(source?.has_secret)}
          onChange={(k, v) => setSecret((cur) => ({ ...cur, [k]: v }))}
        />
      </div>
      {adapter.secret_fields.length ? <p className="muted small">Keys are encrypted in the database and never shown again.</p> : null}
      {error ? <Notice>{errorMessage(error)}</Notice> : null}
      <div className="row">
        <Button type="submit" busy={m.createSource.isPending || m.updateSource.isPending}>
          {source ? "Save source" : "Add source"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function statusText(s: Source): string {
  if (s.pending) return "Fetching…";
  if (!s.enabled) return "Off";
  if (s.last_status === "never") return "Not fetched yet";
  const when = s.last_run_at ? new Date(s.last_run_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
  if (s.last_status === "error") return `Failed ${when}`;
  return `${s.last_found} found, ${s.last_new} new · ${when}`;
}

function SourceRow({ source, adapter }: { source: Source; adapter?: Adapter }) {
  const m = useDiscoveryMutations();
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  if (mode === "edit" && adapter) {
    return (
      <li className="entry entry-editing">
        <SourceForm adapter={adapter} source={source} onDone={() => setMode("view")} />
      </li>
    );
  }
  return (
    <li className="entry">
      <div className="entry-main">
        <div className="entry-text">
          <div className="entry-title-row">
            <span className="kind-tag">{source.label}</span>
            <span className="entry-title">{source.name}</span>
            {source.last_status === "error" ? <span className="badge badge-warn">Error</span> : null}
          </div>
          <p className="muted small" aria-live="polite">
            {statusText(source)}
          </p>
          {source.last_status === "error" && source.last_error ? <p className="small text-danger">{source.last_error}</p> : null}
        </div>
        <div className="entry-actions">
          <Button
            variant="ghost"
            disabled={!source.enabled || source.pending}
            busy={m.runSource.isPending && m.runSource.variables === source.id}
            onClick={() => m.runSource.mutate(source.id)}
          >
            Fetch now
          </Button>
          <Button variant="ghost" onClick={() => m.updateSource.mutate({ id: source.id, body: { enabled: !source.enabled } })}>
            {source.enabled ? "Turn off" : "Turn on"}
          </Button>
          <Button variant="ghost" onClick={() => setMode("edit")}>
            Edit
          </Button>
          <Button variant="ghost" onClick={() => setMode("delete")}>
            Delete
          </Button>
        </div>
      </div>
      {mode === "delete" ? (
        <div className="confirm-row">
          <span className="small">Delete this source? Jobs it already found stay.</span>
          <Button variant="danger" onClick={() => m.deleteSource.mutate(source.id)}>
            Delete
          </Button>
          <Button variant="ghost" onClick={() => setMode("view")}>
            Keep
          </Button>
        </div>
      ) : null}
    </li>
  );
}

export function SourcesTab() {
  const sources = useSources();
  const adapters = useAdapters();
  const [adding, setAdding] = useState<string>("");
  if (sources.isPending || adapters.isPending) return <FullPageLoader />;
  if (sources.isError) return <Notice>{errorMessage(sources.error)}</Notice>;
  if (adapters.isError) return <Notice>{errorMessage(adapters.error)}</Notice>;
  const byKind = new Map(adapters.data.map((a) => [a.kind, a]));
  const chosen = byKind.get(adding);

  return (
    <section className="stack" aria-label="Sources">
      <p className="muted small">
        Each source uses an official API or a public feed and runs in the background on its schedule. Waypoint never scrapes sites
        that don&apos;t allow it.
      </p>
      {chosen ? (
        <div className="card">
          <h2>New source: {chosen.label}</h2>
          <SourceForm adapter={chosen} onDone={() => setAdding("")} />
        </div>
      ) : (
        <div className="filters">
          <SelectField
            label="Add a source"
            value=""
            placeholder="Choose a source type…"
            options={adapters.data.map((a) => ({ value: a.kind, label: a.label }))}
            onChange={(e) => setAdding(e.target.value)}
          />
        </div>
      )}
      {sources.data.length === 0 ? (
        <div className="empty">
          <p>No sources yet. Company boards (Greenhouse, Lever, Ashby) need no key; search APIs need a free key.</p>
        </div>
      ) : (
        <ul className="entry-list">
          {sources.data.map((s) => (
            <SourceRow key={s.id} source={s} adapter={byKind.get(s.kind)} />
          ))}
        </ul>
      )}
    </section>
  );
}
