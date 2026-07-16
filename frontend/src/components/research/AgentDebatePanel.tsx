import { ChevronDown, Loader2, Users } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { unescapeLiteralEscapes } from "@/lib/sourceContent";
import type { AgentDebateArtifact } from "@/lib/api";

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeHighlight];

const debateProseClass =
  "prose prose-sm dark:prose-invert max-w-none text-[11px] leading-relaxed prose-p:my-1 prose-headings:my-1.5";

interface Props {
  ticker: string;
  debate: AgentDebateArtifact | null;
  running?: boolean;
  error?: string | null;
  onRunDebate?: () => void;
}

function DebateSection({ title, body }: { title: string; body?: string | null }) {
  if (!body?.trim()) return null;
  const text = unescapeLiteralEscapes(body);
  return (
    <div className="space-y-1">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</div>
      <div className={debateProseClass}>
        <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
          {text}
        </ReactMarkdown>
      </div>
    </div>
  );
}

export function AgentDebatePanel({ ticker, debate, running, error, onRunDebate }: Props) {
  const [open, setOpen] = useState(true);
  const hasContent = Boolean(debate?.rating || debate?.investment_debate || debate?.risk_debate);

  return (
    <div className="overflow-hidden rounded-xl border border-violet-500/20 bg-gradient-to-br from-violet-500/5 via-card to-card shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-muted/20"
      >
        <div className="flex min-w-0 items-center gap-2">
          <div className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-violet-500/15 text-violet-600 dark:text-violet-400">
            <Users className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold">Agent debate · {ticker}</h3>
            <p className="truncate text-[11px] text-muted-foreground">
              {running
                ? "Running multi-agent analysis…"
                : debate?.rating
                  ? `Underlying view: ${debate.rating} (not an options leg)`
                  : "Not run yet"}
            </p>
          </div>
        </div>
        <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition", open && "rotate-180")} />
      </button>

      {open && (
        <div className="space-y-4 border-t px-4 py-4 text-[12px]">
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Multi-agent bull/bear/risk debate on the <strong className="font-medium text-foreground">underlying</strong>.
            Use this to sense-check direction — your options strategy comes from the Trade plan tab.
          </p>
          {running && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span className="text-[11px]">TradingAgents debate in progress (may take a few minutes)…</span>
            </div>
          )}

          {error && (
            <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
              {error}
            </p>
          )}

          {!running && !hasContent && onRunDebate && (
            <button
              type="button"
              onClick={onRunDebate}
              className="w-full rounded-lg border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-xs font-medium text-violet-700 transition hover:bg-violet-500/15 dark:text-violet-300"
            >
              Run TradingAgents debate
            </button>
          )}

          {debate?.investment_debate && (
            <div className="space-y-3">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                Investment debate
              </div>
              <DebateSection title="Bull" body={debate.investment_debate.bull_summary} />
              <DebateSection title="Bear" body={debate.investment_debate.bear_summary} />
              <DebateSection title="Manager" body={debate.investment_debate.judge_decision} />
            </div>
          )}

          {debate?.risk_debate && (
            <div className="space-y-3">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Risk debate</div>
              <DebateSection title="Aggressive" body={debate.risk_debate.aggressive_summary} />
              <DebateSection title="Conservative" body={debate.risk_debate.conservative_summary} />
              <DebateSection title="Neutral" body={debate.risk_debate.neutral_summary} />
              <DebateSection title="Portfolio" body={debate.risk_debate.judge_decision} />
            </div>
          )}

          {debate?.final_trade_decision && (
            <DebateSection title="Final decision" body={debate.final_trade_decision} />
          )}

          {!running && hasContent && onRunDebate && (
            <button
              type="button"
              onClick={onRunDebate}
              className="text-[11px] text-muted-foreground underline-offset-2 hover:underline"
            >
              Refresh debate
            </button>
          )}
        </div>
      )}
    </div>
  );
}
