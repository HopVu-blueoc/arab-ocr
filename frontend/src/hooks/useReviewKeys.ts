import { useEffect } from "react";
import type { LineDto } from "../api/types";

interface Options {
  lines: LineDto[];
  selectedId: number | null;
  select: (id: number | null) => void;
  onApproveLine: (lineId: number) => void;
  onApproveImage: () => void;
  onNextImage: () => void;
}

export function useReviewKeys(opts: Options) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      const typing = target.tagName === "INPUT" || target.tagName === "TEXTAREA";
      if (typing && event.key !== "Escape") return;

      const index = opts.lines.findIndex((l) => l.id === opts.selectedId);
      const move = (delta: number) => {
        const next = opts.lines[Math.min(Math.max(index + delta, 0), opts.lines.length - 1)];
        if (next) opts.select(next.id);
      };

      switch (event.key) {
        case "j":
          move(index === -1 ? 0 : 1);
          break;
        case "k":
          move(-1);
          break;
        case "a":
          if (opts.selectedId !== null) opts.onApproveLine(opts.selectedId);
          break;
        case "A":
          opts.onApproveImage();
          break;
        case "n":
          opts.onNextImage();
          break;
        case "Enter": {
          const el = document.querySelector<HTMLInputElement>(".line-selected .line-text");
          el?.focus();
          event.preventDefault();
          break;
        }
        default:
          return;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [opts]);
}
