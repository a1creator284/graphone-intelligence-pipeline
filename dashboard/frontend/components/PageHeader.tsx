import { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  description: string;
  /** Total row count; rendered as a live badge once known. */
  total?: number;
  loading?: boolean;
  aside?: ReactNode;
}

export default function PageHeader({
  title,
  description,
  total,
  loading,
  aside,
}: PageHeaderProps) {
  return (
    <header className="page-header">
      <div>
        <div className="page-header__title-row">
          <h1>{title}</h1>
          {loading ? (
            <span className="count-badge count-badge--skeleton" aria-hidden="true" />
          ) : total !== undefined ? (
            <span className="count-badge">{total.toLocaleString()}</span>
          ) : null}
        </div>
        <p>{description}</p>
      </div>
      {aside ?? null}
    </header>
  );
}
