// Display formatting shared across pages.

const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** Human-readable size with 1024-based units, e.g. "1.8 TB" (same as backend stats.format_bytes). */
export function formatBytes(n: number): string {
  let size = Math.max(0, n);
  for (const unit of UNITS) {
    if (size < 1024 || unit === "TB") {
      return unit === "B" ? `${Math.trunc(size)} B` : `${toFixed1(size)} ${unit}`;
    }
    size /= 1024;
  }
  return `${toFixed1(size)} TB`;
}

/**
 * One decimal place, ties to even like Python's `:.1f` (toFixed rounds exact ties up).
 * A double is an exact tie at one decimal only when 4x is an odd integer (e.g. 1.25).
 */
function toFixed1(x: number): string {
  const quarters = x * 4;
  if (Number.isInteger(quarters) && quarters % 2 === 1) {
    const tenths = Math.floor(x * 10);
    return ((tenths % 2 === 0 ? tenths : tenths + 1) / 10).toFixed(1);
  }
  return x.toFixed(1);
}

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["second", 60],
  ["minute", 60],
  ["hour", 24],
  ["day", 30],
  ["month", 12],
  ["year", Infinity],
];

/** "3 hours ago" / "in 2 days", relative to now. */
export function formatRelative(iso: string, now: number = Date.now()): string {
  let value = (new Date(iso).getTime() - now) / 1000;
  for (const [unit, size] of STEPS) {
    if (Math.abs(value) < size) return RELATIVE.format(Math.round(value), unit);
    value /= size;
  }
  return RELATIVE.format(Math.round(value), "year");
}

/** Local date and time, e.g. "Oct 26, 2026, 10:05 AM". */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
