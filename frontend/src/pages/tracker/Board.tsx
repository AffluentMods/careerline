// SPDX-License-Identifier: Apache-2.0
import { useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { Button, Notice } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  addDays,
  CLOSED_STAGES,
  daysInStage,
  formatDate,
  formatDue,
  formatPay,
  groupByStage,
  localToday,
  OPEN_STAGES,
  STAGE_LABEL,
  STAGES,
  useTrackerMutations,
  type ApplicationSummary,
  type Stage,
} from "../../lib/tracker";

const DRAG_TYPE = "application/x-careerline-application";

/** Shown after a card moves to Applied: offer (never create) a follow-up reminder. */
export function FollowUpOffer({ app, onClose }: { app: { id: string; title: string }; onClose: () => void }) {
  const m = useTrackerMutations();
  const due = addDays(localToday(), 7);
  return (
    <div className="callout callout-info" role="status">
      <div>
        <strong>Moved “{app.title}” to Applied.</strong>
        <p className="muted">Remind you to follow up on {formatDate(due)}?</p>
      </div>
      <div className="row">
        <Button
          variant="secondary"
          busy={m.addReminder.isPending}
          onClick={async () => {
            await m.addReminder.mutateAsync({
              title: `Follow up: ${app.title}`,
              kind: "follow_up",
              due_on: due,
              due_time: null,
              application_id: app.id,
            });
            onClose();
          }}
        >
          Add reminder
        </Button>
        <Button variant="ghost" onClick={onClose}>
          No thanks
        </Button>
      </div>
    </div>
  );
}

export function useStageMove() {
  const m = useTrackerMutations();
  const [followUp, setFollowUp] = useState<{ id: string; title: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function move(app: Pick<ApplicationSummary, "id" | "title" | "stage">, stage: Stage) {
    if (app.stage === stage) return;
    setError(null);
    try {
      await m.moveStage.mutateAsync({ id: app.id, stage });
      setFollowUp(stage === "applied" ? { id: app.id, title: app.title } : null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return { move, followUp, clearFollowUp: () => setFollowUp(null), error };
}

export function MoveMenu({
  app,
  onMove,
}: {
  app: Pick<ApplicationSummary, "id" | "title" | "stage">;
  onMove: (stage: Stage) => void;
}) {
  return (
    <select
      className="move-menu"
      aria-label={`Move ${app.title} to`}
      value={app.stage}
      onChange={(e) => onMove(e.target.value as Stage)}
    >
      {STAGES.map((s) => (
        <option key={s} value={s}>
          {s === app.stage ? `${STAGE_LABEL[s]} (current)` : `Move to ${STAGE_LABEL[s]}`}
        </option>
      ))}
    </select>
  );
}

function Card({ app, onMove }: { app: ApplicationSummary; onMove: (stage: Stage) => void }) {
  const today = localToday();
  const pay = formatPay(app);
  const days = daysInStage(app.stage_changed_at);
  const overdue = app.next_due && app.next_due.due_on < today;
  return (
    <li
      className="board-card"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_TYPE, app.id);
        e.dataTransfer.effectAllowed = "move";
      }}
    >
      <div className="board-card-head">
        <Link to={`/tracker/applications/${app.id}`} className="board-card-title">
          {app.title}
        </Link>
        {app.priority === "high" ? <span className="badge badge-warn">High</span> : null}
      </div>
      {app.company ? <p className="small">{app.company}</p> : null}
      <p className="muted small">
        {[pay, app.location, `${days} day${days === 1 ? "" : "s"} in stage`].filter(Boolean).join(" · ")}
      </p>
      {app.next_due ? (
        <p className={`small due${overdue ? " due-overdue" : ""}`}>
          {formatDue(app.next_due, today)}: {app.next_due.title}
        </p>
      ) : null}
      <MoveMenu app={app} onMove={onMove} />
    </li>
  );
}

function Column({
  stage,
  apps,
  onDropApp,
  onMove,
  label,
}: {
  stage: Stage;
  apps: ApplicationSummary[];
  onDropApp: (id: string, stage: Stage) => void;
  onMove: (app: ApplicationSummary, stage: Stage) => void;
  label?: string;
}) {
  const [over, setOver] = useState(false);
  const accept = (e: DragEvent) => {
    if (e.dataTransfer.types.includes(DRAG_TYPE)) {
      e.preventDefault();
      setOver(true);
    }
  };
  return (
    <section
      className={`board-col${over ? " is-over" : ""}`}
      aria-label={label ?? STAGE_LABEL[stage]}
      onDragOver={accept}
      onDragEnter={accept}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        setOver(false);
        const id = e.dataTransfer.getData(DRAG_TYPE);
        if (id) onDropApp(id, stage);
      }}
    >
      <h2 className="board-col-head">
        {label ?? STAGE_LABEL[stage]} <span className="muted">{apps.length}</span>
      </h2>
      <ul className="board-list">
        {apps.map((a) => (
          <Card key={a.id} app={a} onMove={(s) => onMove(a, s)} />
        ))}
      </ul>
    </section>
  );
}

export function Board({ apps }: { apps: ApplicationSummary[] }) {
  const { move, followUp, clearFollowUp, error } = useStageMove();
  const [showClosed, setShowClosed] = useState(false);
  const groups = groupByStage(apps);
  const byId = new Map(apps.map((a) => [a.id, a]));
  const closed = CLOSED_STAGES.flatMap((s) => groups[s]);
  const onDrop = (id: string, stage: Stage) => {
    const app = byId.get(id);
    if (app) void move(app, stage);
  };

  return (
    <div className="stack">
      {followUp ? <FollowUpOffer app={followUp} onClose={clearFollowUp} /> : null}
      {error ? <Notice>{error}</Notice> : null}
      <p className="muted small">Drag cards between columns, or use each card&apos;s menu.</p>
      <div className="board">
        {OPEN_STAGES.map((s) => (
          <Column key={s} stage={s} apps={groups[s]} onDropApp={onDrop} onMove={(a, st) => void move(a, st)} />
        ))}
        <section className="board-col board-col-closed" aria-label="Closed">
          <h2 className="board-col-head">
            <button className="link-btn" aria-expanded={showClosed} onClick={() => setShowClosed(!showClosed)}>
              Closed <span className="muted">{closed.length}</span>
            </button>
          </h2>
          {showClosed ? (
            <ul className="board-list">
              {closed.map((a) => (
                <Card key={a.id} app={a} onMove={(s) => void move(a, s)} />
              ))}
            </ul>
          ) : (
            <p className="muted small">Accepted, rejected, and withdrawn.</p>
          )}
        </section>
      </div>
    </div>
  );
}
