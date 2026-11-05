// SPDX-License-Identifier: Apache-2.0
import { Link, useNavigate } from "react-router-dom";
import { Button, FullPageLoader, Notice } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useDocuments, useMaterialsMutations } from "../../lib/materials";

export function MaterialsPage() {
  const docs = useDocuments();
  const m = useMaterialsMutations();
  const navigate = useNavigate();

  return (
    <div className="page">
      <header className="page-head">
        <h1>Materials</h1>
        <p className="muted">
          Resumes and cover letters built only from your verified vault facts. Anything that can&apos;t be traced to a fact is
          flagged, and must be fixed or approved by you before it can be exported.
        </p>
      </header>
      <div className="row-between tab-toolbar">
        <p className="muted small">To tailor for a job, open the job (or its tracker entry) and choose Resume or Cover letter.</p>
        <Button
          busy={m.generate.isPending}
          onClick={async () => {
            try {
              const doc = await m.generate.mutateAsync({ kind: "resume", method: "rules" });
              navigate(`/materials/${doc.id}`);
            } catch {
              // shown below
            }
          }}
        >
          Build master resume
        </Button>
      </div>
      {m.generate.error ? <Notice>{errorMessage(m.generate.error)}</Notice> : null}
      {docs.isPending ? <FullPageLoader /> : null}
      {docs.isError ? <Notice>{errorMessage(docs.error)}</Notice> : null}
      {docs.data && docs.data.length === 0 ? (
        <div className="empty">
          <p>No documents yet. Start with a master resume: everything verified in your vault, ready to export.</p>
        </div>
      ) : null}
      {docs.data?.length ? (
        <ul className="entry-list">
          {docs.data.map((d) => (
            <li key={d.id} className="entry">
              <div className="entry-main">
                <div className="entry-text">
                  <div className="entry-title-row">
                    <span className="kind-tag">{d.kind === "resume" ? "Resume" : "Cover letter"}</span>
                    <Link to={`/materials/${d.id}`} className="entry-title">
                      {d.title}
                    </Link>
                    {d.flagged ? (
                      <span className="badge badge-warn">{d.flagged} to review</span>
                    ) : (
                      <span className="badge badge-ok">Fact-checked</span>
                    )}
                    {d.stale ? <span className="badge">Vault changed since</span> : null}
                  </div>
                  <p className="muted small">
                    {d.method === "ai" ? `AI (${d.model})` : "Template"} · created {new Date(d.created_at).toLocaleString()}
                    {d.exported_at ? ` · exported ${new Date(d.exported_at).toLocaleDateString()}` : ""}
                  </p>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
