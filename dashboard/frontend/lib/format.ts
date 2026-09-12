/**
 * Display helpers.
 *
 * Every one of these is written to survive real ingested data rather than
 * ideal data: timestamps can be null, numbers can be zero (which is *not*
 * the same as missing), and URLs can be long enough to blow out a table cell.
 */

/** Em dash placeholder, so "no value" always looks the same everywhere. */
export const EMPTY = "—";

/** Locale date, or EMPTY when the row genuinely has no timestamp. */
export function formatDate(value?: string | null): string {
  if (!value) return EMPTY;
  const date = parseTimestamp(value);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(value?: string | null): string {
  if (!value) return EMPTY;
  const date = parseTimestamp(value);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return date.toLocaleString();
}

/**
 * Thousands-separated integer. Note the explicit null/undefined check: a
 * falsy test would render a real 0 as "—", which would be a lie.
 */
export function formatNumber(value?: number | null): string {
  if (value === null || value === undefined) return EMPTY;
  return value.toLocaleString();
}

/** "github.com/foo/bar" -- what a person actually wants to read in a cell. */
export function prettyUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname.replace(/\/$/, "");
    const label = `${parsed.hostname.replace(/^www\./, "")}${path}`;
    return label.length > 48 ? `${label.slice(0, 47)}…` : label;
  } catch {
    // Not a parseable URL; show it verbatim rather than hiding it.
    return url.length > 48 ? `${url.slice(0, 47)}…` : url;
  }
}

export function truncate(text: string, max = 90): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** `["A. Author", "B. Writer", ...]` -> `"A. Author, B. Writer +3"`. */
export function formatAuthors(authors: unknown, shown = 2): string {
  if (!Array.isArray(authors) || authors.length === 0) return EMPTY;
  const names = authors
    .map((a) => (typeof a === "string" ? a : String((a as { name?: string })?.name ?? "")))
    .filter(Boolean);
  if (names.length === 0) return EMPTY;
  const head = names.slice(0, shown).join(", ");
  return names.length > shown ? `${head} +${names.length - shown}` : head;
}


/** Pipeline timestamps without an offset represent UTC, not browser-local time. */
function parseTimestamp(value: string): Date {
  const hasTime = /[T ]\d{2}:\d{2}/.test(value);
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return new Date(hasTime && !hasZone ? `${value}Z` : value);
}

/** Only absolute HTTP(S) URLs without embedded credentials are navigable. */
export function safeExternalUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
      ? url.href : null;
  } catch {
    return null;
  }
}
