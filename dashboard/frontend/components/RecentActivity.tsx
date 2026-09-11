"use client";

import { useState } from "react";
import Badge from "@/components/Badge";
import DetailDrawer from "@/components/DetailDrawer";
import ExternalLink from "@/components/ExternalLink";
import { DETAIL_LABELS, DetailSelection, fetchRecentActivity, isRecordKind } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/useApiResource";

export default function RecentActivity() {
  const { data, error, loading, retry } = useApiResource(fetchRecentActivity);
  const [selected, setSelected] = useState<DetailSelection | null>(null);
  return <section className="activity-section" aria-labelledby="activity-title">
    <div className="section-heading"><div>
      <h2 id="activity-title">Recent Activity</h2>
      <p>Latest collected records across all five sources of intelligence.</p>
    </div><button className="btn" type="button" disabled={loading} onClick={retry}>Refresh activity</button></div>
    <div className="panel activity-panel" aria-busy={loading}>
      {loading ? <div className="empty" role="status">Loading recent activity…</div>
        : error ? <div className="alert" role="alert"><div className="alert__title">Could not load recent activity</div><p>{error.message}</p><button className="btn" type="button" onClick={retry}>Retry activity</button></div>
        : !data?.items.length ? <div className="empty"><div className="empty__title">No activity yet</div><p className="empty__hint">No collected records were returned by the database. Activity will appear after ingestion; no sample records are added.</p></div>
        : <ul className="activity-list">{data.items.map((item) => {
          const kind = isRecordKind(item.vertical) ? item.vertical : null;
          return <li className="activity-item" key={`${item.vertical}:${item.id}`}>
            <Badge tone="accent">{kind ? DETAIL_LABELS[kind] : item.vertical}</Badge>
            <div className="activity-item__main">
              {kind ? <button className="text-button" type="button" onClick={() => setSelected({ kind, id: item.id })}>{item.title}</button> : <strong>{item.title}</strong>}
              <div className="cell-sub">{item.source_name} · <ExternalLink href={item.source_url} label="Source" /></div>
            </div>
            <span className="activity-time" title={item.collected_at ?? undefined}>{item.collected_at ? formatDateTime(item.collected_at) : "Collection time unavailable"}</span>
          </li>;
        })}</ul>}
    </div>
    {data?.items.length ? <p className="cell-sub">Showing {data.items.length} latest records (maximum {data.limit}). Times are local; undated records appear last.</p> : null}
    {selected && <DetailDrawer key={`${selected.kind}:${selected.id}`} selection={selected} onClose={() => setSelected(null)} />}
  </section>;
}
