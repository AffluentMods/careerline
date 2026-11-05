// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Field, Notice, SelectField, TextAreaField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  annualCents,
  centsToInput,
  formatMoney,
  hourlyCents,
  parseMoney,
  type Offer,
  type OfferInput,
  type PayPeriod,
} from "../../lib/tracker";

function intOrNull(value: string): number | null {
  const t = value.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) ? n : Number.NaN;
}

export function OfferForm({
  initial,
  defaultCurrency,
  onSubmit,
  onCancel,
}: {
  initial: Offer | null;
  defaultCurrency: string;
  onSubmit: (body: OfferInput) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const [v, setV] = useState({
    base: centsToInput(initial?.base_cents),
    base_period: initial?.base_period ?? "year",
    currency: initial?.currency ?? defaultCurrency,
    hours_per_week: String(initial?.hours_per_week ?? 40),
    bonus: centsToInput(initial?.bonus_cents),
    bonus_notes: initial?.bonus_notes ?? "",
    equity_notes: initial?.equity_notes ?? "",
    pto_days: initial?.pto_days?.toString() ?? "",
    schedule: initial?.schedule ?? "",
    commute_minutes: initial?.commute_minutes?.toString() ?? "",
    growth_notes: initial?.growth_notes ?? "",
    benefits_notes: initial?.benefits_notes ?? "",
    deadline: initial?.deadline ?? "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof v) => (value: string) => setV((cur) => ({ ...cur, [k]: value }));

  const base = parseMoney(v.base);
  const hours = Number(v.hours_per_week) || 40;
  const preview =
    base && !Number.isNaN(base)
      ? `${formatMoney(annualCents(base, v.base_period as PayPeriod, hours), v.currency)}/yr · ${formatMoney(
          hourlyCents(base, v.base_period as PayPeriod, hours),
          v.currency,
        )}/hr`
      : "";

  async function submit(e: FormEvent) {
    e.preventDefault();
    const bonus = parseMoney(v.bonus);
    const pto = intOrNull(v.pto_days);
    const commute = intOrNull(v.commute_minutes);
    const hpw = intOrNull(v.hours_per_week);
    if (!base || Number.isNaN(base)) return setError("Enter the base pay, like 110000 or 52.50.");
    if ([bonus, pto, commute, hpw].some((n) => Number.isNaN(n))) return setError("Use whole numbers for days, minutes, and hours.");
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        currency: v.currency.trim().toUpperCase() || "USD",
        base_cents: base,
        base_period: v.base_period as PayPeriod,
        hours_per_week: hpw ?? 40,
        bonus_cents: bonus,
        bonus_notes: v.bonus_notes,
        equity_notes: v.equity_notes,
        pto_days: pto,
        schedule: v.schedule,
        commute_minutes: commute,
        growth_notes: v.growth_notes,
        benefits_notes: v.benefits_notes,
        deadline: v.deadline || null,
      });
      setBusy(false);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="entry-form" onSubmit={submit}>
      <div className="form-grid">
        <Field label="Base pay" required inputMode="decimal" value={v.base} hint={preview} onChange={(e) => set("base")(e.target.value)} />
        <SelectField
          label="Per"
          value={v.base_period}
          options={[
            { value: "year", label: "Year" },
            { value: "hour", label: "Hour" },
          ]}
          onChange={(e) => set("base_period")(e.target.value)}
        />
        <Field label="Hours per week" inputMode="numeric" value={v.hours_per_week} onChange={(e) => set("hours_per_week")(e.target.value)} />
        <Field label="Currency" maxLength={3} value={v.currency} onChange={(e) => set("currency")(e.target.value)} />
        <Field label="Yearly bonus (optional)" inputMode="decimal" value={v.bonus} onChange={(e) => set("bonus")(e.target.value)} />
        <Field label="PTO days (optional)" inputMode="numeric" value={v.pto_days} onChange={(e) => set("pto_days")(e.target.value)} />
        <Field
          label="Schedule (optional)"
          maxLength={200}
          value={v.schedule}
          hint="For example: Mon–Fri 7–3:30"
          onChange={(e) => set("schedule")(e.target.value)}
        />
        <Field
          label="Commute, minutes one way (optional)"
          inputMode="numeric"
          value={v.commute_minutes}
          onChange={(e) => set("commute_minutes")(e.target.value)}
        />
        <Field label="Decide by (optional)" type="date" value={v.deadline} onChange={(e) => set("deadline")(e.target.value)} />
        <Field label="Bonus details (optional)" value={v.bonus_notes} onChange={(e) => set("bonus_notes")(e.target.value)} />
        <div className="span-2">
          <TextAreaField label="Stock or equity (optional)" rows={2} value={v.equity_notes} onChange={(e) => set("equity_notes")(e.target.value)} />
        </div>
        <div className="span-2">
          <TextAreaField label="Growth path (optional)" rows={2} value={v.growth_notes} onChange={(e) => set("growth_notes")(e.target.value)} />
        </div>
        <div className="span-2">
          <TextAreaField label="Benefits (optional)" rows={2} value={v.benefits_notes} onChange={(e) => set("benefits_notes")(e.target.value)} />
        </div>
      </div>
      {error ? <Notice>{error}</Notice> : null}
      <div className="row">
        <Button type="submit" busy={busy}>
          Save offer
        </Button>
        {onCancel ? (
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}
