// SPDX-License-Identifier: Apache-2.0
import { Link, useNavigate } from "react-router-dom";
import { Button, Card, Notice } from "../../components/ui";
import { useAiSettings } from "../../lib/ai";
import { errorMessage } from "../../lib/api";
import { useDocuments, useMaterialsMutations, type GenerateInput } from "../../lib/materials";

/** "Tailored materials" for one posting or application: generate, then list what exists. */
export function MaterialsCard({ postingId, applicationId }: { postingId?: string; applicationId?: string }) {
  const filter = postingId ? { posting_id: postingId } : { application_id: applicationId ?? "" };
  const docs = useDocuments(filter);
  const ai = useAiSettings();
  const m = useMaterialsMutations();
  const navigate = useNavigate();
  const aiOn = Boolean(ai.data && ai.data.provider !== "none");
  const target = postingId ? { posting_id: postingId } : { application_id: applicationId };

  async function make(kind: GenerateInput["kind"], method: GenerateInput["method"]) {
    try {
      const doc = await m.generate.mutateAsync({ kind, method, ...target });
      navigate(`/materials/${doc.id}`);
    } catch {
      // shown below
    }
  }
  const busy = (kind: string, method: string) =>
    m.generate.isPending && m.generate.variables?.kind === kind && m.generate.variables?.method === method;

  return (
    <Card
      title="Tailored materials"
      description="Built only from your verified vault facts. Every line is linked to the fact it came from."
    >
      <div className="row">
        <Button variant="secondary" busy={busy("resume", "rules")} onClick={() => void make("resume", "rules")}>
          Resume
        </Button>
        {aiOn ? (
          <Button variant="secondary" busy={busy("resume", "ai")} onClick={() => void make("resume", "ai")}>
            Resume with AI
          </Button>
        ) : null}
        <Button variant="secondary" busy={busy("cover_letter", "rules")} onClick={() => void make("cover_letter", "rules")}>
          Cover letter
        </Button>
        {aiOn ? (
          <Button variant="secondary" busy={busy("cover_letter", "ai")} onClick={() => void make("cover_letter", "ai")}>
            Cover letter with AI
          </Button>
        ) : null}
      </div>
      {m.generate.error ? <Notice>{errorMessage(m.generate.error)}</Notice> : null}
      {docs.data?.length ? (
        <ul className="files">
          {docs.data.map((d) => (
            <li key={d.id} className="file">
              <Link to={`/materials/${d.id}`}>{d.title}</Link>
              <span className="muted small">
                {" "}
                {d.method === "ai" ? "AI" : "Template"} · {new Date(d.created_at).toLocaleDateString()}
                {d.flagged ? ` · ${d.flagged} to review` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
