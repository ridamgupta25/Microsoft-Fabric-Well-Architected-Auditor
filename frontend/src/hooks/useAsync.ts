/**
 * A small async-state hook.
 *
 * Every data-loading component needs the same four things: data, loading, error,
 * and a way to re-run. Writing that inline in each component is where stale-state
 * and unmounted-setState bugs come from, so it lives here once.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Run `fn` on mount and whenever `deps` change.
 *
 * @param immediate Set false to require an explicit `reload()` — useful for
 *   actions that should not fire automatically.
 */
export function useAsync<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
  immediate = true,
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(immediate);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  // Keep the latest fn without making it a dependency, so callers can pass an
  // inline arrow function without triggering an infinite reload loop.
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    if (!immediate && tick === 0) return;

    const controller = new AbortController();
    let active = true;

    setLoading(true);
    setError(null);

    fnRef
      .current(controller.signal)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((err: unknown) => {
        if (!active || controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      // Guard against setting state after unmount, and cancel in-flight work.
      active = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, immediate]);

  return { data, loading, error, reload };
}
