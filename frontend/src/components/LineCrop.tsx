import { imageFileUrl } from "../api/client";
import type { ImageDetailDto, LineDto } from "../api/types";
import { polygonBBox } from "./polygon";

const PAD = 12;

export function LineCrop({ image, line }: { image: ImageDetailDto; line: LineDto | null }) {
  if (!line) return <div className="line-crop line-crop-empty">Select a line to magnify it.</div>;

  const box = polygonBBox(line.polygon);
  const vb = [
    Math.max(box.x - PAD, 0),
    Math.max(box.y - PAD, 0),
    box.w + PAD * 2,
    box.h + PAD * 2,
  ].join(" ");

  return (
    <svg className="line-crop" viewBox={vb} preserveAspectRatio="xMidYMid meet">
      <image href={imageFileUrl(image.id)} x={0} y={0} width={image.width} height={image.height} />
    </svg>
  );
}
