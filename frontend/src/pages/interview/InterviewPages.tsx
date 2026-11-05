// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, FullPageLoader, Notice, SelectField, TextAreaField } from "../../components/ui";
import { useAiSettings } from "../../lib/ai";
import { errorMessage } from "../../lib/api";
import {
  CATEGORY_LABEL,
  useInterviewMutations,
  useMock,
  useMocks,
  usePrep,
  usePreps,
  type Prep,
  type Question,
} from "../../lib/assistant";

/** "Interview prep" card for a posting or application page. */
export function InterviewCard({ postingId, applicationId }: { postingId?: string; applicationId?: string }) {
  const preps = usePreps(postingId ? { posting_id: postingId } : { application_id: applicationId ?? "" });
  const ai = useAiSettings();
  const m = useInterviewMutations();
  const navigate = useNavigate();
  const aiOn = Boolean(ai.data && ai.data.provider !== "none");
  const target = postingId ? { posting_id: postingId } : { application_id: applicationId };

  async function make(method: "rules" | "ai") {
    try {
      const prep = await m.createPrep.mutateAsync({ method, ...target });
      navigate(`/interview/${prep.id}`);
    } catch {
      // shown below
    }
  }

  return (
    <Card title="Interview prep" description="Likely questions, matched to your STAR stories.">
      <div className="row">
        <Button variant="secondary" busy={m.createPrep.isPending && m.createPrep.variables?.method === "rules"} onClick={() => void make("rules")}>
          Prepare questions
        </Button>
        {aiOn ? (
          <Button variant="secondary" busy={m.createPrep.isPending && m.createPrep.variables?.method === "ai"} onClick={() => void make("ai")}>
            Prepare with AI
          </Button>
        ) : null}
      </div>
      {m.createPrep.error ? <Notice>{errorMessage(m.createPrep.error)}</Notice> : null}
      {preps.data?.length ? (
        <ul className="files">
          {preps.data.map((p) => (
            <li key={p.id} className="file">
              <Link to={`/interview/${p.id}`}>
                {p.questions.length} questions · {p.method === "ai" ? "AI" : "Template"}
              </Link>
              <span className="muted small"> {new Date(p.created_at).toLocaleDateString()}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

export function InterviewHomePage() {
  const preps = usePreps();
  return (
    <div className="page">
      <header className="page-head">
        <h1>Interview prep</h1>
        <p className="muted">Open a job or a tracked application and choose Prepare questions. Your preps show up here.</p>
      </header>
      {preps.isPending ? <FullPageLoader /> : null}
      {preps.data && preps.data.length === 0 ? (
        <div className="empty">
          <p>No interview prep yet. Add STAR stories to your vault first so questions can be matched to them.</p>
          <Link className="btn btn-secondary" to="/vault/stories">
            Write a story
          </Link>
        </div>
      ) : null}
      <ul className="entry-list">
        {(preps.data ?? []).map((p) => (
          <li key={p.id} className="entry">
            <div className="entry-main">
              <div className="entry-text">
                <Link to={`/interview/${p.id}`} className="entry-title">
                  {p.target_title}
                  {p.target_company ? ` at ${p.target_company}` : ""}
                </Link>
                <p className="muted small">
                  {p.questions.length} questions · {p.method === "ai" ? "AI" : "Template"} · {new Date(p.created_at).toLocaleDateString()}
                </p>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function QuestionItem({ prep, q }: { prep: Prep; q: Question }) {
  const m = useInterviewMutations();
  const [notes, setNotes] = useState(q.notes);
  const [open, setOpen] = useState(false);
  const storyOptions = Object.entries(prep.stories);
  return (
    <li className="entry">
      <div className="entry-text">
        <p className="entry-title">{q.question}</p>
        <p className="muted small">{q.why}</p>
        {q.story_ids.length ? (
          <p className="small">
            Use: {q.story_ids.map((id) => prep.stories[id] ?? "a story").join("; ")}
          </p>
        ) : (
          <p className="small muted">No matching story yet.</p>
        )}
        <button className="link-btn small" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Close notes" : q.notes ? "Edit notes" : "Add notes"}
        </button>
        {open ? (
          <div className="stack-sm">
            <TextAreaField label="Your notes" rows={3} maxLength={5000} value={notes} onChange={(e) => setNotes(e.target.value)} />
            {storyOptions.length ? (
              <SelectField
                label="Link a story"
                value=""
                placeholder="Choose a story…"
                options={storyOptions.filter(([id]) => !q.story_ids.includes(id)).map(([value, label]) => ({ value, label }))}
                onChange={(e) =>
                  e.target.value &&
                  m.patchQuestion.mutate({ prepId: prep.id, questionId: q.id, story_ids: [...q.story_ids, e.target.value] })
                }
              />
            ) : null}
            <Button busy={m.patchQuestion.isPending} onClick={() => m.patchQuestion.mutate({ prepId: prep.id, questionId: q.id, notes })}>
              Save notes
            </Button>
          </div>
        ) : null}
      </div>
    </li>
  );
}

export function PrepPage() {
  const { prepId = "" } = useParams();
  const prep = usePrep(prepId);
  const mocks = useMocks(prepId);
  const ai = useAiSettings();
  const m = useInterviewMutations();
  const navigate = useNavigate();
  const [count, setCount] = useState("5");
  if (prep.isPending) return <FullPageLoader />;
  if (prep.isError) return <Notice>{errorMessage(prep.error)}</Notice>;
  const p = prep.data;
  const aiOn = Boolean(ai.data && ai.data.provider !== "none");
  const groups = (Object.keys(CATEGORY_LABEL) as Question["category"][])
    .map((cat) => [cat, p.questions.filter((q) => q.category === cat)] as const)
    .filter(([, qs]) => qs.length);

  return (
    <div className="page page-wide">
      <Link to="/interview" className="small">
        ← Interview prep
      </Link>
      <header className="page-head">
        <h1>
          {p.target_title}
          {p.target_company ? ` at ${p.target_company}` : ""}
        </h1>
        <p className="muted">{p.questions.length} likely questions, matched to your verified STAR stories.</p>
      </header>
      <div className="app-grid">
        <div className="stack">
          {groups.map(([cat, qs]) => (
            <div key={cat} className="stack-sm">
              <h2 className="section-title">{CATEGORY_LABEL[cat]}</h2>
              <ul className="entry-list">
                {qs.map((q) => (
                  <QuestionItem key={q.id} prep={p} q={q} />
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="stack">
          <Card title="Practice interview" description="An AI interviewer asks questions one at a time and gives feedback on each answer.">
            {aiOn ? (
              <>
                <SelectField
                  label="Questions"
                  value={count}
                  options={["3", "5", "8"].map((v) => ({ value: v, label: v }))}
                  onChange={(e) => setCount(e.target.value)}
                />
                <Button
                  busy={m.startMock.isPending}
                  onClick={async () => {
                    const mock = await m.startMock.mutateAsync({ prep_id: p.id, max_questions: Number(count) });
                    navigate(`/interview/mock/${mock.id}`);
                  }}
                >
                  Start practice
                </Button>
                {m.startMock.error ? <Notice>{errorMessage(m.startMock.error)}</Notice> : null}
              </>
            ) : (
              <p className="small muted">
                Needs an AI provider. <Link to="/settings/ai">Turn one on</Link>.
              </p>
            )}
            {mocks.data?.length ? (
              <ul className="files">
                {mocks.data.map((mk) => (
                  <li key={mk.id} className="file">
                    <Link to={`/interview/mock/${mk.id}`}>{new Date(mk.created_at).toLocaleString()}</Link>
                    <span className="muted small"> {mk.status === "finished" ? "finished" : "in progress"}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </Card>
          {p.posting_id ? (
            <Card title="Job">
              <Link to={`/jobs/postings/${p.posting_id}`}>Open the posting</Link>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function MockPage() {
  const { mockId = "" } = useParams();
  const mock = useMock(mockId);
  const m = useInterviewMutations();
  const [answer, setAnswer] = useState("");
  if (mock.isPending) return <FullPageLoader />;
  if (mock.isError) return <Notice>{errorMessage(mock.error)}</Notice>;
  const s = mock.data;
  const answered = s.turns.filter((t) => t.role === "candidate").length;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const text = answer.trim();
    if (!text) return;
    try {
      await m.answer.mutateAsync({ id: s.id, text });
      setAnswer("");
    } catch {
      // shown below
    }
  }

  return (
    <div className="page">
      {s.prep_id ? (
        <Link to={`/interview/${s.prep_id}`} className="small">
          ← Back to prep
        </Link>
      ) : null}
      <header className="page-head">
        <h1>Practice interview</h1>
        <p className="muted">
          {s.target_title}
          {s.target_company ? ` at ${s.target_company}` : ""} · question {Math.min(answered + 1, s.max_questions)} of {s.max_questions}
        </p>
      </header>
      <ol className="chat-messages">
        {s.turns.map((t, i) => (
          <li key={i} className={`chat-bubble ${t.role === "candidate" ? "chat-user" : "chat-assistant"}`}>
            {t.role === "interviewer" && (t.feedback || t.strengths?.length || t.improvements?.length) ? (
              <div className="feedback">
                {t.rating ? <p className="small kind-tag">Answer rating: {t.rating}/5</p> : null}
                {t.feedback ? <p className="plain-text">{t.feedback}</p> : null}
                {t.strengths?.length ? (
                  <ul className="reason-list ok">
                    {t.strengths.map((x, j) => (
                      <li key={j}>{x}</li>
                    ))}
                  </ul>
                ) : null}
                {t.improvements?.length ? (
                  <ul className="reason-list gap">
                    {t.improvements.map((x, j) => (
                      <li key={j}>{x}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
            {t.text ? <p className={t.role === "interviewer" ? "question" : "plain-text"}>{t.text}</p> : null}
          </li>
        ))}
      </ol>
      {s.status === "active" ? (
        <form className="chat-input" onSubmit={submit}>
          <label htmlFor="answer" className="sr-only">
            Your answer
          </label>
          <textarea
            id="answer"
            rows={5}
            maxLength={6000}
            value={answer}
            placeholder="Answer as you would in the interview. Situation, Task, Action, Result."
            disabled={m.answer.isPending}
            onChange={(e) => setAnswer(e.target.value)}
          />
          <Button type="submit" busy={m.answer.isPending} disabled={!answer.trim()}>
            Answer
          </Button>
        </form>
      ) : (
        <Notice tone="success">That&apos;s the end of this practice interview.</Notice>
      )}
      {m.answer.error ? <Notice>{errorMessage(m.answer.error)}</Notice> : null}
    </div>
  );
}
