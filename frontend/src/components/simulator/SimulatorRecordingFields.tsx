import { createContext, useContext } from "react";
import { Database, LineChart, Receipt, User2, Radio, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  DEFAULT_CATEGORY_INTERVALS,
  DEFAULT_EQUITY_INTERVALS,
  DEFAULT_HISTORICAL_CONFIG,
  DEFAULT_WS_THROTTLE_HZ,
  HISTORICAL_INTERVAL_OPTIONS,
  HISTORICAL_LOOKBACK_OPTIONS,
  INTERVAL_OPTIONS,
  WS_THROTTLE_OPTIONS,
  clampHistoricalLookbackDays,
  daysToHistoricalLookbackKey,
  historicalIntervalMaxLookbackDays,
  historicalLookbackKeyToDays,
  hzToWsKey,
  intervalKeyToSeconds,
  secondsToIntervalKey,
  wsKeyToHz,
  type HistoricalIntervalKey,
  type HistoricalLookbackKey,
} from "@/lib/intervalOptions";

/**
 * Disclosure listing every INDmoney (IndStocks) API surface that the
 * stock simulator recorder can — or could — persist during a recording
 * session. The recorder currently captures a subset of these; the rest
 * are listed for transparency so the user can see the full surface and
 * what would be needed to extend coverage.
 *
 * Cadence per item is governed by two knobs the recording config
 * surfaces today (`poll_interval_s` for the REST poll cycle, default 10s,
 * and the WebSocket tick stream which delivers sub-second underlying
 * LTPs whenever the upstream pushes). Anything tagged "Per-poll cycle"
 * runs at `poll_interval_s`; "Per tick" runs whenever the WS pushes;
 * "Per request" runs once at recording start.
 */
export type RecordingFieldStatus = "recorded" | "wired" | "available";

export type RecordingFieldCadence =
  | "per-tick"
  | "per-poll"
  | "per-request"
  | "session-end"
  | "on-demand";

export interface RecordingField {
  /** Human-readable field label, e.g. "Per-strike option chain (CE/PE)" */
  label: string;
  /** Concrete data points we capture — LTP, OI, IV, greeks, depth, ... */
  fields: string[];
  /** How often this item is sampled during a recording. */
  cadence: RecordingFieldCadence;
  /** Recorded today / wired elsewhere on OpenAlgo / available but not wired. */
  status: RecordingFieldStatus;
  /** Endpoint path on the INDmoney REST API, where applicable. */
  endpoint?: string;
  /**
   * If this row is user-controllable (intervalled by the user from the
   * disclosure), the kind of control it renders. The category key the
   * control binds to is `controlCategory` (for REST polls) or implied
   * (for the WS LTP row, which is the only WebSocket control).
   */
  controlKind?: "interval" | "ws-throttle" | "historical";
  /**
   * The category key passed to the recorder (matches
   * ``CategoryScheduler.intervals`` keys). Only set when
   * ``controlKind === "interval"``.
   */
  controlCategory?:
    | "option_chain"
    | "market_depth"
    | "full_quote"
    | "equity_option_chain"
    | "equity_market_depth"
    | "equity_full_quote";
}

export interface RecordingFieldGroup {
  /** Group label rendered as a row header. */
  title: string;
  /** Lucide icon for the row header. */
  icon: React.ComponentType<{ className?: string }>;
  /** One-line subtitle that appears under the title. */
  hint: string;
  /** Sub-groups, each rendered as its own sub-section. */
  sections: {
    name: string;
    fields: RecordingField[];
  }[];
}

const CADENCE_LABEL: Record<RecordingFieldCadence, string> = {
  "per-tick": "Per tick (WebSocket)",
  "per-poll": "Per poll cycle (configurable: 10s default, 1m supported)",
  "per-request": "Once per session (instruments master)",
  "session-end": "On session end / export",
  "on-demand": "On demand (triggered)",
};

