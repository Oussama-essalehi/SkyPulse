import type { AssistantAnswer } from "./types";

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  /** Assistant reply: executed SQL and result table, when there is one. */
  answer?: AssistantAnswer;
  /** The question failed (assistant down, quota...): not sent back as history. */
  error?: boolean;
}

export interface Turn {
  role: "user" | "assistant";
  content: string;
}

/** Exchanges sent back with a new question, oldest first (the server keeps the last few). */
export const HISTORY_TURNS = 6;

/**
 * Previous exchanges of the conversation for the model: each question with its answer,
 * failed and off-topic ones skipped, so follow-ups like "et hier ?" keep their context.
 */
export function chatHistory(messages: ChatMessage[], turns = HISTORY_TURNS): Turn[] {
  const pairs: Turn[][] = [];
  for (let i = 0; i + 1 < messages.length; i++) {
    const [q, a] = [messages[i], messages[i + 1]];
    if (q.role === "user" && a.role === "assistant" && !a.error && !a.answer?.off_topic) {
      pairs.push([
        { role: "user", content: q.text },
        { role: "assistant", content: a.text },
      ]);
      i++;
    }
  }
  return pairs.slice(-turns).flat();
}

/** Readable message for a failed question. */
export function chatError(message: string): string {
  if (message.startsWith("503"))
    return "L'assistant est désactivé : renseignez ANTHROPIC_API_KEY ou OLLAMA_BASE_URL dans .env et lancez le profil full.";
  if (/^50[24] /.test(message)) return `L'assistant n'a pas pu répondre : ${message.slice(4)}`;
  return message;
}
