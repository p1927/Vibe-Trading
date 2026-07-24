import ReactMarkdown, { type Options as ReactMarkdownOptions } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { normalizeMathDelimiters } from "@/lib/markdown";
import { SourceCitationLink } from "@/components/research/ContextDrawer";
import { preprocessCitationLinks } from "@/stores/provenance";

// singleDollarTextMath off: dollar amounts ("$150 to $120") must never parse as
// formulas; LLM \(...\)/\[...\] delimiters are normalized to $$ before render.
const remarkPlugins: ReactMarkdownOptions["remarkPlugins"] = [
  remarkGfm,
  [remarkMath, { singleDollarTextMath: false }],
];
const rehypePlugins: ReactMarkdownOptions["rehypePlugins"] = [rehypeHighlight, rehypeKatex];

const proseClassName =
  "prose prose-sm dark:prose-invert max-w-none leading-relaxed prose-table:border prose-table:border-border/50 prose-th:bg-muted/30 prose-th:px-3 prose-th:py-1.5 prose-td:px-3 prose-td:py-1.5 prose-th:text-left prose-th:text-xs prose-th:font-medium prose-td:text-xs prose-hr:hidden";

interface MarkdownContentProps {
  content: string;
  showCursor?: boolean;
}

export function MarkdownContent({ content, showCursor = false }: MarkdownContentProps) {
  const markdown = preprocessCitationLinks(content);

  return (
    <div className={proseClassName}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#source-")) {
              const refId = href.slice("#source-".length);
              return <SourceCitationLink refId={refId} label={String(children)} />;
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {normalizeMathDelimiters(markdown)}
      </ReactMarkdown>
      {showCursor && (
        <span className="inline-block w-0.5 h-4 bg-primary ml-0.5 animate-pulse align-middle" />
      )}
    </div>
  );
}
