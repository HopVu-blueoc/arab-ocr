import { useEffect, useRef } from "react";
import type { LineDto } from "../api/types";
import { useSelection } from "../store/selection";

export function LineRow({ line }: { line: LineDto }) {
  const { selectedId, hoveredId, select, hover } = useSelection();
  const ref = useRef<HTMLDivElement>(null);
  const isSelected = line.id === selectedId;

  useEffect(() => {
    if (isSelected) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [isSelected]);

  return (
    <div
      ref={ref}
      className={[
        "line-row",
        isSelected ? "line-selected" : "",
        line.id === hoveredId ? "line-hovered" : "",
      ].join(" ")}
      onClick={() => select(line.id)}
      onMouseEnter={() => hover(line.id)}
      onMouseLeave={() => hover(null)}
    >
      <span className="line-index">{line.reading_order + 1}</span>
      <span className="line-text" dir="rtl" lang="ar">
        {line.final_text}
      </span>
      <span className="line-score">{line.score.toFixed(2)}</span>
    </div>
  );
}
