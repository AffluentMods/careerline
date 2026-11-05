// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { FullPageLoader, Notice } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate, formatMoney, STAGE_LABEL, useOffers, type Offer } from "../../lib/tracker";

interface Row {
  label: string;
  value: (o: Offer) => ReactNode;
  /** Numeric score used to highlight the best offer in this row. */
  score?: (o: Offer) => number | null;
  better?: "high" | "low";
}

const ROWS: Row[] = [
  {
    label: "Base",
    value: (o) => `${formatMoney(o.base_cents, o.currency)}/${o.base_period === "hour" ? "hr" : "yr"}`,
  },
  { label: "Yearly equivalent", value: (o) => formatMoney(o.annual_cents, o.currency), score: (o) => o.annual_cents, better: "high" },
  { label: "Hourly equivalent", value: (o) => formatMoney(o.hourly_cents, o.currency), score: (o) => o.hourly_cents, better: "high" },
  { label: "Hours per week", value: (o) => o.hours_per_week },
  {
    label: "Bonus",
    value: (o) => [o.bonus_cents ? formatMoney(o.bonus_cents, o.currency) : "", o.bonus_notes].filter(Boolean).join(" · ") || "—",
  },
  {
    label: "Total per year",
    value: (o) => formatMoney(o.total_annual_cents, o.currency),
    score: (o) => o.total_annual_cents,
    better: "high",
  },
  { label: "Stock or equity", value: (o) => o.equity_notes || "—" },
  { label: "PTO", value: (o) => (o.pto_days === null ? "—" : `${o.pto_days} days`), score: (o) => o.pto_days, better: "high" },
  { label: "Schedule", value: (o) => o.schedule || "—" },
  {
    label: "Commute (one way)",
    value: (o) => (o.commute_minutes === null ? "—" : `${o.commute_minutes} min`),
    score: (o) => o.commute_minutes,
    better: "low",
  },
  { label: "Growth path", value: (o) => o.growth_notes || "—" },
  { label: "Benefits", value: (o) => o.benefits_notes || "—" },
  { label: "Decide by", value: (o) => (o.deadline ? formatDate(o.deadline) : "—") },
];

/** Ids of the offers that are best for a row (ties all win). Empty if fewer than two scored. */
export function bestIds(offers: Offer[], row: Pick<Row, "score" | "better">): Set<string> {
  if (!row.score) return new Set();
  const scored = offers
    .map((o) => ({ id: o.id, s: row.score!(o) }))
    .filter((x): x is { id: string; s: number } => x.s !== null);
  if (scored.length < 2) return new Set();
  const target = row.better === "low" ? Math.min(...scored.map((x) => x.s)) : Math.max(...scored.map((x) => x.s));
  if (scored.every((x) => x.s === target)) return new Set();
  return new Set(scored.filter((x) => x.s === target).map((x) => x.id));
}

export function groupByCurrency(offers: Offer[]): [string, Offer[]][] {
  const map = new Map<string, Offer[]>();
  for (const o of offers) map.set(o.currency, [...(map.get(o.currency) ?? []), o]);
  return [...map.entries()];
}

export function OffersTab() {
  const offers = useOffers();
  if (offers.isPending) return <FullPageLoader />;
  if (offers.isError) return <Notice>{errorMessage(offers.error)}</Notice>;
  if (offers.data.length === 0) {
    return (
      <div className="empty">
        <p>No offers yet. When you get one, add it on the application&apos;s page and it shows up here.</p>
      </div>
    );
  }
  const groups = groupByCurrency(offers.data);
  return (
    <section className="stack" aria-label="Offers">
      <p className="muted small">
        Yearly and hourly figures use each offer&apos;s hours per week over 52 weeks. The best value in a row is highlighted.
        {groups.length > 1 ? " Offers in different currencies are compared separately." : ""}
      </p>
      {groups.map(([currency, list]) => (
        <div key={currency} className="table-wrap">
          <table className="data-table compare-table">
            {groups.length > 1 ? <caption>{currency}</caption> : null}
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">Detail</span>
                </th>
                {list.map((o) => (
                  <th key={o.id} scope="col">
                    <Link to={`/tracker/applications/${o.application_id}`}>{o.application_title}</Link>
                    <span className="muted small">
                      {o.company ? ` · ${o.company}` : ""} · {STAGE_LABEL[o.stage]}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => {
                const best = bestIds(list, row);
                return (
                  <tr key={row.label}>
                    <th scope="row">{row.label}</th>
                    {list.map((o) => (
                      <td key={o.id} className={best.has(o.id) ? "best" : undefined}>
                        {row.value(o)}
                        {best.has(o.id) ? <span className="sr-only"> (best)</span> : null}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}
