// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Button, Field, FullPageLoader, Notice, SelectField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  addDays,
  agendaGroup,
  formatDue,
  localToday,
  useAgenda,
  useApplications,
  useTrackerMutations,
  type AgendaGroup,
  type AgendaItem,
  type ReminderKind,
} from "../../lib/tracker";
import { REMINDER_KINDS } from "./ApplicationPage";

const GROUP_LABEL: Record<AgendaGroup, string> = {
  overdue: "Overdue",
  today: "Today",
  week: "Next 7 days",
  later: "Later",
};

const SOURCE_LABEL: Record<AgendaItem["source"], string> = {
  reminder: "Reminder",
  application_deadline: "Deadline",
  offer_deadline: "Offer deadline",
  referral_follow_up: "Referral",
};

export function groupAgenda(items: AgendaItem[], today: string): [AgendaGroup, AgendaItem[]][] {
  const groups: Record<AgendaGroup, AgendaItem[]> = { overdue: [], today: [], week: [], later: [] };
  for (const item of items) groups[agendaGroup(item.due_on, today)].push(item);
  return (Object.keys(groups) as AgendaGroup[]).filter((g) => groups[g].length).map((g) => [g, groups[g]]);
}

export function AgendaTab() {
  const today = localToday();
  const agenda = useAgenda(today, 60);
  const apps = useApplications();
  const m = useTrackerMutations();
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<ReminderKind>("task");
  const [due, setDue] = useState(today);
  const [time, setTime] = useState("");
  const [appId, setAppId] = useState("");
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
        application_id: appId || null,
      });
      setAdding(false);
      setTitle("");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (agenda.isPending) return <FullPageLoader />;
  if (agenda.isError) return <Notice>{errorMessage(agenda.error)}</Notice>;

  return (
    <section className="stack" aria-label="Agenda">
      <div className="row-between tab-toolbar">
        <p className="muted small">Reminders, apply-by dates, offer deadlines, and referral follow-ups for the next 60 days.</p>
        {!adding ? <Button onClick={() => setAdding(true)}>Add reminder</Button> : null}
      </div>
      {adding ? (
        <form className="card entry-form" onSubmit={submit}>
          <h2>New reminder</h2>
          <Field label="Remind me to" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
          <div className="form-grid">
            <SelectField label="Kind" value={kind} options={REMINDER_KINDS} onChange={(e) => setKind(e.target.value as ReminderKind)} />
            <SelectField
              label="Application (optional)"
              value={appId}
              placeholder="None"
              options={(apps.data ?? []).map((a) => ({ value: a.id, label: a.company ? `${a.title} — ${a.company}` : a.title }))}
              onChange={(e) => setAppId(e.target.value)}
            />
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

      {agenda.data.length === 0 ? (
        <div className="empty">
          <p>Nothing due in the next 60 days.</p>
        </div>
      ) : null}

      {groupAgenda(agenda.data, today).map(([group, items]) => (
        <div key={group} className="stack-sm">
          <h2 className={`section-title${group === "overdue" ? " text-danger" : ""}`}>{GROUP_LABEL[group]}</h2>
          <ul className="entry-list">
            {items.map((item) => (
              <AgendaRow key={`${item.source}-${item.id}`} item={item} today={today} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

export function AgendaRow({ item, today, compact }: { item: AgendaItem; today: string; compact?: boolean }) {
  const m = useTrackerMutations();
  const reminder = item.source === "reminder";
  const overdue = item.due_on < today;
  return (
    <li className="entry agenda-row">
      <div className="entry-main">
        {reminder ? (
          <input
            type="checkbox"
            className="entry-check"
            aria-label={`Mark "${item.title}" done`}
            onChange={() => m.updateReminder.mutate({ id: item.id, body: { done: true } })}
          />
        ) : null}
        <div className="entry-text">
          <div className="entry-title-row">
            <span className="kind-tag">{SOURCE_LABEL[item.source]}</span>
            <span className="entry-title">{item.title}</span>
          </div>
          <p className={`small${overdue ? " text-danger" : " muted"}`}>
            {formatDue(item, today)}
            {item.application_id && item.source === "reminder" && item.application_title ? (
              <>
                {" · "}
                <Link to={`/tracker/applications/${item.application_id}`}>
                  {item.application_title}
                  {item.company ? ` (${item.company})` : ""}
                </Link>
              </>
            ) : null}
            {item.application_id && item.source !== "reminder" ? (
              <>
                {" · "}
                <Link to={`/tracker/applications/${item.application_id}`}>Open</Link>
              </>
            ) : null}
          </p>
        </div>
        {reminder && !compact ? (
          <div className="entry-actions">
            <Button
              variant="ghost"
              onClick={() => m.updateReminder.mutate({ id: item.id, body: { due_on: addDays(item.due_on < today ? today : item.due_on, 1) } })}
            >
              +1 day
            </Button>
            <Button
              variant="ghost"
              onClick={() => m.updateReminder.mutate({ id: item.id, body: { due_on: addDays(item.due_on < today ? today : item.due_on, 7) } })}
            >
              +1 week
            </Button>
          </div>
        ) : null}
      </div>
    </li>
  );
}
