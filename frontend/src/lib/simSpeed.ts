// Poll cadence for the Stock Simulator's replay consumers (chart, option
// chain, sim-clock status bar) — scales with replay speed so a faster
// replay actually delivers ticks more often, not just bigger sim-time jumps
// per tick, capped at 4/sec (250ms) to stay sane for a UI. Mirrors
// SimClock.emit_interval_seconds() in
// integrations/trade_integrations/stock_simulator/sim_clock.py — keep the
// two in sync if this formula ever changes.
export function replayPollMs(speed: number | undefined): number {
  const s = Math.max(0.01, Math.min(speed ?? 1, 4));
  return Math.round(1000 / s);
}
