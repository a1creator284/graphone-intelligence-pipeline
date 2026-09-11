"use client";

import { useEffect, useState } from "react";

/** Small cancellation-safe loader for details and activity; list paging stays separate. */
export function useApiResource<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    load(controller.signal)
      .then((value) => { if (!controller.signal.aborted) setData(value); })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause : new Error("Could not load this data."));
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [load, attempt]);

  return { data, error, loading, retry: () => setAttempt((value) => value + 1) };
}
