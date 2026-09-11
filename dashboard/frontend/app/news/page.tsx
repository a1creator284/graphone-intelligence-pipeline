"use client";

import Badge from "@/components/Badge";
import ExternalLink from "@/components/ExternalLink";
import PageHeader from "@/components/PageHeader";
import RecordTable, { Column } from "@/components/RecordTable";
import { NewsArticle } from "@/lib/api";
import { formatDateTime, truncate } from "@/lib/format";
import { useRecordPage } from "@/lib/useRecordPage";

const COLUMNS: Column<NewsArticle>[] = [
  {
    key: "title",
    header: "Headline",
    render: (row) => (
      // The headline is the primary affordance here, so the whole thing is
      // the outbound link rather than a separate "Link" column.
      <ExternalLink
        href={row.url}
        label={truncate(row.title, 120)}
        title={row.title}
      />
    ),
  },
  {
    key: "source_name",
    header: "Source",
    width: "170px",
    render: (row) => <Badge>{row.source_name}</Badge>,
  },
  {
    key: "published_at",
    header: "Published",
    width: "190px",
    // News is ordered by published_at, so the exact time (not just the date)
    // is what makes the ordering legible.
    render: (row) => formatDateTime(row.published_at),
  },
];

export default function NewsPage() {
  const state = useRecordPage<NewsArticle>("/api/news");

  return (
    <>
      <PageHeader
        title="News"
        description="Articles collected from RSS and aggregator sources, newest first."
        total={state.error ? undefined : state.total}
        loading={state.initialLoading}
      />
      <RecordTable
        state={state}
        columns={COLUMNS}
        rowKey={(row) => row.id}
        searchPlaceholder="Search headline or source…"
        emptyTitle="No news articles ingested yet"
        emptyHint="Run the news pipeline to populate this table."
      />
    </>
  );
}
