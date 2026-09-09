import { useEffect, useRef, useState } from "react";
import { updateLine } from "../api/client";
import { copyToClipboard } from "../api/lines";
import type { LineDto } from "../api/types";
import { useSelection } from "../store/selection";

export function LineRow({
  line,
  onChange,
}: {
  line: LineDto;
  onChange: (line: LineDto) => void;
}) {
  const { selectedId, hoveredId, select, hover } = useSelection();
  const ref = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState(line.final_text);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const isSelected = line.id === selectedId;

  async function copyText() {
    try {
      await copyToClipboard(line.final_text);
      setCopyError(null);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch (err) {
      setCopyError(err instanceof Error ? err.message : String(err));
    }
  }

  // A silently dropped save loses the reviewer's correction, so every write
  // reports failure in the row itself.
  async function save(patch: { corrected_text?: string | null; status?: LineDto["status"] }) {
    setSaving(true);
    setSaveError(null);
    try {
      onChange(await updateLine(line.id, patch));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => setDraft(line.final_text), [line.id, line.final_text]);
  useEffect(() => {
    if (isSelected) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [isSelected]);

  async function commit() {
    if (draft === line.final_text) return;
    await save({ corrected_text: draft === line.rec_text ? null : draft });
  }

  async function approve() {
    await save({ status: "approved" });
  }

  async function revert() {
    setDraft(line.rec_text);
    await save({ corrected_text: null });
  }

  return (
    <div
      ref={ref}
      className={[
        "line-row",
        `line-status-${line.status}`,
        saveError ? "line-save-failed" : "",
        isSelected ? "line-selected" : "",
        line.id === hoveredId ? "line-hovered" : "",
      ].join(" ")}
      onMouseEnter={() => hover(line.id)}
      onMouseLeave={() => hover(null)}
      onClick={() => select(line.id)}
    >
      <span className="line-index">{line.reading_order + 1}</span>
      <input
        className="line-text"
        dir="rtl"
        lang="ar"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onFocus={() => select(line.id)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            (e.target as HTMLInputElement).blur();
          }
          if (e.key === "Escape") setDraft(line.final_text);
        }}
      />
      <span className="line-score" title={saveError ?? `confidence ${line.score}`}>
        {saving ? "…" : saveError ? "⚠ unsaved" : line.score.toFixed(2)}
      </span>
      <div className="line-actions">
        <button onClick={copyText} title={copyError ?? "Copy this line's text"}>
          {copied ? "✓" : copyError ? "⚠" : "📋"}
        </button>
        <button onClick={approve} title="Mark this line correct">
          ✓
        </button>
        <button onClick={revert} disabled={line.corrected_text === null} title="Restore OCR text">
          ⟲
        </button>
      </div>
    </div>
  );
}
