export interface Point {
  x: number
  y: number
}

export interface HandleMeasurement {
  type: 'source' | 'target'
  width: number
  height: number
}

export interface ConnectionPoints {
  source: Point
  target: Point
}

const visible_handle_diameter = 10
const visible_handle_radius = visible_handle_diameter / 2

/** Moves the saved edge's handle-boundary anchors onto the visible circle rims. */
export function saved_edge_points(source: Point, target: Point): ConnectionPoints {
  return {
    source: { x: source.x, y: source.y + visible_handle_radius },
    target,
  }
}

/** Converts XYFlow's measured-handle center anchors to circle rims while dragging. */
export function connection_points(
  from: Point,
  from_handle: HandleMeasurement,
  to: Point,
  to_handle: HandleMeasurement | null,
): ConnectionPoints {
  const source_y = from_handle.type === 'source'
    ? from.y + from_handle.height / 2 + visible_handle_radius
    : from.y - from_handle.height / 2
  return {
    source: { x: from.x, y: source_y },
    target: to_handle ? { x: to.x, y: to.y - to_handle.height / 2 } : to,
  }
}
