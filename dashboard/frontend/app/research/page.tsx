"use client";

import Badge from "@/components/Badge";
import ExternalLink from "@/components/ExternalLink";
import PageHeader from "@/components/PageHeader";
import RecordTable, { Column } from "@/components/RecordTable";
import { ResearchPaper } from "@/lib/api";
import { formatAuthors, formatDate, formatNumber, truncate } from "@/lib/format";
import { useRecordPage } from "@/lib/useRecordPage";

const COLUMNS: Column<ResearchPaper>[] = [
  {
    key: "title",
    header: "Title",
    // Paper titles are long; truncate for layout but keep the full string in
    // the tooltip so nothing is actually lost.
    render: (row) => (
      <span className="cell-strong" title={row.title}>
        {truncate(row.title, 110)}
      </span>
    ),
  },
  {
    key: "authors",
    header: "Authors",
    width: "220px",
    secondary: true,
    render: (row) => (
      <span className="muted">{formatAuthors(row.authors)}</span>
    ),
  },
  {
    key: "published_date",
    header: "Published",
    width: "130px",
    render: (row) => formatDate(row.published_date),
  },
  {
    key: "github_stars",
    header: "Stars",
    numeric: true,
    width: "90px",
    render: (row) => formatNumber(row.github_stars),
  },
  {
    key: "source_name",
    header: "Source",
    width: "130px",
    render: (row) => <Badge>{row.source_name}</Badge>,
  },
  {
    key: "links",
    header: "Links",
    width: "200px",
    render: (row) => (
      <div className="link-stack">
        <ExternalLink href={row.paper_url} label="Paper" />
        {row.github_url ? (
          <ExternalLink href={row.github_url} label="Code" />
        ) : null}
      </div>
    ),
  },
];

export default function ResearchPage() {
  const state = useRecordPage<ResearchPaper>("/api/research-papers");

  return (
    <>
      <PageHeader
        title="Research"
        description="Papers indexed from arXiv, OpenAlex and Papers with Code."
        total={state.error ? undefined : state.total}
        loading={state.initialLoading}
      />
      <RecordTable
        state={state}
        columns={COLUMNS}
        rowKey={(row) => row.id}
        searchPlaceholder="Search title or source…"
        emptyTitle="No research papers ingested yet"
        emptyHint="Run the research pipeline to populate this table."
      />
    </>
  );
}