const STATUS_LABEL: Record<RecordingFieldStatus, string> = {
  recorded: "Recorded today",
  wired: "Wired (used by OpenAlgo, not the recorder)",
  available: "Available — not yet wired",
};

const STATUS_STYLE: Record<RecordingFieldStatus, string> = {
  recorded: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  wired: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  available: "bg-muted text-muted-foreground",
};

export const RECORDING_FIELD_GROUPS: RecordingFieldGroup[] = [
  {
    title: "Market data",
    icon: LineChart,
    hint: "Quotes, depth, chains, and historical candles — what the recorder captures per poll cycle.",
    sections: [
      {
        name: "Live (WebSocket tick stream)",
        fields: [
          {
            label: "Underlying LTP ticks",
            fields: [
              "last_traded_price",
              "timestamp (server time)",
              "symbol / scrip_code",
            ],
            cadence: "per-tick",
            status: "recorded",
            endpoint: "wss (IndStocks tick stream)",
            controlKind: "ws-throttle",
          },
        ],
      },
      {
        name: "REST poll (interval = recording config, default 10s)",
        fields: [
          {
            label: "Per-strike option chain (CE/PE)",
            fields: [
              "security_id",
              "last_price (LTP)",
              "oi",
              "volume",
              "top_bid_price / top_bid_quantity",
              "top_ask_price / top_ask_quantity",
              "iv (implied volatility)",
              "greeks.delta / gamma / theta / vega",
            ],
            cadence: "per-poll",
            status: "recorded",
            endpoint: "/market/option-chain",
            controlKind: "interval",
            controlCategory: "option_chain",
          },
          {
            label: "5-level market depth (bid/ask)",
            fields: [
              "5 buy levels (price, quantity)",
              "5 sell levels (price, quantity)",
              "aggregate.total_buy / total_sell",
            ],
            cadence: "per-poll",
            status: "recorded",
            endpoint: "/market/quotes/mkt",
            controlKind: "interval",
            controlCategory: "market_depth",
          },
          {
            label: "Full quote (OHLC + volume + circuits)",
            fields: [
              "live_price",
              "day_open / day_high / day_low",
              "volume",
              "lower_circuit / upper_circuit",
              "previous_close",
            ],
            cadence: "per-poll",
            status: "recorded",
            endpoint: "/market/quotes/full",
            controlKind: "interval",
            controlCategory: "full_quote",
          },
          {
            label: "Last traded price (single scrip)",
            fields: ["ltp", "timestamp"],
            cadence: "on-demand",
            status: "available",
            endpoint: "/market/quotes/ltp",
          },
          {
            label: "Bulk quotes (multi scrip-codes)",
            fields: ["ltp + open/high/low/volume per scrip"],
            cadence: "on-demand",
            status: "wired",
            endpoint: "/market/quotes",
          },
        ],
      },
      {
        name: "Per-equity (NIFTY50) REST polls (shared cadence across selected equities)",
        fields: [
          {
            label: "Per-equity option chain (CE/PE)",
            fields: [
              "security_id",
              "last_price (LTP)",
              "oi",
              "volume",
              "top_bid_price / top_bid_quantity",
              "top_ask_price / top_ask_quantity",
              "iv (implied volatility)",
              "greeks.delta / gamma / theta / vega",
            ],
            cadence: "per-poll",
            status: "recorded",
            endpoint: "/market/option-chain",
            controlKind: "interval",
            controlCategory: "equity_option_chain",
          },
          {
            label: "Per-equity 5-level depth (bid/ask)",
            fields: [
              "5 buy levels (price, quantity)",
              "5 sell levels (price, quantity)",
              "aggregate.total_buy / total_sell",
            ],
            cadence: "per-poll",
            status: "recorded",
            endpoint: "/market/quotes/mkt",
            controlKind: "interval",
            controlCategory: "equity_market_depth",
          },
          {
            label: "Per-equity full quote (OHLC + volume)",
            fields: [
              "live_price",
              "day_open / day_high / day_low",
              "volume",
              "lower_circuit / upper_circuit",
              "previous_close",
            ],
            cadence: "per-poll",
            status: "recorded",
            endpoint: "/market/quotes/full",
            controlKind: "interval",
            controlCategory: "equity_full_quote",
          },
        ],
      },
      {
        name: "Master + historical",
        fields: [
          {
            label: "Instrument master (security_id, exchange, segment)",
            fields: [
              "trading_symbol",
              "security_id",
              "exchange",
              "segment (INDEX / NSE_EQ / NFO / ...)",
              "tick_size / lot_size / freeze_qty",
            ],
            cadence: "per-request",
            status: "recorded",
            endpoint: "/market/instruments",
          },
          {
            label: "Historical candles (once per session, all selected equities)",
            fields: [
              "open / high / low / close / volume",
              "intervals: 1m / 5m / 15m / 30m / 1h / 2h / 4h / 1d / 1w / 1mo",
              "lookback: 1d .. 365d (clamped per interval)",
            ],
            cadence: "per-request",
            status: "recorded",
            endpoint: "/market/historical/{interval}",
            controlKind: "historical",
          },
        ],
      },
    ],
  },
  {
    title: "Session metadata",
    icon: Database,
    hint: "What the recorder itself writes around the captured data.",
    sections: [
      {
        name: "Job + export bookkeeping",
        fields: [
          {
            label: "Per-job metadata",
            fields: [
              "session_date (IST)",
              "job_id",
              "poll_interval_s",
              "underlyings[]",
              "equities[]",
              "status (queued/running/done/error)",
              "started_at / stopped_at",
              "cycles / errors[]",
            ],
            cadence: "per-request",
            status: "recorded",
          },
          {
            label: "Replay parquet export",
            fields: [
              "option_chain.parquet (per expiry)",
              "market_depth.parquet (per poll)",
              "market_ticks.parquet (WS + REST)",
            ],
            cadence: "session-end",
            status: "recorded",
          },
        ],
      },
    ],
  },
];

