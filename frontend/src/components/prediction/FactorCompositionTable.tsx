import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { labelFactor } from "@/lib/factorLabels";
import { researchNoteForFactor } from "@/lib/factorResearchNotes";
import { MACRO_MODEL_KEYS } from "@/lib/predictionVerification";
import type { IndexFactorContributor, IndexGlobalFactor } from "@/lib/api";

interface SensitivityCurve {
  factor?: string;
  label?: string;
  points?: Array<{ factor_delta_pct?: number; index_level?: number; return_pct?: number }>;
}

interface Props {
  globalFactors?: IndexGlobalFactor[];
  contributors?: IndexFactorContributor[];
  sensitivity?: SensitivityCurve[];
}

function fmtVal(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  if (abs >= 10) return v.toFixed(2);
  return v.toFixed(4);
}

function impactFromCurve(curve: SensitivityCurve | undefined): { down: string; up: string } {
  const pts = curve?.points ?? [];
  if (pts.length < 2) return { down: "—", up: "—" };
  const sorted = [...pts].sort(
    (a, b) => Number(a.factor_delta_pct ?? 0) - Number(b.factor_delta_pct ?? 0),
  );
  const low = sorted[0];
  const high = sorted[sorted.length - 1];
  const fmt = (p: typeof low) => {
    const lvl = p?.index_level;
    const ret = p?.return_pct;
    if (lvl != null && Number.isFinite(lvl))
      return lvl.toLocaleString("en-IN", { maximumFractionDigits: 0 });
    if (ret != null && Number.isFinite(ret)) return `${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%`;
    return "—";
  };
  return { down: fmt(low), up: fmt(high) };
}

function baseFactorKey(key: string): string {
  return key.split(" ")[0]?.replace(/\^2$/, "") ?? key;
}

export function FactorCompositionTable({ globalFactors = [], contributors = [], sensitivity = [] }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const sensByFactor = useMemo(() => {
    const map = new Map<string, SensitivityCurve>();
    for (const row of sensitivity) {
      if (row.factor) {
        map.set(row.factor, row);
        map.set(baseFactorKey(row.factor), row);
      }
    }
    return map;
  }, [sensitivity]);

  const rows = useMemo(() => {
    const contribByFactor = new Map<string, IndexFactorContributor>();
    for (const c of contributors) {
      if (c.factor) contribByFactor.set(c.factor, c);
    }

    const valueByFactor = new Map<string, IndexGlobalFactor>();
    for (const gf of globalFactors) {
      const key = gf.factor || "";
      if (key) valueByFactor.set(key, gf);
    }

    const keys = new Set<string>();
    for (const gf of globalFactors) {
      if (gf.factor) keys.add(gf.factor);
    }
    for (const c of contributors) {
      if (c.factor) keys.add(c.factor);
    }

    return [...keys]
      .map((key) => {
        const gf = valueByFactor.get(key);
        const c = contribByFactor.get(key);
        const inModel = (MACRO_MODEL_KEYS as readonly string[]).includes(baseFactorKey(key));
        return {
          key,
          label: gf?.label || c?.label || labelFactor(key),
          value: gf?.value ?? c?.value,
          zScore: gf?.z_score,
          contribution: c?.contribution_pct,
          source: gf?.source,
          inModel,
          hasSensitivity: sensByFactor.has(key),
        };
      })
      .sort((a, b) => Math.abs(b.contribution ?? 0) - Math.abs(a.contribution ?? 0));
  }, [globalFactors, contributors, sensByFactor]);

  if (!rows.length) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground">
        No factor levels yet — run analysis to populate macro snapshot and Ridge attribution.
      </div>
    );
  }

  const modelCount = rows.filter((r) => r.inModel).length;

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted-foreground">
        {modelCount} factors feed the live Ridge model · {contributors.length} with macro attribution today
      </p>
      <div className="overflow-hidden rounded-xl border">
        <table className="w-full text-left text-[12px]">
          <thead>
            <tr className="border-b bg-muted/30 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              <th className="px-3 py-2 font-semibold">Factor</th>
              <th className="px-3 py-2 font-semibold">Live value</th>
              <th className="px-3 py-2 font-semibold">Macro Δ</th>
              <th className="px-3 py-2 font-semibold">In forecast</th>
              <th className="w-8 px-2 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const open = expanded === row.key;
              const curve = sensByFactor.get(row.key);
              const impact = impactFromCurve(curve);
              const contrib = row.contribution;
              return (
                <Fragment key={row.key}>
                  <tr
                    className="cursor-pointer border-b last:border-0 hover:bg-muted/20"
                    onClick={() => setExpanded(open ? null : row.key)}
                  >
                    <td className="px-3 py-2">
                      <p className="font-medium">{row.label}</p>
                      <p className="font-mono text-[10px] text-muted-foreground">{row.key}</p>
                    </td>
                    <td className="px-3 py-2 tabular-nums">{fmtVal(row.value)}</td>
                    <td
                      className={cn(
                        "px-3 py-2 tabular-nums",
                        contrib != null && contrib > 0 && "text-emerald-600 dark:text-emerald-400",
                        contrib != null && contrib < 0 && "text-red-600 dark:text-red-400",
                      )}
                    >
                      {contrib != null && Number.isFinite(contrib)
                        ? `${contrib >= 0 ? "+" : ""}${contrib.toFixed(2)}%`
                        : "—"}
                    </td>
                    <td className="px-3 py-2">
                      {row.inModel ? (
                        <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
                          Yes
                        </span>
                      ) : (
                        <span className="text-[10px] text-muted-foreground">Context</span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-muted-foreground">
                      {row.hasSensitivity ? (
                        open ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )
                      ) : null}
                    </td>
                  </tr>
                  {open && row.hasSensitivity ? (
                    <tr className="border-b bg-muted/10">
                      <td colSpan={5} className="px-3 py-2 text-[11px] text-muted-foreground">
                        <span className="text-red-600 dark:text-red-400">−10% shock → index {impact.down}</span>
                        <span className="mx-2">·</span>
                        <span className="text-emerald-600 dark:text-emerald-400">+10% shock → index {impact.up}</span>
                        {row.source ? <span className="ml-2 opacity-70">Source: {row.source}</span> : null}
                        {(() => {
                          const note = researchNoteForFactor(row.key);
                          return note ? (
                            <p className="mt-1.5 leading-snug opacity-90">
                              <span className="font-medium text-foreground/80">Research:</span> {note.summary}
                              {note.caveat ? ` ${note.caveat}` : ""}
                            </p>
                          ) : null;
                        })()}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
