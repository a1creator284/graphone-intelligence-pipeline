/**
 * Typed client for the GraphOne dashboard API.
 *
 * The base URL is configuration, never hardcoded to a deployed host: set
 * NEXT_PUBLIC_API_BASE_URL in .env.local (see .env.local.example).
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface DashboardStats {
  startups: number;
  products: number;
  research_papers: number;
  jobs: number;
  news: number;
  canonical_entities: number;
  raw_documents: number;
}

export interface StatsResponse {
  stats: DashboardStats;
  generated_at: string;
}

export interface HealthResponse {
  status: string;
  database: string;
  detail?: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

/** Distinguishes "backend unreachable" from "backend returned an error". */
export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      // Always show live database state; a cached page would be misleading.
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (cause) {
    throw new ApiError(
      `Could not reach the dashboard API at ${API_BASE_URL}. Is the backend running?`,
    );
  }

  if (!response.ok) {
    throw new ApiError(
      `API request to ${path} failed with status ${response.status}.`,
      response.status,
    );
  }

  return (await response.json()) as T;
}

export const fetchStats = (signal?: AbortSignal) =>
  apiGet<StatsResponse>("/api/dashboard/stats", signal);

export const fetchHealth = (signal?: AbortSignal) =>
  apiGet<HealthResponse>("/api/health", signal);

/* ------------------------------------------------------------------ *
 * Record types. These mirror the Pydantic response models one-for-one;
 * anything the API marks optional is optional here too, because real
 * ingested rows genuinely do have missing timestamps and null columns.
 * ------------------------------------------------------------------ */

export interface Startup {
  id: string;
  entity_name: string;
  source_name: string;
  source_url: string;
  employee_count?: number | null;
  collected_at?: string | null;
}

export interface Product {
  id: string;
  product_name?: string | null;
  startup_name: string;
  source_name: string;
  source_url: string;
  pricing_model?: string | null;
  collected_at?: string | null;
}

export interface ResearchPaper {
  id: string;
  title: string;
  authors: string[];
  paper_url: string;
  github_url?: string | null;
  github_stars?: number | null;
  published_date?: string | null;
  source_name: string;
}

export interface Job {
  id: string;
  company: string;
  title: string;
  url: string;
  posted_at?: string | null;
  is_remote?: boolean | null;
  role_family?: string | null;
  source_name: string;
}

export interface NewsArticle {
  id: string;
  title: string;
  url: string;
  source_name: string;
  published_at?: string | null;
}

export interface CanonicalEntity {
  id: string;
  canonical_name: string;
  normalized_name: string;
  entity_type: string;
  created_at?: string | null;
  alias_count: number;
  startup_count: number;
  product_count: number;
  job_count: number;
  total_records: number;
  aliases: string[];
}

export interface ListQuery {
  limit: number;
  offset: number;
  /** Case-insensitive substring filter; omitted when blank. */
  q?: string;
  /** Only the entities endpoint understands this. */
  sort?: string;
}

function buildQuery(query: ListQuery): string {
  const params = new URLSearchParams({
    limit: String(query.limit),
    offset: String(query.offset),
  });
  // Only send q/sort when meaningful -- a trailing "&q=" would make the URL
  // (and therefore the cache key and the backend's job) needlessly noisy.
  if (query.q && query.q.trim()) params.set("q", query.q.trim());
  if (query.sort) params.set("sort", query.sort);
  return params.toString();
}

/** Fetch one bounded page from any of the list endpoints. */
export function fetchPage<T>(
  path: string,
  query: ListQuery,
  signal?: AbortSignal,
): Promise<Page<T>> {
  return apiGet<Page<T>>(`${path}?${buildQuery(query)}`, signal);
}
