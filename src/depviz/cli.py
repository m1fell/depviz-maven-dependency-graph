"""Parse command-line options and coordinate one application run."""

import argparse
from pathlib import Path
import sys

from .api import serve_api
from .config import ConfigError, load_config
from .graph import DependencyGraph, build_graph
from .render import save_diagrams
from .repository import (
    DependencyRepository,
    JsonTestRepository,
    MavenRepository,
    RepositoryError,
)

MIN_PORT = 1
MAX_PORT = 65535
MIN_MENU_PACKAGES = 2
FIRST_PACKAGE_INDEX = 1
DEFAULT_PORT = 8000


def _select_package(
    repository: DependencyRepository,
    configured_package: str,
    requested_package: str | None,
) -> str:
    """Choose a root package from CLI input, test menu, or configuration."""
    if requested_package:
        return requested_package
    if not isinstance(repository, JsonTestRepository):
        return configured_package

    packages = sorted(repository.graph)
    if len(packages) < MIN_MENU_PACKAGES:
        return packages[0] if packages else configured_package
    return _read_package_menu(packages, configured_package)


def _read_package_menu(packages: list[str], configured_package: str) -> str:
    """Display test packages and repeat until a valid choice arrives."""
    print("Выберите пакет для графа:")
    for index, package in enumerate(packages, start=FIRST_PACKAGE_INDEX):
        print(f"{index}. {package}")

    default_package = _default_package(packages, configured_package)
    while True:
        choice = _read_choice(default_package)
        selected = _resolve_choice(choice, packages, default_package)
        if selected:
            return selected
        print("Выбор неверен.")


def _default_package(packages: list[str], configured_package: str) -> str:
    """Return the configured test package or the first available package."""
    if configured_package in packages:
        return configured_package
    return packages[0]


def _read_choice(default_package: str) -> str:
    """Read a package selection or explain how to handle closed input."""
    prompt = f"Пакет [по умолчанию {default_package}]: "
    try:
        return input(prompt).strip()
    except EOFError as exc:
        raise ConfigError(
            "Не удалось прочитать выбор пакета. "
            "Укажите его параметром --package."
        ) from exc


def _resolve_choice(
    choice: str,
    packages: list[str],
    default_package: str,
) -> str | None:
    """Match an empty, numbered, or named menu selection to a package."""
    if not choice:
        return default_package
    if choice in packages:
        return choice
    return _package_by_index(choice, packages)


def _package_by_index(choice: str, packages: list[str]) -> str | None:
    """Return a package for a valid one-based index, otherwise no match."""
    if not choice.isdigit():
        return None
    index = int(choice)
    if FIRST_PACKAGE_INDEX <= index <= len(packages):
        return packages[index - FIRST_PACKAGE_INDEX]
    return None


def _make_parser(default_config: Path) -> argparse.ArgumentParser:
    """Build the CLI parser and describe all supported runtime options."""
    parser = argparse.ArgumentParser(
        description="API графа Maven-зависимостей",
    )
    parser.add_argument(
        "--config",
        default=str(default_config),
        help="путь к INI-конфигурации",
    )
    parser.add_argument(
        "--reverse",
        metavar="PACKAGE",
        help="вывести обратные зависимости узла",
    )
    parser.add_argument(
        "--package",
        help="выбрать пакет без вопроса",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="адрес локального HTTP API",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="порт HTTP API"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="не открывать SVG автоматически",
    )
    return parser


def _create_repository(config) -> DependencyRepository:
    """Choose a JSON test graph or a remote Maven repository from settings."""
    if config.test_mode:
        return JsonTestRepository(config.test_repository)
    return MavenRepository(config.repository_url)


def _print_run_summary(config, args, package, direct, graph) -> None:
    """Print loaded settings, direct dependencies, and all graph edges."""
    print("Параметры конфигурации:")
    settings = {
        "package": package,
        "repository_url": config.repository_url,
        "test_mode": str(config.test_mode).lower(),
        "test_repository": config.test_repository,
        "filter_substring": config.filter_substring,
        "output_dir": config.output_dir,
        "api_host": args.host,
        "api_port": args.port,
    }
    for key, value in settings.items():
        print(f"{key} = {value}")

    print(f"\nПрямые зависимости {package}:")
    direct_text = "\n".join(f"- {item}" for item in direct)
    empty_message = "(нет прямых зависимостей)"
    print(direct_text if direct_text else empty_message)
    _print_graph_edges(graph)


def _print_graph_edges(graph: DependencyGraph) -> None:
    """Print the number of graph nodes and each directed relationship."""
    print(f"\nПакетов в графе: {len(graph.nodes)}")
    for package, dependencies in graph.edges.items():
        for dependency in dependencies:
            print(f"{package} -> {dependency}")


def _print_reverse(graph: DependencyGraph, package: str) -> None:
    """Print all packages in the graph that depend on the selected package."""
    if package not in graph.nodes:
        message = f"\nПакет '{package}' вне графа."
        print(message)
        return
    reverse = graph.reverse_dependencies(package)
    print(f"\nПакеты, зависящие от {package}:")
    result = "\n".join(f"- {node}" for node in reverse)
    print(result if result else "(не найдены)")


def _export_graph(graph: DependencyGraph, output_dir: Path) -> None:
    """Save PlantUML and SVG files and report their locations."""
    puml_path, svg_path = save_diagrams(graph, output_dir)
    print(f"\nPlantUML сохранён: {puml_path}")
    print(f"SVG-граф сохранён: {svg_path}")


def _run(config, args) -> None:
    """Load dependencies, export the selected graph, and start its HTTP API."""
    repository = _create_repository(config)
    package = _select_package(repository, config.package, args.package)
    direct = repository.dependencies(package)
    graph = build_graph(repository, package, config.filter_substring)
    _print_run_summary(config, args, package, direct, graph)
    if args.reverse:
        _print_reverse(graph, args.reverse)
    _export_graph(graph, config.output_dir)
    serve_api(
        repository,
        package,
        config.filter_substring,
        config.test_mode,
        args.host,
        args.port,
        open_browser=not args.no_browser,
    )


def main(argv: list[str] | None = None) -> int:
    """Validate options and run the app with clear error messages."""
    default_config = Path(__file__).resolve().parents[2] / "config.ini"
    parser = _make_parser(default_config)
    args = parser.parse_args(argv)
    if not MIN_PORT <= args.port <= MAX_PORT:
        parser.error(
            f"Порт допустим от {MIN_PORT} до {MAX_PORT}."
        )

    try:
        config = load_config(args.config)
        _run(config, args)
        return 0
    except (ConfigError, RepositoryError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nAPI остановлен пользователем.")
        return 130
