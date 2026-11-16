// SPDX-License-Identifier: Apache-2.0
import { Link } from "react-router-dom";
import { useAiSettings } from "../lib/ai";
import { useMe } from "../lib/auth";
import { MODE_LABEL, scoreTone, usePostings, useSources } from "../lib/discovery";
import { useFollowUps } from "../lib/followups";
import { localToday, OPEN_STAGES, STAGE_LABEL, useAgenda, useTrackerSummary } from "../lib/tracker";
import { useSummary } from "../lib/vault";
import { FollowUpsList } from "./tracker/FollowUps";
import { AgendaRow } from "./tracker/AgendaTab";

const DUE_SOON_LIMIT = 5;
const MATCH_LIMIT = 6;

type Step = { done: boolean; title: string; body: string; to: string; action: string };

function SetupSteps({ steps }: { steps: Step[] }) {
  const left = steps.filter((s) => !s.done);
  if (!left.length) return null;
  return (
    <section className="card stack-sm" aria-label="Set up">
      <div className="row-between">
        <h2>
          Set up: {steps.length - left.length} of {steps.length} done
        </h2>
        <Link to="/guide" className="small">
          Picture guide
        </Link>
      </div>
      <ol className="setup-steps">
        {steps.map((s) => (
          <li key={s.title} className={s.done ? "setup-done" : ""}>
            <span className="setup-mark" aria-hidden="true">
              {s.done ? "✓" : ""}
            </span>
            <div className="setup-text">
              <strong>{s.title}</strong>
              {!s.done ? <p className="muted small">{s.body}</p> : null}
            </div>
            {!s.done ? (
              <Link className="btn btn-secondary" to={s.to}>
                {s.action}
              </Link>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function money(cents: number | null, period: string | null, currency: string): string {
  if (!cents) return "";
  const n = cents / 100;
  const shown = period === "hour" ? `${n.toFixed(0)}/hr` : `${Math.round(n / 1000)}k`;
  return `${currency === "USD" ? "$" : `${currency} `}${shown}`;
}

function BestMatches() {
  const matches = usePostings("matches");
  const items = (matches.data?.items ?? []).filter((p) => p.status === "new").slice(0, MATCH_LIMIT);
  return (
    <section className="card stack-sm" aria-label="Best new matches">
      <div className="row-between">
        <h2>Best new matches</h2>
        <Link to="/jobs" className="small">
          All jobs ({matches.data?.counts.matches ?? 0})
        </Link>
      </div>
      {matches.isPending ? <p className="muted small">Loading…</p> : null}
      {!matches.isPending && !items.length ? (
        <p className="muted small">No new matches yet. Add sources in Jobs → Sources and they&apos;ll show up here.</p>
      ) : null}
      <ul className="match-list">
        {items.map((p) => (
          <li key={p.id}>
            <Link to={`/jobs/postings/${p.id}`} className="match">
              {p.score ? (
                <span className={`score score-${scoreTone(p.score.score)}`} aria-label={`Fit ${p.score.score}`}>
                  {p.score.score}
                </span>
              ) : (
                <span className="score">…</span>
              )}
              <span className="match-text">
                <span className="match-title">{p.title}</span>
                <span className="muted small">
                  {[
                    p.company,
                    p.work_mode ? MODE_LABEL[p.work_mode] : p.location,
                    money(p.pay_min_cents, p.pay_period, p.currency),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function OverviewPage() {
  const me = useMe();
  const firstName = me.data?.display_name.split(" ")[0] ?? "";
  const summary = useSummary();
  const sources = useSources();
  const ai = useAiSettings();
  const followUps = useFollowUps();
  const review = summary.data?.needs_review ?? 0;
  const today = localToday();
  const agenda = useAgenda(today, 7);
  const tracker = useTrackerSummary(today);
  const due = agenda.data ?? [];
  const verified = summary.data
    ? Object.values(summary.data.kinds).reduce((n, k) => n + k.verified, 0)
    : 0;
  const failing = (sources.data ?? []).filter((s) => s.enabled && s.last_status === "error");

  const steps: Step[] = [
    {
      done: verified > 0,
      title: "Add your experience",
      body: "Import your resume (it becomes drafts you approve) or add roles by hand. Scores and resumes are built from these.",
      to: "/vault",
      action: "Open vault",
    },
    {
      done: (sources.data?.length ?? 0) > 0,
      title: "Tell CareerLine where to look",
      body: "Add job sources: your LinkedIn/Indeed alert emails, remote boards, or companies you like.",
      to: "/jobs/sources",
      action: "Add sources",
    },
    {
      done: Boolean(ai.data && ai.data.provider !== "none"),
      title: "Optional: connect an AI model",
      body: "Better fit scores, tailored resumes with a second-opinion review, and the assistant. Works without it too.",
      to: "/settings/ai",
      action: "Set up AI",
    },
    {
      done: Boolean(me.data?.mfa_enabled),
      title: "Protect your account",
      body: "Turn on two-factor sign-in. Your vault holds your whole work history.",
      to: "/settings/security",
      action: "Turn on",
    },
  ];

  return (
    <div className="page">
      <header className="page-head">
        <h1>{firstName ? `Hi, ${firstName}` : "Today"}</h1>
        <p className="muted">What needs you today.</p>
      </header>

      {failing.length ? (
        <div className="callout">
          <div>
            <strong>
              {failing.length} job source{failing.length === 1 ? " isn't" : "s aren't"} working.
            </strong>
            <p className="muted">{failing.map((s) => `${s.name}: ${s.last_error}`).join(" · ")}</p>
          </div>
          <Link className="btn btn-secondary" to="/jobs/sources">
            Fix
          </Link>
        </div>
      ) : null}

      {review ? (
        <div className="callout">
          <div>
            <strong>
              {review} vault draft{review === 1 ? "" : "s"} to check.
            </strong>
            <p className="muted">Approve the ones that are true; they&apos;re then used for scores and resumes.</p>
          </div>
          <Link className="btn btn-secondary" to="/vault/experience">
            Review
          </Link>
        </div>
      ) : null}

      <SetupSteps steps={steps} />

      <div className="overview-grid">
        <BestMatches />
        <section className="card stack-sm" aria-label="Follow up">
          <div className="row-between">
            <h2>Follow up</h2>
            {followUps.data?.length ? (
              <Link to="/tracker/agenda" className="small">
                All ({followUps.data.length})
              </Link>
            ) : null}
          </div>
          <FollowUpsList limit={3} />
        </section>
      </div>

      {tracker.data && tracker.data.open_total > 0 ? (
        <div className="overview-grid">
          <section className="card" aria-label="Due soon">
            <div className="card-head">
              <h2>Due this week</h2>
              <Link to="/tracker/agenda" className="small">
                Agenda
              </Link>
            </div>
            {due.length ? (
              <ul className="entry-list">
                {due.slice(0, DUE_SOON_LIMIT).map((item) => (
                  <AgendaRow key={`${item.source}-${item.id}`} item={item} today={today} compact />
                ))}
              </ul>
            ) : (
              <p className="muted small">Nothing due in the next 7 days.</p>
            )}
            {due.length > DUE_SOON_LIMIT ? <p className="muted small">and {due.length - DUE_SOON_LIMIT} more</p> : null}
          </section>
          <section className="card" aria-label="Pipeline">
            <div className="card-head">
              <h2>Pipeline</h2>
              <Link to="/tracker/board" className="small">
                Board
              </Link>
            </div>
            <ul className="pipeline">
              {OPEN_STAGES.map((s) => (
                <li key={s}>
                  <span className="pipeline-n">{tracker.data.stages[s]}</span>
                  <span className="muted small">{STAGE_LABEL[s]}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      ) : null}
    </div>
  );
}
