// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Link, NavLink, Navigate, useNavigate, useParams } from "react-router-dom";
import { Button, Field, FullPageLoader, Notice, TextAreaField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  CLEARANCE_LABEL,
  MODE_LABEL,
  scoreTone,
  SHIFT_OPTIONS,
  useDiscoveryMutations,
  usePostings,
  type Posting,
  type View,
} from "../../lib/discovery";
import { formatDate, formatPay } from "../../lib/tracker";
import { FiltersTab } from "./FiltersTab";
import { SourcesTab } from "./SourcesTab";

const TABS: { slug: string; label: string; view?: View }[] = [
  { slug: "matches", label: "Matches", view: "matches" },
  { slug: "saved", label: "Saved", view: "saved" },
  { slug: "filtered", label: "Filtered out", view: "filtered" },
  { slug: "dismissed", label: "Dismissed", view: "dismissed" },
  { slug: "sources", label: "Sources" },
  { slug: "filters", label: "Filters" },
];

export function JobsPage() {
  const { tab } = useParams();
  const current = TABS.find((t) => t.slug === tab);
  if (!current) return <Navigate to="/jobs/matches" replace />;
  return (
    <div className="page page-wide">
      <header className="page-head">
        <h1>Jobs</h1>
        <p className="muted">Postings from the sources you turn on, de-duplicated and checked against your filters.</p>
      </header>
      <nav className="tabs" aria-label="Job views">
        {TABS.map((t) => (
          <NavLink key={t.slug} to={`/jobs/${t.slug}`} className="tab">
            {t.label}
          </NavLink>
        ))}
      </nav>
      {current.view ? <PostingsView view={current.view} /> : null}
      {current.slug === "sources" ? <SourcesTab /> : null}
      {current.slug === "filters" ? <FiltersTab /> : null}
    </div>
  );
}

function PostingsView({ view }: { view: View }) {
  const postings = usePostings(view);
  const [importing, setImporting] = useState(false);
  const [q, setQ] = useState("");
  if (postings.isPending) return <FullPageLoader />;
  if (postings.isError) return <Notice>{errorMessage(postings.error)}</Notice>;
  const { items, counts } = postings.data;
  const needle = q.trim().toLowerCase();
  const shown = needle ? items.filter((p) => `${p.title} ${p.company} ${p.location}`.toLowerCase().includes(needle)) : items;

  return (
    <section className="stack" aria-label="Postings">
      <div className="row-between tab-toolbar">
        <p className="muted small">
          {counts.matches} matching · {counts.saved} saved · {counts.filtered} filtered out · {counts.dismissed} dismissed
        </p>
        {!importing ? <Button onClick={() => setImporting(true)}>Add a job</Button> : null}
      </div>
      {importing ? <ImportForm onDone={() => setImporting(false)} /> : null}
      {items.length > 8 ? (
        <div className="filters">
          <Field label="Search these jobs" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      ) : null}
      {shown.length === 0 ? (
        <div className="empty">
          <p>
            {view === "matches"
              ? counts.all === 0
                ? "No jobs yet. Add a source, or paste a job you found."
                : "Nothing matches right now. Check Filtered out to see why."
              : "Nothing here."}
          </p>
          {view === "matches" && counts.all === 0 ? (
            <Link className="btn btn-secondary" to="/jobs/sources">
              Add a source
            </Link>
          ) : null}
        </div>
      ) : (
        <ul className="posting-list">
          {shown.map((p) => (
            <PostingCard key={p.id} posting={p} />
          ))}
        </ul>
      )}
    </section>
  );
}

export function ScoreBadge({ score }: { score: Posting["score"] }) {
  if (!score) return null;
  return (
    <span
      className={`score score-${scoreTone(score.score)}`}
      title={`${score.method === "ai" ? "AI" : "Keyword"} fit score${score.stale ? " (your vault changed since)" : ""}`}
      aria-label={`Fit score ${score.score} out of 100`}
    >
      {score.score}
    </span>
  );
}

export function PayVsTarget({ value }: { value: Posting["filter"]["pay_vs_target"] }) {
  if (!value) return null;
  const label = { above: "Above your target", within: "Around your target", below: "Below your target" }[value];
  return <span className={`badge ${value === "below" ? "badge-warn" : "badge-ok"}`}>{label}</span>;
}

