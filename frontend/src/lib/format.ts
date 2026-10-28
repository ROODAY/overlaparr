// Display formatting shared across pages.

const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** Human-readable size with 1024-based units, e.g. "1.8 TB" (same as backend stats.format_bytes). */
export function formatBytes(n: number): string {
  let size = Math.max(0, n);
  for (const unit of UNITS) {
    if (size < 1024 || unit === "TB") {
      return unit === "B" ? `${Math.trunc(size)} B` : `${size.toFixed(1)} ${unit}`;
    }
    size /= 1024;
  }
  return `${size.toFixed(1)} TB`;
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
