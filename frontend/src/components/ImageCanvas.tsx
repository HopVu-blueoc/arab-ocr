import { imageFileUrl } from "../api/client";
import type { ImageDetailDto } from "../api/types";
import { useSelection } from "../store/selection";
import { polygonToPoints } from "./polygon";

export function ImageCanvas({ image }: { image: ImageDetailDto }) {
  const { selectedId, hoveredId, select, hover } = useSelection();

  return (
    <div className="canvas-frame">
      <div className="canvas-stack" style={{ aspectRatio: `${image.width} / ${image.height}` }}>
        <img src={imageFileUrl(image.id)} alt={image.filename} className="canvas-img" />
        <svg
          className="canvas-svg"
          viewBox={`0 0 ${image.width} ${image.height}`}
          preserveAspectRatio="xMidYMid meet"
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
        </svg>
      </div>
    </div>
  );
}
