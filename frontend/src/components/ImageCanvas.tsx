import { useRef, useState } from "react";
import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";
import { detectBox, imageFileUrl } from "../api/client";
import type { ImageDetailDto, LineDto } from "../api/types";
import { useSelection } from "../store/selection";
import { polygonToPoints } from "./polygon";

const MIN_BOX_SIDE = 8; // image pixels; matches the backend's own minimum

interface DraftBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export function ImageCanvas({
  image,
  drawMode,
  onLinesChanged,
}: {
  image: ImageDetailDto;
  drawMode: boolean;
  onLinesChanged: (lines: LineDto[]) => void;
}) {
  const { selectedId, hoveredId, select, hover } = useSelection();
  const svgRef = useRef<SVGSVGElement>(null);
  const [draft, setDraft] = useState<DraftBox | null>(null);
  const [pending, setPending] = useState(false);
  const [drawError, setDrawError] = useState<string | null>(null);

  // The shared container+viewBox pattern (see the polygon overlay below)
  // means this conversion stays correct through any zoom/pan state without
  // any manual scale arithmetic.
  function toImagePoint(clientX: number, clientY: number): { x: number; y: number } {
    const svg = svgRef.current!;
    const point = svg.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    const { x, y } = point.matrixTransform(svg.getScreenCTM()!.inverse());
    return {
      x: Math.min(Math.max(x, 0), image.width),
      y: Math.min(Math.max(y, 0), image.height),
    };
  }

  function onPointerDown(e: React.PointerEvent<SVGSVGElement>) {
    if (!drawMode || pending) return;
    const p = toImagePoint(e.clientX, e.clientY);
    setDrawError(null);
    setDraft({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  }

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!draft || pending) return;
    const p = toImagePoint(e.clientX, e.clientY);
    setDraft({ ...draft, x1: p.x, y1: p.y });
  }

  async function onPointerUp() {
    if (!draft) return;
    const x0 = Math.min(draft.x0, draft.x1);
    const y0 = Math.min(draft.y0, draft.y1);
    const x1 = Math.max(draft.x0, draft.x1);
    const y1 = Math.max(draft.y0, draft.y1);

    if (x1 - x0 < MIN_BOX_SIDE || y1 - y0 < MIN_BOX_SIDE) {
      setDraft(null); // a stray click, not a drag - fail fast, save a request
      return;
    }

    setPending(true);
    try {
      const lines = await detectBox(image.id, [
        [x0, y0],
        [x1, y0],
        [x1, y1],
        [x0, y1],
      ]);
      onLinesChanged(lines);
      setDraft(null);
    } catch (err) {
      setDrawError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="canvas-frame">
      <TransformWrapper
        minScale={0.5}
        maxScale={8}
        doubleClick={{ mode: "reset" }}
        wheel={{ step: 0.15 }}
        panning={{ disabled: drawMode }}
      >
        <TransformComponent wrapperClass="canvas-wrapper" contentClass="canvas-content">
          {/* img and svg share this box, so the transform moves them together
              and there is no scale arithmetic anywhere. */}
          <div
            className="canvas-stack"
            style={{ aspectRatio: `${image.width} / ${image.height}` }}
          >
            <img src={imageFileUrl(image.id)} alt={image.filename} className="canvas-img" />
            <svg
              ref={svgRef}
              className={["canvas-svg", drawMode ? "canvas-svg-draw" : ""].join(" ")}
              viewBox={`0 0 ${image.width} ${image.height}`}
              preserveAspectRatio="xMidYMid meet"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
            >
              {image.lines.map((line) => (
                <polygon
                  key={line.id}
                  points={polygonToPoints(line.polygon)}
                  className={[
                    "box",
                    line.id === selectedId ? "box-selected" : "",
                    line.id === hoveredId ? "box-hovered" : "",
                  ].join(" ")}
                  onClick={() => select(line.id)}
                  onMouseEnter={() => hover(line.id)}
                  onMouseLeave={() => hover(null)}
                />
              ))}
              {draft && (
                <rect
                  x={Math.min(draft.x0, draft.x1)}
                  y={Math.min(draft.y0, draft.y1)}
                  width={Math.abs(draft.x1 - draft.x0)}
                  height={Math.abs(draft.y1 - draft.y0)}
                  className={pending ? "draw-box draw-box-pending" : "draw-box"}
                />
              )}
            </svg>
          </div>
        </TransformComponent>
      </TransformWrapper>
      {drawError && <p className="canvas-error">{drawError}</p>}
    </div>
  );
}
