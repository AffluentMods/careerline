// SPDX-License-Identifier: Apache-2.0
import { useId, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { FullPageLoader, Notice } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  days as fmtDays,
  niceTicks,
  percent,
  RANGES,
  shortDate,
  useInsights,
  type Insights,
} from "../../lib/insights";
import { STAGE_LABEL } from "../../lib/tracker";

export function InsightsPage() {
  const [range, setRange] = useState("");
  const q = useInsights(range);
  const data = q.data;

  return (
    <div className="page page-wide">
      <header className="page-head">
        <h1>Insights</h1>
        <p className="muted">How your applications are doing, from what you&apos;ve tracked.</p>
      </header>
      <div className="filter-row">
        <label htmlFor="range" className="sr-only">
          Time range
        </label>
        <select id="range" value={range} onChange={(e) => setRange(e.target.value)}>
          {RANGES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </div>
      {q.isPending ? <FullPageLoader /> : null}
      {q.isError ? <Notice>{errorMessage(q.error)}</Notice> : null}
      {data ? (
        <div className={`stack${q.isFetching && q.isPlaceholderData ? " refetching" : ""}`}>
          {data.totals.applied === 0 ? (
            <div className="empty">
              <p>
                No applications {range ? "in this time range" : "yet"}. Insights fill in as you move applications
                from Saved to Applied and beyond.
              </p>
              <Link className="btn btn-secondary" to="/tracker">
                Open the tracker
              </Link>
            </div>
          ) : (
            <Dashboard data={data} />
          )}
        </div>
      ) : null}
    </div>
  );
}

function Dashboard({ data }: { data: Insights }) {
  const t = data.totals;
  return (
    <>
      <section className="kpi-row" aria-label="Summary">
        <Stat label="Applied" value={String(t.applied)} note={`${t.tracked} tracked in all`} />
        <Stat label="Response rate" value={percent(data.response_rate)} note={`${t.responded} heard back`} />
        <Stat label="Interviews" value={String(t.interviews)} note={`${percent(data.positive_rate)} got a screen or better`} />
        <Stat label="Offers" value={String(t.offers)} />
        <Stat
          label="Median time to response"
          value={fmtDays(data.median_days_to_response)}
          note={data.measured_responses ? `from ${data.measured_responses} replies` : "no replies with dates yet"}
        />
      </section>
      {t.quiet ? (
        <Notice tone="info">
          {t.quiet} application{t.quiet === 1 ? " has" : "s have"} had no answer for {data.quiet_days}+ days.{" "}
          <Link to="/tracker/board">Follow up or close {t.quiet === 1 ? "it" : "them"}</Link>.
        </Notice>
      ) : null}
      <div className="insights-grid">
        <ChartCard
          title="Stage funnel"
          description="How many applications reached each stage, and the share that moved on from the stage before."
          table={
            <DataTable
              head={["Stage", "Applications", "From previous stage", "From applied"]}
              rows={data.funnel.map((s) => [
                STAGE_LABEL[s.stage],
                String(s.count),
                percent(s.rate_from_previous),
                percent(s.rate_from_applied),
              ])}
            />
          }
        >
          <HBars
            max={data.funnel[0]?.count ?? 0}
            items={data.funnel.map((s) => ({
              key: s.stage,
              label: STAGE_LABEL[s.stage],
              value: s.count,
              valueText: String(s.count),
              note: s.rate_from_previous === null ? undefined : `${percent(s.rate_from_previous)} of previous`,
            }))}
          />
        </ChartCard>

        <ChartCard
          title="Applications per week"
          description="Last 12 weeks, by the date you applied."
          table={
            <DataTable
              head={["Week of", "Applied"]}
              rows={data.weekly.map((w) => [shortDate(w.week_start), String(w.applied)])}
            />
          }
        >
          <Columns weeks={data.weekly} />
        </ChartCard>

        <ChartCard
          title="Response rate by source"
          description="Where you found the job, and how often the employer answered (including rejections)."
          wide
        >
          <SourceTable data={data} />
        </ChartCard>

        <ChartCard
          title="Time to first response"
          description="Days from applying to the first answer."
          table={
            <DataTable
              head={["Time", "Replies"]}
              rows={data.response_times.map((b) => [b.bucket, String(b.count)])}
            />
          }
        >
          {data.measured_responses ? (
            <HBars
              max={Math.max(...data.response_times.map((b) => b.count))}
              items={data.response_times.map((b) => ({
                key: b.bucket,
                label: b.bucket,
                value: b.count,
                valueText: String(b.count),
              }))}
            />
          ) : (
            <p className="muted small">No replies with dates yet.</p>
          )}
        </ChartCard>
      </div>
      <p className="muted small">
        A response is any answer from the employer: a screen, an interview, an offer, or a rejection. Withdrawing
        yourself doesn&apos;t count. Applications added straight into a later stage have no measurable response time.
      </p>
    </>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="stat">
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {note ? <p className="stat-note muted small">{note}</p> : null}
    </div>
  );
}

function ChartCard({
  title,
  description,
  children,
  table,
  wide,
}: {
  title: string;
  description: string;
  children: ReactNode;
  table?: ReactNode;
  wide?: boolean;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className={`card chart-card${wide ? " chart-wide" : ""}`} aria-label={title}>
      <header className="card-head">
        <div>
          <h2>{title}</h2>
          <p className="muted">{description}</p>
        </div>
        {table ? (
          <button className="link-btn small" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
            {asTable ? "Show chart" : "Show table"}
          </button>
        ) : null}
      </header>
      {asTable && table ? table : children}
    </section>
  );
}

function DataTable({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r[0]}>
              {r.map((cell, i) =>
                i === 0 ? (
                  <th key={i} scope="row">
                    {cell}
                  </th>
                ) : (
                  <td key={i} data-label={head[i]}>
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface BarItem {
  key: string;
  label: string;
  value: number;
  valueText: string;
  note?: string;
}

/** Horizontal bars on one baseline, one hue; the value sits at the bar's tip. */
function HBars({ items, max }: { items: BarItem[]; max: number }) {
  return (
    <ul className="hbars">
      {items.map((it) => {
        const width = max > 0 ? (it.value / max) * 100 : 0;
        const name = `${it.label}: ${it.valueText}${it.note ? `, ${it.note}` : ""}`;
        return (
          <li key={it.key} className="hbar-row" tabIndex={0} aria-label={name} title={name}>
            <span className="hbar-label">{it.label}</span>
            <span className="hbar-track">
              {it.value > 0 ? <span className="hbar-fill" style={{ width: `${Math.max(width, 1.5)}%` }} /> : null}
              <span className="hbar-value">{it.valueText}</span>
            </span>
            <span className="hbar-note muted small">{it.note ?? ""}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** Weekly column chart: one series, so no legend; the latest and the busiest weeks are labeled. */
function Columns({ weeks }: { weeks: Insights["weekly"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const tipId = useId();
  const max = Math.max(...weeks.map((w) => w.applied), 0);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || 1;
  const W = 560;
  const H = 180;
  const left = 28;
  const bottom = 22;
  const plotW = W - left - 4;
  const plotH = H - bottom - 8;
  const band = plotW / weeks.length;
  const barW = Math.min(24, band - 6);
  const busiest = weeks.reduce((best, w, i) => (w.applied > (weeks[best]?.applied ?? 0) ? i : best), 0);
  const hovered = hover === null ? undefined : weeks[hover];
  const y = (v: number) => 8 + plotH - (v / top) * plotH;

  return (
    <div className="columns-chart" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Applications per week, last 12 weeks">
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={left} x2={W - 4} y1={y(t)} y2={y(t)} />
            <text className="tick" x={left - 6} y={y(t) + 4} textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        {weeks.map((w, i) => {
          const x = left + i * band + (band - barW) / 2;
          const h = (w.applied / top) * plotH;
          const label = `Week of ${shortDate(w.week_start)}: ${w.applied} applied`;
          const showValue = w.applied > 0 && (i === busiest || i === weeks.length - 1);
          return (
            <g
              key={w.week_start}
              tabIndex={0}
              role="img"
              aria-label={label}
              aria-describedby={hover === i ? tipId : undefined}
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              className={hover === i ? "col hovered" : "col"}
            >
              {/* Hit area: the whole band, not just the painted bar. */}
              <rect x={left + i * band} y={8} width={band} height={plotH} fill="transparent" />
              {h > 0 ? (
                <path
                  className="col-fill"
                  d={columnPath(x, y(w.applied), barW, h)}
                />
              ) : null}
              {showValue ? (
                <text className="col-value" x={x + barW / 2} y={y(w.applied) - 5} textAnchor="middle">
                  {w.applied}
                </text>
              ) : null}
              {i % 2 === weeks.length % 2 ? (
                <text className="tick" x={x + barW / 2} y={H - 6} textAnchor="middle">
                  {shortDate(w.week_start)}
                </text>
              ) : null}
            </g>
          );
        })}
        <line className="baseline" x1={left} x2={W - 4} y1={y(0)} y2={y(0)} />
      </svg>
      {hover !== null && hovered ? (
        <div
          id={tipId}
          role="tooltip"
          className="chart-tip"
          style={{ left: `${((left + hover * band + band / 2) / W) * 100}%` }}
        >
          <strong>{hovered.applied}</strong> applied
          <span className="muted small">Week of {shortDate(hovered.week_start)}</span>
        </div>
      ) : null}
    </div>
  );
}

/** A column with a 4px rounded top and a square base. */
function columnPath(x: number, top: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  const base = top + h;
  return `M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + w - r} Q${x + w},${top} ${x + w},${top + r} V${base} Z`;
}

function SourceTable({ data }: { data: Insights }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Source</th>
            <th scope="col">Applied</th>
            <th scope="col">Heard back</th>
            <th scope="col" className="col-meter">
              Response rate
            </th>
            <th scope="col">Screen or better</th>
            <th scope="col">Median days to answer</th>
          </tr>
        </thead>
        <tbody>
          {data.sources.map((s) => (
            <tr key={s.source}>
              <th scope="row">{s.source}</th>
              <td data-label="Applied">{s.applied}</td>
              <td data-label="Heard back">{s.responded}</td>
              <td className="col-meter" data-label="Response rate">
                <span className="meter" aria-hidden="true">
                  <span className="meter-fill" style={{ width: `${(s.response_rate ?? 0) * 100}%` }} />
                </span>
                <span>{percent(s.response_rate)}</span>
              </td>
              <td data-label="Screen or better">{s.positive}</td>
              <td data-label="Median days">{s.median_days_to_response ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
