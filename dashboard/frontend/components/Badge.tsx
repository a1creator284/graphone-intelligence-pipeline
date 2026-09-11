type Tone = "neutral" | "accent" | "ok" | "warn";

interface BadgeProps {
  children: React.ReactNode;
  tone?: Tone;
  title?: string;
}

/** Small pill for enum-ish values: source name, pricing model, role family. */
export default function Badge({ children, tone = "neutral", title }: BadgeProps) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {children}
    </span>
  );
}

/**
 * Pricing is a closed enum in the schema (FREE / FREEMIUM / PAID /
 * ENTERPRISE) but is nullable, so the null case is rendered explicitly
 * rather than being silently dropped.
 */
export function PricingBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="muted">—</span>;

  const tone: Tone =
    value === "FREE" ? "ok" : value === "ENTERPRISE" ? "warn" : "accent";

  return (
    <Badge tone={tone}>
      {value.charAt(0) + value.slice(1).toLowerCase()}
    </Badge>
  );
}