export function postingFacts(p: Posting): string[] {
  return [
    p.company,
    p.location,
    p.work_mode ? (MODE_LABEL[p.work_mode] ?? "") : "",
    formatPay(p),
    p.shifts.length ? SHIFT_OPTIONS.filter((s) => p.shifts.includes(s.value)).map((s) => s.label).join(", ") + " shift" : "",
    p.clearance ? `${CLEARANCE_LABEL[p.clearance] ?? p.clearance} clearance` : "",
  ].filter((x): x is string => Boolean(x));
}

function PostingCard({ posting: p }: { posting: Posting }) {
  const m = useDiscoveryMutations();
  const navigate = useNavigate();
  return (
    <li className={`posting${p.filter.passed ? "" : " posting-filtered"}`}>
      <div className="posting-main">
        <ScoreBadge score={p.score} />
        <div className="posting-text">
          <div className="entry-title-row">
            <Link to={`/jobs/postings/${p.id}`} className="posting-title">
              {p.title}
            </Link>
            {p.filter.preferred ? <span className="badge badge-ok">Preferred company</span> : null}
            <PayVsTarget value={p.filter.pay_vs_target} />
            {p.status === "tracked" ? <span className="badge">In tracker</span> : null}
          </div>
          <p className="muted small">{postingFacts(p).join(" · ")}</p>
          {p.filter.reasons.length ? <p className="small text-danger">{p.filter.reasons.join(" ")}</p> : null}
          {p.score?.summary ? <p className="small">{p.score.summary}</p> : null}
          <p className="muted small">
            {p.sources.join(", ")}
            {p.posted_at ? ` · posted ${formatDate(p.posted_at.slice(0, 10))}` : ""}
          </p>
        </div>
        <div className="entry-actions posting-actions">
          {p.status === "tracked" && p.application_id ? (
            <Button variant="ghost" onClick={() => navigate(`/tracker/applications/${p.application_id}`)}>
              Open in tracker
            </Button>
          ) : (
            <>
              <Button
                variant="ghost"
                onClick={async () => {
                  const tracked = await m.track.mutateAsync(p.id);
                  if (tracked.application_id) navigate(`/tracker/applications/${tracked.application_id}`);
                }}
              >
                Track
              </Button>
              {p.status === "saved" ? (
                <Button variant="ghost" onClick={() => m.setStatus.mutate({ id: p.id, status: "new" })}>
                  Unsave
                </Button>
              ) : (
                <Button variant="ghost" onClick={() => m.setStatus.mutate({ id: p.id, status: "saved" })}>
                  Save
                </Button>
              )}
              {p.status === "dismissed" ? (
                <Button variant="ghost" onClick={() => m.setStatus.mutate({ id: p.id, status: "new" })}>
                  Restore
                </Button>
              ) : (
                <Button variant="ghost" onClick={() => m.setStatus.mutate({ id: p.id, status: "dismissed" })}>
                  Dismiss
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </li>
  );
}

function ImportForm({ onDone }: { onDone: () => void }) {
  const m = useDiscoveryMutations();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"url" | "text">("url");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const posting = await m.importPosting.mutateAsync(
        mode === "url" ? { url: url.trim() } : { text, title: title.trim(), company: company.trim(), location: location.trim(), url: url.trim() },
      );
      onDone();
      navigate(`/jobs/postings/${posting.id}`);
    } catch {
      // shown below
    }
  }

  return (
    <form className="card entry-form" onSubmit={submit}>
      <h2>Add a job you found</h2>
      <div className="segmented" role="radiogroup" aria-label="How to add it">
        <button type="button" role="radio" aria-checked={mode === "url"} onClick={() => setMode("url")}>
          From a link
        </button>
        <button type="button" role="radio" aria-checked={mode === "text"} onClick={() => setMode("text")}>
          Paste the text
        </button>
      </div>
      {mode === "url" ? (
        <Field
          label="Link to the posting"
          type="url"
          required
          value={url}
          hint="Waypoint reads the page once, if the site allows it. Some sites (LinkedIn, Indeed) don't, so paste the text instead."
          onChange={(e) => setUrl(e.target.value)}
        />
      ) : (
        <>
          <div className="form-grid">
            <Field label="Job title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
            <Field label="Company (optional)" maxLength={200} value={company} onChange={(e) => setCompany(e.target.value)} />
            <Field label="Location (optional)" maxLength={200} value={location} onChange={(e) => setLocation(e.target.value)} />
            <Field label="Link (optional)" type="url" value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>
          <TextAreaField
            label="Job description"
            required
            rows={8}
            maxLength={50_000}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </>
      )}
      {m.importPosting.error ? <Notice>{errorMessage(m.importPosting.error)}</Notice> : null}
      <div className="row">
        <Button type="submit" busy={m.importPosting.isPending}>
          Add job
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