function StatusBadge({ status }: { status: RecordingFieldStatus }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10px] font-medium",
        STATUS_STYLE[status],
      )}
      title={STATUS_LABEL[status]}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

function CadenceLabel({ cadence }: { cadence: RecordingFieldCadence }) {
  return (
    <span className="inline-flex items-center rounded border bg-background/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      {CADENCE_LABEL[cadence]}
    </span>
  );
}

interface FieldRowProps {
  field: RecordingField;
  /** Whether the row's control (if any) is interactive. */
  disabled?: boolean;
}

function FieldRow({ field, disabled }: FieldRowProps) {
  return (
    <div className="rounded-lg border bg-background/40 p-2.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-foreground/90">{field.label}</span>
            {field.endpoint ? (
              <code className="rounded bg-muted px-1 py-px text-[10px] font-mono text-muted-foreground">
                {field.endpoint}
              </code>
            ) : null}
          </div>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {field.fields.map((f) => (
              <li
                key={f}
                className="rounded border bg-background/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
              >
                {f}
              </li>
            ))}
          </ul>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <StatusBadge status={field.status} />
          <CadenceLabel cadence={field.cadence} />
          <RowControl field={field} disabled={disabled} />
        </div>
      </div>
    </div>
  );
}

/**
 * Per-row editor. Renders nothing for rows without a ``controlKind``
 * (per-request / on-demand / available — those are read-only).
 */
interface RowControlProps {
  field: RecordingField;
  disabled?: boolean;
}

function RowControl({ field, disabled }: RowControlProps) {
  if (!field.controlKind) return null;
  if (field.controlKind === "interval" && field.controlCategory) {
    return (
      <IntervalControl
        category={field.controlCategory}
        disabled={disabled}
      />
    );
  }
  if (field.controlKind === "ws-throttle") {
    return <WsThrottleControl disabled={disabled} />;
  }
  if (field.controlKind === "historical") {
    return <HistoricalControl disabled={disabled} />;
  }
  return null;
}

