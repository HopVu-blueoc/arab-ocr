export function polygonToPoints(polygon: number[][]): string {
  return polygon.map(([x, y]) => `${x},${y}`).join(" ");
}
