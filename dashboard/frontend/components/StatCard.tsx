interface StatCardProps {
  label: string;
  value?: number;
  hint?: string;
  loading?: boolean;
}

export default function StatCard({ label, value, hint, loading }: StatCardProps) {
  return (
    <div className="stat-card">
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
    </div>
  );
}
