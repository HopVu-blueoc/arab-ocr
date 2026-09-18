import { useCallback, useEffect, useRef, useState } from "react";
import { nextPollDelay } from "../api/poll";
import {
  type Page,
  type PageState,
  appendPage,
  emptyPageState,
  fromFirstPage,
  mergeFirstPage,
  trimPages,
} from "../api/paging";

const isVisible = () => typeof document === "undefined" || document.visibilityState === "visible";

/** A keyset-paged list that polls its first page while there is work to watch.
 *
 * The delay is derived from state rather than fixed, so the same hook covers
 * "OCR is running, check often", "nothing is happening, check rarely" and
 * "this tab is hidden, don't check at all". A `cycle` counter reschedules
 * after each attempt; without it an unchanged delay would leave the effect's
 * dependencies equal and never arm the next timer.
 */
export function usePagedPoll<T>({
  fetchPage,
  itemKey,
  hasWork,
  enabled = true,
  resetKey,
  maxItems,
  onError,
}: {
  fetchPage: (cursor: string | null) => Promise<Page<T>>;
  itemKey: (item: T) => number;
  hasWork: (items: T[]) => boolean;
  enabled?: boolean;
  /** Changing this throws the loaded pages away - e.g. a different batch. */
  resetKey?: unknown;
  maxItems?: number;
  onError?: (error: unknown) => void;
}) {
  const [state, setState] = useState<PageState<T>>(emptyPageState<T>);
  const [failures, setFailures] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [visible, setVisible] = useState(isVisible);
  const [cycle, setCycle] = useState(0);

  // Resetting during render rather than in an effect: React's documented way
  // to drop state when a prop changes, and it avoids rendering one frame of
  // the previous batch's images under the new batch.
  const [seenResetKey, setSeenResetKey] = useState(resetKey);
  if (seenResetKey !== resetKey) {
    setSeenResetKey(resetKey);
    setState(emptyPageState<T>());
    setFailures(0);
  }

  const onErrorRef = useRef(onError);
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const refresh = useCallback(async () => {
    const page = await fetchPage(null);
    setState((current) =>
      current.loadedPages === 0 ? fromFirstPage(page) : mergeFirstPage(current, page, itemKey),
    );
  }, [fetchPage, itemKey]);

  /** Reset to a single fresh first page - for a user action, not a poll. */
  const reload = useCallback(async () => {
    const page = await fetchPage(null);
    setState(fromFirstPage(page));
  }, [fetchPage]);

  const loadMore = useCallback(async () => {
    if (state.nextCursor === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await fetchPage(state.nextCursor);
      setState((latest) => {
        const merged = appendPage(latest, page, itemKey);
        return maxItems ? trimPages(merged, maxItems) : merged;
      });
    } finally {
      setLoadingMore(false);
    }
  }, [fetchPage, itemKey, loadingMore, maxItems, state.nextCursor]);

  const runPoll = useCallback(async () => {
    try {
      await refresh();
      setFailures(0);
    } catch (error) {
      setFailures((n) => n + 1);
      onErrorRef.current?.(error);
    }
    setCycle((n) => n + 1); // arms the next timer
  }, [refresh]);

  const runPollRef = useRef(runPoll);
  useEffect(() => {
    runPollRef.current = runPoll;
  }, [runPoll]);

  // First load, and again whenever the target changes.
  useEffect(() => {
    if (!enabled) return;
    void runPollRef.current();
  }, [enabled, resetKey]);

  useEffect(() => {
    const onChange = () => {
      const now = isVisible();
      setVisible(now);
      // Refetch on return, or the tab shows data up to a poll interval stale.
      if (now && enabled) void runPollRef.current();
    };
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, [enabled]);

  const delay = enabled
    ? nextPollDelay({ hasWork: hasWork(state.items), visible, consecutiveFailures: failures })
    : null;

  useEffect(() => {
    if (delay === null) return;
    const timer = setTimeout(() => void runPollRef.current(), delay);
    return () => clearTimeout(timer);
  }, [delay, cycle]);

  return {
    items: state.items,
    hasMore: state.nextCursor !== null,
    loadingMore,
    failures,
    loadMore,
    refresh,
    reload,
  };
}
