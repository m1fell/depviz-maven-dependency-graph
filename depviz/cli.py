"""Этап 3: построение полного графа зависимостей обходом в ширину."""

import argparse
from .config import ConfigError, load_config
from .graph import build_graph
from .repository import JsonTestRepository, MavenRepository, RepositoryError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Визуализатор зависимостей Maven")
    parser.add_argument("--config", default="config.ini", help="путь к INI-конфигурации")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        repository = JsonTestRepository(config.test_repository) if config.test_mode else MavenRepository(config.repository_url)
        dependencies = repository.dependencies(config.package)
        graph = build_graph(repository, config.package, config.filter_substring)
    except (ConfigError, RepositoryError) as exc:
        parser.error(str(exc))
    print(f"Прямые зависимости {config.package}:")
    for dependency in dependencies:
        print(f"- {dependency}")
    print(f"\nПакетов в графе: {len(graph.nodes)}")
    print("Рёбра:")
    for parent, children in graph.edges.items():
        for child in children:
            print(f"{parent} -> {child}")
    return 0
