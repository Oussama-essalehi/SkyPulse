import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import { type ChatMessage, chatError, chatHistory } from "../lib/chat";
import type { AssistantAnswer } from "../lib/types";

const EXAMPLES = [
  "Combien d'avions survolent la France en ce moment ?",
  "Quels sont les 5 aéroports les plus actifs aujourd'hui ?",
  "Quelles sont les routes les plus fréquentes cette semaine ?",
  "Y a-t-il des urgences en cours ?",
];

interface Props {
  open: boolean;
  onClose: () => void;
}

function Details({ answer }: { answer: AssistantAnswer }) {
  if (!answer.sql && answer.rows.length === 0) return null;
  return (
    <details className="chat-details">
      <summary>
        {answer.rows.length > 0
          ? `Données (${answer.rows.length} ligne${answer.rows.length > 1 ? "s" : ""})`
          : "Requête"}
      </summary>
      {answer.rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {answer.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {answer.rows.slice(0, 50).map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{cell === null ? "—" : String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {answer.sql && <pre>{answer.sql}</pre>}
    </details>
  );
}

/** The assistant as a conversation, in a sidebar on the right of the map. */
export function AssistantChat({ open, onClose }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const nextId = useRef(1);
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  // Follow the conversation: scroll to the newest message.
  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  const send = async (text: string) => {
    const question = text.trim();
    if (question.length < 3 || loading) return;
    const history = chatHistory(messages);
    const add = (m: Omit<ChatMessage, "id">) => {
      const id = nextId.current++;
      setMessages((all) => [...all, { ...m, id }]);
    };
    add({ role: "user", text: question });
    setDraft("");
    setLoading(true);
    try {
      const answer = await api.ask(question, history);
      add({ role: "assistant", text: answer.answer, answer });
    } catch (e) {
      add({
        role: "assistant",
        text: chatError(e instanceof Error ? e.message : String(e)),
        error: true,
      });
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(draft);
  };
  // Enter sends, Shift+Enter goes to the line.
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(draft);
    }
  };

  return (
    <aside className="chat" aria-label="Assistant" hidden={!open}>
      <header className="chat-header">
        <span className="chat-avatar" aria-hidden="true">
          ✦
        </span>
        <div className="chat-title">
          <h2>Assistant SkyRadar</h2>
          <span className="muted small">Questions sur le trafic aérien</span>
        </div>
        {messages.length > 0 && (
          <button
            className="link small chat-reset"
            onClick={() => setMessages([])}
            disabled={loading}
          >
            Nouvelle discussion
          </button>
        )}
        <button className="chat-close" onClick={onClose} aria-label="Fermer l'assistant">
          ×
        </button>
      </header>
      <div className="chat-messages" ref={list} aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-welcome">
            <p>
              Bonjour ! Je réponds à vos questions sur le trafic aérien en direct et l'historique de
              SkyRadar.
            </p>
            <div className="chat-examples">
              {EXAMPLES.map((q) => (
                <button key={q} className="chip" onClick={() => void send(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`chat-msg chat-${m.role}${m.error ? " chat-error" : ""}${m.answer?.off_topic ? " chat-off-topic" : ""}`}
          >
            <p>{m.text}</p>
            {m.answer && <Details answer={m.answer} />}
          </div>
        ))}
        {loading && (
          <div className="chat-msg chat-assistant chat-typing" aria-label="L'assistant écrit">
            <span />
            <span />
            <span />
          </div>
        )}
      </div>
      <form className="chat-composer" onSubmit={onSubmit}>
        <textarea
          ref={input}
          rows={2}
          value={draft}
          maxLength={500}
          placeholder="Posez une question sur le trafic…"
          aria-label="Votre question"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" disabled={loading || draft.trim().length < 3} aria-label="Envoyer">
          ➤
        </button>
      </form>
    </aside>
  );
}
