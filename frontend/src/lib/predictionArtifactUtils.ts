import type { IndexPredictionArtifact } from "@/lib/api";

/** Pick the artifact with the latest as_of timestamp. */
export function pickNewestArtifact(
  a: IndexPredictionArtifact | null,
  b: IndexPredictionArtifact | null,
): IndexPredictionArtifact | null {
  if (!a) return b;
  if (!b) return a;
  const aTime = a.as_of ? Date.parse(String(a.as_of)) : 0;
  const bTime = b.as_of ? Date.parse(String(b.as_of)) : 0;
  if (!Number.isFinite(aTime) && !Number.isFinite(bTime)) return a;
  if (!Number.isFinite(aTime)) return b;
  if (!Number.isFinite(bTime)) return a;
  return aTime >= bTime ? a : b;
}
