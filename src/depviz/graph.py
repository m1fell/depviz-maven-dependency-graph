"""Build forward and reverse Maven dependency graphs with iterative BFS."""

from collections import deque
from dataclasses import dataclass

from .repository import DependencyRepository

INITIAL_DEPTH = 0
DEPTH_INCREMENT = 1


@dataclass
class DependencyGraph:
    """Store package relationships and each node's distance from the root."""

    root: str
    edges: dict[str, list[str]]
    depths: dict[str, int]

    @property
    def nodes(self) -> set[str]:
        """Return all packages represented as graph nodes."""
        return set(self.edges)

    def reverse_dependencies(self, package: str) -> list[str]:
        """Find all reachable packages that depend on the requested package."""
        reverse_edges = _build_reverse_edges(self.edges)
        found: list[str] = []
        visited = {package}
        queue = deque([package])

        while queue:
            current = queue.popleft()
            parents = reverse_edges.get(current, [])
            for parent in parents:
                if parent in visited:
                    continue
                visited.add(parent)
                found.append(parent)
                queue.append(parent)
        return found


def _build_reverse_edges(edges: dict[str, list[str]]) -> dict[str, list[str]]:
    """Create an adjacency list whose arrows point from dependency to user."""
    reverse_edges = {node: [] for node in edges}
    for package, dependencies in edges.items():
        for dependency in dependencies:
            reverse_edges.setdefault(dependency, []).append(package)
    return reverse_edges


def _is_filtered(package: str, substring: str) -> bool:
    """Tell whether a package name contains the configured exclusion text."""
    return bool(substring and substring in package)


def build_graph(
    repository: DependencyRepository,
    root: str,
    filter_substring: str = "",
) -> DependencyGraph:
    """Traverse dependencies by BFS, filtering names and stopping cycles."""
    if _is_filtered(root, filter_substring):
        return DependencyGraph(root, {}, {})

    edges: dict[str, list[str]] = {}
    depths = {root: INITIAL_DEPTH}
    queue = deque([root])
    while queue:
        package = queue.popleft()
        dependencies = repository.dependencies(package)
        kept = [
            dependency
            for dependency in dependencies
            if not _is_filtered(dependency, filter_substring)
        ]
        edges[package] = kept
        _add_new_dependencies(package, kept, depths, queue)
    return DependencyGraph(root, edges, depths)


def _add_new_dependencies(
    package: str,
    dependencies: list[str],
    depths: dict[str, int],
    queue: deque[str],
) -> None:
    """Record and queue dependencies that have not been visited before."""
    for dependency in dependencies:
        if dependency in depths:
            continue
        depths[dependency] = depths[package] + DEPTH_INCREMENT
        queue.append(dependency)
