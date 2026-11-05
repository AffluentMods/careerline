// SPDX-License-Identifier: Apache-2.0
import { useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, Checkbox, Field, FullPageLoader, Notice, SelectField, TextAreaField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  addDays,
  downloadUrl,
  formatBytes,
  formatDate,
  formatDue,
  formatMoney,
  formatPay,
  localToday,
  STAGE_LABEL,
  useApplication,
  useContacts,
  useTrackerMutations,
  type ApplicationDetail,
  type Attachment,
  type ContactRole,
  type EventData,
  type EventKind,
  type ReferralStatus,
  type ReminderKind,
  type TimelineEvent,
} from "../../lib/tracker";
import { InterviewCard } from "../interview/InterviewPages";
import { MaterialsCard } from "../materials/MaterialsCard";
import { ApplicationForm } from "./ApplicationForm";
import { FollowUpOffer, MoveMenu, useStageMove } from "./Board";
import { OfferForm } from "./OfferForm";
import { webHref } from "../../lib/links";

export const ROLE_LABEL: Record<ContactRole, string> = {
  recruiter: "Recruiter",
  hiring_manager: "Hiring manager",
  interviewer: "Interviewer",
  referrer: "Referrer",
  other: "Other",
};
const ROLE_OPTIONS = Object.entries(ROLE_LABEL).map(([value, label]) => ({ value, label }));

const REFERRAL_LABEL: Record<ReferralStatus, string> = {
  to_ask: "To ask",
  asked: "Asked",
  agreed: "Agreed",
  submitted: "Submitted",
  declined: "Declined",
};

const EVENT_LABEL: Record<EventKind, string> = {
  stage_change: "Stage",
  note: "Note",
  sent: "Sent",
  interview: "Interview",
  contact: "Contact",
  other: "Other",
};

export const REMINDER_KINDS: { value: ReminderKind; label: string }[] = [
  { value: "follow_up", label: "Follow-up" },
  { value: "deadline", label: "Deadline" },
  { value: "interview", label: "Interview" },
  { value: "task", label: "Task" },
];

export function ApplicationPage() {
  const { id = "" } = useParams();
  const detail = useApplication(id);
  if (detail.isPending) return <FullPageLoader />;
  if (detail.isError) {
    return (
      <div className="page">
        <Notice>{errorMessage(detail.error)}</Notice>
        <Link to="/tracker/board">Back to the tracker</Link>
      </div>
    );
  }
  return <ApplicationView detail={detail.data} />;
}

