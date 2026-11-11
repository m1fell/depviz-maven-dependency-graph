"""Export PlantUML text and render the dependency graph as an SVG image."""

from html import escape
from pathlib import Path

from .graph import DependencyGraph

GRID_STEP = 16
NODE_WIDTH = 170
COLUMN_WIDTH = 240
ROW_HEIGHT = 76
LEFT_MARGIN = 32
FIRST_ROW = 67
NODE_HEIGHT = 48
EMPTY_GRAPH_WIDTH = 800
EMPTY_GRAPH_HEIGHT = 120
LABEL_LIMIT = 22
COORDINATE_PARTS_COUNT = 2


def plantuml(graph: DependencyGraph) -> str:
    """Translate graph nodes and edges to a PlantUML diagram description."""
    nodes = sorted(graph.nodes)
    identifiers = {node: f"n{index}" for index, node in enumerate(nodes)}
    lines = ["@startuml", "left to right direction"]
    lines.append("skinparam packageStyle rectangle")
    lines.extend(_plantuml_nodes(nodes, identifiers))
    lines.extend(_plantuml_edges(graph.edges, identifiers))
    lines.append("@enduml")
    return "\n".join(lines) + "\n"


def _plantuml_nodes(
    nodes: list[str], identifiers: dict[str, str]
) -> list[str]:
    """Create PlantUML aliases for each package node."""
    return [f'"{node}" as {identifiers[node]}' for node in nodes]


def _plantuml_edges(
    edges: dict[str, list[str]],
    identifiers: dict[str, str],
) -> list[str]:
    """Create directed PlantUML links for all package dependencies."""
    lines = []
    for package, dependencies in edges.items():
        for dependency in dependencies:
            lines.append(
                f"{identifiers[package]} --> {identifiers[dependency]}"
            )
    return lines


