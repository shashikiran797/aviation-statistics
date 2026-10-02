const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const plain = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const full = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export function fmt(v, format = "compact") {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (format === "percent") return `${plain.format(v)}%`;
  if (format === "number") return plain.format(v);
  return compact.format(v);
}
export const fmtFull = (v) => (v === null || v === undefined ? "–" : full.format(v));

export function monthLabel(d) {
  return new Date(d).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
}
export function toDate(v) {
  return v instanceof Date ? v : new Date(typeof v === "number" ? v : String(v));
}
