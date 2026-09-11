"use client";

import Badge from "@/components/Badge";
import ExternalLink from "@/components/ExternalLink";
import PageHeader from "@/components/PageHeader";
import RecordTable, { Column } from "@/components/RecordTable";
import { Startup } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import { useRecordPage } from "@/lib/useRecordPage";

const COLUMNS: Column<Startup>[] = [
  {
    key: "entity_name",
    header: "Company",
    render: (row) => <span className="cell-strong">{row.entity_name}</span>,
  },
  {
    key: "employee_count",
    header: "Employees",
    numeric: true,
    width: "120px",
    // Employee count is nullable in the schema and frequently absent, so
    // formatNumber renders an em dash rather than a misleading 0.
    render: (row) => formatNumber(row.employee_count),
  },
  {
    key: "source_name",
    header: "Source",
    width: "150px",
    render: (row) => <Badge>{row.source_name}</Badge>,
  },
  {
    key: "source_url",
    header: "Link",
    render: (row) => <ExternalLink href={row.source_url} />,
  },
  {
    key: "collected_at",
    header: "Collected",
    width: "130px",
    secondary: true,
    render: (row) => formatDate(row.collected_at),
  },
];

export default function StartupsPage() {
  const state = useRecordPage<Startup>("/api/startups");

  return (
    <>
      <PageHeader
        title="Startups"
        description="Companies ingested from startup directories, newest first."
        total={state.error ? undefined : state.total}
        loading={state.initialLoading}
      />
      <RecordTable
        detailKind="startups"
        state={state}
        columns={COLUMNS}
        rowKey={(row) => row.id}
        searchPlaceholder="Search company or source…"
        emptyTitle="No startups ingested yet"
        emptyHint="Run the startups pipeline to populate this table."
      />
    </>
  );
}