/**
 * Reads / writes the per-category interval via the
 * ``useRecordingConfig`` hook (provided by the parent page). The
 * hook lifts state out of this component so the parent can pass the
 * map into ``api.startRecording``.
 */
interface RecordingConfig {
  categoryIntervals: Record<string, number>;
  equityIntervals: Record<string, number>;
  wsThrottleHz: number | null;
  historicalConfig: {
    interval: HistoricalIntervalKey;
    lookback_days: HistoricalLookbackKey;
  } | null;
  setCategoryInterval: (category: string, seconds: number) => void;
  setEquityInterval: (category: string, seconds: number) => void;
  setWsThrottle: (hz: number | null) => void;
  setHistoricalConfig: (
    next: {
      interval: HistoricalIntervalKey;
      lookback_days: HistoricalLookbackKey;
    } | null,
  ) => void;
}

const RecordingConfigContext = createContext<RecordingConfig | null>(null);

function _resolveConfig(overrides?: Partial<RecordingConfig>): RecordingConfig {
  return {
    categoryIntervals: { ...DEFAULT_CATEGORY_INTERVALS, ...(overrides?.categoryIntervals ?? {}) },
    equityIntervals: { ...DEFAULT_EQUITY_INTERVALS, ...(overrides?.equityIntervals ?? {}) },
    wsThrottleHz: overrides?.wsThrottleHz ?? DEFAULT_WS_THROTTLE_HZ,
    historicalConfig: overrides?.historicalConfig ?? DEFAULT_HISTORICAL_CONFIG,
    setCategoryInterval: overrides?.setCategoryInterval ?? (() => {}),
    setEquityInterval: overrides?.setEquityInterval ?? (() => {}),
    setWsThrottle: overrides?.setWsThrottle ?? (() => {}),
    setHistoricalConfig: overrides?.setHistoricalConfig ?? (() => {}),
  };
}

/** The page wraps the disclosure (or the whole record card) in this
 * provider so the row dropdowns share state with the page's "Start
 * Recording" handler. */
export function RecordingConfigProvider(props: {
  children: React.ReactNode;
  config?: Partial<RecordingConfig>;
}) {
  return (
    <RecordingConfigContext.Provider value={_resolveConfig(props.config)}>
      {props.children}
    </RecordingConfigContext.Provider>
  );
}

export function useRecordingConfig(): RecordingConfig {
  const ctx = useContext(RecordingConfigContext);
  if (ctx) return ctx;
  // Fallback when used outside a provider (e.g. unit tests): a no-op
  // config. Renders still work; dropdowns are effectively inert.
  return _resolveConfig();
}

/** Fallback cadence restored when a toggle is switched back on after
 * having been turned off (so re-enabling doesn't leave the interval at
 * 0/off). Mirrors the page's non-zero defaults. */
const RESTORE_SECONDS_FALLBACK: Record<string, number> = {
  option_chain: 10,
  market_depth: 10,
  full_quote: 30,
  equity_option_chain: 10,
  equity_market_depth: 10,
  equity_full_quote: 30,
};

