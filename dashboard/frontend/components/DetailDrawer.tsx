"use client";

import { ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import Badge from "@/components/Badge";
import ExternalLink from "@/components/ExternalLink";
import { ApiError, DETAIL_LABELS, DetailSelection, EntityDetail, RecordDetail, fetchDetail } from "@/lib/api";
import { formatAuthors, formatDateTime, formatNumber } from "@/lib/format";
import { useApiResource } from "@/lib/useApiResource";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div className="detail-field"><dt>{label}</dt><dd>{children ?? "Not recorded"}</dd></div>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="detail-section"><h3>{title}</h3>{children}</section>;
}

function titleOf(data: RecordDetail | EntityDetail): string {
  if ("canonical_name" in data) return data.canonical_name;
  if ("entity_name" in data) return data.entity_name;
  if ("startup_name" in data) return data.product_name || data.startup_name;
  return data.title;
}

function RecordFields({ data, navigate }: { data: RecordDetail; navigate: (value: DetailSelection) => void }) {
  const sourceUrl = "source_url" in data ? data.source_url : "paper_url" in data ? data.paper_url : data.url;
  const metadata = "metadata_json" in data ? data.metadata_json : "extracted_metadata" in data ? data.extracted_metadata : null;
  const provenance = data.provenance;
  return <>
    <Section title="Record overview">
      <dl className="detail-grid">
        <Field label="Source"><Badge>{data.source_name}</Badge></Field>
        <Field label="Source link"><ExternalLink href={sourceUrl} /></Field>
        {"entity_name" in data && <>
          <Field label="Company">{data.entity_name}</Field>
          <Field label="Employees">{formatNumber(data.employee_count)}</Field>
        </>}
        {"startup_name" in data && <>
          <Field label="Product name">{data.product_name}</Field>
          <Field label="Vendor">{data.startup_name}</Field>
          <Field label="Pricing model">{data.pricing_model}</Field>
          <Field label="Source external ID">{data.source_external_id}</Field>
        </>}
        {"paper_url" in data && <>
          <Field label="Authors">{formatAuthors(data.authors, Number.MAX_SAFE_INTEGER)}</Field>
          <Field label="Paper external ID">{data.paper_external_id}</Field>
          <Field label="Code repository"><ExternalLink href={data.github_url} /></Field>
          <Field label="GitHub stars">{formatNumber(data.github_stars)}</Field>
        </>}
        {"company" in data && <>
          <Field label="Company">{data.company}</Field>
          <Field label="Role family">{data.role_family}</Field>
          <Field label="Work arrangement">{data.is_remote == null ? "Not recorded" : data.is_remote ? "Remote" : "On-site"}</Field>
        </>}
        <Field label="Record ID"><code>{data.id}</code></Field>
      </dl>
    </Section>
    <Section title="Timestamps">
      <p className="cell-sub">Times are shown in your local timezone. Missing timestamps are not inferred.</p>
      <dl className="detail-grid">
        <Field label="Collected">{formatDateTime(data.collected_at)}</Field>
        {"published_date" in data && <Field label="Published">{formatDateTime(data.published_date)}</Field>}
        {"published_at" in data && <Field label="Published">{formatDateTime(data.published_at)}</Field>}
        {"posted_at" in data && <Field label="Posted">{formatDateTime(data.posted_at)}</Field>}
      </dl>
    </Section>
    <Section title="Canonical entity">
      {data.canonical_entity ? <button className="relationship-link" type="button"
        onClick={() => navigate({ kind: "entities", id: data.canonical_entity!.id })}>
        <span><strong>{data.canonical_entity.canonical_name}</strong><span className="cell-sub">{data.canonical_entity.entity_type} · {data.canonical_entity.normalized_name}</span></span>
        <span className="link">View entity →</span>
      </button> : <p className="muted">{data.canonical_entity_id
        ? `Linked entity ${data.canonical_entity_id} is unavailable.`
        : "No canonical entity relationship is recorded for this item."}</p>}
    </Section>
    <Section title="Source & provenance">
      <dl className="detail-grid">
        <Field label="Raw document ID"><code>{data.raw_document_id ?? "Not recorded"}</code></Field>
        {provenance && <>
          <Field label="Provenance ID"><code>{provenance.id}</code></Field>
          <Field label="Original source">{provenance.source_name}</Field>
          <Field label="Retrieved">{formatDateTime(provenance.retrieved_at)}</Field>
          <Field label="Original URL"><ExternalLink href={provenance.source_url} /></Field>
          <Field label="Canonical source URL"><ExternalLink href={provenance.canonical_url} /></Field>
          <Field label="HTTP status">{provenance.http_status}</Field>
          <Field label="Extraction status">{provenance.extraction_status}</Field>
          <Field label="Content hash"><code>{provenance.content_hash}</code></Field>
        </>}
      </dl>
      {!provenance && <p className="muted">No provenance document is available for this record.</p>}
    </Section>
    {metadata && <details className="detail-section"><summary>Additional source metadata</summary>
      <pre className="metadata-json">{JSON.stringify(metadata, null, 2)}</pre>
    </details>}
  </>;
}

