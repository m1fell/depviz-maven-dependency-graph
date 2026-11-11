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
