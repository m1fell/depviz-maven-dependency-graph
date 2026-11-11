"""Этап 3: итеративное построение графа зависимостей обходом BFS."""

from collections import deque
from dataclasses import dataclass
from .repository import DependencyRepository


@dataclass
class DependencyGraph:
    root: str
    edges: dict[str, list[str]]
    depths: dict[str, int]

    @property
    def nodes(self):
        return set(self.edges)

    def reverse_dependencies(self, package: str) -> list[str]:
        """Найти BFS-обходом узлы, зависящие от указанного пакета."""
        reverse: dict[str, list[str]] = {node: [] for node in self.edges}
        for parent, children in self.edges.items():
            for child in children:
                reverse.setdefault(child, []).append(parent)
        found: list[str] = []
        visited = {package}
        queue = deque([package])
        while queue:
            current = queue.popleft()
            for parent in reverse.get(current, []):
                if parent not in visited:
                    visited.add(parent)
                    found.append(parent)
                    queue.append(parent)
        return found


def build_graph(repository: DependencyRepository, root: str, filter_substring: str = "") -> DependencyGraph:
    if filter_substring and filter_substring in root:
        return DependencyGraph(root, {}, {})
    edges: dict[str, list[str]] = {}
    depths = {root: 0}
    queue = deque([root])
    while queue:
        package = queue.popleft()
        children = [name for name in repository.dependencies(package) if not filter_substring or filter_substring not in name]
        edges[package] = children
        for child in children:
            if child not in depths:
                depths[child] = depths[package] + 1
                queue.append(child)
    return DependencyGraph(root, edges, depths)
