import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Local-calendar-date ISO string (YYYY-MM-DD), read from `Date`'s local
 * getters. `Date.toISOString()` converts to UTC first, which silently
 * rolls the date back a day for any positive-UTC-offset timezone (e.g.
 * IST, UTC+5:30) whenever the local time is near midnight — a local
 * midnight is still the previous evening in UTC. Always use this instead
 * of `d.toISOString().slice(0, 10)` for calendar-date keys.
 */
export function localIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
