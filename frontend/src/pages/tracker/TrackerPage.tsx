// SPDX-License-Identifier: Apache-2.0
import { useMemo, useState } from "react";
import { Link, NavLink, Navigate, useNavigate, useParams } from "react-router-dom";
import { Button, Field, FullPageLoader, Notice, SelectField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  formatDate,
  formatDue,
  formatPay,
  localToday,
  STAGE_LABEL,
  STAGES,
  useApplications,
  useTrackerMutations,
  useTrackerSummary,
  type ApplicationSummary,
  type Stage,
} from "../../lib/tracker";
import { AgendaTab } from "./AgendaTab";
import { ApplicationForm } from "./ApplicationForm";
import { Board, FollowUpOffer, MoveMenu, useStageMove } from "./Board";
import { ContactsTab } from "./ContactsTab";
import { OffersTab } from "./OffersTab";

const TABS = [
  { slug: "board", label: "Board" },
  { slug: "list", label: "List" },
  { slug: "agenda", label: "Agenda" },
  { slug: "contacts", label: "Contacts" },
  { slug: "offers", label: "Offers" },
];

export function TrackerPage() {
  const { tab } = useParams();
  const summary = useTrackerSummary(localToday());
  if (!tab) return <Navigate to="/tracker/board" replace />;
  if (!TABS.some((t) => t.slug === tab)) return <Navigate to="/tracker/board" replace />;
  const due = (summary.data?.overdue ?? 0) + (summary.data?.due_today ?? 0);

  return (
    <div className="page page-wide">
      <header className="page-head">
        <h1>Tracker</h1>
        <p className="muted">Every application from Saved to a decision, with what you sent and who you talked to.</p>
      </header>
      <nav className="tabs" aria-label="Tracker sections">
        {TABS.map((t) => (
          <NavLink key={t.slug} to={`/tracker/${t.slug}`} className="tab">
            {t.label}
            {t.slug === "agenda" && due ? (
              <span className="tab-count" title={`${due} due today or overdue`}>
                {due}
              </span>
            ) : null}
          </NavLink>
        ))}
      </nav>
      {tab === "board" || tab === "list" ? <Applications view={tab} /> : null}
      {tab === "agenda" ? <AgendaTab /> : null}
      {tab === "contacts" ? <ContactsTab /> : null}
      {tab === "offers" ? <OffersTab /> : null}
    </div>
  );
}

function Applications({ view }: { view: "board" | "list" }) {
  const apps = useApplications();
  const m = useTrackerMutations();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);

  if (apps.isPending) return <FullPageLoader />;
  if (apps.isError) return <Notice>{errorMessage(apps.error)}</Notice>;

  return (
    <section className="stack" aria-label="Applications">
      <div className="row-between tab-toolbar">
        <p className="muted small">
          {apps.data.length} application{apps.data.length === 1 ? "" : "s"}
        </p>
        {!adding ? <Button onClick={() => setAdding(true)}>Add application</Button> : null}
      </div>
      {adding ? (
        <div className="card">
          <h2>New application</h2>
          <ApplicationForm
            withStage
            submitLabel="Add application"
            onCancel={() => setAdding(false)}
            onSubmit={async (body) => {
              const created = await m.createApplication.mutateAsync(body);
              navigate(`/tracker/applications/${created.id}`);
            }}
          />
        </div>
      ) : null}
      {apps.data.length === 0 && !adding ? (
        <div className="empty">
          <p>Nothing tracked yet. Add the first job you&apos;re considering, even if you haven&apos;t applied.</p>
        </div>
      ) : view === "board" ? (
        <Board apps={apps.data} />
      ) : (
        <ListView apps={apps.data} />
      )}
    </section>
  );
}

type SortKey = "company" | "title" | "stage" | "applied_on" | "last_activity_at" | "next_due";

export function sortApplications(apps: ApplicationSummary[], key: SortKey, asc: boolean): ApplicationSummary[] {
  const value = (a: ApplicationSummary): string => {
    switch (key) {
      case "stage":
        return String(STAGES.indexOf(a.stage)).padStart(2, "0");
      case "next_due":
        return a.next_due?.due_on ?? "9999-12-31";
      case "applied_on":
        return a.applied_on ?? "";
      default:
        return String(a[key] ?? "").toLowerCase();
    }
  };
  const sorted = [...apps].sort((a, b) => value(a).localeCompare(value(b)));
  return asc ? sorted : sorted.reverse();
}

function ListView({ apps }: { apps: ApplicationSummary[] }) {
  const [stage, setStage] = useState<Stage | "">("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: "last_activity_at", asc: false });
  const { move, followUp, clearFollowUp, error } = useStageMove();
  const today = localToday();

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const filtered = apps.filter(
      (a) =>
        (!stage || a.stage === stage) &&
        (!needle || `${a.title} ${a.company} ${a.location}`.toLowerCase().includes(needle)),
    );
    return sortApplications(filtered, sort.key, sort.asc);
  }, [apps, stage, q, sort]);

  const header = (key: SortKey, label: string) => (
    <th aria-sort={sort.key === key ? (sort.asc ? "ascending" : "descending") : "none"}>
      <button
        className="link-btn sort-btn"
        onClick={() => setSort((cur) => ({ key, asc: cur.key === key ? !cur.asc : key !== "last_activity_at" }))}
      >
        {label}
        {sort.key === key ? (sort.asc ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );

  return (
    <div className="stack">
      {followUp ? <FollowUpOffer app={followUp} onClose={clearFollowUp} /> : null}
      {error ? <Notice>{error}</Notice> : null}
      <div className="filters">
        <Field label="Search" value={q} onChange={(e) => setQ(e.target.value)} />
        <SelectField
          label="Stage"
          value={stage}
          placeholder="All stages"
          options={STAGES.map((s) => ({ value: s, label: STAGE_LABEL[s] }))}
          onChange={(e) => setStage(e.target.value as Stage | "")}
        />
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              {header("title", "Job")}
              {header("company", "Company")}
              {header("stage", "Stage")}
              {header("applied_on", "Applied")}
              {header("next_due", "Next due")}
              {header("last_activity_at", "Last activity")}
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id}>
                <td data-label="Job">
                  <Link to={`/tracker/applications/${a.id}`}>{a.title}</Link>
                  {formatPay(a) ? <span className="muted small"> · {formatPay(a)}</span> : null}
                </td>
                <td data-label="Company">{a.company}</td>
                <td data-label="Stage">
                  <MoveMenu app={a} onMove={(s) => void move(a, s)} />
                </td>
                <td data-label="Applied">{formatDate(a.applied_on)}</td>
                <td data-label="Next due">{a.next_due ? `${formatDue(a.next_due, today)}: ${a.next_due.title}` : ""}</td>
                <td data-label="Last activity">{new Date(a.last_activity_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 ? <p className="muted small table-empty">No applications match.</p> : null}
      </div>
    </div>
  );
}
