// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, FullPageLoader, Notice, SelectField, TextAreaField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  docItems,
  exportUrl,
  isResume,
  useAtsCheck,
  useDiff,
  useDocument,
  useMaterialsMutations,
  type DocDetail,
  type DocItem,
  type LetterContent,
} from "../../lib/materials";

export function DocumentPage() {
  const { id = "" } = useParams();
  const doc = useDocument(id);
  if (doc.isPending) return <FullPageLoader />;
  if (doc.isError) {
    return (
      <div className="page">
        <Notice>{errorMessage(doc.error)}</Notice>
        <Link to="/materials">Back to materials</Link>
      </div>
    );
  }
  return <DocumentView doc={doc.data} />;
}

const STATUS: Record<DocItem["status"], { label: string; cls: string }> = {
  linked: { label: "Linked", cls: "badge-ok" },
  approved: { label: "Approved by you", cls: "" },
  flagged: { label: "Needs review", cls: "badge-warn" },
};

function ItemRow({ doc, item }: { doc: DocDetail; item: DocItem }) {
  const m = useMaterialsMutations();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.text);
  const [showSources, setShowSources] = useState(item.status === "flagged");
  const s = STATUS[item.status];
  const patch = (action: "approve" | "edit" | "remove") =>
    m.patchItem.mutateAsync({ docId: doc.id, itemId: item.id, action, text }).then(() => setEditing(false));

  return (
    <li className={`doc-item doc-item-${item.status}`}>
      {editing ? (
        <div className="stack-sm">
          <TextAreaField label="Edit" rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="row">
            <Button busy={m.patchItem.isPending} onClick={() => void patch("edit")}>
              Save
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="doc-item-main">
            <p className="doc-item-text">{item.text}</p>
            <span className={`badge ${s.cls}`}>{s.label}</span>
          </div>
          {item.reason && item.status !== "linked" ? <p className="small text-danger">{item.reason}</p> : null}
          <div className="row doc-item-actions">
            <button className="link-btn small" aria-expanded={showSources} onClick={() => setShowSources(!showSources)}>
              {showSources ? "Hide sources" : `Sources (${item.fact_ids.length})`}
            </button>
            <button className="link-btn small" onClick={() => setEditing(true)}>
              Edit
            </button>
            {item.status === "flagged" ? (
              <button className="link-btn small" onClick={() => void patch("approve")}>
                Approve as true
              </button>
            ) : null}
            <button className="link-btn small" onClick={() => void patch("remove")}>
              Remove
            </button>
          </div>
          {showSources ? (
            <ul className="sources">
              {item.fact_ids.length ? (
                item.fact_ids.map((fid) => <li key={fid}>{doc.sources[fid] ?? "Unknown fact"}</li>)
              ) : (
                <li className="muted">No verified fact cited.</li>
              )}
            </ul>
          ) : null}
        </>
      )}
    </li>
  );
}

