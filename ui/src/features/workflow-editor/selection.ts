export interface Rect { x: number; y: number; width: number; height: number }

export const selection_threshold = 0.5

export function to_flow_rect(rect: Rect, transform: [number, number, number]): Rect {
  const [tx, ty, zoom] = transform
  return { x: (rect.x - tx) / zoom, y: (rect.y - ty) / zoom, width: rect.width / zoom, height: rect.height / zoom }
}

export function covered_ratio(selection: Rect, node: Rect): number {
  const node_area = node.width * node.height
  if (node_area <= 0) return 0
  const width = Math.max(0, Math.min(selection.x + selection.width, node.x + node.width) - Math.max(selection.x, node.x))
  const height = Math.max(0, Math.min(selection.y + selection.height, node.y + node.height) - Math.max(selection.y, node.y))
  return (width * height) / node_area
}

export function covered_node_ids(
  nodes: Iterable<{ id: string; selectable?: boolean; hidden?: boolean; rect: Rect }>,
  selection: Rect,
): Set<string> {
  return new Set(Array.from(nodes)
    .filter(node => node.selectable !== false && node.hidden !== true && covered_ratio(selection, node.rect) > selection_threshold)
    .map(node => node.id))
}
