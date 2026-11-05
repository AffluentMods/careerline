// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Field, Notice, SelectField, TextAreaField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  centsToInput,
  parseMoney,
  STAGE_LABEL,
  STAGES,
  type Application,
  type ApplicationFields,
  type PayPeriod,
  type Priority,
  type Stage,
  type WorkMode,
  localToday,
} from "../../lib/tracker";

export type ApplicationSubmit = ApplicationFields & { stage?: Stage };

interface Props {
  initial?: Application;
  withStage?: boolean;
  submitLabel: string;
  onSubmit: (body: ApplicationSubmit) => Promise<unknown>;
  onCancel: () => void;
}

export function ApplicationForm({ initial, withStage, submitLabel, onSubmit, onCancel }: Props) {
  const [v, setV] = useState({
    title: initial?.title ?? "",
    company: initial?.company ?? "",
    location: initial?.location ?? "",
    work_mode: initial?.work_mode ?? "",
    job_url: initial?.job_url ?? "",
    source: initial?.source ?? "",
    pay_min: centsToInput(initial?.pay_min_cents),
    pay_max: centsToInput(initial?.pay_max_cents),
    pay_period: initial?.pay_period ?? "year",
    currency: initial?.currency ?? "USD",
    description: initial?.description ?? "",
    applied_on: initial?.applied_on ?? "",
    deadline: initial?.deadline ?? "",
    priority: initial?.priority ?? "normal",
    stage: "saved" as Stage,
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof v) => (value: string) => setV((cur) => ({ ...cur, [k]: value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const lo = parseMoney(v.pay_min);
    const hi = parseMoney(v.pay_max);
    if (Number.isNaN(lo) || Number.isNaN(hi)) {
      setError("Pay amounts must be numbers, like 52.50 or 110000.");
      return;
    }
    if (lo !== null && hi !== null && lo > hi) {
      setError("The lowest pay can't be above the highest.");
      return;
    }
    const hasPay = lo !== null || hi !== null;
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        title: v.title.trim(),
        company: v.company.trim(),
        location: v.location.trim(),
        work_mode: (v.work_mode || null) as WorkMode | null,
        job_url: v.job_url.trim(),
        source: v.source.trim(),
        pay_min_cents: lo,
        pay_max_cents: hi,
        pay_period: hasPay ? (v.pay_period as PayPeriod) : null,
        currency: v.currency.trim().toUpperCase() || "USD",
        description: v.description,
        // Adding something you've already applied to: record today as the applied date.
        applied_on: v.applied_on || (withStage && v.stage !== "saved" ? localToday() : null),
        deadline: v.deadline || null,
        priority: v.priority as Priority,
        ...(withStage ? { stage: v.stage } : {}),
      });
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="entry-form" onSubmit={submit}>
      <div className="form-grid">
        <Field label="Job title" required maxLength={200} value={v.title} onChange={(e) => set("title")(e.target.value)} />
        <Field label="Company (optional)" maxLength={200} value={v.company} onChange={(e) => set("company")(e.target.value)} />
        <Field label="Location (optional)" maxLength={200} value={v.location} onChange={(e) => set("location")(e.target.value)} />
        <SelectField
          label="Work mode (optional)"
          value={v.work_mode}
          placeholder="Not set"
          options={[
            { value: "onsite", label: "On-site" },
            { value: "hybrid", label: "Hybrid" },
            { value: "remote", label: "Remote" },
          ]}
          onChange={(e) => set("work_mode")(e.target.value)}
        />
        <Field
          label="Pay from (optional)"
          inputMode="decimal"
          value={v.pay_min}
          onChange={(e) => set("pay_min")(e.target.value)}
        />
        <Field label="Pay to (optional)" inputMode="decimal" value={v.pay_max} onChange={(e) => set("pay_max")(e.target.value)} />
        <SelectField
          label="Pay is per"
          value={v.pay_period}
          options={[
            { value: "year", label: "Year" },
            { value: "hour", label: "Hour" },
          ]}
          onChange={(e) => set("pay_period")(e.target.value)}
        />
        <Field
          label="Currency"
          maxLength={3}
          pattern="[A-Za-z]{3}"
          value={v.currency}
          hint="Three letters, like USD"
          onChange={(e) => set("currency")(e.target.value)}
        />
        <Field
          label="Job link (optional)"
          type="url"
          maxLength={500}
          value={v.job_url}
          hint="https:// only"
          onChange={(e) => set("job_url")(e.target.value)}
        />
        <Field
          label="Found via (optional)"
          maxLength={100}
          value={v.source}
          hint="For example: company site, referral, job board"
          onChange={(e) => set("source")(e.target.value)}
        />
        <Field label="Apply by (optional)" type="date" value={v.deadline} onChange={(e) => set("deadline")(e.target.value)} />
        {initial ? (
          <Field label="Applied on (optional)" type="date" value={v.applied_on} onChange={(e) => set("applied_on")(e.target.value)} />
        ) : null}
        <SelectField
          label="Priority"
          value={v.priority}
          options={[
            { value: "low", label: "Low" },
            { value: "normal", label: "Normal" },
            { value: "high", label: "High" },
          ]}
          onChange={(e) => set("priority")(e.target.value)}
        />
        {withStage ? (
          <SelectField
            label="Stage"
            value={v.stage}
            options={STAGES.map((s) => ({ value: s, label: STAGE_LABEL[s] }))}
            onChange={(e) => set("stage")(e.target.value)}
          />
        ) : null}
        <div className="span-2">
          <TextAreaField
            label="Job description (optional)"
            rows={5}
            maxLength={50_000}
            value={v.description}
            hint="Paste it here so you still have it if the posting disappears."
            onChange={(e) => set("description")(e.target.value)}
          />
        </div>
      </div>
      {error ? <Notice>{error}</Notice> : null}
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