function ResumeBody({ doc }: { doc: DocDetail }) {
  if (!isResume(doc)) return null;
  const c = doc.content;
  return (
    <div className="doc">
      {c.summary ? (
        <section>
          <h2 className="doc-heading">Summary</h2>
          <ul className="doc-items">
            <ItemRow doc={doc} item={c.summary} />
          </ul>
        </section>
      ) : null}
      {c.experience.length ? (
        <section>
          <h2 className="doc-heading">Experience</h2>
          {c.experience.map((e) => (
            <div key={e.fact_id} className="doc-entry">
              <p>
                <strong>{e.title}</strong>
                {e.employer ? `, ${e.employer}` : ""}
              </p>
              <p className="muted small">{[e.location, e.dates].filter(Boolean).join(" | ")}</p>
              <ul className="doc-items">
                {e.bullets.map((b) => (
                  <ItemRow key={b.id} doc={doc} item={b} />
                ))}
              </ul>
            </div>
          ))}
        </section>
      ) : null}
      {c.projects.length ? (
        <section>
          <h2 className="doc-heading">Projects</h2>
          {c.projects.map((p) => (
            <div key={p.fact_id} className="doc-entry">
              <p>
                <strong>{p.name}</strong>
                {p.role ? ` | ${p.role}` : ""}
              </p>
              <ul className="doc-items">
                {p.bullets.map((b) => (
                  <ItemRow key={b.id} doc={doc} item={b} />
                ))}
              </ul>
            </div>
          ))}
        </section>
      ) : null}
      {c.education.length ? (
        <section>
          <h2 className="doc-heading">Education</h2>
          {c.education.map((e) => (
            <p key={e.fact_id}>{e.text}</p>
          ))}
        </section>
      ) : null}
      {c.certifications.length ? (
        <section>
          <h2 className="doc-heading">Certifications</h2>
          {c.certifications.map((e) => (
            <p key={e.fact_id}>{e.text}</p>
          ))}
        </section>
      ) : null}
      {c.skills.length ? (
        <section>
          <h2 className="doc-heading">Skills</h2>
          {c.skills.map((g) => (
            <p key={g.category}>
              <strong>{g.category}:</strong> {g.names.join(", ")}
            </p>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function LetterBody({ doc }: { doc: DocDetail }) {
  const c = doc.content as LetterContent;
  return (
    <div className="doc">
      <p className="muted small">Greeting, date, and sign-off are added when you export.</p>
      <ul className="doc-items">
        {c.paragraphs.map((p) => (
          <ItemRow key={p.id} doc={doc} item={p} />
        ))}
      </ul>
    </div>
  );
}

function DiffCard({ doc }: { doc: DocDetail }) {
  const [open, setOpen] = useState(false);
  const diff = useDiff(doc.id, open);
  return (
    <Card
      title="Compared with your master resume"
      actions={
        <Button variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"}
        </Button>
      }
    >
      {open && diff.data ? (
        <>
          <p className="muted small">
            {diff.data.added} line{diff.data.added === 1 ? "" : "s"} added, {diff.data.removed} removed.
          </p>
          <pre className="diff">
            {diff.data.ops.map((o, i) => (
              <span key={i} className={`diff-${o.op}`}>
                {o.op === "added" ? "+ " : o.op === "removed" ? "- " : "  "}
                {o.text}
                {"\n"}
              </span>
            ))}
          </pre>
        </>
      ) : null}
      {open && diff.isError ? <Notice>{errorMessage(diff.error)}</Notice> : null}
    </Card>
  );
}

function ReviewCard({ doc }: { doc: DocDetail }) {
  const m = useMaterialsMutations();
  const review = doc.content.review;
  const hasJob = Boolean(doc.posting_id || doc.application_id);
  if (!hasJob) return null;
  const changes = review?.changes ?? [];
  const missing = review?.missing ?? [];
  const canAdd = missing.filter((x) => x.supported);
  const gaps = missing.filter((x) => !x.supported);
  return (
    <Card
      title="Second opinion"
      description="A separate AI pass reads it like a recruiter would and tightens weak lines. Rewrites that don't match your facts are thrown away."
    >
      {review?.error ? <Notice tone="info">The review didn&apos;t run: {review.error}</Notice> : null}
      {review?.summary ? <p>{review.summary}</p> : null}
      {review && !review.error ? (
        <p className="muted small">
          {changes.length ? `${changes.length} line${changes.length === 1 ? "" : "s"} improved` : "No lines needed changing"}
          {review.discarded?.length ? `; ${review.discarded.length} suggestion${review.discarded.length === 1 ? "" : "s"} rejected for adding things your facts don't say` : ""}.
        </p>
      ) : null}
      {changes.length ? (
        <details className="review-changes">
          <summary className="small">What changed</summary>
          <ul className="stack-sm">
            {changes.map((c) => (
              <li key={c.item_id} className="small">
                <span className="diff-removed">{c.before}</span>
                <br />
                <span className="diff-added">{c.after}</span>
                {c.why ? <span className="muted"> ({c.why})</span> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {canAdd.length ? (
        <p className="small">
          <strong>The job asks for, and your facts support:</strong> {canAdd.map((x) => x.requirement).join(", ")}. Consider
          making these visible.
        </p>
      ) : null}
      {gaps.length ? (
        <p className="small">
          <strong>Real gaps</strong> (not in your facts, so not added): {gaps.map((x) => x.requirement).join(", ")}.
        </p>
      ) : null}
      {m.review.error ? <Notice>{errorMessage(m.review.error)}</Notice> : null}
      <div>
        <Button variant="secondary" busy={m.review.isPending} onClick={() => m.review.mutate(doc.id)}>
          {review && !review.error ? "Review again" : "Get a second opinion"}
        </Button>
      </div>
    </Card>
  );
}

function AtsCard({ doc, format }: { doc: DocDetail; format: "pdf" | "docx" }) {
  const [run, setRun] = useState(false);
  const check = useAtsCheck(doc.id, format, run);
  const [showText, setShowText] = useState(false);
  const r = check.data;
  return (
    <Card
      title="Applicant tracking check"
      description="Reads your exported file the way screening software does: just the text."
    >
      {!run ? (
        <div>
          <Button variant="secondary" onClick={() => setRun(true)}>
            Check the {format === "pdf" ? "PDF" : "Word file"}
          </Button>
        </div>
      ) : null}
      {check.isFetching ? <p className="muted small">Reading the file…</p> : null}
      {check.isError ? <Notice>{errorMessage(check.error)}</Notice> : null}
      {r ? (
        <div className="stack-sm">
          <ul className="ats-checks">
            {r.checks.map((c) => (
              <li key={c.id} className={c.ok ? "ats-ok" : "ats-bad"}>
                <span aria-hidden="true">{c.ok ? "✓" : "!"}</span> {c.label}
                {c.detail ? <span className="muted small"> — {c.detail}</span> : null}
              </li>
            ))}
          </ul>
          {r.keywords ? (
            <div className="stack-sm">
              <p>
                <strong>Keyword match: {r.keywords.score ?? 0}%</strong>{" "}
                <span className="muted small">
                  ({r.keywords.covered.length} of {r.keywords.total} terms from the posting)
                </span>
              </p>
              {r.keywords.could_add.length ? (
                <p className="small">
                  <strong>In your vault but not on this page:</strong> {r.keywords.could_add.join(", ")}
                </p>
              ) : null}
              {r.keywords.gaps.length ? (
                <p className="small muted">
                  <strong>Not in your facts</strong> (don&apos;t add unless true): {r.keywords.gaps.join(", ")}
                </p>
              ) : null}
            </div>
          ) : null}
          <button className="link-btn small" aria-expanded={showText} onClick={() => setShowText(!showText)}>
            {showText ? "Hide" : "Show"} what the software reads
          </button>
          {showText ? <pre className="diff">{r.text}</pre> : null}
          {r.flagged ? (
            <p className="muted small">Checked with {r.flagged} flagged line(s) included; review them before sending.</p>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

function DocumentView({ doc }: { doc: DocDetail }) {
  const m = useMaterialsMutations();
  const navigate = useNavigate();
  const [format, setFormat] = useState<"pdf" | "docx">("pdf");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const items = docItems(doc);
  const flagged = items.filter((i) => i.status === "flagged").length;

  return (
    <div className="page page-wide">
      <Link to="/materials" className="small">
        ← Materials
      </Link>
      <header className="app-head">
        <div className="page-head">
          <h1>{doc.title}</h1>
          <p className="muted">
            {doc.method === "ai" ? `Written by ${doc.model}` : "Built straight from your vault"}: {items.length} line
            {items.length === 1 ? "" : "s"}, each traced to your verified facts.
          </p>
        </div>
      </header>
      {doc.stale ? <Notice tone="info">Your vault changed after this was created. Generate a new one to include the changes.</Notice> : null}
      {flagged ? (
        <Notice tone="error">
          {flagged} item{flagged === 1 ? "" : "s"} can&apos;t be traced to your verified facts. Edit, approve, or remove{" "}
          {flagged === 1 ? "it" : "them"} before exporting.
        </Notice>
      ) : (
        <Notice tone="success">Every line is linked to a verified fact or approved by you.</Notice>
      )}
      <div className="app-grid">
        <Card title={doc.kind === "resume" ? "Resume" : "Cover letter"}>
          {doc.kind === "resume" ? <ResumeBody doc={doc} /> : <LetterBody doc={doc} />}
        </Card>
        <div className="stack">
          <Card title="Export" description="Simple single-column layouts that applicant tracking systems read well.">
            <SelectField
              label="Format"
              value={format}
              options={[
                { value: "pdf", label: "PDF" },
                { value: "docx", label: "Word (.docx)" },
              ]}
              onChange={(e) => setFormat(e.target.value as "pdf" | "docx")}
            />
            <div className="row">
              {flagged ? (
                <Button disabled>Download</Button>
              ) : (
                <a className="btn btn-primary" href={exportUrl(doc.id, format)} download>
                  Download
                </a>
              )}
              {doc.application_id ? (
                <Button
                  variant="secondary"
                  disabled={Boolean(flagged)}
                  busy={m.attach.isPending}
                  onClick={() => m.attach.mutate({ id: doc.id, format })}
                >
                  Attach to tracker
                </Button>
              ) : null}
            </div>
            {m.attach.data ? (
              <Notice tone="success">
                Saved as {m.attach.data.filename} on the{" "}
                <Link to={`/tracker/applications/${m.attach.data.application_id}`}>application</Link>.
              </Notice>
            ) : null}
            {m.attach.error ? <Notice>{errorMessage(m.attach.error)}</Notice> : null}
          </Card>
          <AtsCard key={format} doc={doc} format={format} />
          <ReviewCard doc={doc} />
          {doc.kind === "resume" && !doc.is_master ? <DiffCard doc={doc} /> : null}
          {doc.posting_id ? (
            <Card title="Job">
              <Link to={`/jobs/postings/${doc.posting_id}`}>
                {doc.target_title}
                {doc.target_company ? ` at ${doc.target_company}` : ""}
              </Link>
            </Card>
          ) : null}
          <Card title="Delete">
            {confirmDelete ? (
              <div className="confirm-row">
                <span className="small">Delete this document?</span>
                <Button
                  variant="danger"
                  onClick={async () => {
                    await m.remove.mutateAsync(doc.id);
                    navigate("/materials");
                  }}
                >
                  Delete
                </Button>
                <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
                  Keep
                </Button>
              </div>
            ) : (
              <Button variant="secondary" onClick={() => setConfirmDelete(true)}>
                Delete…
              </Button>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
