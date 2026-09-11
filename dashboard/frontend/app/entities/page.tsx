"use client";

import Badge from "@/components/Badge";
import PageHeader from "@/components/PageHeader";
import RecordTable, { Column } from "@/components/RecordTable";
import { CanonicalEntity } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useRecordPage } from "@/lib/useRecordPage";

/**
 * Aliases are the point of entity resolution, so they are shown inline
 * rather than hidden behind a detail view. Only the first two fit on a row;
 * the rest are summarised and available in the tooltip.
 */
function AliasCell({ aliases }: { aliases: string[] }) {
  if (aliases.length === 0) return <span className="muted">—</span>;

  const shown = aliases.slice(0, 2);
  const extra = aliases.length - shown.length;

  return (
    <div className="alias-cell" title={aliases.join(", ")}>
      {shown.map((alias) => (
        <Badge key={alias}>{alias}</Badge>
      ))}
      {extra > 0 ? <span className="muted">+{extra}</span> : null}
    </div>
  );
}

/** Zero is meaningful here (an entity nothing resolved onto), so it is
 *  rendered dimmed rather than replaced with a dash. */
function CountCell({ value }: { value: number }) {
  return value === 0 ? (
    <span className="muted">0</span>
  ) : (
    <span className="cell-strong">{value.toLocaleString()}</span>
  );
}

const COLUMNS: Column<CanonicalEntity>[] = [
  {
    key: "canonical_name",
    header: "Canonical name",
    render: (row) => (
      <div>
        <div className="cell-strong">{row.canonical_name}</div>
        <div className="cell-sub" title={row.normalized_name}>
          {row.normalized_name}
        </div>
      </div>
    ),
  },
  {
    key: "entity_type",
    header: "Type",
    width: "110px",
    render: (row) => <Badge tone="accent">{row.entity_type}</Badge>,
  },
  {
    key: "aliases",
    header: "Aliases",
    width: "230px",
    secondary: true,
    render: (row) => <AliasCell aliases={row.aliases} />,
  },
  {
    key: "startup_count",
    header: "Startups",
    numeric: true,
    width: "100px",
    render: (row) => <CountCell value={row.startup_count} />,
  },
  {
    key: "product_count",
    header: "Products",
    numeric: true,
    width: "100px",
    render: (row) => <CountCell value={row.product_count} />,
  },
  {
    key: "job_count",
    header: "Jobs",
    numeric: true,
    width: "90px",
    render: (row) => <CountCell value={row.job_count} />,
  },
  {
    key: "total_records",
    header: "Linked",
    numeric: true,
    width: "100px",
    render: (row) => <CountCell value={row.total_records} />,
  },
  {
    key: "created_at",
    header: "Resolved",
    width: "130px",
    secondary: true,
    render: (row) => formatDate(row.created_at),
  },
];

const SORTS = [
  { value: "records", label: "Most connected" },
  { value: "name", label: "Name (A–Z)" },
  { value: "recent", label: "Recently resolved" },
];

export default function EntitiesPage() {
  const state = useRecordPage<CanonicalEntity>("/api/entities", { sort: "records" });

  const sortControl = (
    <label className="select-field">
      <span>Sort</span>
      <select
        value={state.sort ?? "records"}
        onChange={(event) => state.setSort(event.target.value)}
        disabled={state.initialLoading}
        aria-label="Sort entities"
      >
        {SORTS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <>
      <PageHeader
        title="Entities"
        description="Canonical entities produced by entity resolution, with the records that resolved onto each one."
        total={state.error ? undefined : state.total}
        loading={state.initialLoading}
      />

      <div className="panel panel--note">
        <p>
          Each row is one resolved identity. The counts show how many
          startups, products and job postings the pipeline mapped onto it —
          an entity with zero links was created but never matched again.
          Searching also matches aliases, so you can look an entity up by a
          name that was folded away.
        </p>
      </div>

      <RecordTable
        detailKind="entities"
        state={state}
        columns={COLUMNS}
        rowKey={(row) => row.id}
        searchPlaceholder="Search name or alias…"
        emptyTitle="No canonical entities yet"
        emptyHint="Entity resolution runs as part of the ingestion pipeline; run it to populate this view."
        toolbarExtra={sortControl}
      />
    </>
  );
}
