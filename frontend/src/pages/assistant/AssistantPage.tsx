// SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, NavLink, useLocation, useNavigate, useParams } from "react-router-dom";
import { Button, FullPageLoader, Notice } from "../../components/ui";
import { useAiSettings } from "../../lib/ai";
import { errorMessage } from "../../lib/api";
import {
  SUGGESTIONS,
  useAssistantMutations,
  useConversation,
  useConversations,
  type ChatItem,
} from "../../lib/assistant";

export function AssistantPage() {
  const { id } = useParams();
  const ai = useAiSettings();
  const conversations = useConversations();
  const m = useAssistantMutations();
  const navigate = useNavigate();

  if (ai.isPending) return <FullPageLoader />;
  const aiOn = Boolean(ai.data && ai.data.provider !== "none");

  return (
    <div className="page page-wide">
      <header className="page-head">
        <h1>Assistant</h1>
        <p className="muted">
          Ask about your search. It reads your CareerLine data; anything that would change it waits for your OK, and it never
          sends messages for you.
        </p>
      </header>
      {!aiOn ? (
        <Notice tone="info">
          The assistant needs an AI provider. <Link to="/settings/ai">Turn one on in Settings → AI</Link>.
        </Notice>
      ) : (
        <div className="chat-layout">
          <aside className="chat-list" aria-label="Conversations">
            <Button
              variant="secondary"
              busy={m.create.isPending}
              onClick={async () => {
                const c = await m.create.mutateAsync();
                navigate(`/assistant/${c.id}`);
              }}
            >
              New conversation
            </Button>
            <ul>
              {(conversations.data ?? []).map((c) => (
                <li key={c.id}>
                  <NavLink to={`/assistant/${c.id}`} className="chat-link">
                    {c.title}
                  </NavLink>
                </li>
              ))}
            </ul>
          </aside>
          {id ? <Chat key={id} id={id} /> : <Welcome />}
        </div>
      )}
    </div>
  );
}

function Welcome() {
  const m = useAssistantMutations();
  const navigate = useNavigate();
  async function start(text: string) {
    const c = await m.create.mutateAsync();
    navigate(`/assistant/${c.id}`, { state: { draft: text } });
  }
  return (
    <section className="chat-panel chat-welcome">
      <p className="muted">Try one of these, or start a new conversation:</p>
      <div className="suggestions-grid">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="suggestion" onClick={() => void start(s)}>
            {s}
          </button>
        ))}
      </div>
    </section>
  );
}

function Bubble({ item }: { item: ChatItem }) {
  const m = useAssistantMutations();
  if (item.role === "tool") {
    return <li className="chat-tool muted small">Looked up {item.tool?.replaceAll("_", " ")}</li>;
  }
  if (item.role === "action") {
    return (
      <li className={`chat-action chat-action-${item.status}`}>
        <p className="small kind-tag">Proposed change</p>
        <p>{item.text}</p>
        {item.status === "pending" ? (
          <div className="row">
            <Button
              busy={m.decide.isPending && m.decide.variables?.actionId === item.id && m.decide.variables.confirm}
              disabled={m.decide.isPending}
              onClick={() => m.decide.mutate({ actionId: item.id, confirm: true })}
            >
              Confirm
            </Button>
            <Button
              variant="secondary"
              disabled={m.decide.isPending}
              onClick={() => m.decide.mutate({ actionId: item.id, confirm: false })}
            >
              Reject
            </Button>
          </div>
        ) : (
          <p className="small muted">
            {item.status === "confirmed" ? "Done" : item.status === "rejected" ? "Rejected" : "Couldn't be applied"}
            {item.result ? `: ${item.result}` : ""}
          </p>
        )}
      </li>
    );
  }
  return (
    <li className={`chat-bubble chat-${item.role}`}>
      <p className="plain-text">{item.text}</p>
      {item.role === "assistant" ? (
        <button className="link-btn small" onClick={() => void navigator.clipboard?.writeText(item.text)}>
          Copy
        </button>
      ) : null}
    </li>
  );
}

function Chat({ id }: { id: string }) {
  const conv = useConversation(id);
  const m = useAssistantMutations();
  const navigate = useNavigate();
  const location = useLocation();
  const [text, setText] = useState(() => (location.state as { draft?: string } | null)?.draft ?? "");
  const endRef = useRef<HTMLDivElement>(null);
  const count = conv.data?.messages.length ?? 0;

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [count, m.send.isPending]);

  if (conv.isPending) return <FullPageLoader />;
  if (conv.isError) return <Notice>{errorMessage(conv.error)}</Notice>;
  const c = conv.data;

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    const message = text.trim();
    if (!message || c.pending) return;
    setText("");
    try {
      await m.send.mutateAsync({ id: c.id, text: message });
    } catch {
      setText(message);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <section className="chat-panel" aria-label={c.title}>
      <div className="row-between">
        <h2>{c.title}</h2>
        <button
          className="link-btn small"
          onClick={async () => {
            await m.remove.mutateAsync(c.id);
            navigate("/assistant");
          }}
        >
          Delete conversation
        </button>
      </div>
      <ol className="chat-messages" aria-live="polite">
        {c.messages.map((item) => (
          <Bubble key={item.id} item={item} />
        ))}
        {m.send.isPending ? (
          <li className="chat-bubble chat-assistant chat-thinking">
            <span className="spinner" aria-hidden="true" /> Thinking…
          </li>
        ) : null}
      </ol>
      <div ref={endRef} />
      {m.send.error ? <Notice>{errorMessage(m.send.error)}</Notice> : null}
      {m.decide.error ? <Notice>{errorMessage(m.decide.error)}</Notice> : null}
      <form className="chat-input" onSubmit={submit}>
        <label htmlFor="chat-text" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-text"
          rows={2}
          maxLength={4000}
          value={text}
          placeholder={c.pending ? "Confirm or reject the proposed changes first." : "Ask about your search…"}
          disabled={Boolean(c.pending) || m.send.isPending}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <Button type="submit" busy={m.send.isPending} disabled={!text.trim() || Boolean(c.pending)}>
          Send
        </Button>
      </form>
    </section>
  );
}
