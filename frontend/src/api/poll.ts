/** How long to wait before the next poll, or null to not poll at all.
 *
 * The old behaviour was a flat 3s interval that never stopped, on an endpoint
 * whose cost grew with the whole corpus. Three things changed:
 *
 * - a hidden tab polls not at all, rather than burning requests nobody sees;
 * - an idle list backs off to 30s instead of stopping, because there is no
 *   push channel: a batch created in another tab has to surface somehow;
 * - repeated failures back off exponentially, so 100 clients retrying a
 *   struggling server every 3s cannot keep it down.
 */

export const ACTIVE_POLL_MS = 3000;
export const IDLE_POLL_MS = 30000;
export const MAX_BACKOFF_MS = 60000;
const MAX_BACKOFF_DOUBLINGS = 4;

export function nextPollDelay({
  hasWork,
  visible,
  consecutiveFailures = 0,
}: {
  hasWork: boolean;
  visible: boolean;
  consecutiveFailures?: number;
}): number | null {
  if (!visible) return null;
  const base = hasWork ? ACTIVE_POLL_MS : IDLE_POLL_MS;
  if (consecutiveFailures <= 0) return base;
  const backoff = base * 2 ** Math.min(consecutiveFailures, MAX_BACKOFF_DOUBLINGS);
  return Math.min(backoff, MAX_BACKOFF_MS);
}

/** Message for a failing poll, or null while it is not worth interrupting for.
 *
 * One dropped request is noise - a flaky network recovers by the next tick.
 * From the second consecutive failure it is worth telling the user, because
 * the data on screen is now visibly stale.
 */
export function pollErrorMessage(consecutiveFailures: number): string | null {
  if (consecutiveFailures < 2) return null;
  return "Not reaching the server — showing the last data loaded. Still retrying.";
}
