import type { ProvenanceSource } from "@/lib/api";

export type SourceContentKind =
  | "json"
  | "markdown"
  | "text"
  | "structured_research"
  | "structured_debate"
  | "structured_evidence";

export interface ParsedSourceContent {
  kind: SourceContentKind;
  text: string;
  data?: unknown;
  truncated?: boolean;
}

/** Turn literal \\n / \\t in plain text into real whitespace for display. */
export function unescapeLiteralEscapes(text: string): string {
  return text
    .replace(/\\r\\n/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\r/g, "\r");
}

function tryParseJsonOnce(raw: string): unknown | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[") && !trimmed.startsWith('"')) {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

/** Parse JSON that may be string-encoded multiple times. */
export function tryParseJsonDeep(raw: string, maxDepth = 3): unknown | null {
  let current: unknown = raw.trim();
  let parsed = false;

  for (let depth = 0; depth < maxDepth; depth++) {
    if (typeof current !== "string") return parsed ? current : null;
    const next = tryParseJsonOnce(current);
    if (next === null) return parsed ? current : null;
    current = next;
    parsed = true;
    if (typeof current !== "string") return current;
  }

  return parsed ? current : null;
}

function looksLikeMarkdown(text: string): boolean {
  const sample = text.slice(0, 4000);
  return (
    /^#{1,6}\s/m.test(sample) ||
    /\*\*[^*\n]+\*\*/.test(sample) ||
    /^[-*+]\s/m.test(sample) ||
    /^\d+\.\s/m.test(sample) ||
    /\[.+\]\([^)]+\)/.test(sample) ||
    /^>\s/m.test(sample) ||
    /^```/m.test(sample)
  );
}

function isDocumentTool(source?: ProvenanceSource): boolean {
  const tool = (source?.tool_name || "").toLowerCase();
  return tool === "read_document" || tool === "read_url" || tool === "web_search";
}

function normalizeDisplayText(text: string): string {
  if (!text.includes("\\n") && !text.includes("\\t") && !text.includes("\\r")) {
    return text;
  }
  const unescaped = unescapeLiteralEscapes(text);
  if (unescaped.includes("\n") || unescaped.includes("\t")) return unescaped;
  return text;
}

export function parseSourceContent(raw: string, source?: ProvenanceSource): ParsedSourceContent {
  const text = (raw || "").trim();
  if (!text) return { kind: "text", text: "" };

  const truncated = text.length >= 12_000;
  const sourceType = source?.source_type || "";

  if (sourceType === "hub_research") {
    const data = tryParseJsonDeep(text);
    if (data && typeof data === "object") {
      return { kind: "structured_research", text, data, truncated };
    }
  }

  if (sourceType === "agent_debate") {
    const data = tryParseJsonDeep(text);
    if (data && typeof data === "object") {
      return { kind: "structured_debate", text, data, truncated };
    }
  }

  if (sourceType === "goal_evidence") {
    const data = tryParseJsonDeep(text);
    if (data && typeof data === "object") {
      return { kind: "structured_evidence", text, data, truncated };
    }
  }

  const jsonData = tryParseJsonDeep(text) ?? tryParseJsonDeep(unescapeLiteralEscapes(text));
  if (jsonData !== null) {
    if (typeof jsonData === "string") {
      const inner = normalizeDisplayText(jsonData);
      if (looksLikeMarkdown(inner) || isDocumentTool(source)) {
        return { kind: "markdown", text: inner, truncated };
      }
      return { kind: "text", text: inner, truncated };
    }

    return {
      kind: "json",
      text,
      data: jsonData,
      truncated,
    };
  }

  const normalized = normalizeDisplayText(text);

  if (isDocumentTool(source) || looksLikeMarkdown(normalized)) {
    return { kind: "markdown", text: normalized, truncated };
  }

  return { kind: "text", text: normalized, truncated };
}
