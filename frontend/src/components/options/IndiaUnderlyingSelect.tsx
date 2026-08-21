import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { IndiaOptionsSource } from "@/lib/options";

interface Props {
  source: IndiaOptionsSource;
  value: string;
  onChange: (ticker: string) => void;
}

/**
 * Underlying (index/equity) picker for India Options Lab — populated from
 * `GET /options/india/underlyings`, not a guess-a-ticker free-text box.
 * Indexes (NIFTY/BANKNIFTY/SENSEX) are always offered; recorded equities
 * only appear for `stock_simulator` (a cheap local listing — the live
 * vendor sources would need an ~80k-row scrip-master download to enumerate
 * theirs, so they only offer the known indexes here).
 */
export function IndiaUnderlyingSelect({ source, value, onChange }: Props) {
  const { t } = useTranslation();
  const [indexes, setIndexes] = useState<string[]>(["NIFTY", "BANKNIFTY", "SENSEX"]);
  const [equities, setEquities] = useState<string[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getIndiaUnderlyings(source)
      .then((res) => {
        if (cancelled || !res.ok || !res.data) return;
        setIndexes(res.data.indexes);
        setEquities(res.data.equities);
      })
      .catch(() => {
        // Best-effort — keep the fallback index list on failure.
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  const equityOptions = (equities ?? []).map((eq) => `${eq}.NS`);
  const known = [...indexes, ...equityOptions];

  return (
    <select
      value={known.includes(value) ? value : "__other__"}
      onChange={(e) => {
        if (e.target.value !== "__other__") onChange(e.target.value);
      }}
      aria-label={t("options.india.underlyingLabel", { defaultValue: "Underlying" })}
      className="rounded-md border border-border/60 bg-background px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/40"
    >
      <optgroup label="Indexes">
        {indexes.map((idx) => (
          <option key={idx} value={idx}>
            {idx}
          </option>
        ))}
      </optgroup>
      {equities && equities.length > 0 && (
        <optgroup label="Equities (recorded)">
          {equityOptions.map((eq) => (
            <option key={eq} value={eq}>
              {eq.replace(/\.NS$/, "")}
            </option>
          ))}
        </optgroup>
      )}
      {!known.includes(value) && (
        <option value="__other__">
          {value || t("options.india.otherUnderlying", { defaultValue: "Other…" })}
        </option>
      )}
    </select>
  );
}
