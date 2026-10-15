/**
 * The one gate for a link that came from data (a news article, an extracted forecast's source page). Returns the
 * trimmed URL only when it is an absolute http(s) URL; anything else (`javascript:`, `data:`, a relative or
 * protocol-relative value, whitespace tricks inside the scheme) is undefined, so the caller renders no link.
 */
export function safeHttpUrl(value: string | null | undefined): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  try {
    const parsed = new URL(trimmed);
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || !parsed.hostname) return undefined;
  } catch {
    return undefined;
  }
  return trimmed;
}
