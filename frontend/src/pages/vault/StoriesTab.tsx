// SPDX-License-Identifier: Apache-2.0
import { useMemo, useState, type FormEvent } from "react";
import { Button, Checkbox, Field, FullPageLoader, Notice, TextAreaField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  KIND_LABEL,
  entryTitle,
  useEntries,
  useStories,
  useVaultMutations,
  type Entry,
  type Story,
  type StoryInput,
} from "../../lib/vault";

const STAR: { key: "situation" | "task" | "action" | "result" | "reflection"; label: string; hint: string }[] = [
  { key: "situation", label: "Situation", hint: "Where and when. Keep it short." },
  { key: "task", label: "Task", hint: "What you were responsible for." },
  { key: "action", label: "Action", hint: "What you did, in first person." },
  { key: "result", label: "Result", hint: "What changed. Numbers help." },
  { key: "reflection", label: "Reflection", hint: "What you learned or would do differently." },
];

export function parseTags(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const tag = part.trim().slice(0, 50);
    if (tag && !seen.has(tag.toLowerCase())) {
      seen.add(tag.toLowerCase());
      out.push(tag);
    }
  }
  return out.slice(0, 20);
}

export function StoriesTab() {
  const stories = useStories();
  const entries = useEntries();
  const [editing, setEditing] = useState<string | "new" | null>(null);

  if (stories.isPending || entries.isPending) return <FullPageLoader />;
  if (stories.isError) return <Notice>{errorMessage(stories.error)}</Notice>;
  if (entries.isError) return <Notice>{errorMessage(entries.error)}</Notice>;

  const byId = new Map(entries.data.map((e) => [e.id, e]));

  return (
    <section className="stack" aria-label="Stories">
      <div className="row-between tab-toolbar">
        <p className="muted small">
          STAR stories for interviews. Link each one to the vault facts it's based on.
        </p>
        {editing !== "new" ? <Button onClick={() => setEditing("new")}>Add story</Button> : null}
      </div>

      {editing === "new" ? (
        <div className="card">
          <h2>New story</h2>
          <StoryForm entries={entries.data} onDone={() => setEditing(null)} />
        </div>
      ) : null}

      {stories.data.length === 0 && editing !== "new" ? (
        <div className="empty">
          <p>No stories yet. A good first one: the achievement you're proudest of.</p>
        </div>
      ) : null}

      <ul className="story-list">
        {stories.data.map((s) =>
          editing === s.id ? (
            <li key={s.id} className="card">
              <StoryForm story={s} entries={entries.data} onDone={() => setEditing(null)} />
            </li>
          ) : (
            <StoryCard key={s.id} story={s} byId={byId} onEdit={() => setEditing(s.id)} />
          ),
        )}
      </ul>
    </section>
  );
}

function StoryCard({ story, byId, onEdit }: { story: Story; byId: Map<string, Entry>; onEdit: () => void }) {
  const m = useVaultMutations();
  const [confirm, setConfirm] = useState(false);
  return (
    <li className="card story">
      <div className="row-between">
        <div className="entry-title-row">
          <h2>{story.title}</h2>
          {story.verified ? (
            <span className="badge badge-ok">Verified</span>
          ) : (
            <span className="badge badge-warn">Draft</span>
          )}
        </div>
        <div className="row">
          <Button variant="ghost" onClick={onEdit}>
            Edit
          </Button>
          {confirm ? (
            <>
              <Button variant="danger" busy={m.removeStory.isPending} onClick={() => m.removeStory.mutate(story.id)}>
                Delete
              </Button>
              <Button variant="ghost" onClick={() => setConfirm(false)}>
                Keep
              </Button>
            </>
          ) : (
            <Button variant="ghost" onClick={() => setConfirm(true)}>
              Delete
            </Button>
          )}
        </div>
      </div>
      <dl className="star">
        {STAR.filter(({ key }) => story[key]).map(({ key, label }) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>{story[key]}</dd>
          </div>
        ))}
      </dl>
      {story.tags.length ? (
        <div className="chips" aria-label="Tags">
          {story.tags.map((t) => (
            <span key={t} className="chip">
              {t}
            </span>
          ))}
        </div>
      ) : null}
      {story.entry_ids.length ? (
        <p className="small muted">
          Based on:{" "}
          {story.entry_ids
            .map((id) => byId.get(id))
            .filter((e): e is Entry => Boolean(e))
            .map((e) => `${KIND_LABEL[e.kind]}: ${entryTitle(e)}`)
            .join("; ")}
        </p>
      ) : null}
    </li>
  );
}

function StoryForm({ story, entries, onDone }: { story?: Story; entries: Entry[]; onDone: () => void }) {
  const m = useVaultMutations();
  const [title, setTitle] = useState(story?.title ?? "");
  const [star, setStar] = useState(() => ({
    situation: story?.situation ?? "",
    task: story?.task ?? "",
    action: story?.action ?? "",
    result: story?.result ?? "",
    reflection: story?.reflection ?? "",
  }));
  const [tags, setTags] = useState(story?.tags.join(", ") ?? "");
  const [linked, setLinked] = useState<Set<string>>(() => new Set(story?.entry_ids ?? []));
  const [verified, setVerified] = useState(story?.verified ?? true);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<unknown>(null);
  const busy = m.createStory.isPending || m.updateStory.isPending;

  const linkable = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return entries
      .filter((e) => e.kind !== "link" && e.kind !== "skill")
      .filter((e) => !needle || entryTitle(e).toLowerCase().includes(needle) || linked.has(e.id));
  }, [entries, filter, linked]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const body: StoryInput = {
      title: title.trim(),
      ...star,
      tags: parseTags(tags),
      entry_ids: [...linked],
      verified,
    };
    try {
      if (story) await m.updateStory.mutateAsync({ id: story.id, body });
      else await m.createStory.mutateAsync(body);
      onDone();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form className="entry-form" onSubmit={submit}>
      <Field label="Title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
      {STAR.map(({ key, label, hint }) => (
        <TextAreaField
          key={key}
          label={`${label} (optional)`}
          hint={hint}
          maxLength={4000}
          value={star[key]}
          onChange={(e) => setStar((cur) => ({ ...cur, [key]: e.target.value }))}
        />
      ))}
      <Field
        label="Tags (optional)"
        hint="Comma separated, for example: leadership, incident response"
        value={tags}
        onChange={(e) => setTags(e.target.value)}
      />
      <fieldset className="link-picker">
        <legend>Based on these vault facts</legend>
        {entries.length > 8 ? (
          <Field label="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        ) : null}
        {linkable.length ? (
          <ul>
            {linkable.map((e) => (
              <li key={e.id}>
                <Checkbox
                  checked={linked.has(e.id)}
                  onChange={(ev) =>
                    setLinked((cur) => {
                      const next = new Set(cur);
                      if (ev.target.checked) next.add(e.id);
                      else next.delete(e.id);
                      return next;
                    })
                  }
                  label={
                    <>
                      <span className="kind-tag">{KIND_LABEL[e.kind]}</span> {entryTitle(e)}
                      {!e.verified ? <span className="muted"> (draft)</span> : null}
                    </>
                  }
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">Add roles and achievements first, then link them here.</p>
        )}
      </fieldset>
      <Checkbox
        checked={verified}
        onChange={(e) => setVerified(e.target.checked)}
        label="I confirm this story is accurate"
        hint="Drafts are never used by the AI."
      />
      {error ? <Notice>{errorMessage(error)}</Notice> : null}
      <div className="row">
        <Button type="submit" busy={busy}>
          {story ? "Save story" : "Add story"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
