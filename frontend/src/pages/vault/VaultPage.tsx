// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Link, NavLink, Navigate, useParams, useSearchParams } from "react-router-dom";
import { Button, Checkbox, FullPageLoader, Notice } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { childrenOf, KIND_LABEL, useEntries, useSummary, useVaultMutations, type Entry, type Kind } from "../../lib/vault";
import { EntryForm, EntryItem } from "./entries";
import { ImportReview, ImportsTab } from "./imports";
import { ProfileTab } from "./ProfileTab";
import { StoriesTab } from "./StoriesTab";

interface EntryTab {
  slug: string;
  label: string;
  kind: Kind;
  childKinds?: Kind[];
  intro: string;
}

const ENTRY_TABS: EntryTab[] = [
  {
    slug: "experience",
    label: "Experience",
    kind: "role",
    childKinds: ["responsibility", "achievement"],
    intro: "Use the official title and dates from your offer letter or HR records.",
  },
  {
    slug: "projects",
    label: "Projects",
    kind: "project",
    childKinds: ["achievement"],
    intro: "Work or personal projects you can talk about in detail.",
  },
  {
    slug: "certifications",
    label: "Certifications",
    kind: "certification",
    intro: "Mark certifications you're still working toward as in progress.",
  },
  {
    slug: "education",
    label: "Education",
    kind: "education",
    childKinds: ["achievement"],
    intro: "Degrees, diplomas, and formal programs.",
  },
  { slug: "skills", label: "Skills", kind: "skill", intro: "Group skills by category to keep them easy to scan." },
  { slug: "links", label: "Links", kind: "link", intro: "Portfolio, LinkedIn, GitHub, or a personal site." },
];

const TABS = [
  { slug: "profile", label: "Profile" },
  ...ENTRY_TABS.map(({ slug, label }) => ({ slug, label })),
  { slug: "stories", label: "Stories" },
  { slug: "imports", label: "Imports" },
];

export function VaultPage() {
  const { tab, importId } = useParams();
  const summary = useSummary();
  if (!tab) return <Navigate to="/vault/experience" replace />;

  const kinds = summary.data?.kinds;
  const draftsFor = (slug: string): number => {
    if (!kinds) return 0;
    const t = ENTRY_TABS.find((x) => x.slug === slug);
    if (slug === "stories") return summary.data!.stories.total - summary.data!.stories.verified;
    if (!t) return 0;
    return [t.kind, ...(t.childKinds ?? [])]
      .filter((k) => slug === "experience" || k === t.kind)
      .reduce((n, k) => n + (kinds[k].total - kinds[k].verified), 0);
  };
  const total = kinds
    ? Object.values(kinds).reduce((n, k) => n + k.total, 0) + (summary.data?.stories.total ?? 0)
    : null;

  return (
    <div className="page page-wide">
      <header className="page-head">
        <h1>Career Vault</h1>
        <p className="muted">
          Your career facts in one place. Only <strong>verified</strong> items are ever used by the AI to
          write or score anything.
        </p>
      </header>

      {total === 0 && tab !== "imports" ? (
        <section className="empty empty-start">
          <h2>Start your vault</h2>
          <p>Bring in what you already have, or add facts one at a time.</p>
          <div className="row">
            <Link className="btn btn-primary" to="/vault/imports">
              Import a resume
            </Link>
            <Link className="btn btn-secondary" to="/vault/experience?add=1">
              Add your first role
            </Link>
          </div>
        </section>
      ) : null}

      <nav className="tabs" aria-label="Vault sections">
        {TABS.map((t) => {
          const drafts = draftsFor(t.slug);
          return (
            <NavLink key={t.slug} to={`/vault/${t.slug}`} className="tab">
              {t.label}
              {drafts ? (
                <span className="tab-count" title={`${drafts} draft${drafts === 1 ? "" : "s"} to review`}>
                  {drafts}
                </span>
              ) : null}
            </NavLink>
          );
        })}
      </nav>

      <VaultTab tab={tab} importId={importId} />
    </div>
  );
}

