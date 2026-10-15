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

const TIME_HM = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** "09:30:05 IST" ("09:30 IST" with `withSeconds` false), or "—" when the value is missing or unreadable. */
export function formatIstTime(value: string | number | Date | null | undefined, withSeconds = true): string {
  const date = toDate(value);
  if (!date) return "—";
  return `${(withSeconds ? TIME : TIME_HM).format(date)} IST`;
}

const IST_YMD = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });

/** The IST calendar day as "YYYY-MM-DD": what "today" means for an Indian-market event date, in any browser zone. */
export function istTodayIso(now: Date = new Date()): string {
  return IST_YMD.format(now);
}

const DATE_Y = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric" });
const DATE_NO_Y = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });

/** The IST calendar day: "15 Oct 2026" ("15 Oct" with `withYear` false), or "—". A bare YYYY-MM-DD stays that day. */
export function formatIstDate(value: string | number | Date | null | undefined, withYear = true): string {
  const date = toDate(value);
  return date ? (withYear ? DATE_Y : DATE_NO_Y).format(date) : "—";
}
