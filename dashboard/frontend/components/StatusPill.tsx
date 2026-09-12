export type ConnectionState = "loading" | "ok" | "degraded" | "offline";

const LABELS: Record<ConnectionState, string> = {
  loading: "Checking backend…",
  ok: "Backend connected",
  degraded: "Backend up · database unavailable",
  offline: "Backend unreachable",
};

const MODIFIERS: Record<ConnectionState, string> = {
  loading: "",
  ok: " status--ok",
  degraded: " status--warn",
  offline: " status--error",
};

export default function StatusPill({ state }: { state: ConnectionState }) {
  return (
    <span
      className={`status${MODIFIERS[state]}`}
      role="status"
      aria-live="polite"
    >
      <span className="status__dot" aria-hidden="true" />
      {LABELS[state]}
    </span>
  );
}
