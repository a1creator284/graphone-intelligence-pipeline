interface PlaceholderPageProps {
  title: string;
  description: string;
  endpoint?: string;
}

/**
 * Phase 1 ships the overview only. These routes exist so the sidebar links
 * resolve instead of 404-ing, and they name the API endpoint that already
 * backs the view.
 */
export default function PlaceholderPage({
  title,
  description,
  endpoint,
}: PlaceholderPageProps) {
  return (
    <>
      <header className="page-header">
        <div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
      </header>
      <div className="panel">
        <p>This view is planned for a later phase.</p>
        {endpoint ? (
          <p>
            The backing endpoint is already available and paginated:{" "}
            <code>{endpoint}</code>
          </p>
        ) : null}
      </div>
    </>
  );
}