function EntityFields({ data, navigate }: { data: EntityDetail; navigate: (value: DetailSelection) => void }) {
  const groups = [
    { kind: "startups" as const, label: "Startups", total: data.startup_count, rows: data.startups.map((r) => ({ id: r.id, title: r.entity_name, source: r.source_name })) },
    { kind: "products" as const, label: "Products", total: data.product_count, rows: data.products.map((r) => ({ id: r.id, title: r.product_name || r.startup_name, source: r.source_name })) },
    { kind: "jobs" as const, label: "Jobs", total: data.job_count, rows: data.jobs.map((r) => ({ id: r.id, title: r.title, source: r.source_name })) },
  ];
  return <>
    <Section title="Resolved identity"><dl className="detail-grid">
      <Field label="Normalized name">{data.normalized_name}</Field>
      <Field label="Type"><Badge tone="accent">{data.entity_type}</Badge></Field>
      <Field label="Created">{formatDateTime(data.created_at)}</Field>
      <Field label="Entity ID"><code>{data.id}</code></Field>
    </dl></Section>
    <div className="entity-counts" aria-label="Linked record counts">
      {groups.map((group) => <div key={group.kind}><strong>{formatNumber(group.total)}</strong><span>{group.label}</span></div>)}
      <div><strong>{formatNumber(data.total_records)}</strong><span>Total linked</span></div>
    </div>
    <Section title={`Aliases (${formatNumber(data.alias_count)})`}>
      {data.aliases.length ? <div className="alias-cell">{data.aliases.map((alias, index) => <Badge key={`${index}:${alias}`}>{alias}</Badge>)}</div>
        : <p className="muted">No aliases recorded.</p>}
      {data.alias_count > data.aliases.length && <p className="cell-sub">Showing {data.aliases.length} of {data.alias_count} aliases (API limit {data.relationship_limit}).</p>}
    </Section>
    <Section title="Linked records">
      <p className="cell-sub">Stored relationships only. Up to {data.relationship_limit} records per type, newest collected first.</p>
      {groups.map((group) => <div className="relationship-group" key={group.kind}>
        <h4>{group.label} <span className="muted">{group.rows.length} of {group.total}</span></h4>
        {group.rows.length ? group.rows.map((row) => <button type="button" className="relationship-link" key={row.id} onClick={() => navigate({ kind: group.kind, id: row.id })}>
          <span><strong>{row.title}</strong><span className="cell-sub">{row.source}</span></span><span aria-hidden="true">→</span>
        </button>) : <p className="muted">No linked {group.label.toLowerCase()}.</p>}
      </div>)}
    </Section>
  </>;
}

function DetailContent({ selection, navigate, titleId }: { selection: DetailSelection; navigate: (value: DetailSelection) => void; titleId: string }) {
  const load = useCallback((signal: AbortSignal) => fetchDetail(selection.kind, selection.id, signal), [selection.kind, selection.id]);
  const { data, loading, error, retry } = useApiResource(load);
  return <div className="drawer__content" aria-busy={loading}>
    <Badge tone="accent">{DETAIL_LABELS[selection.kind]}</Badge>
    <h2 id={titleId}>{data ? titleOf(data) : "Intelligence detail"}</h2>
    {loading && <div className="detail-loading" role="status"><span className="skeleton" />Loading record details…</div>}
    {error && <div className="alert" role="alert">
      <div className="alert__title">{error instanceof ApiError && error.status === 404 ? "Record not found" : "Could not load details"}</div>
      <p>{error instanceof ApiError && error.status === 404 ? "This record may no longer exist. Close the drawer to return to the list." : error.message}</p>
      <button className="btn" type="button" onClick={retry}>Retry details</button>
    </div>}
    {data && ("canonical_name" in data ? <EntityFields data={data} navigate={navigate} /> : <RecordFields data={data} navigate={navigate} />)}
  </div>;
}

/** Native modal supplies focus trapping, inert background and Escape support. */
export default function DetailDrawer({ selection, onClose }: { selection: DetailSelection; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [history, setHistory] = useState<DetailSelection[]>([selection]);
  const current = history[history.length - 1];
  const titleId = useId();
  const navigate = (next: DetailSelection) => {
    setHistory((items) => [...items, next]);
    dialogRef.current?.scrollTo({ top: 0 });
  };

  useEffect(() => {
    const dialog = dialogRef.current!;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  return <dialog ref={dialogRef} className="drawer" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
    }}>
    <div className="drawer__toolbar">
      {history.length > 1 ? <button type="button" className="btn" onClick={() => setHistory((items) => items.slice(0, -1))}>← Back</button> : <span className="eyebrow">Intelligence explorer</span>}
      <button type="button" className="btn" onClick={onClose} autoFocus aria-label="Close details">Close ×</button>
    </div>
    <DetailContent key={`${current.kind}:${current.id}`} selection={current} navigate={navigate} titleId={titleId} />
  </dialog>;
}
