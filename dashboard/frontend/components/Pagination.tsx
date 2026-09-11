"use client";

const PAGE_SIZES = [10, 25, 50, 100];

interface PaginationProps {
  page: number;
  pageCount: number;
  pageSize: number;
  total: number;
  offset: number;
  shown: number;
  disabled?: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

export default function Pagination({
  page,
  pageCount,
  pageSize,
  total,
  offset,
  shown,
  disabled,
  onPageChange,
  onPageSizeChange,
}: PaginationProps) {
  // "Showing 26–50 of 1,204" is far more useful than a bare page number,
  // especially when the last page is short.
  const from = total === 0 ? 0 : offset + 1;
  const to = offset + shown;

  return (
    <div className="pager">
      <div className="pager__summary">
        {total === 0
          ? "No rows"
          : `Showing ${from.toLocaleString()}–${to.toLocaleString()} of ${total.toLocaleString()}`}
      </div>

      <div className="pager__controls">
        <label className="pager__size">
          <span>Rows</span>
          <select
            value={pageSize}
            disabled={disabled}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
            aria-label="Rows per page"
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>

        <div className="pager__buttons">
          <button
            type="button"
            className="btn"
            onClick={() => onPageChange(1)}
            disabled={disabled || page <= 1}
            aria-label="First page"
          >
            «
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => onPageChange(page - 1)}
            disabled={disabled || page <= 1}
          >
            Prev
          </button>
          <span className="pager__page" aria-live="polite">
            Page {page.toLocaleString()} of {pageCount.toLocaleString()}
          </span>
          <button
            type="button"
            className="btn"
            onClick={() => onPageChange(page + 1)}
            disabled={disabled || page >= pageCount}
          >
            Next
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => onPageChange(pageCount)}
            disabled={disabled || page >= pageCount}
            aria-label="Last page"
          >
            »
          </button>
        </div>
      </div>
    </div>
  );
}
