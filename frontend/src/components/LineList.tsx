import type { LineDto } from "../api/types";
import { LineRow } from "./LineRow";

export function LineList({ lines }: { lines: LineDto[] }) {
  if (lines.length === 0) return <p className="empty">No text detected.</p>;
  return (
    <div className="line-list">
      {lines.map((line) => (
        <LineRow key={line.id} line={line} />
      ))}
    </div>
  );
}
