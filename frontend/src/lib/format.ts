// Presentation-only formatting. Generic: no evidence key, playbook or tool is
// named here, so a new KB entry is displayed without a frontend change.

// Short tokens that read as acronyms when a snake_case name is humanised.
const ACRONYMS: Record<string, string> = {
  kyb: "KYB", kyc: "KYC", pep: "PEP", mcc: "MCC", mccs: "MCCs", mlro: "MLRO",
  bo: "BO", ubo: "UBO", id: "ID", ids: "IDs", eu: "EU",
};
const SMALL = new Set(["of", "and", "or", "to", "in", "on", "for", "the", "a", "an", "by"]);

/** kyb_status -> "KYB Status"; source_of_funds -> "Source of Funds". */
export function human(name: string | null | undefined): string {
  if (!name) return "";
  return String(name)
    .split("_")
    .filter(Boolean)
    .map((w, i) => {
      const lw = w.toLowerCase();
      if (ACRONYMS[lw]) return ACRONYMS[lw];
      if (i > 0 && SMALL.has(lw)) return lw;
      return lw.charAt(0).toUpperCase() + lw.slice(1);
    })
    .join(" ");
}

/** get_customer -> "Customer"; get_transaction_summary -> "Transaction Summary". */
export function toolLabel(tool: string): string {
  return human(tool.replace(/^get_/, ""));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ISO_DT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

/** ISO-8601 -> "22 Sep 2026, 17:00 UTC". Anything else is returned unchanged. */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
}

// Detected-alert times are shown and entered in Indian Standard Time. IST is a
// fixed UTC+05:30 with no daylight saving, so the conversion is exact. Stored
// values stay in UTC; only the display and the form input use IST.
const IST_OFFSET_MS = 330 * 60_000;

/** A stored UTC detection time -> "03 Oct 2026, 14:30 IST". */
export function fmtDetected(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const t = new Date(d.getTime() + IST_OFFSET_MS);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(t.getUTCDate())} ${MONTHS[t.getUTCMonth()]} ${t.getUTCFullYear()}, ${p(t.getUTCHours())}:${p(t.getUTCMinutes())} IST`;
}

/**
 * An IST date ("2026-10-03") and time ("14:30") -> the UTC ISO timestamp the
 * API stores ("2026-10-03T09:00:00Z"). Null if either part is not a real date
 * or time.
 */
export function istToUtcIso(date: string, time: string): string | null {
  const dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  const tm = /^(\d{2}):(\d{2})(?::\d{2})?$/.exec(time);
  if (!dm || !tm) return null;
  const [y, mo, da, h, mi] = [dm[1], dm[2], dm[3], tm[1], tm[2]].map(Number);
  if (mo < 1 || mo > 12 || h > 23 || mi > 59) return null;
  const day = new Date(Date.UTC(y, mo - 1, da));
  if (day.getUTCMonth() !== mo - 1 || day.getUTCDate() !== da) return null; // e.g. 31 Feb
  return new Date(Date.UTC(y, mo - 1, da, h, mi) - IST_OFFSET_MS).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** The current moment as an IST date ("2026-10-05") and time ("14:45"), for form defaults. */
export function istNow(now: Date = new Date()): { date: string; time: string } {
  const iso = new Date(now.getTime() + IST_OFFSET_MS).toISOString();
  return { date: iso.slice(0, 10), time: iso.slice(11, 16) };
}

/** Lists longer than this are summarised as a count with an expandable list. */
export const LIST_INLINE_MAX = 6;

/**
 * A FOUND evidence value as text. Values are shown as returned: a
 * lower_snake_case word has its underscores replaced for readability, but is
 * never reworded; identifiers, numbers and booleans are unchanged.
 */
export function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return v.toLocaleString("en-GB");
  if (Array.isArray(v)) {
    if (v.length === 0) return "none listed";
    if (v.length > LIST_INLINE_MAX) return `${v.length} items`;
    return v.map(fmtValue).join(", ");
  }
  if (typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${human(k)}: ${fmtValue(x)}`)
      .join(" · ");
  }
  const s = String(v);
  if (ISO_DT.test(s)) return fmtDate(s);
  if (/^[a-z][a-z0-9_]*$/.test(s)) return s.replace(/_/g, " ");
  return s;
}
