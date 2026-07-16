import { ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { tryParseJsonDeep } from "@/lib/sourceContent";
import { cn } from "@/lib/utils";

const LONG_STRING = 1200;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatPrimitive(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  return String(value);
}

function StringValue({ value, depth }: { value: string; depth: number }) {
  const nested = useMemo(() => tryParseJsonDeep(value), [value]);
  const [expanded, setExpanded] = useState(true);
  const collapsed = !expanded && value.length > LONG_STRING;

  if (nested !== null && typeof nested !== "string") {
    return (
      <span className="inline min-w-0">
        <span className="text-emerald-700 dark:text-emerald-400">(parsed string)</span>
        <div className="mt-0.5">
          <JsonNode value={nested} depth={depth + 1} />
        </div>
      </span>
    );
  }

  return (
    <span className="inline min-w-0">
      <span className="text-emerald-700 dark:text-emerald-400">"</span>
      {collapsed ? (
        <>
          <span className="whitespace-pre-wrap break-words text-emerald-800 dark:text-emerald-300">
            {value.slice(0, LONG_STRING)}…
          </span>
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="ml-1 text-[9px] text-primary underline-offset-2 hover:underline"
          >
            show {value.length - LONG_STRING} more chars
          </button>
        </>
      ) : (
        <span className="whitespace-pre-wrap break-words text-emerald-800 dark:text-emerald-300">{value}</span>
      )}
      {expanded && value.length > LONG_STRING && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="ml-1 block text-[9px] text-primary underline-offset-2 hover:underline"
        >
          collapse
        </button>
      )}
      <span className="text-emerald-700 dark:text-emerald-400">"</span>
    </span>
  );
}

function CollectionHeader({
  label,
  count,
  collapsed,
  onToggle,
}: {
  label: string;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="inline-flex items-center gap-0.5 text-left text-muted-foreground hover:text-foreground"
    >
      {collapsed ? <ChevronRight className="h-3 w-3 shrink-0" /> : <ChevronDown className="h-3 w-3 shrink-0" />}
      <span className="font-mono text-sky-700 dark:text-sky-400">{label}</span>
      <span className="text-[9px] text-muted-foreground/70">({count} item{count === 1 ? "" : "s"})</span>
    </button>
  );
}

function JsonNode({ name, value, depth = 0 }: { name?: string; value: unknown; depth?: number }) {
  const [collapsed, setCollapsed] = useState(false);

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return (
        <div className="pl-3">
          {name !== undefined && <span className="text-violet-700 dark:text-violet-400">{name}: </span>}
          <span className="text-muted-foreground">[]</span>
        </div>
      );
    }

    return (
      <div className={cn(depth > 0 && "border-l border-border/40 pl-2")}>
        <CollectionHeader
          label={name !== undefined ? `${name} [` : "["}
          count={value.length}
          collapsed={collapsed}
          onToggle={() => setCollapsed((v) => !v)}
        />
        {!collapsed && (
          <div className="mt-0.5 space-y-0.5">
            {value.map((item, index) => (
              <JsonNode key={index} name={String(index)} value={item} depth={depth + 1} />
            ))}
          </div>
        )}
        {!collapsed && <div className="text-muted-foreground">{name !== undefined ? "]" : ""}</div>}
      </div>
    );
  }

  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) {
      return (
        <div className="pl-3">
          {name !== undefined && <span className="text-violet-700 dark:text-violet-400">{name}: </span>}
          <span className="text-muted-foreground">{"{}"}</span>
        </div>
      );
    }

    return (
      <div className={cn(depth > 0 && "border-l border-border/40 pl-2")}>
        <CollectionHeader
          label={name !== undefined ? `${name} {` : "{"}
          count={entries.length}
          collapsed={collapsed}
          onToggle={() => setCollapsed((v) => !v)}
        />
        {!collapsed && (
          <div className="mt-0.5 space-y-0.5">
            {entries.map(([key, child]) => (
              <JsonNode key={key} name={key} value={child} depth={depth + 1} />
            ))}
          </div>
        )}
        {!collapsed && <div className="text-muted-foreground">{name !== undefined ? "}" : ""}</div>}
      </div>
    );
  }

  const primitive = formatPrimitive(value);

  return (
    <div className="min-w-0 pl-3 leading-relaxed">
      {name !== undefined && <span className="text-violet-700 dark:text-violet-400">{name}: </span>}
      {typeof value === "string" ? (
        <StringValue value={value} depth={depth} />
      ) : typeof value === "number" ? (
        <span className="tabular-nums text-amber-700 dark:text-amber-400">{primitive}</span>
      ) : typeof value === "boolean" ? (
        <span className="text-sky-700 dark:text-sky-400">{primitive}</span>
      ) : (
        <span className="text-muted-foreground">{primitive}</span>
      )}
    </div>
  );
}

export function JsonTreeView({ data }: { data: unknown }) {
  const root = useMemo(() => data, [data]);

  return (
    <div className="font-mono text-[10px] leading-relaxed text-foreground">
      <JsonNode value={root} depth={0} />
    </div>
  );
}
