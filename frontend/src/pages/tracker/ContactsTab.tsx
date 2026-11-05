// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Button, Field, FullPageLoader, Notice, TextAreaField } from "../../components/ui";
import { errorMessage, fieldErrors } from "../../lib/api";
import { STAGE_LABEL, useContacts, useTrackerMutations, type Contact, type ContactInput } from "../../lib/tracker";
import { ROLE_LABEL } from "./ApplicationPage";
import { webHref } from "../../lib/links";

const EMPTY: ContactInput = { name: "", company: "", title: "", email: "", phone: "", linkedin_url: "", notes: "" };

function ContactForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: ContactInput;
  submitLabel: string;
  onSubmit: (body: ContactInput) => Promise<unknown>;
  onCancel: () => void;
}) {
  const [v, setV] = useState(initial);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const errs = fieldErrors(error);
  const set = (k: keyof ContactInput) => (value: string) => setV((cur) => ({ ...cur, [k]: value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ ...v, name: v.name.trim() });
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <form className="entry-form" onSubmit={submit}>
      <div className="form-grid">
        <Field label="Name" required maxLength={200} value={v.name} onChange={(e) => set("name")(e.target.value)} />
        <Field label="Company (optional)" maxLength={200} value={v.company} onChange={(e) => set("company")(e.target.value)} />
        <Field label="Title (optional)" maxLength={200} value={v.title} onChange={(e) => set("title")(e.target.value)} />
        <Field label="Email (optional)" type="email" value={v.email} hint={errs.email} onChange={(e) => set("email")(e.target.value)} />
        <Field label="Phone (optional)" type="tel" maxLength={50} value={v.phone} onChange={(e) => set("phone")(e.target.value)} />
        <Field
          label="LinkedIn or profile link (optional)"
          type="url"
          value={v.linkedin_url}
          hint={errs.linkedin_url ?? "https:// only"}
          onChange={(e) => set("linkedin_url")(e.target.value)}
        />
        <div className="span-2">
          <TextAreaField label="Notes (optional)" rows={2} value={v.notes} onChange={(e) => set("notes")(e.target.value)} />
        </div>
      </div>
      <p className="muted small">Email and phone are encrypted in the database.</p>
      {error ? <Notice>{errorMessage(error)}</Notice> : null}
      <div className="row">
        <Button type="submit" busy={busy}>
          {submitLabel}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function ContactRow({ contact }: { contact: Contact }) {
  const m = useTrackerMutations();
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  if (mode === "edit") {
    const input: ContactInput = {
      name: contact.name,
      company: contact.company,
      title: contact.title,
      email: contact.email,
      phone: contact.phone,
      linkedin_url: contact.linkedin_url,
      notes: contact.notes,
    };
    return (
      <li className="entry entry-editing">
        <ContactForm
          initial={input}
          submitLabel="Save"
          onCancel={() => setMode("view")}
          onSubmit={async (body) => {
            await m.updateContact.mutateAsync({ id: contact.id, body });
            setMode("view");
          }}
        />
      </li>
    );
  }
  return (
    <li className="entry">
      <div className="entry-main">
        <div className="entry-text">
          <span className="entry-title">{contact.name}</span>
          <p className="muted small">{[contact.title, contact.company].filter(Boolean).join(" · ")}</p>
          <p className="small">
            {[
              contact.email ? (
                <a key="e" href={`mailto:${contact.email}`}>
                  {contact.email}
                </a>
              ) : null,
              contact.phone ? <span key="p">{contact.phone}</span> : null,
              contact.linkedin_url ? (
                <a key="l" href={webHref(contact.linkedin_url)} target="_blank" rel="noopener noreferrer">
                  Profile
                </a>
              ) : null,
            ]
              .filter(Boolean)
              .flatMap((node, i) => (i ? [" · ", node] : [node]))}
          </p>
          {contact.links.length ? (
            <p className="small">
              {contact.links.map((l, i) => (
                <span key={l.application_id}>
                  {i ? "; " : ""}
                  <Link to={`/tracker/applications/${l.application_id}`}>{l.title}</Link>
                  <span className="muted">
                    {" "}
                    ({ROLE_LABEL[l.role]}, {STAGE_LABEL[l.stage]})
                  </span>
                </span>
              ))}
            </p>
          ) : null}
          {contact.notes ? <p className="small plain-text">{contact.notes}</p> : null}
        </div>
        <div className="entry-actions">
          <Button variant="ghost" onClick={() => setMode("edit")}>
            Edit
          </Button>
          <Button variant="ghost" onClick={() => setMode("delete")}>
            Delete
          </Button>
        </div>
      </div>
      {mode === "delete" ? (
        <div className="confirm-row">
          <span className="small">Delete {contact.name}? They'll be removed from every application and referral.</span>
          <Button variant="danger" busy={m.deleteContact.isPending} onClick={() => m.deleteContact.mutate(contact.id)}>
            Delete
          </Button>
          <Button variant="ghost" onClick={() => setMode("view")}>
            Keep
          </Button>
        </div>
      ) : null}
    </li>
  );
}

export function ContactsTab() {
  const contacts = useContacts();
  const m = useTrackerMutations();
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  if (contacts.isPending) return <FullPageLoader />;
  if (contacts.isError) return <Notice>{errorMessage(contacts.error)}</Notice>;
  const needle = q.trim().toLowerCase();
  const shown = contacts.data.filter((c) => !needle || `${c.name} ${c.company} ${c.title}`.toLowerCase().includes(needle));

  return (
    <section className="stack" aria-label="Contacts">
      <div className="row-between tab-toolbar">
        <div className="filters">
          <Field label="Search contacts" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {!adding ? <Button onClick={() => setAdding(true)}>Add contact</Button> : null}
      </div>
      {adding ? (
        <div className="card">
          <h2>New contact</h2>
          <ContactForm
            initial={EMPTY}
            submitLabel="Add contact"
            onCancel={() => setAdding(false)}
            onSubmit={async (body) => {
              await m.createContact.mutateAsync(body);
              setAdding(false);
            }}
          />
        </div>
      ) : null}
      {contacts.data.length === 0 && !adding ? (
        <div className="empty">
          <p>No contacts yet. Add recruiters, hiring managers, and the people who refer you.</p>
        </div>
      ) : null}
      <ul className="entry-list">
        {shown.map((c) => (
          <ContactRow key={c.id} contact={c} />
        ))}
      </ul>
    </section>
  );
}
