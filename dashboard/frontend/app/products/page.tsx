"use client";

import Badge, { PricingBadge } from "@/components/Badge";
import ExternalLink from "@/components/ExternalLink";
import PageHeader from "@/components/PageHeader";
import RecordTable, { Column } from "@/components/RecordTable";
import { Product } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useRecordPage } from "@/lib/useRecordPage";

const COLUMNS: Column<Product>[] = [
  {
    key: "product_name",
    header: "Product",
    render: (row) =>
      // product_name is nullable (some sources publish only a vendor and a
      // URL), so fall back to a muted placeholder instead of an empty cell.
      row.product_name ? (
        <span className="cell-strong">{row.product_name}</span>
      ) : (
        <span className="muted">Unnamed product</span>
      ),
  },
  {
    key: "startup_name",
    header: "Vendor",
    render: (row) => row.startup_name,
  },
  {
    key: "pricing_model",
    header: "Pricing",
    width: "120px",
    render: (row) => <PricingBadge value={row.pricing_model} />,
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

export default function ProductsPage() {
  const state = useRecordPage<Product>("/api/products");

  return (
    <>
      <PageHeader
        title="Products"
        description="AI products and models published by their vendors, newest first."
        total={state.error ? undefined : state.total}
        loading={state.initialLoading}
      />
      <RecordTable
        detailKind="products"
        state={state}
        columns={COLUMNS}
        rowKey={(row) => row.id}
        searchPlaceholder="Search product, vendor or source…"
        emptyTitle="No products ingested yet"
        emptyHint="Run the products pipeline to populate this table."
      />
    </>
  );
}