function ApplicationView({ detail }: { detail: ApplicationDetail }) {
  const app = detail.application;
  const m = useTrackerMutations();
  const navigate = useNavigate();
  const { move, followUp, clearFollowUp, error } = useStageMove();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const pay = formatPay(app);

  return (
    <div className="page page-wide">
      <Link to="/tracker/board" className="small">
        ← Tracker
      </Link>
      <header className="app-head">
        <div className="page-head">
          <h1>{app.title}</h1>
          <p className="muted">{[app.company, app.location, pay].filter(Boolean).join(" · ")}</p>
        </div>
        <div className="row">
          <span className="badge">{STAGE_LABEL[app.stage]}</span>
          <MoveMenu app={app} onMove={(s) => void move(app, s)} />
        </div>
      </header>
      {followUp ? <FollowUpOffer app={followUp} onClose={clearFollowUp} /> : null}
      {error ? <Notice>{error}</Notice> : null}

      <div className="app-grid">
        <div className="stack">
          <Card
            title="Details"
            actions={
              !editing ? (
                <Button variant="ghost" onClick={() => setEditing(true)}>
                  Edit
                </Button>
              ) : null
            }
          >
            {editing ? (
              <ApplicationForm
                initial={app}
                submitLabel="Save"
                onCancel={() => setEditing(false)}
                onSubmit={async (body) => {
                  await m.updateApplication.mutateAsync({ id: app.id, body });
                  setEditing(false);
                }}
              />
            ) : (
              <dl className="details">
                {(
                  [
                    ["Applied", formatDate(app.applied_on)],
                    ["Apply by", formatDate(app.deadline)],
                    ["Work mode", app.work_mode ? app.work_mode[0]!.toUpperCase() + app.work_mode.slice(1) : ""],
                    ["Found via", app.source],
                    ["Priority", app.priority === "normal" ? "" : app.priority === "high" ? "High" : "Low"],
                  ] as const
                )
                  .filter(([, value]) => value)
                  .map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                {app.job_url ? (
                  <div>
                    <dt>Posting</dt>
                    <dd>
                      <a href={webHref(app.job_url)} target="_blank" rel="noopener noreferrer">
                        {app.job_url}
                      </a>
                    </dd>
                  </div>
                ) : null}
              </dl>
            )}
            {!editing && app.description ? (
              <details className="description">
                <summary>Job description</summary>
                {/* Pasted text from elsewhere: always shown as plain text. */}
                <pre className="plain-text">{app.description}</pre>
              </details>
            ) : null}
          </Card>

          <Timeline detail={detail} />
        </div>

        <div className="stack">
          <MaterialsCard applicationId={app.id} />
          <InterviewCard applicationId={app.id} />
          <RemindersCard detail={detail} />
          <PeopleCard detail={detail} />
          <OfferCard detail={detail} />
          <Card title="Delete application">
            {confirmDelete ? (
              <div className="confirm-row">
                <span className="small">
                  Delete this application with its timeline, files, reminders, and offer? Contacts are kept.
                </span>
                <Button
                  variant="danger"
                  busy={m.deleteApplication.isPending}
                  onClick={async () => {
                    await m.deleteApplication.mutateAsync(app.id);
                    navigate("/tracker/board");
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

// --- timeline ----------------------------------------------------------------------------

function FileLink({ file, onDelete }: { file: Attachment; onDelete?: () => void }) {
  return (
    <li className="file">
      <a href={downloadUrl(file.id)} download>
        {file.filename}
      </a>
      <span className="muted small"> {formatBytes(file.size_bytes)}</span>
      {onDelete ? (
        <button className="link-btn small" onClick={onDelete} aria-label={`Delete ${file.filename}`}>
          Remove
        </button>
      ) : null}
    </li>
  );
}

function eventSummary(e: TimelineEvent): string {
  const d = e.data;
  const parts = [
    d.materials,
    d.interview_format ? `${d.interview_format[0]!.toUpperCase()}${d.interview_format.slice(1)} interview` : "",
    d.channel ? d.channel.replace("_", " ") : "",
    d.with_whom ? `with ${d.with_whom}` : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

function nowLocalInput(): string {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

function Timeline({ detail }: { detail: ApplicationDetail }) {
  const app = detail.application;
  const m = useTrackerMutations();
  const [kind, setKind] = useState<Exclude<EventKind, "stage_change">>("note");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [when, setWhen] = useState(nowLocalInput);
  const [data, setData] = useState<EventData>({});
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fileKey, setFileKey] = useState(0);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const event = await m.addEvent.mutateAsync({
        applicationId: app.id,
        body: { kind, title: title.trim(), body: body.trim(), occurred_at: new Date(when).toISOString(), data },
      });
      if (file) await m.upload.mutateAsync({ applicationId: app.id, file, eventId: event.id });
      setTitle("");
      setBody("");
      setData({});
      setFile(null);
      setFileKey((k) => k + 1);
      setWhen(nowLocalInput());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function uploadLoose(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setError(null);
    try {
      await m.upload.mutateAsync({ applicationId: app.id, file: f });
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card title="Timeline" description="What you sent, interviews, calls, and notes. Stage changes are added for you.">
      <form className="entry-form timeline-form" onSubmit={submit}>
        <div className="form-grid">
          <SelectField
            label="Add"
            value={kind}
            options={[
              { value: "note", label: "Note" },
              { value: "sent", label: "Sent something" },
              { value: "interview", label: "Interview" },
              { value: "contact", label: "Call or message" },
              { value: "other", label: "Other" },
            ]}
            onChange={(e) => {
              setKind(e.target.value as typeof kind);
              setData({});
            }}
          />
          <Field label="When" type="datetime-local" value={when} required onChange={(e) => setWhen(e.target.value)} />
          {kind === "sent" ? (
            <div className="span-2">
              <Field
                label="What you sent"
                maxLength={200}
                hint="Which resume version, cover letter, portfolio…"
                value={data.materials ?? ""}
                onChange={(e) => setData({ ...data, materials: e.target.value })}
              />
            </div>
          ) : null}
          {kind === "interview" ? (
            <SelectField
              label="Format"
              value={data.interview_format ?? ""}
              placeholder="Not set"
              options={[
                { value: "phone", label: "Phone" },
                { value: "video", label: "Video" },
                { value: "onsite", label: "On-site" },
                { value: "other", label: "Other" },
              ]}
              onChange={(e) =>
                setData({ ...data, interview_format: (e.target.value || undefined) as EventData["interview_format"] })
              }
            />
          ) : null}
          {kind === "contact" ? (
            <SelectField
              label="How"
              value={data.channel ?? ""}
              placeholder="Not set"
              options={[
                { value: "email", label: "Email" },
                { value: "phone", label: "Phone" },
                { value: "message", label: "Message" },
                { value: "in_person", label: "In person" },
                { value: "other", label: "Other" },
              ]}
              onChange={(e) => setData({ ...data, channel: (e.target.value || undefined) as EventData["channel"] })}
            />
          ) : null}
          {kind === "interview" || kind === "contact" ? (
            <Field
              label="With (optional)"
              maxLength={200}
              value={data.with_whom ?? ""}
              onChange={(e) => setData({ ...data, with_whom: e.target.value })}
            />
          ) : null}
          <div className="span-2">
            <Field label="Title (optional)" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="span-2">
            <TextAreaField label="Details (optional)" rows={3} maxLength={10_000} value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
          <div className="span-2 field">
            <label htmlFor="event-file">Attach a file (optional)</label>
            <input
              key={fileKey}
              id="event-file"
              type="file"
              accept=".pdf,.docx,.png,.jpg,.jpeg,.txt"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <p className="field-hint">PDF, Word, PNG, JPEG, or text, up to 10 MB. Stored encrypted.</p>
          </div>
        </div>
        {error ? <Notice>{error}</Notice> : null}
        <Button type="submit" busy={busy}>
          Add to timeline
        </Button>
      </form>

      <ol className="timeline">
        {detail.events.map((e) => (
          <li key={e.id} className={`tl-item tl-${e.kind}`}>
            <div className="tl-head">
              <span className="kind-tag">{EVENT_LABEL[e.kind]}</span>
              <span className="tl-title">
                {e.kind === "stage_change" ? e.title : e.title || eventSummary(e) || EVENT_LABEL[e.kind]}
              </span>
              <time className="muted small" dateTime={e.occurred_at}>
                {new Date(e.occurred_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
              </time>
              {e.kind !== "stage_change" ? (
                <button
                  className="link-btn small"
                  aria-label={`Delete ${EVENT_LABEL[e.kind].toLowerCase()} from ${new Date(e.occurred_at).toLocaleDateString()}`}
                  onClick={() => m.deleteEvent.mutate(e.id)}
                >
                  Delete
                </button>
              ) : null}
            </div>
            {e.title && eventSummary(e) ? <p className="small muted">{eventSummary(e)}</p> : null}
            {e.body ? <p className="small plain-text">{e.body}</p> : null}
            {e.attachments.length ? (
              <ul className="files">
                {e.attachments.map((f) => (
                  <FileLink key={f.id} file={f} onDelete={() => m.deleteAttachment.mutate(f.id)} />
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>

      <div className="stack-sm">
        <h3 className="section-title">Other files</h3>
        {detail.attachments.length ? (
          <ul className="files">
            {detail.attachments.map((f) => (
              <FileLink key={f.id} file={f} onDelete={() => m.deleteAttachment.mutate(f.id)} />
            ))}
          </ul>
        ) : (
          <p className="muted small">None yet.</p>
        )}
        <label className="btn btn-secondary file-btn">
          Upload a file
          <input type="file" className="sr-only" accept=".pdf,.docx,.png,.jpg,.jpeg,.txt" onChange={(e) => void uploadLoose(e)} />
        </label>
      </div>
    </Card>
  );
}

// --- reminders ------------------------------------------------------------------------------

function RemindersCard({ detail }: { detail: ApplicationDetail }) {
  const app = detail.application;
  const m = useTrackerMutations();
  const today = localToday();
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<ReminderKind>("follow_up");
  const [due, setDue] = useState(addDays(today, 7));
  const [time, setTime] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await m.addReminder.mutateAsync({
        title: title.trim(),
        kind,
        due_on: due,
        due_time: time || null,
        application_id: app.id,
      });
      setAdding(false);
      setTitle("");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card
      title="Reminders"
      actions={
        !adding ? (
          <Button variant="ghost" onClick={() => setAdding(true)}>
            Add
          </Button>
        ) : null
      }
    >
      {adding ? (
        <form className="entry-form" onSubmit={submit}>
          <Field label="Remind me to" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
          <div className="form-grid">
            <SelectField label="Kind" value={kind} options={REMINDER_KINDS} onChange={(e) => setKind(e.target.value as ReminderKind)} />
            <Field label="On" type="date" required value={due} onChange={(e) => setDue(e.target.value)} />
            <Field label="At (optional)" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
          </div>
          {error ? <Notice>{error}</Notice> : null}
          <div className="row">
            <Button type="submit" busy={m.addReminder.isPending}>
              Add reminder
            </Button>
            <Button type="button" variant="ghost" onClick={() => setAdding(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
      {detail.reminders.length ? (
        <ul className="check-list">
          {detail.reminders.map((r) => (
            <li key={r.id} className={r.done_at ? "is-done" : undefined}>
              <Checkbox
                checked={Boolean(r.done_at)}
                onChange={(e) => m.updateReminder.mutate({ id: r.id, body: { done: e.target.checked } })}
                label={r.title}
                hint={r.done_at ? "Done" : formatDue(r, today)}
              />
            </li>
          ))}
        </ul>
      ) : !adding ? (
        <p className="muted small">No reminders.</p>
      ) : null}
    </Card>
  );
}

// --- people -----------------------------------------------------------------------------------

function PeopleCard({ detail }: { detail: ApplicationDetail }) {
  const app = detail.application;
  const m = useTrackerMutations();
  const contacts = useContacts();
  const [mode, setMode] = useState<"none" | "link" | "referral">("none");
  const [contactId, setContactId] = useState("");
  const [role, setRole] = useState<ContactRole>("recruiter");
  const [newName, setNewName] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [status, setStatus] = useState<ReferralStatus>("to_ask");
  const [followUp, setFollowUp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const linked = new Set(detail.contacts.map((c) => c.contact.id));
  const referred = new Set(detail.referrals.map((r) => r.contact_id));

  function reset() {
    setMode("none");
    setContactId("");
    setNewName("");
    setNewTitle("");
    setNewEmail("");
    setFollowUp("");
    setError(null);
  }

  async function resolveContact(): Promise<string> {
    if (contactId !== "new") return contactId;
    const created = await m.createContact.mutateAsync({
      name: newName.trim(),
      title: newTitle.trim(),
      email: newEmail.trim(),
      company: app.company,
    });
    return created.id;
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const id = await resolveContact();
      if (mode === "link") {
        await m.linkContact.mutateAsync({ applicationId: app.id, contactId: id, role });
      } else {
        await m.addReferral.mutateAsync({
          applicationId: app.id,
          body: { contact_id: id, status, follow_up_on: followUp || null },
        });
      }
      reset();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const options = (contacts.data ?? [])
    .filter((c) => (mode === "referral" ? !referred.has(c.id) : !linked.has(c.id)))
    .map((c) => ({ value: c.id, label: c.company ? `${c.name} (${c.company})` : c.name }));

  return (
    <Card title="People" description="Recruiters, interviewers, and who referred you.">
      {detail.contacts.length ? (
        <ul className="people">
          {detail.contacts.map(({ contact, role: r }) => (
            <li key={contact.id}>
              <div>
                <strong>{contact.name}</strong>
                <span className="muted small">
                  {" "}
                  · {ROLE_LABEL[r]}
                  {contact.title && contact.title.toLowerCase() !== ROLE_LABEL[r].toLowerCase()
                    ? ` · ${contact.title}`
                    : ""}
                </span>
                {contact.email ? (
                  <p className="small">
                    <a href={`mailto:${contact.email}`}>{contact.email}</a>
                  </p>
                ) : null}
              </div>
              <button
                className="link-btn small"
                aria-label={`Remove ${contact.name} from this application`}
                onClick={() => m.unlinkContact.mutate({ applicationId: app.id, contactId: contact.id })}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {detail.referrals.length ? (
        <div className="stack-sm">
          <h3 className="section-title">Referrals</h3>
          <ul className="people">
            {detail.referrals.map((r) => (
              <li key={r.id}>
                <div>
                  <strong>{r.contact_name}</strong>
                  {r.follow_up_on ? <span className="muted small"> · follow up {formatDate(r.follow_up_on)}</span> : null}
                </div>
                <select
                  aria-label={`Referral status for ${r.contact_name}`}
                  value={r.status}
                  onChange={(e) => m.updateReferral.mutate({ id: r.id, body: { status: e.target.value as ReferralStatus } })}
                >
                  {Object.entries(REFERRAL_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {mode === "none" ? (
        <div className="row">
          <Button variant="secondary" onClick={() => setMode("link")}>
            Add person
          </Button>
          <Button variant="secondary" onClick={() => setMode("referral")}>
            Add referral
          </Button>
        </div>
      ) : (
        <form className="entry-form" onSubmit={submit}>
          <SelectField
            label={mode === "referral" ? "Who's referring you" : "Person"}
            required
            value={contactId}
            placeholder="Choose…"
            options={[...options, { value: "new", label: "+ New contact" }]}
            onChange={(e) => setContactId(e.target.value)}
          />
          {contactId === "new" ? (
            <div className="form-grid">
              <Field label="Name" required maxLength={200} value={newName} onChange={(e) => setNewName(e.target.value)} />
              <Field label="Title (optional)" maxLength={200} value={newTitle} onChange={(e) => setNewTitle(e.target.value)} />
              <div className="span-2">
                <Field label="Email (optional)" type="email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
              </div>
            </div>
          ) : null}
          {mode === "link" ? (
            <SelectField label="Role" value={role} options={ROLE_OPTIONS} onChange={(e) => setRole(e.target.value as ContactRole)} />
          ) : (
            <div className="form-grid">
              <SelectField
                label="Status"
                value={status}
                options={Object.entries(REFERRAL_LABEL).map(([value, label]) => ({ value, label }))}
                onChange={(e) => setStatus(e.target.value as ReferralStatus)}
              />
              <Field label="Follow up on (optional)" type="date" value={followUp} onChange={(e) => setFollowUp(e.target.value)} />
            </div>
          )}
          {error ? <Notice>{error}</Notice> : null}
          <div className="row">
            <Button type="submit" busy={m.linkContact.isPending || m.addReferral.isPending || m.createContact.isPending}>
              {mode === "referral" ? "Add referral" : "Add person"}
            </Button>
            <Button type="button" variant="ghost" onClick={reset}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

// --- offer ------------------------------------------------------------------------------------

function OfferCard({ detail }: { detail: ApplicationDetail }) {
  const app = detail.application;
  const m = useTrackerMutations();
  const offer = detail.offer;
  const [editing, setEditing] = useState(false);
  if (!offer && app.stage !== "offer" && app.stage !== "accepted" && !editing) {
    return (
      <Card title="Offer">
        <p className="muted small">Add the offer details when you get one.</p>
        <Button variant="secondary" onClick={() => setEditing(true)}>
          Add offer
        </Button>
      </Card>
    );
  }
  if (!offer || editing) {
    return (
      <Card title="Offer">
        <OfferForm
          initial={offer}
          defaultCurrency={app.currency}
          onCancel={offer ? () => setEditing(false) : undefined}
          onSubmit={async (body) => {
            await m.saveOffer.mutateAsync({ applicationId: app.id, body });
            setEditing(false);
          }}
        />
      </Card>
    );
  }
  return (
    <Card
      title="Offer"
      actions={
        <Button variant="ghost" onClick={() => setEditing(true)}>
          Edit
        </Button>
      }
    >
      <dl className="details">
        <div>
          <dt>Base</dt>
          <dd>
            {formatMoney(offer.base_cents, offer.currency)}/{offer.base_period === "hour" ? "hr" : "yr"}
          </dd>
        </div>
        <div>
          <dt>Works out to</dt>
          <dd>
            {formatMoney(offer.annual_cents, offer.currency)}/yr · {formatMoney(offer.hourly_cents, offer.currency)}/hr
          </dd>
        </div>
        {offer.bonus_cents ? (
          <div>
            <dt>Total with bonus</dt>
            <dd>{formatMoney(offer.total_annual_cents, offer.currency)}/yr</dd>
          </div>
        ) : null}
        {offer.pto_days !== null ? (
          <div>
            <dt>PTO</dt>
            <dd>{offer.pto_days} days</dd>
          </div>
        ) : null}
        {offer.deadline ? (
          <div>
            <dt>Decide by</dt>
            <dd>{formatDate(offer.deadline)}</dd>
          </div>
        ) : null}
      </dl>
      <Link to="/tracker/offers" className="small">
        Compare offers
      </Link>
    </Card>
  );
}
