import { TRACK_CHART_COLORS } from "../trackScoreboardUtils";
import { trackDisplayLabel } from "../trackScoreboardReplayUtils";
import type { Provenance } from "./types";

export interface SourceStyle {
  label: string;
  color: string;
  /** Canvas/SVG dash array; empty = solid. Provenance is never colour-only. */
  dash: number[];
  hatch: boolean;
}

const DASH: Record<Provenance, number[]> = { live: [], replay: [6, 4], backtest: [2, 3] };

function hashColor(id: string): string {
  let h = 2166136261; // FNV-1a
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619) >>> 0;
  return `hsl(${h % 360}, 62%, 50%)`;
}

/** Table colour for known ids (`track:` prefix stripped), stable hash colour otherwise; label from the server first. */
export function sourceStyle(sourceId: string, provenance: Provenance, label?: string): SourceStyle {
  const key = sourceId.replace(/^track:/, "");
  return {
    label: label ?? trackDisplayLabel(key),
    color: TRACK_CHART_COLORS[key] ?? hashColor(sourceId),
    dash: DASH[provenance],
    hatch: provenance === "backtest",
  };
}
