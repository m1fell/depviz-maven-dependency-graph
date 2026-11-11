"""Этап 2: получение прямых зависимостей из POM Maven."""

import argparse
from .config import ConfigError, load_config
from .repository import JsonTestRepository, MavenRepository, RepositoryError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Визуализатор зависимостей Maven")
    parser.add_argument("--config", default="config.ini", help="путь к INI-конфигурации")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        repository = JsonTestRepository(config.test_repository) if config.test_mode else MavenRepository(config.repository_url)
        dependencies = repository.dependencies(config.package)
    except (ConfigError, RepositoryError) as exc:
        parser.error(str(exc))
    print(f"Прямые зависимости {config.package}:")
    for dependency in dependencies:
        print(f"- {dependency}")
    if not dependencies:
        print("(нет прямых зависимостей)")
    return 0
