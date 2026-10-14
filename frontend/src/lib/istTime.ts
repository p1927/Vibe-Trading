/**
 * The one place a data timestamp is shown to the trader (D306). The product trades the Indian markets, so every
 * as-of / last-updated / halt time is IST with an explicit "IST" label - never the browser's zone (a laptop on
 * another clock would shift a forecast's as-of by hours) and never "Invalid Date" for a missing value.
 */

const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "15 Oct 2026, 09:30 IST", or "—" when the value is missing or unreadable. */
export function formatIst(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  if (!date) return "—";
  // en-GB puts a comma-less "15 Oct 2026, 09:30"; normalise to exactly one comma.
  return `${DATE_TIME.format(date).replace(/,/g, "").replace(/^(\d{1,2} \w{3} \d{4}) /, "$1, ")} IST`;
}

/** "09:30:05 IST", or "—" when the value is missing or unreadable. */
export function formatIstTime(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  return date ? `${TIME.format(date)} IST` : "—";
}
