import { useEffect, useState } from "react";
import { api, type ForecastEngineRecipeInfo } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ForecastEngineChart } from "@/components/prediction/ForecastEngineChart";
import { ForecastEnginePredictedVsActualChart } from "@/components/prediction/ForecastEnginePredictedVsActualChart";

interface Props {
  ticker?: string;
}

/** Owns the dataset-recipe selector for the Forecast Engine tab. Multiple recipes are expected
 * over time (docs/add/forecast_engine.md's registry) — this is the one place a viewer picks
 * which one's dataset/forecast/backtest-history to look at, rather than each chart guessing. */
export function ForecastEngineSection({ ticker = "NIFTY" }: Props) {
  const [recipes, setRecipes] = useState<ForecastEngineRecipeInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api
      .getForecastEngineRecipes()
      .then((res) => {
        if (cancelled) return;
        const list = res.recipes ?? [];
        setRecipes(list);
        setError(null);
        setSelected((prev) => prev ?? res.default_recipe ?? list[0]?.name ?? null);
      })
      .catch((e) => {
        if (!cancelled) {
          setRecipes([]);
          setError(e instanceof Error ? e.message : "Recipe list fetch failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const activeRecipe = recipes.find((r) => r.name === selected);

  return (
    <div className="space-y-3">
      {loading ? (
        <p className="text-[11px] text-muted-foreground">Loading available datasets…</p>
      ) : error ? (
        <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p>
      ) : recipes.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No forecast_engine recipes registered.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/60 bg-card/40 p-1">
            {recipes.map((r) => (
              <button
                key={r.name}
                type="button"
                onClick={() => setSelected(r.name)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors",
                  selected === r.name
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {r.name}
                <span className="ml-1 font-normal opacity-70">v{r.version}</span>
              </button>
            ))}
          </div>
          {activeRecipe?.description ? (
            <p className="text-[11px] text-muted-foreground">{activeRecipe.description}</p>
          ) : null}

          {selected ? (
            <>
              <ForecastEngineChart ticker={ticker} recipe={selected} />
              <ForecastEnginePredictedVsActualChart ticker={ticker} recipe={selected} />
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
