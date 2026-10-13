import { useState } from "react";

import { api } from "../lib/api";
import type { AssistantAnswer } from "../lib/types";

const EXAMPLES = [
  "Combien d'avions survolent la France en ce moment ?",
  "Quels sont les 5 aéroports les plus actifs aujourd'hui ?",
  "Quelles sont les routes les plus fréquentes cette semaine ?",
];

export function AssistantPanel() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const ask = async (q: string) => {
    if (q.trim().length < 3) return;
    setLoading(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(await api.ask(q));
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setError(
        message.startsWith("503")
          ? "Assistant désactivé : renseignez ANTHROPIC_API_KEY (ou Ollama) et lancez le profil full."
          : message,
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="panel-body assistant">
      <h2>Assistant</h2>
      <p className="muted small">
        Questions en langage naturel sur le trafic. Requêtes SQL en lecture seule sur les marts.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
      >
        <textarea
          rows={3}
          value={question}
          placeholder="Posez une question…"
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" disabled={loading}>
          {loading ? "Analyse…" : "Demander"}
        </button>
      </form>
      <div className="examples">
        {EXAMPLES.map((q) => (
          <button
            key={q}
            className="chip"
            onClick={() => {
              setQuestion(q);
              void ask(q);
            }}
          >
            {q}
          </button>
        ))}
      </div>
      {error && <p className="error small">{error}</p>}
      {answer && (
        <div className="answer">
          <p>{answer.answer}</p>
          {answer.sql && (
            <details>
              <summary>SQL exécuté</summary>
              <pre>{answer.sql}</pre>
            </details>
          )}
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
        </div>
      )}
    </div>
  );
}
