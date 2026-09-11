"use client";

import { ReactNode } from "react";
import Pagination from "@/components/Pagination";
import { RecordPageState } from "@/lib/useRecordPage";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  /** Right-align and tabular-number numeric columns. */
  numeric?: boolean;
  /** Keeps short values (dates, badges) from being stretched by long text. */
  width?: string;
  /** Hidden below the narrow breakpoint to keep mobile readable. */
  secondary?: boolean;
}

interface RecordTableProps<T> {
  state: RecordPageState<T>;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  /** Placeholder text for the search box; omit to hide search entirely. */
  searchPlaceholder?: string;
  /** Shown when the table is empty and no filter is applied. */
  emptyTitle: string;
  emptyHint: string;
  /** Optional extra controls (e.g. a sort selector) beside the search box. */
  toolbarExtra?: ReactNode;
}

const SKELETON_ROWS = 6;

/**
 * One table renderer shared by every list page.
 *
 * The four states a real data view has to handle are all here and all
 * distinct, which is the whole point of centralising this:
 *
 *  - loading  : skeleton rows sized to the real columns (no layout jump)
 *  - error    : the message plus a retry button, never a blank screen
 *  - empty    : distinguishes "nothing ingested yet" from "your filter
 *               matched nothing", because the fix differs
 *  - data     : rows, dimmed while a page change is in flight
 */
export default function RecordTable<T>({
  state,
  columns,
  rowKey,
  searchPlaceholder,
  emptyTitle,
  emptyHint,
  toolbarExtra,
}: RecordTableProps<T>) {
  const {
    items,
    total,
    initialLoading,
    refreshing,
    error,
    page,
    pageCount,
    pageSize,
    offset,
    search,
    setSearch,
    setPageSize,
    goToPage,
    retry,
  } = state;

  const isFiltered = search.trim().length > 0;
  const showEmpty = !initialLoading && !error && items.length === 0;

  return (
    <section className="table-section">
      {(searchPlaceholder || toolbarExtra) && (
        <div className="toolbar">
          {searchPlaceholder ? (
            <div className="toolbar__search">
              <input
                type="search"
                className="input"
                value={search}
                placeholder={searchPlaceholder}
                onChange={(event) => setSearch(event.target.value)}
                aria-label={searchPlaceholder}
              />
              {isFiltered ? (
                <button type="button" className="btn btn--ghost" onClick={() => setSearch("")}>
                  Clear
                </button>
              ) : null}
            </div>
          ) : (
            <div />
          )}
          {toolbarExtra ? <div className="toolbar__extra">{toolbarExtra}</div> : null}
        </div>
      )}

      {error ? (
        <div className="alert" role="alert">
          <div className="alert__title">Could not load these records</div>
          <div>{error}</div>
          <button type="button" className="btn btn--primary" onClick={retry} style={{ marginTop: 12 }}>
            Retry
          </button>
        </div>
      ) : (
        <>
          <div className={`table-wrap${refreshing && !initialLoading ? " table-wrap--busy" : ""}`}>
            <table className="table">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th
                      key={column.key}
                      className={[
                        column.numeric ? "num" : "",
                        column.secondary ? "col--secondary" : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                      style={column.width ? { width: column.width } : undefined}
                      scope="col"
                    >
                      {column.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {initialLoading
                  ? Array.from({ length: SKELETON_ROWS }).map((_, rowIndex) => (
                      <tr key={`skeleton-${rowIndex}`} aria-hidden="true">
                        {columns.map((column) => (
                          <td
                            key={column.key}
                            className={column.secondary ? "col--secondary" : undefined}
                          >
                            <span className="skeleton" />
                          </td>
                        ))}
                      </tr>
                    ))
                  : items.map((row) => (
                      <tr key={rowKey(row)}>
                        {columns.map((column) => (
                          <td
                            key={column.key}
                            className={[
                              column.numeric ? "num" : "",
                              column.secondary ? "col--secondary" : "",
                            ]
                              .filter(Boolean)
                              .join(" ")}
                          >
                            {column.render(row)}
                          </td>
                        ))}
                      </tr>
                    ))}
              </tbody>
            </table>

            {initialLoading ? (
              <div className="sr-only" role="status">
                Loading records
              </div>
            ) : null}

            {showEmpty ? (
              <div className="empty">
                <div className="empty__title">
                  {isFiltered ? "No matches" : emptyTitle}
                </div>
                <div className="empty__hint">
                  {isFiltered
                    ? `Nothing matched “${search.trim()}”. Try a shorter or different term.`
                    : emptyHint}
                </div>
                {isFiltered ? (
                  <button type="button" className="btn" onClick={() => setSearch("")}>
                    Clear search
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>

          {!showEmpty ? (
            <Pagination
              page={page}
              pageCount={pageCount}
              pageSize={pageSize}
              total={total}
              offset={offset}
              shown={items.length}
              disabled={initialLoading || refreshing}
              onPageChange={goToPage}
              onPageSizeChange={setPageSize}
            />
          ) : null}
        </>
      )}
    </section>
  );
}
