import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ConstituentSignal } from "@/lib/api";
import { ConstituentDetailPanel } from "@/components/prediction/ConstituentDetailPanel";

interface Props {
  signals?: ConstituentSignal[];
  limit?: number | null;
}

function fmtWeight(w: number | undefined): string {
  if (w == null || !Number.isFinite(w)) return "—";
  const pct = w <= 1 ? w * 100 : w;
  return `${pct.toFixed(2)}%`;
}

export function ConstituentDrivers({ signals = [], limit = null }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const pageSize = 25;

  const rows = useMemo(
    () =>
      [...signals]
        .sort(
          (a, b) =>
            Math.abs(b.contribution_to_index_pct ?? 0) - Math.abs(a.contribution_to_index_pct ?? 0),
        )
        .slice(0, limit ?? undefined),
    [signals, limit],
  );

  const totalPages = Math.ceil(rows.length / pageSize);
  const pageRows = rows.slice(page * pageSize, page * pageSize + pageSize);

  if (!rows.length) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground shadow-sm">
        No constituent drivers yet — run analysis to load Nifty 50 company research.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border shadow-sm">
      <div className="flex items-center justify-between border-b bg-muted/20 px-3 py-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          {rows.length} Nifty 50 constituents
        </p>
        {totalPages > 1 ? (
          <div className="flex items-center gap-2 text-[10px]">
            <button
              type="button"
              disabled={page === 0}
              className="rounded border px-2 py-0.5 disabled:opacity-40"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Prev
            </button>
            <span className="text-muted-foreground">
              {page + 1} / {totalPages}
            </span>
            <button
              type="button"
              disabled={page >= totalPages - 1}
              className="rounded border px-2 py-0.5 disabled:opacity-40"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            >
              Next
            </button>
          </div>
        ) : null}
      </div>
      <table className="w-full text-left text-[12px]">
        <thead>
          <tr className="border-b bg-muted/30 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
            <th className="w-8 px-2 py-2" />
            <th className="px-3 py-2 font-semibold">Stock</th>
            <th className="px-3 py-2 font-semibold">Weight</th>
            <th className="px-3 py-2 font-semibold">Sentiment</th>
            <th className="px-3 py-2 font-semibold">Index Δ</th>
            <th className="px-3 py-2 font-semibold">Events</th>
          </tr>
        </thead>
        <tbody>
          {pageRows.map((row) => {
            const sym = row.symbol || "?";
            const isOpen = expanded === sym;
            const eventCount = row.events?.length ?? 0;
            return (
              <Fragment key={sym}>
                <tr
                  className="border-b last:border-0 hover:bg-muted/20 cursor-pointer"
                  onClick={() => setExpanded(isOpen ? null : sym)}
                >
                  <td className="px-2 py-2 text-muted-foreground">
                    {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  </td>
                  <td className="px-3 py-2">
                    <span className="font-medium">{sym}</span>
                    {row.sector ? (
                      <span className="ml-1.5 text-[10px] text-muted-foreground">{row.sector}</span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{fmtWeight(row.weight)}</td>
                  <td className="px-3 py-2 tabular-nums">
                    {row.sentiment_score != null ? (
                      row.sentiment_score.toFixed(2)
                    ) : (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[9px] text-muted-foreground">
                        no signal
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    {row.contribution_to_index_pct != null
                      ? `${row.contribution_to_index_pct >= 0 ? "+" : ""}${row.contribution_to_index_pct.toFixed(3)}%`
                      : "—"}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-muted-foreground">{eventCount || "—"}</td>
                </tr>
                {isOpen ? (
                  <tr className="border-b bg-muted/10">
                    <td colSpan={6} className="px-4 py-3">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-[200px] flex-1">
                          <ConstituentDetailPanel signal={row} />
                        </div>
                        <Link
                          to={`/agent?ticker=${encodeURIComponent(sym)}`}
                          className="inline-flex items-center gap-1 text-[10px] text-primary hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          Stock research
                          <ExternalLink className="h-3 w-3" />
                        </Link>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
