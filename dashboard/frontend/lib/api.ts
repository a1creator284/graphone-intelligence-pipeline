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
