// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Card, Checkbox, Field, FullPageLoader, Notice, SelectField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { CLEARANCE_LABEL, SHIFT_OPTIONS, splitList, useDiscoveryMutations, useFilters, type Filters } from "../../lib/discovery";
import { centsToInput, parseMoney } from "../../lib/tracker";

export function FiltersTab() {
  const filters = useFilters();
  if (filters.isPending) return <FullPageLoader />;
  if (filters.isError) return <Notice>{errorMessage(filters.error)}</Notice>;
  return <FiltersForm initial={filters.data} />;
}

function ShiftPicker({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset className="check-group">
      <legend>{label}</legend>
      {SHIFT_OPTIONS.map((s) => (
        <Checkbox
          key={s.value}
          label={s.label}
          checked={value.includes(s.value)}
          onChange={(e) => onChange(e.target.checked ? [...value, s.value] : value.filter((x) => x !== s.value))}
        />
      ))}
    </fieldset>
  );
}

function FiltersForm({ initial }: { initial: Filters }) {
  const m = useDiscoveryMutations();
  const [f, setF] = useState<Filters>(initial);
  const [text, setText] = useState({
    locations: initial.locations.join(", "),
    exclude: initial.exclude_keywords.join(", "),
    require: initial.require_keywords.join(", "),
    preferred: initial.preferred_companies.join(", "),
    blocked: initial.blocked_companies.join(", "),
    minPay: centsToInput(initial.min_hourly_cents),
    target: centsToInput(initial.target_hourly_cents),
    lat: initial.home_lat?.toString() ?? "",
    lon: initial.home_lon?.toString() ?? "",
    radius: initial.radius_miles?.toString() ?? "",
  });
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => {
    setSaved(false);
    setF((cur) => ({ ...cur, [k]: v }));
  };
  const setT = (k: keyof typeof text) => (v: string) => {
    setSaved(false);
    setText((cur) => ({ ...cur, [k]: v }));
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    const minPay = parseMoney(text.minPay);
    const target = parseMoney(text.target);
    if (Number.isNaN(minPay) || Number.isNaN(target)) return setError("Pay must be a number, like 50 or 52.50.");
    const num = (v: string) => (v.trim() === "" ? null : Number(v));
    const lat = num(text.lat);
    const lon = num(text.lon);
    const radius = num(text.radius);
    if ([lat, lon, radius].some((n) => n !== null && Number.isNaN(n))) return setError("Coordinates and radius must be numbers.");
    setError(null);
    try {
      await m.saveFilters.mutateAsync({
        ...f,
        locations: splitList(text.locations),
        exclude_keywords: splitList(text.exclude),
        require_keywords: splitList(text.require),
        preferred_companies: splitList(text.preferred),
        blocked_companies: splitList(text.blocked),
        min_hourly_cents: minPay,
        target_hourly_cents: target,
        home_lat: lat,
        home_lon: lon,
        radius_miles: radius === null ? null : Math.round(radius),
      });
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <p className="muted small">
        Hard filters move jobs to <strong>Filtered out</strong> with the reason. Nothing is deleted, and jobs with unknown values
        (no pay posted, no location) still show unless you say otherwise.
      </p>
      <Card title="Where">
        <div className="check-row">
          <Checkbox label="Remote" checked={f.remote_ok} onChange={(e) => set("remote_ok", e.target.checked)} />
          <Checkbox label="Hybrid" checked={f.hybrid_ok} onChange={(e) => set("hybrid_ok", e.target.checked)} />
          <Checkbox label="On-site" checked={f.onsite_ok} onChange={(e) => set("onsite_ok", e.target.checked)} />
        </div>
        <Field
          label="Locations (optional)"
          value={text.locations}
          hint="Comma separated city or region names, like Tukwila, Seattle, Renton"
          onChange={(e) => setT("locations")(e.target.value)}
        />
        <div className="form-grid">
          <Field label="Home latitude (optional)" inputMode="decimal" value={text.lat} onChange={(e) => setT("lat")(e.target.value)} />
          <Field label="Home longitude (optional)" inputMode="decimal" value={text.lon} onChange={(e) => setT("lon")(e.target.value)} />
          <Field
            label="Radius in miles (optional)"
            inputMode="numeric"
            value={text.radius}
            hint="Used when a posting includes coordinates"
            onChange={(e) => setT("radius")(e.target.value)}
          />
        </div>
      </Card>
      <Card title="Pay">
        <div className="form-grid">
          <Field
            label="Minimum, per hour (optional)"
            inputMode="decimal"
            value={text.minPay}
            hint="Yearly pay is converted at 2,080 hours"
            onChange={(e) => setT("minPay")(e.target.value)}
          />
          <Field
            label="Target, per hour (optional)"
            inputMode="decimal"
            value={text.target}
            hint="Shown next to each posting's pay"
            onChange={(e) => setT("target")(e.target.value)}
          />
        </div>
        <Checkbox
          label="Hide jobs that don't post pay"
          checked={f.hide_unpaid_postings}
          onChange={(e) => set("hide_unpaid_postings", e.target.checked)}
        />
      </Card>
      <Card title="Schedule">
        <ShiftPicker label="Shifts I want (optional)" value={f.shifts_wanted} onChange={(v) => set("shifts_wanted", v)} />
        <ShiftPicker label="Shifts to exclude" value={f.shifts_excluded} onChange={(v) => set("shifts_excluded", v)} />
      </Card>
      <Card title="Keywords and companies">
        <Field
          label="Exclude jobs mentioning"
          value={text.exclude}
          hint='Comma separated, like "Tier 1 SOC", "commission only"'
          onChange={(e) => setT("exclude")(e.target.value)}
        />
        <Field label="Only jobs mentioning one of (optional)" value={text.require} onChange={(e) => setT("require")(e.target.value)} />
        <Field label="Preferred companies" value={text.preferred} hint="Shown first" onChange={(e) => setT("preferred")(e.target.value)} />
        <Field label="Blocked companies" value={text.blocked} onChange={(e) => setT("blocked")(e.target.value)} />
        <SelectField
          label="Security clearance I hold"
          value={f.clearance_held}
          options={Object.entries(CLEARANCE_LABEL).map(([value, label]) => ({ value, label }))}
          hint="Jobs requiring a higher clearance are filtered out"
          onChange={(e) => set("clearance_held", e.target.value as Filters["clearance_held"])}
        />
      </Card>
      {error ? <Notice>{error}</Notice> : null}
      {saved ? <Notice tone="success">Filters saved.</Notice> : null}
      <div className="row">
        <Button type="submit" busy={m.saveFilters.isPending}>
          Save filters
        </Button>
      </div>
    </form>
  );
}