function VaultTab({ tab, importId }: { tab: string; importId?: string }) {
  const [params] = useSearchParams();
  if (tab === "profile") return <ProfileTab />;
  if (tab === "stories") return <StoriesTab />;
  if (tab === "imports") return importId ? <ImportReview id={importId} /> : <ImportsTab />;
  const def = ENTRY_TABS.find((t) => t.slug === tab);
  if (!def) return <Navigate to="/vault/experience" replace />;
  const add = params.has("add");
  return <EntryListTab key={`${def.slug}${add ? "-add" : ""}`} def={def} startAdding={add} />;
}

function EntryListTab({ def, startAdding }: { def: EntryTab; startAdding: boolean }) {
  const entries = useEntries();
  const m = useVaultMutations();
  const [adding, setAdding] = useState(startAdding);
  const [draftsOnly, setDraftsOnly] = useState(false);

  if (entries.isPending) return <FullPageLoader />;
  if (entries.isError) return <Notice>{errorMessage(entries.error)}</Notice>;

  const all = entries.data;
  const kids = childrenOf(all);
  const childKinds = def.childKinds ?? [];
  const hasDraft = (e: Entry) => !e.verified || (kids.get(e.id) ?? []).some((c) => !c.verified);
  let top = all.filter((e) => e.kind === def.kind);
  if (draftsOnly) top = top.filter(hasDraft);
  const loose =
    def.slug === "experience"
      ? all.filter((e) => e.kind === "achievement" && !e.parent_id && (!draftsOnly || !e.verified))
      : [];

  const groups =
    def.kind === "skill"
      ? Array.from(
          top.reduce((map, e) => {
            const cat = (e.data.category as string) || "Other";
            map.set(cat, [...(map.get(cat) ?? []), e]);
            return map;
          }, new Map<string, Entry[]>()),
        ).sort(([a], [b]) => (a === "Other" ? 1 : b === "Other" ? -1 : a.localeCompare(b)))
      : [["", top] as [string, Entry[]]];

  const label = KIND_LABEL[def.kind].toLowerCase();

  return (
    <section className="stack" aria-label={def.label}>
      <div className="row-between tab-toolbar">
        <p className="muted small">{def.intro}</p>
        <div className="row">
          <Checkbox label="Drafts only" checked={draftsOnly} onChange={(e) => setDraftsOnly(e.target.checked)} />
          {!adding ? <Button onClick={() => setAdding(true)}>Add {label}</Button> : null}
        </div>
      </div>

      {adding ? (
        <div className="card">
          <h2>New {label}</h2>
          <EntryForm
            kind={def.kind}
            entries={all}
            submitLabel={`Add ${label}`}
            onCancel={() => setAdding(false)}
            onSubmit={async (input) => {
              await m.create.mutateAsync(input);
              setAdding(false);
            }}
          />
        </div>
      ) : null}

      {top.length === 0 && loose.length === 0 && !adding ? (
        <div className="empty">
          <p>{draftsOnly ? "No drafts here. Everything is verified." : `No ${def.label.toLowerCase()} yet.`}</p>
        </div>
      ) : null}

      {groups.map(([group, list]) =>
        list.length ? (
          <div key={group || "all"} className="stack-sm">
            {group ? <h2 className="section-title">{group}</h2> : null}
            <ul className={`entry-list${def.kind === "skill" ? " entry-list-compact" : ""}`}>
              {list.map((e) => (
                <EntryItem
                  key={e.id}
                  entry={e}
                  entries={all}
                  childEntries={(kids.get(e.id) ?? []).filter(
                    (c) => childKinds.includes(c.kind) && (!draftsOnly || !c.verified || !e.verified),
                  )}
                  childKinds={childKinds}
                />
              ))}
            </ul>
          </div>
        ) : null,
      )}

      {loose.length ? (
        <div className="stack-sm">
          <h2 className="section-title">Achievements not tied to a role</h2>
          <ul className="entry-list">
            {loose.map((e) => (
              <EntryItem key={e.id} entry={e} entries={all} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
