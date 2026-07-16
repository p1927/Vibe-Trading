import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import type { GoalEvidence, ProvenanceSource } from "@/lib/api";
import { parseSourceContent, unescapeLiteralEscapes } from "@/lib/sourceContent";
import { JsonTreeView } from "@/components/research/JsonTreeView";
import { cn } from "@/lib/utils";

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeHighlight];

const proseClass =
  "prose prose-sm dark:prose-invert max-w-none text-[11px] leading-relaxed prose-p:my-1.5 prose-headings:my-2 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5 prose-pre:my-2 prose-table:text-[10px] prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1";

function TruncationNote() {
  return (
    <p className="mb-2 text-[10px] text-amber-700 dark:text-amber-300">
      Preview truncated — full source may be larger.
    </p>
  );
}

function MarkdownBlock({ content }: { content: string }) {
  return (
    <div className={proseClass}>
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

function StructuredEvidenceView({ data }: { data: GoalEvidence }) {
  const meta: Array<{ label: string; value: string }> = [];
  if (data.source_provider) meta.push({ label: "Provider", value: data.source_provider });
  if (data.evidence_type) meta.push({ label: "Type", value: data.evidence_type });
  if (data.freshness_status) meta.push({ label: "Freshness", value: data.freshness_status });
  if (data.data_as_of) meta.push({ label: "Data as of", value: data.data_as_of });
  if (data.source_uri) meta.push({ label: "Source", value: data.source_uri });

  return (
    <div className="space-y-2 text-[11px]">
      {meta.length > 0 && (
        <dl className="grid grid-cols-2 gap-1.5">
          {meta.map((item) => (
            <div key={item.label} className="rounded border bg-background/50 px-2 py-1.5">
              <dt className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">{item.label}</dt>
              <dd className="mt-0.5 break-words text-foreground">{item.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {data.text?.trim() && <MarkdownBlock content={unescapeLiteralEscapes(data.text)} />}
      {data.caveat?.trim() && (
        <p className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-900 dark:text-amber-200">
          {unescapeLiteralEscapes(data.caveat)}
        </p>
      )}
    </div>
  );
}

interface Props {
  raw: string;
  source?: ProvenanceSource;
  className?: string;
}

export function SourceContentView({ raw, source, className }: Props) {
  const parsed = useMemo(() => parseSourceContent(raw, source), [raw, source]);

  if (!parsed.text) return null;

  const isStructured = parsed.kind === "structured_evidence";

  return (
    <div
      className={cn(
        "p-2",
        !isStructured && "rounded-b-md bg-muted/20",
        className,
      )}
    >
      {parsed.truncated && <TruncationNote />}

      {(parsed.kind === "json" ||
        parsed.kind === "structured_research" ||
        parsed.kind === "structured_debate") &&
        parsed.data !== undefined && (
          <div className="space-y-1.5">
            <div className="text-[9px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              Full source data
            </div>
            <JsonTreeView data={parsed.data} />
          </div>
        )}

      {parsed.kind === "structured_evidence" && <StructuredEvidenceView data={parsed.data as GoalEvidence} />}

      {parsed.kind === "json" && parsed.data === undefined && (
        <pre className="whitespace-pre-wrap break-words text-[10px] leading-relaxed text-muted-foreground">
          {parsed.text}
        </pre>
      )}

      {parsed.kind === "markdown" && <MarkdownBlock content={parsed.text} />}

      {parsed.kind === "text" && (
        <pre className="whitespace-pre-wrap break-words text-[10px] leading-relaxed text-muted-foreground">
          {parsed.text}
        </pre>
      )}
    </div>
  );
}
