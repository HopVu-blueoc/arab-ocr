export function polygonToPoints(polygon: number[][]): string {
  return polygon.map(([x, y]) => `${x},${y}`).join(" ");
}

export function polygonBBox(polygon: number[][]) {
  const xs = polygon.map(([x]) => x);
  const ys = polygon.map(([, y]) => y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}
