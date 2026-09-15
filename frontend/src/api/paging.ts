/** Keyset page state and the merge rules a poll has to obey.
 *
 * A poll refetches only the FIRST page - refetching every loaded page on each
 * tick is the load pattern this whole change exists to remove. That makes the
 * merge subtle: page 1 carries no information about pages 2..n, so it must
 * never be treated as the complete list.
 */

export interface PageState<T> {
  items: T[];
  nextCursor: string | null;
  /** Pages fetched so far. 1 means only the first page is loaded. */
  loadedPages: number;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export const emptyPageState = <T>(): PageState<T> => ({
  items: [],
  nextCursor: null,
  loadedPages: 0,
});

export const fromFirstPage = <T>(page: Page<T>): PageState<T> => ({
  items: page.items,
  nextCursor: page.next_cursor,
  loadedPages: 1,
});

/** Append the next keyset page, dropping anything already held.
 *
 * The dedupe is not paranoia: rows inserted between two requests shift the
 * boundary, so a page can legitimately repeat an item already loaded.
 */
export function appendPage<T>(
  state: PageState<T>,
  page: Page<T>,
  key: (item: T) => number,
): PageState<T> {
  const known = new Set(state.items.map(key));
  return {
    items: [...state.items, ...page.items.filter((item) => !known.has(key(item)))],
    nextCursor: page.next_cursor,
    loadedPages: state.loadedPages + 1,
  };
}

/** Merge a freshly-polled first page into the loaded list.
 *
 * Three rules, and the third is the one that matters:
 *
 * 1. A known id is replaced IN PLACE. Order is preserved and React keys are
 *    ids, so nothing remounts and the scroll position holds.
 * 2. An unknown id is inserted at the front - for both lists the first page is
 *    the head of the sort order, so a new row belongs there.
 * 3. A loaded item missing from the fresh page is deleted ONLY if it sits
 *    within the range page 1 actually covers. Beyond that boundary its absence
 *    means "page 1 didn't reach it", not "it was deleted" - without this rule
 *    every poll would wipe pages 2..n.
 */
export function mergeFirstPage<T>(
  state: PageState<T>,
  page: Page<T>,
  key: (item: T) => number,
): PageState<T> {
  if (state.loadedPages === 0) return fromFirstPage(page);

  const fresh = new Map(page.items.map((item) => [key(item), item]));

  // The covered prefix ends at the last loaded item the fresh page still
  // knows about; anything after it is out of page 1's reach.
  let covered = -1;
  state.items.forEach((item, index) => {
    if (fresh.has(key(item))) covered = index;
  });

  const kept: T[] = [];
  state.items.forEach((item, index) => {
    const id = key(item);
    const replacement = fresh.get(id);
    if (replacement !== undefined) {
      kept.push(replacement);
      fresh.delete(id);
    } else if (index > covered) {
      kept.push(item); // beyond page 1's reach - no evidence either way
    }
    // else: inside the covered prefix and gone from the server - drop it
  });

  // Whatever the fresh page still holds is new since the last load.
  const added = page.items.filter((item) => fresh.has(key(item)));
  return { ...state, items: [...added, ...kept] };
}

/** Cap how much a long-lived session accumulates.
 *
 * Cheaper than a virtualisation dependency, and the strip only ever needs a
 * window around where the reviewer is working.
 */
export function trimPages<T>(state: PageState<T>, maxItems: number): PageState<T> {
  if (state.items.length <= maxItems) return state;
  return { ...state, items: state.items.slice(0, maxItems) };
}

/** Whether to pull the next page because the reviewer is nearing the end.
 *
 * Without this, arrowing through a batch stops dead at the page boundary:
 * "next image" walks the loaded array and silently does nothing past its end.
 */
export function shouldFetchNextPage(
  activeIndex: number,
  loadedCount: number,
  hasMore: boolean,
  threshold = 20,
): boolean {
  if (!hasMore || activeIndex < 0) return false;
  return loadedCount - activeIndex - 1 <= threshold;
}
