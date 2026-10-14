import { describe, expect, it } from "vitest";

import { type ChatMessage, chatError, chatHistory } from "./chat";

const user = (id: number, text: string): ChatMessage => ({ id, role: "user", text });
const reply = (id: number, text: string, error = false): ChatMessage => ({
  id,
  role: "assistant",
  text,
  error,
});

describe("chat", () => {
  it("sends back answered questions, oldest first", () => {
    const messages = [user(1, "q1"), reply(2, "r1"), user(3, "q2"), reply(4, "r2")];
    expect(chatHistory(messages)).toEqual([
      { role: "user", content: "q1" },
      { role: "assistant", content: "r1" },
      { role: "user", content: "q2" },
      { role: "assistant", content: "r2" },
    ]);
  });

  it("skips off-topic exchanges", () => {
    const offTopic: ChatMessage = {
      ...reply(2, "Je réponds uniquement…"),
      answer: {
        answer: "Je réponds uniquement…",
        sql: null,
        columns: [],
        rows: [],
        off_topic: true,
      },
    };
    const messages = [user(1, "la gravité ?"), offTopic, user(3, "q2"), reply(4, "r2")];
    expect(chatHistory(messages).map((t) => t.content)).toEqual(["q2", "r2"]);
  });

  it("skips failed questions and keeps the last exchanges", () => {
    const messages = [
      user(1, "q1"),
      reply(2, "502 erreur", true),
      user(3, "q2"),
      reply(4, "r2"),
      user(5, "q3"),
      reply(6, "r3"),
      user(7, "en cours…"),
    ];
    expect(chatHistory(messages, 1)).toEqual([
      { role: "user", content: "q3" },
      { role: "assistant", content: "r3" },
    ]);
    expect(chatHistory(messages).map((t) => t.content)).toEqual(["q2", "r2", "q3", "r3"]);
  });

  it("explains errors", () => {
    expect(chatError("503 assistant disabled")).toContain("OLLAMA_BASE_URL");
    expect(chatError("502 assistant error (ollama): modèle absent")).toBe(
      "L'assistant n'a pas pu répondre : assistant error (ollama): modèle absent",
    );
    expect(chatError("Trop de questions : réessayez dans une minute.")).toBe(
      "Trop de questions : réessayez dans une minute.",
    );
  });
});
