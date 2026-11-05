// SPDX-License-Identifier: Apache-2.0
import { Link } from "react-router-dom";
import { useMe } from "../lib/auth";
import {
  localToday,
  OPEN_STAGES,
  STAGE_LABEL,
  useAgenda,
  useTrackerSummary,
} from "../lib/tracker";
import { useSummary } from "../lib/vault";
import { AgendaRow } from "./tracker/AgendaTab";

/** Phases that are built and usable today. */
const AVAILABLE_THROUGH_PHASE = 8;
const DUE_SOON_LIMIT = 5;

const STEPS = [
  { title: "Build your Career Vault", body: "Add verified roles, achievements, and certifications.", to: "/vault", phase: 2 },
  { title: "Track applications", body: "Move each one from Saved to Offer, with reminders.", to: "/tracker", phase: 3 },
  { title: "Find jobs", body: "Pull postings from the sources you choose.", to: "/jobs", phase: 4 },
  { title: "Score and tailor", body: "Fit scores and fact-checked resumes, from vault facts only.", to: "/jobs", phase: 5 },
  { title: "Prepare for interviews", body: "Likely questions matched to your stories, and practice runs.", to: "/interview", phase: 7 },
  { title: "Stay on top of it", body: "A daily digest and new-match alerts, sent where you choose.", to: "/settings/notifications", phase: 8 },
  { title: "See what's working", body: "Response rates by source, stage conversion, and time to response.", to: "/insights", phase: 8 },
];

export function OverviewPage() {
  const me = useMe();
  const firstName = me.data?.display_name.split(" ")[0] ?? "";
  const summary = useSummary();
  const review = summary.data?.needs_review ?? 0;
  const today = localToday();
  const agenda = useAgenda(today, 7);
  const tracker = useTrackerSummary(today);
  const due = agenda.data ?? [];

  return (
    <div className="page">
      <header className="page-head">
        <h1>Hi{firstName ? `, ${firstName}` : ""}</h1>
        <p className="muted">Here's where your job search stands.</p>
      </header>

      {me.data && !me.data.mfa_enabled ? (
        <div className="callout">
          <div>
            <strong>Turn on two-factor authentication.</strong>
            <p className="muted">Your vault will hold your whole work history. Protect it with a second step.</p>
          </div>
          <Link className="btn btn-primary" to="/settings/security">
            Set up
          </Link>
        </div>
      ) : null}

      {review ? (
        <div className="callout">
          <div>
            <strong>
              {review} vault draft{review === 1 ? "" : "s"} to review.
            </strong>
            <p className="muted">Drafts aren't used for anything until you check and approve them.</p>
          </div>
          <Link className="btn btn-secondary" to="/vault/experience">
            Review
          </Link>
        </div>
      ) : null}

      {tracker.data && tracker.data.open_total > 0 ? (
        <div className="overview-grid">
          <section className="card" aria-label="Due soon">
            <div className="card-head">
              <h2>Due soon</h2>
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
            {due.length > DUE_SOON_LIMIT ? (
              <p className="muted small">and {due.length - DUE_SOON_LIMIT} more</p>
            ) : null}
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

      <h2 className="section-title">Getting started</h2>
      <ol className="steps">
        {STEPS.map((s, i) => (
          <li key={s.title} className="step">
            <span className="step-num" aria-hidden="true">
              {i + 1}
            </span>
            <div>
              <Link to={s.to} className="step-title">
                {s.title}
              </Link>
              <p className="muted">{s.body}</p>
            </div>
            {s.phase <= AVAILABLE_THROUGH_PHASE ? (
              <span className="badge badge-ok">Ready</span>
            ) : (
              <span className="badge">Phase {s.phase}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
