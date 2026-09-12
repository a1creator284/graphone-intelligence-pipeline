"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, ListQuery, Page, fetchPage } from "@/lib/api";

export interface RecordPageState<T> {
  items: T[];
  total: number;
  /** True only for the very first load, so paging does not blank the table. */
  initialLoading: boolean;
  /** True for any in-flight request, used to dim the table while paging. */
  refreshing: boolean;
  error: string | null;
  page: number;
  pageCount: number;
  pageSize: number;
  offset: number;
  hasMore: boolean;
  search: string;
  sort?: string;
  setSearch: (value: string) => void;
  setSort: (value: string) => void;
  setPageSize: (value: number) => void;
  goToPage: (page: number) => void;
  retry: () => void;
}

interface Options {
  pageSize?: number;
  /** Initial sort key; only the entities endpoint uses this. */
  sort?: string;
}

const SEARCH_DEBOUNCE_MS = 300;

/**
 * Owns everything a list page needs: paging, debounced search, sort, and the
 * loading / empty / error tri-state.
 *
 * Two details worth calling out:
 *
 * 1. Each request carries an AbortController and a monotonically increasing
 *    request id. A slow page-1 response landing after a fast page-2 response
 *    would otherwise overwrite the newer data; the id check drops it.
 * 2. `initialLoading` and `refreshing` are separate. Paging keeps the old
 *    rows on screen and just dims them, which avoids the layout jump you get
 *    from unmounting the table on every click.
 */
export function useRecordPage<T>(path: string, options: Options = {}): RecordPageState<T> {
  const pageSizeDefault = options.pageSize ?? 25;

  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSizeState] = useState(pageSizeDefault);
  const [search, setSearchState] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [sort, setSortState] = useState(options.sort);
  const [reloadToken, setReloadToken] = useState(0);

  const requestId = useRef(0);
  const loadedOnce = useRef(false);

  // Debounce the search box so typing does not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    const id = ++requestId.current;

    const query: ListQuery = { limit: pageSize, offset };
    if (debouncedSearch.trim()) query.q = debouncedSearch;
    if (sort) query.sort = sort;

    setRefreshing(true);
    if (!loadedOnce.current) setInitialLoading(true);

    fetchPage<T>(path, query, controller.signal)
      .then((payload: Page<T>) => {
        if (id !== requestId.current) return; // A newer request superseded this one.
        setItems(payload.items);
        setTotal(payload.total);
        setHasMore(payload.has_more);
        setError(null);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || id !== requestId.current) return;
        setError(
          err instanceof ApiError || err instanceof Error
            ? err.message
            : "Something went wrong loading this page.",
        );
        setItems([]);
        setTotal(0);
        setHasMore(false);
      })
      .finally(() => {
        if (id !== requestId.current) return;
        loadedOnce.current = true;
        setInitialLoading(false);
        setRefreshing(false);
      });

    return () => controller.abort();
  }, [path, pageSize, offset, debouncedSearch, sort, reloadToken]);

  const setSearch = useCallback((value: string) => {
    setSearchState(value);
    setOffset(0); // A new filter invalidates the current page number.
  }, []);

  const setSort = useCallback((value: string) => {
    setSortState(value);
    setOffset(0);
  }, []);

  const setPageSize = useCallback((value: number) => {
    setPageSizeState(value);
    setOffset(0);
  }, []);

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.floor(offset / pageSize) + 1;

  const goToPage = useCallback(
    (next: number) => {
      const clamped = Math.min(Math.max(1, next), Math.max(1, Math.ceil(total / pageSize)));
      setOffset((clamped - 1) * pageSize);
    },
    [total, pageSize],
  );

  const retry = useCallback(() => setReloadToken((n) => n + 1), []);

  return {
    items,
    total,
    initialLoading,
    refreshing,
    error,
    page,
    pageCount,
    pageSize,
    offset,
    hasMore,
    search,
    sort,
    setSearch,
    setSort,
    setPageSize,
    goToPage,
    retry,
  };
}
