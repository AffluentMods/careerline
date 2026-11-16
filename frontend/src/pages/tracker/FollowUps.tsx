// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, Field, Notice, TextAreaField } from "../../components/ui";
import { useAiSettings } from "../../lib/ai";
import { errorMessage } from "../../lib/api";
import { mailtoLink, useFollowUpMutations, useFollowUps, type FollowUpDraft, type FollowUpItem } from "../../lib/followups";

function DraftEditor({ item, onClose }: { item: FollowUpItem; onClose: () => void }) {
  const m = useFollowUpMutations();
  const ai = useAiSettings();
  const aiOn = Boolean(ai.data && ai.data.provider !== "none");
  const [draft, setDraft] = useState<FollowUpDraft | null>(null);
  const [copied, setCopied] = useState(false);

  async function make(method: "rules" | "ai") {
    setDraft(await m.draft.mutateAsync({ id: item.application_id, method }));
  }

  if (!draft) {
    return (
      <div className="stack-sm">
        <div className="row">
          <Button busy={m.draft.isPending && m.draft.variables?.method === "rules"} onClick={() => void make("rules")}>
            Draft a short note
          </Button>
          {aiOn ? (
            <Button
              variant="secondary"
              busy={m.draft.isPending && m.draft.variables?.method === "ai"}
              onClick={() => void make("ai")}
            >
              Write it with AI
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
        {m.draft.error ? <Notice>{errorMessage(m.draft.error)}</Notice> : null}
      </div>
    );
  }
  const update = (patch: Partial<FollowUpDraft>) => setDraft({ ...draft, ...patch });
  return (
    <div className="stack-sm">
      {draft.warnings.map((w) => (
        <Notice key={w} tone="info">
          {w}
        </Notice>
      ))}
      <Field label="Subject" value={draft.subject} onChange={(e) => update({ subject: e.target.value })} />
      <TextAreaField label="Message" rows={8} value={draft.body} onChange={(e) => update({ body: e.target.value })} />
      <p className="muted small">
        CareerLine never sends email. Open it in your email app{draft.to ? ` (to ${draft.to})` : ""}, send it, then mark it sent
        here so the tracker knows.
      </p>
      <div className="row">
        <a className="btn btn-primary" href={mailtoLink(draft)}>
          Open in email
        </a>
        <Button
          variant="secondary"
          onClick={async () => {
            await navigator.clipboard?.writeText(`Subject: ${draft.subject}\n\n${draft.body}`);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button
          variant="secondary"
          busy={m.sent.isPending}
          onClick={async () => {
            await m.sent.mutateAsync({ id: item.application_id, subject: draft.subject, body: draft.body });
            onClose();
          }}
        >
          Mark as sent
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      {m.sent.error ? <Notice>{errorMessage(m.sent.error)}</Notice> : null}
    </div>
  );
}

function Row({ item }: { item: FollowUpItem }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="entry">
      <div className="entry-main">
        <div className="entry-text">
          <Link to={`/tracker/applications/${item.application_id}`} className="entry-title">
            {item.title}
            {item.company ? ` at ${item.company}` : ""}
          </Link>
          <p className="muted small">
            No news for {item.days_quiet} days
            {item.follow_ups_sent ? ` · followed up ${item.follow_ups_sent}×` : ""}
            {item.contact_name ? ` · ${item.contact_name}` : ""}
          </p>
        </div>
        {open ? null : (
          <div className="entry-actions">
            <Button variant="secondary" onClick={() => setOpen(true)}>
              {item.follow_ups_sent ? "Follow up again" : "Follow up"}
            </Button>
          </div>
        )}
      </div>
      {open ? <DraftEditor item={item} onClose={() => setOpen(false)} /> : null}
    </li>
  );
}

/** Applications that have gone quiet, with a one-click follow-up draft. */
export function FollowUpsList({ limit }: { limit?: number }) {
  const list = useFollowUps();
  if (list.isError) return <Notice>{errorMessage(list.error)}</Notice>;
  const items = list.data ?? [];
  if (list.isPending) return <p className="muted small">Loading…</p>;
  if (!items.length) {
    return <p className="muted small">Nothing to follow up on. Applications show up here after 10 quiet days.</p>;
  }
  const shown = limit ? items.slice(0, limit) : items;
  return (
    <>
      <ul className="entry-list" aria-label="Follow-ups">
        {shown.map((i) => (
          <Row key={i.application_id} item={i} />
        ))}
      </ul>
      {limit && items.length > limit ? <p className="muted small">and {items.length - limit} more</p> : null}
    </>
  );
}
