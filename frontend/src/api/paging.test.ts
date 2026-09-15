import { describe, expect, it } from "vitest";
import {
  appendPage,
  emptyPageState,
  fromFirstPage,
  mergeFirstPage,
  shouldFetchNextPage,
  trimPages,
} from "./paging";
import { batch } from "../test/factories";
import type { BatchDto } from "./types";

const key = (b: BatchDto) => b.id;
const page = (ids: number[], next: string | null = null) => ({
  items: ids.map((id) => batch({ id, name: `b${id}` })),
  next_cursor: next,
});

const ids = (items: BatchDto[]) => items.map((i) => i.id);

describe("appendPage", () => {
  it("appends the next page and advances the cursor", () => {
    const state = fromFirstPage(page([1, 2], "c1"));
    const merged = appendPage(state, page([3, 4], "c2"), key);
    expect(ids(merged.items)).toEqual([1, 2, 3, 4]);
    expect(merged.nextCursor).toBe("c2");
    expect(merged.loadedPages).toBe(2);
  });

  it("drops items already loaded when a concurrent insert shifts the boundary", () => {
    const state = fromFirstPage(page([1, 2], "c1"));
    const merged = appendPage(state, page([2, 3], null), key);
    expect(ids(merged.items)).toEqual([1, 2, 3]);
  });
});

describe("mergeFirstPage", () => {
  it("adopts the page when nothing is loaded yet", () => {
    const merged = mergeFirstPage(emptyPageState<BatchDto>(), page([1, 2], "c"), key);
    expect(ids(merged.items)).toEqual([1, 2]);
    expect(merged.loadedPages).toBe(1);
  });

  it("updates a known item in place without reordering", () => {
    const state = fromFirstPage(page([1, 2, 3], "c"));
    const updated = batch({ id: 2, name: "renamed", done_count: 7 });
    const fresh = { items: [batch({ id: 1 }), updated, batch({ id: 3 })], next_cursor: "c" };

    const merged = mergeFirstPage(state, fresh, key);

    expect(ids(merged.items)).toEqual([1, 2, 3]);
    expect(merged.items[1].name).toBe("renamed");
    expect(merged.items[1].done_count).toBe(7);
  });

  it("puts a newly created batch at the head", () => {
    const state = fromFirstPage(page([2, 3], "c"));
    const merged = mergeFirstPage(state, page([1, 2, 3], "c"), key);
    expect(ids(merged.items)).toEqual([1, 2, 3]);
  });

  it("removes an item deleted inside the range page 1 covers", () => {
    const state = fromFirstPage(page([1, 2, 3], null));
    const merged = mergeFirstPage(state, page([1, 3], null), key);
    expect(ids(merged.items)).toEqual([1, 3]);
  });

  it("keeps pages 2..n when a poll refreshes only page 1", () => {
    // The rule this whole module exists for: page 1 says nothing about what
    // lies beyond it, so its silence must not delete loaded pages.
    let state = fromFirstPage(page([1, 2], "c1"));
    state = appendPage(state, page([3, 4], "c2"), key);
    state = appendPage(state, page([5, 6], "c3"), key);
    state = appendPage(state, page([7, 8], "c4"), key);
    state = appendPage(state, page([9, 10], null), key);
    expect(state.items).toHaveLength(10);

    const merged = mergeFirstPage(state, page([1, 2], "c1"), key);

    expect(ids(merged.items)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(merged.loadedPages).toBe(5);
    expect(merged.nextCursor).toBeNull();
  });

  it("leaves the cursor and page count to loadMore alone", () => {
    const state = appendPage(fromFirstPage(page([1], "c1")), page([2], "c2"), key);
    const merged = mergeFirstPage(state, page([1], "c1"), key);
    expect(merged.nextCursor).toBe("c2");
    expect(merged.loadedPages).toBe(2);
  });
});

describe("trimPages", () => {
  it("caps unbounded growth from the far end", () => {
    const state = fromFirstPage(page([1, 2, 3, 4, 5], null));
    expect(ids(trimPages(state, 3).items)).toEqual([1, 2, 3]);
  });

  it("leaves a short list alone", () => {
    const state = fromFirstPage(page([1, 2], null));
    expect(trimPages(state, 5)).toBe(state);
  });
});

describe("shouldFetchNextPage", () => {
  it("fetches as the active item nears the loaded tail", () => {
    expect(shouldFetchNextPage(85, 100, true, 20)).toBe(true);
  });

  it("stays put while there is plenty loaded ahead", () => {
    expect(shouldFetchNextPage(10, 100, true, 20)).toBe(false);
  });

  it("never fetches when there is no next page", () => {
    expect(shouldFetchNextPage(99, 100, false, 20)).toBe(false);
  });

  it("ignores an active item that is not in the list", () => {
    expect(shouldFetchNextPage(-1, 100, true, 20)).toBe(false);
  });
});
