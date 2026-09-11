"use client";

import { useCallback, useEffect, useState } from "react";
import StatCard from "@/components/StatCard";
import StatusPill, { ConnectionState } from "@/components/StatusPill";
import {
  API_BASE_URL,
  DashboardStats,
  fetchHealth,
  fetchStats,
} from "@/lib/api";

const CARDS: { key: keyof DashboardStats; label: string; hint: string }[] = [
  { key: "startups", label: "Startups", hint: "Companies ingested" },
  { key: "products", label: "Products", hint: "AI products & models" },
  { key: "research_papers", label: "Research Papers", hint: "Papers indexed" },
  { key: "jobs", label: "Jobs", hint: "Open postings" },
  { key: "news", label: "News", hint: "Articles collected" },
  {
    key: "canonical_entities",
    label: "Canonical Entities",
    hint: "After entity resolution",
  },
  {
    key: "raw_documents",
    label: "Raw Documents",
    hint: "Provenance records",
  },
];

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);

    // Health first: it tells us whether a stats failure means "API down" or
    // "API up, database down", which are very different things to report.
    try {
      const health = await fetchHealth(signal);
      setConnection(health.database === "connected" ? "ok" : "degraded");
    } catch {
      setConnection("offline");
      setError(
        `Could not reach the dashboard API at ${API_BASE_URL}. Start the backend, then reload.`,
      );
      setLoading(false);
      return;
    }

    try {
      const payload = await fetchStats(signal);
      setStats(payload.stats);
      setGeneratedAt(payload.generated_at);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load statistics.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
    <>
      <header className="page-header">
        <div>
          <h1>GraphOne Intelligence</h1>
          <p>Live counts from the ingestion database.</p>
        </div>
        <StatusPill state={connection} />
      </header>

      {error ? (
        <div className="alert" role="alert">
          <div className="alert__title">Unable to load dashboard data</div>
          <div>{error}</div>
          <div style={{ marginTop: 8 }}>
            Expected backend at <code>{API_BASE_URL}</code>
          </div>
        </div>
      ) : null}

      <section aria-label="Database statistics">
        <div className="stat-grid">
          {CARDS.map((card) => (
            <StatCard
              key={card.key}
              label={card.label}
              hint={card.hint}
              loading={loading}
              value={stats ? stats[card.key] : undefined}
            />
          ))}
        </div>
      </section>

      <h2 className="section-title">About this view</h2>
      <div className="panel">
        <p>
          This dashboard is strictly read-only. It queries the same PostgreSQL
          database the ingestion pipeline writes to, using the pipeline&apos;s
          own SQLAlchemy models — it never crawls, writes, or mutates data.
        </p>
        <p>
          Startups, Products, Research, News, Jobs and Entities views are
          reserved for a later phase; their API endpoints are already live and
          paginated.
        </p>
      </div>

      {generatedAt ? (
        <div className="meta-line">
          Last updated {new Date(generatedAt).toLocaleString()}
        </div>
      ) : null}
    </>
  );
}
