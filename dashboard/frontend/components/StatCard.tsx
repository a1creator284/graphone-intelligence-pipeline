import Link from "next/link";

interface StatCardProps {
  label: string;
  value?: number;
  hint?: string;
  loading?: boolean;
  /** When set, the whole card becomes a link to that vertical's table. */
  href?: string;
}

export default function StatCard({ label, value, hint, loading, href }: StatCardProps) {
  const body = (
    <>
      <div className="stat-card__label">{label}</div>
      {loading ? (
        <div
          className="stat-card__value stat-card__value--skeleton"
          role="status"
          aria-label={`${label} loading`}
        />
      ) : (
        <div className="stat-card__value">
          {value === undefined ? "—" : value.toLocaleString()}
        </div>
      )}
      {hint ? <div className="stat-card__hint">{hint}</div> : null}
    </>
  );

  // Counts with a drill-down destination become links; the ones without a
  // dedicated view (raw documents) stay plain so nothing looks clickable
  // that is not.
  if (!href) return <div className="stat-card">{body}</div>;

  return (
    <Link className="stat-card stat-card--link" href={href}>
      {body}
      <span className="stat-card__go" aria-hidden="true">
        View →
      </span>
    </Link>
  );
}
