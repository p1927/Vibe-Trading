/** Coerce API values to human-readable strings (avoids React "[object Object]"). */

export function displayText(value: unknown, maxLen = 400): string | null {
  if (value == null) return null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed.slice(0, maxLen) : null;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    const parts = value.map((item) => displayText(item, 120)).filter(Boolean);
    return parts.length ? parts.join(" · ") : null;
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    for (const key of ["summary", "text", "headline", "title", "description", "rationale", "purpose"]) {
      const nested = displayText(obj[key], maxLen);
      if (nested) return nested;
    }
    if (obj.positive_pct != null || obj.negative_pct != null) {
      const pos = Number(obj.positive_pct ?? 0);
      const neg = Number(obj.negative_pct ?? 0);
      const neu = Number(obj.neutral_pct ?? 0);
      return `Sentiment mix: ${pos.toFixed(0)}% positive, ${neg.toFixed(0)}% negative, ${neu.toFixed(0)}% neutral`;
    }
    try {
      const raw = JSON.stringify(value);
      return raw.length > maxLen ? `${raw.slice(0, maxLen)}…` : raw;
    } catch {
      return null;
    }
  }
  return String(value).slice(0, maxLen);
}

export function formatFactorValue(factor: string, value: number): string {
  if (!Number.isFinite(value)) return "—";
  const absFactors = new Set(["repo_rate", "india_vix", "us_10y", "fii_net_5d", "nifty_pe", "nifty_pcr"]);
  if (absFactors.has(factor)) {
    if (factor === "fii_net_5d") {
      return `${value >= 0 ? "+" : ""}${value.toLocaleString("en-IN", { maximumFractionDigits: 0 })} Cr`;
    }
    if (factor === "repo_rate" || factor === "us_10y") {
      return `${value.toFixed(2)}%`;
    }
    return value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  }
  return value.toLocaleString("en-IN", { maximumFractionDigits: 3 });
}
