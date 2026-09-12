"use client";

import Badge from "@/components/Badge";
import ExternalLink from "@/components/ExternalLink";
import PageHeader from "@/components/PageHeader";
import RecordTable, { Column } from "@/components/RecordTable";
import { Job } from "@/lib/api";
import { formatDate, truncate } from "@/lib/format";
import { useRecordPage } from "@/lib/useRecordPage";

/**
 * `is_remote` is a nullable boolean: null means the source never said, which
 * is genuinely different from "on-site". Rendering null as "On-site" would
 * invent a fact the pipeline never collected, so it stays an em dash.
 */
function RemoteCell({ value }: { value?: boolean | null }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  return value ? <Badge tone="ok">Remote</Badge> : <Badge>On-site</Badge>;
}

const COLUMNS: Column<Job>[] = [
  {
    key: "title",
    header: "Role",
    render: (row) => (
      <ExternalLink
        href={row.url}
        label={truncate(row.title, 80)}
        title={row.title}
      />
    ),
  },
  {
    key: "company",
    header: "Company",
    width: "200px",
    render: (row) => <span className="cell-strong">{row.company}</span>,
  },
  {
    key: "role_family",
    header: "Family",
    width: "150px",
    secondary: true,
    render: (row) =>
      row.role_family ? (
        <Badge tone="accent">{row.role_family}</Badge>
      ) : (
        <span className="muted">—</span>
      ),
  },
  {
    key: "is_remote",
    header: "Location",
    width: "110px",
    render: (row) => <RemoteCell value={row.is_remote} />,
  },
  {
    key: "posted_at",
    header: "Posted",
    width: "130px",
    render: (row) => formatDate(row.posted_at),
  },
  {
    key: "source_name",
    header: "Source",
    width: "160px",
    secondary: true,
    render: (row) => <Badge>{row.source_name}</Badge>,
  },
];

export default function JobsPage() {
  const state = useRecordPage<Job>("/api/jobs");

  return (
    <>
      <PageHeader
        title="Jobs"
        description="Open postings collected from company boards, newest first."
        total={state.error ? undefined : state.total}
        loading={state.initialLoading}
      />
      <RecordTable
        detailKind="jobs"
        state={state}
        columns={COLUMNS}
        rowKey={(row) => row.id}
        searchPlaceholder="Search role, company or source…"
        emptyTitle="No job postings ingested yet"
        emptyHint="Run the jobs pipeline to populate this table."
      />
    </>
  );
}
