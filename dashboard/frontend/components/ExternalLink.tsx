import { prettyUrl } from "@/lib/format";

interface ExternalLinkProps {
  href?: string | null;
  /** Defaults to a shortened, readable form of the URL itself. */
  label?: string;
  title?: string;
}

/**
 * Outbound link to a source.
 *
 * `rel="noopener noreferrer"` is not optional here: these URLs come from
 * crawled third-party data, so the opened page must never get a handle on
 * this window (`noopener`) and must not learn where the click came from
 * (`noreferrer`).
 */
export default function ExternalLink({ href, label, title }: ExternalLinkProps) {
  if (!href) return <span className="muted">—</span>;

  return (
    <a
      className="link"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title ?? href}
    >
      {label ?? prettyUrl(href)}
      <span className="link__icon" aria-hidden="true">
        ↗
      </span>
    </a>
  );
}
