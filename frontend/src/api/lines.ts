import type { LineDto } from "./types";

/** Every line's final text, in reading order, one per line - for "copy all".
 * Blank lines are skipped rather than left as empty rows in the pasted text. */
export function joinFinalText(lines: LineDto[]): string {
  return lines
    .map((l) => l.final_text.trim())
    .filter((text) => text.length > 0)
    .join("\n");
}

/** Copy text to the clipboard, with a fallback for non-secure contexts.
 *
 * navigator.clipboard.writeText needs a secure context (HTTPS or localhost).
 * The on-prem Docker deployment serves plain HTTP on a LAN address/hostname,
 * which is NOT localhost, so that API throws there - this would otherwise
 * silently fail for every user of the actual deployment, not just in a
 * sandboxed browser. document.execCommand("copy") is deprecated but still
 * broadly supported and works without a secure context, so it is the
 * fallback rather than the primary path (the modern API is preferred when
 * it's actually available). Throws if both paths fail, so callers can show
 * the reviewer a clear error instead of a silent no-op.
 */
export async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // fall through to the legacy path below
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed"; // keep it off-screen, out of the layout
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  try {
    const ok = document.execCommand("copy");
    if (!ok) throw new Error("copy command was not successful");
  } catch (err) {
    throw new Error(
      `couldn't copy to clipboard: ${err instanceof Error ? err.message : String(err)}`,
    );
  } finally {
    textarea.remove();
  }
}
