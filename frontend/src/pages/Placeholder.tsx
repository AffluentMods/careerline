// SPDX-License-Identifier: Apache-2.0
export function PlaceholderPage({ title, summary, phase }: { title: string; summary: string; phase?: number }) {
  return (
    <div className="page">
      <header className="page-head">
        <h1>{title}</h1>
      </header>
      <section className="empty">
        <p>{summary}</p>
        {phase ? <span className="badge">Arrives in phase {phase}</span> : null}
      </section>
    </div>
  );
}
