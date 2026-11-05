// SPDX-License-Identifier: Apache-2.0
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, FullPageLoader, Notice } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useAiMutations, useAiSettings } from "../../lib/ai";
import { useDiscoveryMutations, usePosting, type PostingDetail } from "../../lib/discovery";
import { formatDate } from "../../lib/tracker";
import { PayVsTarget, postingFacts, ScoreBadge } from "./JobsPage";
import { PostingExtras } from "./PostingExtras";
import { webHref } from "../../lib/links";

export function PostingPage() {
  const { id = "" } = useParams();
  const posting = usePosting(id);
  if (posting.isPending) return <FullPageLoader />;
  if (posting.isError) {
    return (
      <div className="page">
        <Notice>{errorMessage(posting.error)}</Notice>
        <Link to="/jobs/matches">Back to jobs</Link>
      </div>
    );
  }
  return <PostingView p={posting.data} />;
}

function PostingView({ p }: { p: PostingDetail }) {
  const m = useDiscoveryMutations();
  const ai = useAiMutations();
  const aiSettings = useAiSettings();
  const aiOn = Boolean(aiSettings.data && aiSettings.data.provider !== "none");
  const navigate = useNavigate();
  const sd = p.score_detail;
  const scoreActions = (
    <div className="row">
      {aiOn ? (
        <Button
          variant="secondary"
          busy={ai.score.isPending && ai.score.variables?.method === "ai"}
          onClick={() => ai.score.mutate({ postingId: p.id, method: "ai" })}
        >
          {sd?.method === "ai" ? "Rescore with AI" : "Score with AI"}
        </Button>
      ) : null}
      <Button
        variant="ghost"
        busy={ai.score.isPending && ai.score.variables?.method === "rules"}
        onClick={() => ai.score.mutate({ postingId: p.id, method: "rules" })}
      >
        {sd ? "Rescore by keywords" : "Score by keywords"}
      </Button>
    </div>
  );

  return (
    <div className="page page-wide">
      <Link to="/jobs/matches" className="small">
        ← Jobs
      </Link>
      <header className="app-head">
        <div className="page-head">
          <div className="entry-title-row">
            <ScoreBadge score={p.score} />
            <h1>{p.title}</h1>
          </div>
          <p className="muted">{postingFacts(p).join(" · ")}</p>
          <div className="row">
            <PayVsTarget value={p.filter.pay_vs_target} />
            {p.filter.preferred ? <span className="badge badge-ok">Preferred company</span> : null}
            {p.closes_on ? <span className="badge">Closes {formatDate(p.closes_on)}</span> : null}
          </div>
        </div>
        <div className="row">
          {p.application_id ? (
            <Button onClick={() => navigate(`/tracker/applications/${p.application_id}`)}>Open in tracker</Button>
          ) : (
            <Button
              busy={m.track.isPending}
              onClick={async () => {
                const t = await m.track.mutateAsync(p.id);
                if (t.application_id) navigate(`/tracker/applications/${t.application_id}`);
              }}
            >
              Track this job
            </Button>
          )}
          {p.status !== "tracked" ? (
            <Button
              variant="secondary"
              onClick={() => m.setStatus.mutate({ id: p.id, status: p.status === "saved" ? "new" : "saved" })}
            >
              {p.status === "saved" ? "Unsave" : "Save"}
            </Button>
          ) : null}
          {p.url ? (
            <a className="btn btn-ghost" href={webHref(p.url)} target="_blank" rel="noopener noreferrer">
              Open posting
            </a>
          ) : null}
        </div>
      </header>

      {!p.filter.passed ? (
        <Notice tone="info">
          <strong>Filtered out:</strong> {p.filter.reasons.join(" ")}
        </Notice>
      ) : null}

      <div className="app-grid">
        <div className="stack">
          {sd ? (
            <Card
              title={`Fit: ${sd.score}/100`}
              description={
                sd.method === "ai"
                  ? `Scored by ${sd.model} against your verified vault.`
                  : "Keyword match against your verified vault (turn on an AI provider for a deeper read)."
              }
            >
              {sd.stale ? <Notice tone="info">Your vault changed since this was scored. It will be rescored shortly.</Notice> : null}
              {sd.summary ? <p>{sd.summary}</p> : null}
              {sd.matched.length ? (
                <div className="stack-sm">
                  <h3 className="section-title">Matched</h3>
                  <ul className="reason-list ok">
                    {sd.matched.map((item, i) => (
                      <li key={i}>
                        {item.requirement}
                        {item.evidence.length ? <span className="muted small"> — {item.evidence.join("; ")}</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {sd.gaps.length ? (
                <div className="stack-sm">
                  <h3 className="section-title">Gaps</h3>
                  <ul className="reason-list gap">
                    {sd.gaps.map((g, i) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {scoreActions}
              {ai.score.error ? <Notice>{errorMessage(ai.score.error)}</Notice> : null}
              {sd.red_flags.length ? (
                <div className="stack-sm">
                  <h3 className="section-title">Red flags</h3>
                  <ul className="reason-list flag">
                    {sd.red_flags.map((g, i) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </Card>
          ) : (
            <Card title="Fit" description="Not scored yet. Scores use only your verified vault facts.">
              {scoreActions}
              {ai.score.error ? <Notice>{errorMessage(ai.score.error)}</Notice> : null}
            </Card>
          )}
          <Card title="Description" description="As posted. Shown as plain text.">
            <pre className="plain-text posting-description">{p.description || "No description was provided."}</pre>
          </Card>
        </div>
        <div className="stack">
          <PostingExtras posting={p} />
          <Card title="Seen on">
            <ul className="files">
              {p.sightings.map((s, i) => (
                <li key={i} className="file">
                  {s.url ? (
                    <a href={webHref(s.url)} target="_blank" rel="noopener noreferrer">
                      {s.source_label || "Link"}
                    </a>
                  ) : (
                    s.source_label
                  )}
                  <span className="muted small"> {new Date(s.seen_at).toLocaleDateString()}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