def _empty_svg() -> str:
    """Explain that filtering removed the root from the graph."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{EMPTY_GRAPH_WIDTH}" height="{EMPTY_GRAPH_HEIGHT}" '
        f'viewBox="0 0 {EMPTY_GRAPH_WIDTH} {EMPTY_GRAPH_HEIGHT}" role="img">'
        "<title>Пустой граф</title>"
        '<text x="400" y="65" text-anchor="middle" '
        'font-family="sans-serif" font-size="18" fill="#355ca8">'
        "Пакет исключён фильтром.</text></svg>"
    )


def _graph_positions(
    graph: DependencyGraph,
) -> tuple[dict[str, tuple[int, int]], int, int]:
    """Place nodes into columns by BFS depth and calculate SVG dimensions."""
    layers: dict[int, list[str]] = {}
    for node, depth in graph.depths.items():
        layers.setdefault(depth, []).append(node)

    max_depth = max(layers)
    max_rows = max(len(nodes) for nodes in layers.values())
    width = 2 * LEFT_MARGIN + max_depth * COLUMN_WIDTH + NODE_WIDTH
    height = FIRST_ROW + (max_rows - 1) * ROW_HEIGHT + 54
    positions = _layer_positions(layers)
    return positions, width, height


def _layer_positions(
    layers: dict[int, list[str]],
) -> dict[str, tuple[int, int]]:
    """Assign each package coordinates based on its depth and layer order."""
    positions = {}
    for depth, nodes in layers.items():
        for row, node in enumerate(nodes):
            x = LEFT_MARGIN + depth * COLUMN_WIDTH
            y = FIRST_ROW + row * ROW_HEIGHT
            positions[node] = (x, y)
    return positions


def _svg_header(graph: DependencyGraph, width: int, height: int) -> list[str]:
    """Return the SVG canvas, title, palette, grid, and arrow definitions."""
    title = escape(graph.root)
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" role="img">',
        f"<title>Граф зависимостей {title}</title>",
        "<defs>",
        _svg_grid_pattern(),
        _svg_root_gradient(),
        _svg_arrow_marker("arrow", "#45699e"),
        _svg_arrow_marker("return-arrow", "#d97706"),
        "</defs>",
        '<rect width="100%" height="100%" fill="#f7faff"/>',
        '<rect width="100%" height="100%" fill="url(#grid)"/>',
    ]


def _svg_grid_pattern() -> str:
    """Return the small dotted pattern used as the graph background."""
    return (
        f'<pattern id="grid" width="{GRID_STEP}" height="{GRID_STEP}" '
        'patternUnits="userSpaceOnUse">'
        '<circle cx="1" cy="1" r="0.8" fill="#d5e2f2"/></pattern>'
    )


def _svg_root_gradient() -> str:
    """Return the blue gradient used to highlight the graph root."""
    return (
        '<linearGradient id="root-fill"><stop stop-color="#355ca8"/>'
        '<stop offset="1" stop-color="#287e9a"/></linearGradient>'
    )


def _svg_arrow_marker(name: str, color: str) -> str:
    """Create an SVG arrow marker with the requested identifier and color."""
    return (
        f'<marker id="{name}" markerWidth="8" markerHeight="8" '
        'refX="7" refY="4" orient="auto">'
        f'<path d="M0,0 L8,4 L0,8 z" fill="{color}"/></marker>'
    )


def _edge_path(
    start: tuple[int, int],
    end: tuple[int, int],
) -> str:
    """Draw a forward edge or a curved return arrow for cycles."""
    start_x, start_y = start
    end_x, end_y = end
    if end_x <= start_x + NODE_WIDTH:
        return _cycle_edge(start, end)
    return _forward_edge(start, end)


def _cycle_edge(
    start: tuple[int, int],
    end: tuple[int, int],
) -> str:
    """Draw a dashed curved arrow for a cycle or same-layer dependency."""
    start_x, start_y = start
    end_x, end_y = end
    middle = NODE_WIDTH // 2
    return (
        f'<path d="M {start_x + middle + 12} {start_y - 24} '
        f"C {start_x + 150} {start_y - 66}, {end_x + 20} {end_y - 66}, "
        f'{end_x + middle - 12} {end_y - 24}" fill="none" '
        'stroke="#d97706" stroke-width="2" stroke-dasharray="5 4" '
        'marker-end="url(#return-arrow)"/>'
    )


def _forward_edge(
    start: tuple[int, int],
    end: tuple[int, int],
) -> str:
    """Draw a solid arrow from a package to a dependency in a later layer."""
    start_x, start_y = start
    end_x, end_y = end
    edge_start = start_x + NODE_WIDTH
    return (
        f'<path d="M {edge_start} {start_y} '
        f"C {edge_start + 35} {start_y}, {end_x - 35} {end_y}, "
        f'{end_x} {end_y}" fill="none" stroke="#45699e" '
        'stroke-width="2" marker-end="url(#arrow)"/>'
    )


def _svg_edges(
    graph: DependencyGraph,
    positions: dict[str, tuple[int, int]],
) -> list[str]:
    """Render every graph edge using its source and target coordinates."""
    paths = []
    for package, dependencies in graph.edges.items():
        for dependency in dependencies:
            path = _edge_path(positions[package], positions[dependency])
            paths.append(path)
    return paths


def _node_style(is_root: bool) -> tuple[str, str, str]:
    """Choose fill, outline, and text colors for a root or regular node."""
    if is_root:
        return "url(#root-fill)", "#355ca8", "#ffffff"
    return "#ffffff", "#91acd1", "#213a67"


def _short_label(package: str) -> str:
    """Shorten long Maven coordinates for a compact node label."""
    label = package
    if len(label) > LABEL_LIMIT and label.count(":") == COORDINATE_PARTS_COUNT:
        label = label.split(":", 1)[1]
    if len(label) > LABEL_LIMIT:
        return f"{label[:15]}…{label[-5:]}"
    return label


def _svg_node(package: str, position: tuple[int, int], root: str) -> str:
    """Render one package as a rounded rectangle with an accessible title."""
    x, y = position
    fill, border, ink = _node_style(package == root)
    label = escape(_short_label(package))
    full_name = escape(package)
    node_x = x
    node_y = y - NODE_HEIGHT // 2
    return (
        f"<g><title>{full_name}</title>"
        f'<rect x="{node_x}" y="{node_y}" width="{NODE_WIDTH}" '
        f'height="{NODE_HEIGHT}" rx="11" fill="{fill}" '
        f'stroke="{border}" stroke-width="1.5"/>'
        f'<text x="{x + NODE_WIDTH // 2}" y="{y + 5}" '
        'text-anchor="middle" font-family="Helvetica Neue, Arial, sans-serif" '
        f'font-size="13" font-weight="700" fill="{ink}">{label}</text></g>'
    )


def _svg_nodes(
    graph: DependencyGraph,
    positions: dict[str, tuple[int, int]],
) -> list[str]:
    """Render all package nodes with the root visually emphasized."""
    return [
        _svg_node(package, position, graph.root)
        for package, position in positions.items()
    ]


def svg(graph: DependencyGraph) -> str:
    """Render a dependency graph as a browser-ready SVG image."""
    if not graph.nodes:
        return _empty_svg()
    positions, width, height = _graph_positions(graph)
    elements = _svg_header(graph, width, height)
    elements.extend(_svg_edges(graph, positions))
    elements.extend(_svg_nodes(graph, positions))
    elements.append("</svg>")
    return "\n".join(elements)


def save_diagrams(
    graph: DependencyGraph,
    directory: Path,
) -> tuple[Path, Path]:
    """Save the PlantUML source and SVG image under safe package filenames."""
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in graph.root
    )
    puml_path = directory / f"{safe_name}.puml"
    svg_path = directory / f"{safe_name}.svg"
    puml_path.write_text(plantuml(graph), encoding="utf-8")
    svg_path.write_text(svg(graph), encoding="utf-8")
    return puml_path, svg_path
