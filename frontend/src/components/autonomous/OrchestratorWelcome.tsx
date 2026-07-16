import { memo } from "react";
import { Radio } from "lucide-react";

const EXAMPLES = [
  "Create a NIFTY intraday paper agent — ₹50,000 budget, max loss ₹5,000, watch every 5 minutes.",
  "BANKNIFTY event-vol watcher: paper trade straddles when VIX > 14, research every 2 hours.",
  "RELIANCE swing paper agent with ₹20k budget — use defaults unless you need one clarification.",
];

interface Props {
  onExample: (text: string) => void;
}

export const OrchestratorWelcome = memo(function OrchestratorWelcome({ onExample }: Props) {
  return (
    <div className="mx-auto max-w-lg space-y-4 py-8 text-center">
      <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
        <Radio className="h-3.5 w-3.5" />
        Create autonomous agent
      </div>
      <h2 className="text-lg font-semibold text-foreground">Describe the agent you want</h2>
      <p className="text-sm text-muted-foreground">
        Include symbol, goal (intraday / swing / event), and budget if you care about sizing. I may ask
        <strong> one short question</strong>, then show a proposal card — you confirm to start paper trading,
        watchers, and research on a schedule.
      </p>
      <ul className="space-y-2 text-left text-sm">
        {EXAMPLES.map((ex) => (
          <li key={ex}>
            <button
              type="button"
              onClick={() => onExample(ex)}
              className="w-full rounded-xl border border-border/60 bg-muted/30 px-3 py-2 text-left text-muted-foreground transition hover:border-primary/40 hover:text-foreground"
            >
              {ex}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
});
