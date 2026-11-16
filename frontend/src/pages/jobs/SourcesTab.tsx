// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Checkbox, Field, FullPageLoader, Notice, SelectField } from "../../components/ui";
import { errorMessage, fieldErrors } from "../../lib/api";
import {
  ADAPTER_GROUPS,
  useAdapters,
  useDiscoveryMutations,
  useSources,
  type Adapter,
  type FieldSpec,
  type Source,
} from "../../lib/discovery";

type Values = Record<string, string | boolean | string[]>;

/** Form values -> config/secret objects (numbers parsed, blanks left out). */
export function toPayload(fields: FieldSpec[], values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = values[f.name];
    if (f.type === "checkbox") {
      out[f.name] = Boolean(v);
    } else if (f.type === "multiselect") {
      if (Array.isArray(v) && v.length) out[f.name] = v;
    } else if (typeof v === "string" && v.trim() !== "") {
      out[f.name] = f.type === "number" ? Number(v) : v.trim();
    }
  }
  return out;
}

function fromConfig(fields: FieldSpec[], config: Record<string, unknown>): Values {
  const out: Values = {};
  for (const f of fields) {
    const v = config[f.name] ?? f.default;
    if (f.type === "multiselect") out[f.name] = Array.isArray(v) ? v.map(String) : [...(f.options ?? [])];
    else out[f.name] = f.type === "checkbox" ? Boolean(v) : v === undefined || v === null ? "" : String(v);
  }
  return out;
}

const SITE_LABEL: Record<string, string> = {
  linkedin: "LinkedIn",
  indeed: "Indeed",
  glassdoor: "Glassdoor",
  ziprecruiter: "ZipRecruiter",
  monster: "Monster",
};

function SpecFields({
  fields,
  values,
  onChange,
  errors,
  keepSecretHint,
}: {
  fields: FieldSpec[];
  values: Values;
  onChange: (name: string, value: string | boolean | string[]) => void;
  errors: Record<string, string>;
  keepSecretHint?: boolean;
}) {
  return (
    <>
      {fields.map((f) => {
        if (f.type === "multiselect") {
          const chosen = Array.isArray(values[f.name]) ? (values[f.name] as string[]) : [];
          return (
            <fieldset key={f.name} className="check-group">
              <legend>{f.label}</legend>
              {(f.options ?? []).map((o) => (
                <Checkbox
                  key={o}
                  label={SITE_LABEL[o] ?? o}
                  checked={chosen.includes(o)}
                  onChange={(e) => onChange(f.name, e.target.checked ? [...chosen, o] : chosen.filter((x) => x !== o))}
                />
              ))}
            </fieldset>
          );
        }
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
  const [interval, setInterval] = useState(String(source?.interval_hours ?? Math.max(12, adapter.min_interval_hours)));
  const intervals = [1, 3, 6, 12, 24, 48, 168].filter((h) => h >= adapter.min_interval_hours);
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
      {adapter.how_to ? (
        <div className="how-to">
          <strong className="small">How to set it up</strong>
          <ol className="small">
            {adapter.how_to
              .split(/\s*\d\.\s+/)
              .filter(Boolean)
              .map((step) => (
                <li key={step}>{step}</li>
              ))}
          </ol>
        </div>
      ) : null}
      <div className="form-grid">
        <Field label="Name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        <SelectField
          label="Check every"
          value={interval}
          options={intervals.map((h) => ({ value: String(h), label: h < 24 ? `${h} hours` : h === 168 ? "week" : `${h / 24} days` }))}
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
  // null until the user chooses: the picker opens by itself while there are no sources.
  const [picking, setPicking] = useState<boolean | null>(null);
  if (sources.isPending || adapters.isPending) return <FullPageLoader />;
  if (sources.isError) return <Notice>{errorMessage(sources.error)}</Notice>;
  if (adapters.isError) return <Notice>{errorMessage(adapters.error)}</Notice>;
  const byKind = new Map(adapters.data.map((a) => [a.kind, a]));
  const chosen = byKind.get(adding);

  return (
    <section className="stack" aria-label="Sources">
      {sources.data.length ? (
        <ul className="entry-list" aria-label="Your sources">
          {sources.data.map((s) => (
            <SourceRow key={s.id} source={s} adapter={byKind.get(s.kind)} />
          ))}
        </ul>
      ) : null}
      {chosen ? (
        <div className="card">
          <h2>New source: {chosen.label}</h2>
          <SourceForm
            adapter={chosen}
            onDone={() => {
              setAdding("");
              setPicking(false);
            }}
          />
        </div>
      ) : (picking ?? sources.data.length === 0) ? (
        <SourcePicker adapters={adapters.data} onPick={setAdding} onCancel={sources.data.length ? () => setPicking(false) : undefined} />
      ) : (
        <div>
          <Button onClick={() => setPicking(true)}>Add a source</Button>
        </div>
      )}
      <p className="muted small">
        Sources run in the background on their schedule, using each site&apos;s official API, public feed, or the alert emails it
        sends you. CareerLine never scrapes sites that forbid it.
      </p>
    </section>
  );
}

function SourcePicker({
  adapters,
  onPick,
  onCancel,
}: {
  adapters: Adapter[];
  onPick: (kind: string) => void;
  onCancel?: () => void;
}) {
  return (
    <div className="stack">
      <div className="row-between">
        <h2>Where should CareerLine look for jobs?</h2>
        {onCancel ? (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
      {ADAPTER_GROUPS.map((g) => {
        const items = adapters.filter((a) => a.group === g.group);
        if (!items.length) return null;
        return (
          <section key={g.group} aria-label={g.title} className="stack-sm">
            <div>
              <h3>{g.title}</h3>
              <p className="muted small">{g.blurb}</p>
            </div>
            <ul className="source-grid">
              {items.map((a) => (
                <li key={a.kind}>
                  <button type="button" className="source-card" onClick={() => onPick(a.kind)}>
                    <span className="source-card-title">{a.label}</span>
                    <span className="muted small">{a.description}</span>
                    <span className="badge">{a.secret_fields.length ? (a.group === "inbox" ? "Needs an app password" : "Needs a free key") : "No key needed"}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