function IntervalControl({
  category,
  disabled,
}: {
  category:
    | "option_chain"
    | "market_depth"
    | "full_quote"
    | "equity_option_chain"
    | "equity_market_depth"
    | "equity_full_quote";
  disabled?: boolean;
}) {
  // The six interval keys split into two maps: the three index
  // categories and the three equity categories. We route the lookup
  // (and the setter) so the shared UI works for both groups.
  const isEquity = category.startsWith("equity_");
  const ctx = useRecordingConfig();
  const seconds = isEquity
    ? ctx.equityIntervals[category] ?? 0
    : ctx.categoryIntervals[category] ?? 0;
  const selectedKey = secondsToIntervalKey(seconds);
  const setSeconds = (s: number) => {
    if (isEquity) ctx.setEquityInterval(category, s);
    else ctx.setCategoryInterval(category, s);
  };
  const enabled = seconds > 0;
  return (
    <div className="flex items-center gap-2">
      <label className="inline-flex items-center gap-1.5" title={enabled ? "Recording this field" : "Not recorded"}>
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(e) =>
            setSeconds(e.target.checked ? RESTORE_SECONDS_FALLBACK[category] ?? 10 : 0)
          }
          className="h-3.5 w-3.5 rounded border-border"
          data-testid={`interval-toggle-${category}`}
          aria-label={`Record ${category}`}
        />
      </label>
      <label className="inline-flex items-center gap-1.5">
        <span className="text-[10px] text-muted-foreground">Record every:</span>
        <select
          value={selectedKey}
          disabled={disabled || !enabled}
          onChange={(e) => {
            const next = intervalKeyToSeconds(e.target.value);
            setSeconds(next ?? 0);
          }}
          className="rounded border bg-background px-1.5 py-0.5 text-[10px]"
          data-testid={`interval-select-${category}`}
          aria-label={`Recording interval for ${category}`}
        >
          {INTERVAL_OPTIONS.map((opt) => (
            <option key={opt.key} value={opt.key}>
              {opt.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function HistoricalControl({ disabled }: { disabled?: boolean }) {
  const { historicalConfig, setHistoricalConfig } = useRecordingConfig();
  const interval = historicalConfig?.interval ?? "1d";
  const lookback = historicalConfig?.lookback_days ?? "30d";
  const maxDays = historicalIntervalMaxLookbackDays(interval);
  // When the user changes the interval, auto-clamp the lookback to
  // the new max so they don't end up sending a window that the API
  // rejects (and we don't end up silently clamping on the server).
  const handleIntervalChange = (nextKey: HistoricalIntervalKey) => {
    const nextMax = historicalIntervalMaxLookbackDays(nextKey);
    const currentDays = historicalLookbackKeyToDays(lookback);
    const clampedDays = clampHistoricalLookbackDays(nextKey, currentDays);
    const nextLookback = daysToHistoricalLookbackKey(clampedDays);
    setHistoricalConfig({ interval: nextKey, lookback_days: nextLookback });
    if (clampedDays < currentDays && clampedDays < nextMax) {
      // (silently — the legend reports the max-lookback rule)
    }
  };
  return (
    <div className="flex flex-col items-end gap-1">
      <label className="inline-flex items-center gap-1.5">
        <span className="text-[10px] text-muted-foreground">Interval:</span>
        <select
          value={interval}
          disabled={disabled}
          onChange={(e) => handleIntervalChange(e.target.value as HistoricalIntervalKey)}
          className="rounded border bg-background px-1.5 py-0.5 text-[10px]"
          data-testid="historical-interval-select"
          aria-label="Historical candle interval"
        >
          {HISTORICAL_INTERVAL_OPTIONS.map((opt) => (
            <option key={opt.key} value={opt.key}>
              {opt.label}
            </option>
          ))}
        </select>
      </label>
      <label className="inline-flex items-center gap-1.5">
        <span className="text-[10px] text-muted-foreground">Lookback:</span>
        <select
          value={lookback}
          disabled={disabled}
          onChange={(e) =>
            setHistoricalConfig({
              interval,
              lookback_days: e.target.value as HistoricalLookbackKey,
            })
          }
          className="rounded border bg-background px-1.5 py-0.5 text-[10px]"
          data-testid="historical-lookback-select"
          aria-label="Historical candle lookback window"
        >
          {HISTORICAL_LOOKBACK_OPTIONS.map((opt) => (
            <option key={opt.key} value={opt.key}>
              {opt.label}
            </option>
          ))}
        </select>
        <span className="text-[10px] text-muted-foreground">
          (max {maxDays}d for {interval})
        </span>
      </label>
    </div>
  );
}

function WsThrottleControl({ disabled }: { disabled?: boolean }) {
  const { wsThrottleHz, setWsThrottle } = useRecordingConfig();
  const selectedKey = hzToWsKey(wsThrottleHz);
  return (
    <label className="inline-flex items-center gap-1.5">
      <span className="text-[10px] text-muted-foreground">Max recorded rate:</span>
      <select
        value={selectedKey}
        disabled={disabled}
        onChange={(e) => {
          setWsThrottle(wsKeyToHz(e.target.value));
        }}
        className="rounded border bg-background px-1.5 py-0.5 text-[10px]"
        data-testid="ws-throttle-select"
        aria-label="WebSocket LTP recorded-rate"
      >
        {WS_THROTTLE_OPTIONS.map((opt) => (
          <option key={opt.key} value={opt.key}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * Collapsible "What we record" disclosure.
 *
 * Rendered inside the Record stat card so a user expanding it sees the
 * full INDmoney surface — what we capture today, what OpenAlgo already
 * wires elsewhere, and what would be needed to extend coverage.
 *
 * The disclosure is a **controlled editor**: REST-poll rows render an
 * "interval" dropdown, the WS LTP row renders a "throttle" dropdown.
 * Both bind through ``useRecordingConfig`` to the parent page's state,
 * which is passed into ``api.startRecording`` as ``category_intervals``
 * and ``ws_throttle_hz``.
 */
export function SimulatorRecordingFields({ disabled }: { disabled?: boolean } = {}) {
  return (
    <details
      className="group mt-3 rounded-lg border bg-background/40 px-3 py-2 text-xs"
      data-testid="simulator-recording-fields"
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-muted-foreground transition-colors hover:text-foreground">
        <span className="flex items-center gap-1.5">
          <Receipt className="h-3.5 w-3.5" />
          What we record (INDmoney API surface)
        </span>
        <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
      </summary>

      <div className="mt-3 space-y-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
          <span className="font-semibold uppercase tracking-[0.12em]">Legend:</span>
          {(Object.keys(STATUS_LABEL) as RecordingFieldStatus[]).map((s) => (
            <span key={s} className="inline-flex items-center gap-1">
              <span
                className={cn("h-2 w-2 rounded-full", STATUS_STYLE[s].split(" ")[0])}
                aria-hidden
              />
              {STATUS_LABEL[s]}
            </span>
          ))}
          <span className="inline-flex items-center gap-1">
            <Radio className="h-3 w-3" />
            Each REST poll has its own cadence; WS LTP has its own max recorded rate;
            historical candles pull once per session across all selected NIFTY50 equities.
            Disabled while a recording is active.
          </span>
        </div>

        <div className="space-y-4">
          {RECORDING_FIELD_GROUPS.map((group) => {
            const Icon = group.icon;
            return (
              <section key={group.title} className="space-y-2">
                <header className="flex items-center gap-2 border-b pb-1">
                  <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                  <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-foreground">
                    {group.title}
                  </h3>
                  <span className="text-[10px] text-muted-foreground">— {group.hint}</span>
                </header>
                <div className="space-y-2">
                  {group.sections.map((section) => (
                    <div key={section.name} className="space-y-1.5">
                      <p className="text-[10px] font-medium text-muted-foreground">
                        {section.name}
                      </p>
                      <div className="space-y-1.5">
                        {section.fields.map((field) => (
                          <FieldRow key={field.label} field={field} disabled={disabled} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </div>

        <p className="flex items-center gap-1.5 border-t pt-2 text-[10px] text-muted-foreground">
          <User2 className="h-3 w-3" />
          Read-only endpoints above are consumed by the OpenAlgo adapter
          (<code className="font-mono">openalgo/broker/indmoney/api/</code>); the day
          recorder itself only writes the items tagged <em>Recorded today</em>.
        </p>
      </div>
    </details>
  );
}
