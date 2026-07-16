import { memo, useCallback, useMemo, useState } from "react";
import {
  BarChart3,
  Check,
  ExternalLink,
  Loader2,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { toast } from "sonner";
import { api, type TradePlanScenario, type TradePlanWidget } from "@/lib/api";
import { AgentAvatar } from "./AgentAvatar";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { MiniPayoffChart } from "@/components/charts/MiniPayoffChart";

interface Props {
  widget: TradePlanWidget;
}

function formatInr(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function ScenarioTile({
  scenario,
  active,
  onSelect,
}: {
  scenario: TradePlanScenario;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={[
        "rounded-lg border p-2.5 text-left text-xs transition-colors w-full",
        active ? "border-primary/60 bg-primary/5" : "border-border/60 bg-muted/15 hover:border-primary/30",
      ].join(" ")}
    >
      <div className="font-semibold text-foreground">{scenario.name}</div>
      <div className="mt-1 text-muted-foreground line-clamp-2">{scenario.trigger}</div>
      <div className="mt-1 font-mono text-[10px] text-primary">
        {(Number(scenario.probability) * 100).toFixed(0)}% · {scenario.strategy_hint}
      </div>
    </button>
  );
}

export const TradePlanWidgetCard = memo(function TradePlanWidgetCard({ widget }: Props) {
  const [selectedScenario, setSelectedScenario] = useState(0);
  const [executing, setExecuting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [executed, setExecuted] = useState(false);

  const rec = widget.recommended || {};
  const charges = widget.charges || {};
  const payoff = widget.payoff || {};
  const pred = widget.prediction || {};
  const scenarios = widget.scenarios || [];
  const ranked = widget.ranked_strategies || [];

  const payoffSamples = useMemo(
    () => (payoff.samples || []).filter((s) => s.spot != null && (s.pnl != null || s.net_pnl != null)),
    [payoff.samples],
  );

  const executeOrders = useMemo(() => {
    for (const step of widget.implementation_steps || []) {
      if (step.action === "execute_basket" && step.payload?.orders) {
        return step.payload.orders as Record<string, unknown>[];
      }
    }
    return (rec.legs || []) as Record<string, unknown>[];
  }, [widget.implementation_steps, rec.legs]);

  const handleExecute = useCallback(async () => {
    setExecuting(true);
    try {
      const result = await api.executeTradeBasket({
        widget_id: widget.widget_id,
        orders: executeOrders,
      });
      setExecuted(true);
      toast.success(result.message || "Basket order submitted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Execution failed");
    } finally {
      setExecuting(false);
      setConfirmOpen(false);
    }
  }, [executeOrders, widget.widget_id]);

  const builderUrl = widget.meta?.strategy_builder_execute_url || widget.meta?.strategy_builder_url;

  return (
    <div className="flex gap-3">
      <AgentAvatar />
      <div className="flex-1 min-w-0 space-y-3 rounded-xl border border-border/60 bg-card/50 p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <BarChart3 className="h-4 w-4 text-primary" />
              Trade plan — {widget.underlying}
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              {pred.view || "neutral"} · IV {pred.iv_regime || "—"} · confidence {pred.confidence ?? "—"}
            </p>
          </div>
          {rec.tier && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold uppercase text-primary">
              {rec.tier}
            </span>
          )}
        </div>

        {scenarios.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1.5">Scenarios (agent assumptions)</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {scenarios.slice(0, 4).map((sc, i) => (
                <ScenarioTile
                  key={sc.name || i}
                  scenario={sc}
                  active={selectedScenario === i}
                  onSelect={() => setSelectedScenario(i)}
                />
              ))}
            </div>
          </div>
        )}

        {payoffSamples.length >= 2 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">Payoff at expiry (recommended)</p>
            <MiniPayoffChart samples={payoffSamples} spot={widget.spot} height={120} />
          </div>
        )}

        {rec.name && (
          <div className="rounded-lg border border-border/50 bg-muted/20 p-3 text-xs space-y-2">
            <div className="flex items-center gap-2 font-semibold text-foreground">
              <TrendingUp className="h-3.5 w-3.5" />
              Recommended: {rec.name} (score {rec.score})
            </div>
            {rec.rationale && <p className="text-muted-foreground">{rec.rationale}</p>}
            <div className="grid grid-cols-2 gap-2 font-mono text-[11px]">
              <div>Gross max P: {formatInr(payoff.gross_max_profit ?? rec.max_profit)}</div>
              <div>Gross max L: {formatInr(payoff.gross_max_loss ?? rec.max_loss)}</div>
              <div>Net max P: {formatInr(payoff.net_max_profit ?? rec.net_max_profit)}</div>
              <div>Net max L: {formatInr(payoff.net_max_loss ?? rec.net_max_loss)}</div>
            </div>
            {(rec.legs || []).length > 0 && (
              <ul className="space-y-0.5 text-[11px]">
                {(rec.legs || []).map((leg, i) => (
                  <li key={i} className="font-mono">
                    {leg.side} {leg.quantity}× {leg.symbol} @ {leg.price}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="rounded-lg border border-border/50 p-3 text-xs">
          <div className="flex items-center gap-2 font-semibold mb-1">
            <Wallet className="h-3.5 w-3.5" />
            Charges &amp; costs
          </div>
          <div className="grid grid-cols-2 gap-1 font-mono text-[11px] text-muted-foreground">
            <div>Net debit/credit: {formatInr(charges.net_debit_credit)}</div>
            <div>Round-trip est.: {formatInr(charges.round_trip_charges)}</div>
          </div>
          {(charges.per_leg || []).length > 0 && (
            <ul className="mt-2 space-y-0.5 text-[10px] text-muted-foreground">
              {(charges.per_leg || []).slice(0, 4).map((row, i) => (
                <li key={i}>
                  {row.symbol || row.leg}: brokerage {formatInr(row.brokerage)} · STT {formatInr(row.stt)} · GST {formatInr(row.gst)}
                </li>
              ))}
            </ul>
          )}
        </div>

        {ranked.length > 1 && (
          <div className="text-[10px] text-muted-foreground">
            Alternatives: {ranked.slice(1, 4).map((s) => `${s.name} (${s.tier})`).join(" · ")}
          </div>
        )}

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            type="button"
            disabled={executing || executed || executeOrders.length === 0}
            onClick={() => setConfirmOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
          >
            {executing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : executed ? <Check className="h-3.5 w-3.5" /> : null}
            {executed ? "Submitted" : "Execute in OpenAlgo"}
          </button>
          {builderUrl && (
            <a
              href={builderUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Open Strategy Builder
            </a>
          )}
        </div>

        <ConfirmDialog
          open={confirmOpen}
          title="Execute trade plan?"
          description={`Place ${executeOrders.length} leg(s) for ${widget.underlying} via OpenAlgo. Net debit/credit ${formatInr(charges.net_debit_credit)}.`}
          confirmLabel="Place basket order"
          cancelLabel="Cancel"
          onConfirm={handleExecute}
          onCancel={() => setConfirmOpen(false)}
        />
      </div>
    </div>
  );
});
