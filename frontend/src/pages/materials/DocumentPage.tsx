// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, FullPageLoader, Notice, SelectField, TextAreaField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  docItems,
  exportUrl,
  isResume,
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
            {doc.method === "ai" ? `Written with ${doc.model}` : "Built from templates"} from {items.length} item
            {items.length === 1 ? "" : "s"}, each linked to your verified facts.
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
