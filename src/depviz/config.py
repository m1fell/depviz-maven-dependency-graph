"""Read and validate settings from an INI configuration file."""

from configparser import ConfigParser, Error
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlparse

MAVEN_COORDINATE_PARTS = 3
VALID_URL_SCHEMES = {"http", "https"}


class ConfigError(ValueError):
    """Represent a configuration problem with a user-friendly message."""


@dataclass(frozen=True)
class AppConfig:
    """Store the validated settings used during one application run."""

    package: str
    repository_url: str
    test_mode: bool
    test_repository: Path
    filter_substring: str
    output_dir: Path


def _required(section: ConfigParser, key: str) -> str:
    """Return a required setting or explain which setting is missing."""
    value = section.get(key, fallback="").strip()
    if not value:
        raise ConfigError(
            f"Параметр '{key}' в [app] пуст."
        )
    return value


def _read_ini(config_path: Path) -> ConfigParser:
    """Read the INI file and make file and syntax errors easy to understand."""
    parser = ConfigParser()
    try:
        with config_path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, Error) as exc:
        message = f"Не удалось прочитать INI-файл: {exc}"
        raise ConfigError(message) from exc
    if not parser.has_section("app"):
        raise ConfigError("В INI нет секции [app].")
    return parser


def _parse_test_mode(section: ConfigParser) -> bool:
    """Convert the test mode setting to a boolean and reject other values."""
    value = _required(section, "test_mode").lower()
    if value not in {"true", "false"}:
        raise ConfigError("test_mode должен быть true или false.")
    return value == "true"


def _validate_package(package: str, test_mode: bool) -> None:
    """Check a test package name or a Maven coordinate string."""
    if test_mode:
        valid_name = (
            package.isascii() and package.isalpha() and package.isupper()
        )
        if not valid_name:
            raise ConfigError(
                "Имя пакета должно содержать "
                "больших латинских букв, "
                "например A."
            )
        return

    parts = package.split(":")
    valid_coordinates = len(parts) == MAVEN_COORDINATE_PARTS
    if not valid_coordinates or any(not part.strip() for part in parts):
        raise ConfigError(
            "Параметр 'package' должен иметь формат "
            "groupId:artifactId:version."
        )


def _validate_repository_url(value: str) -> str:
    """Validate the repository URL and remove a trailing slash."""
    parsed_url = urlparse(value)
    valid_scheme = parsed_url.scheme in VALID_URL_SCHEMES
    if not valid_scheme or not parsed_url.netloc:
        raise ConfigError(
            "repository_url должен быть корректным URL "
            "с http:// или https://."
        )
    return value.rstrip("/")


def _valid_test_package(value: object) -> bool:
    """Tell whether a JSON graph node uses allowed uppercase Latin letters."""
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isalpha()
        and value.isupper()
    )


def _valid_test_graph(graph: object) -> bool:
    """Check the JSON graph shape and validate every node and edge."""
    if not isinstance(graph, dict):
        return False
    for node, children in graph.items():
        valid_node = _valid_test_package(node)
        valid_children = isinstance(children, list)
        if not valid_node or not valid_children:
            return False
        if any(not _valid_test_package(child) for child in children):
            return False
    return True


def _read_test_repository(path: Path, test_mode: bool) -> None:
    """Check that the configured test graph exists and contains valid JSON."""
    if not test_mode:
        return
    if not path.is_file():
        message = f"Не найден тестовый файл: {path}"
        raise ConfigError(message)
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigError(
            f"Ошибка чтения JSON репозитория: {exc}"
        ) from exc
    if not _valid_test_graph(graph):
        raise ConfigError(
            "Тестовый граф должен быть "
            "JSON-объектом вида "
            '{"A": ["B"]}; узлы — большие буквы.'
        )


def _resolve_output_dir(section: ConfigParser, config_path: Path) -> Path:
    """Resolve the output folder relative to the INI file when needed."""
    raw_path = section.get("output_dir", "output").strip() or "output"
    output_dir = Path(raw_path)
    if output_dir.is_absolute():
        return output_dir
    return config_path.parent / output_dir


def load_config(path: str | Path) -> AppConfig:
    """Load INI settings, validate them, and return app configuration."""
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        message = f"Не найден config: {config_path}"
        raise ConfigError(message)

    section = _read_ini(config_path)["app"]
    package = _required(section, "package")
    test_mode = _parse_test_mode(section)
    _validate_package(package, test_mode)

    repository_url = _validate_repository_url(
        _required(section, "repository_url")
    )
    test_path = _required(section, "test_repository")
    test_repository = (config_path.parent / test_path).resolve()
    _read_test_repository(test_repository, test_mode)

    if "filter_substring" not in section:
        raise ConfigError(
            "Нет filter_substring в [app] "
            "(можно указать пустое значение)."
        )
    filter_substring = section.get("filter_substring", raw=True).strip()
    output_dir = _resolve_output_dir(section, config_path)
    return AppConfig(
        package=package,
        repository_url=repository_url,
        test_mode=test_mode,
        test_repository=test_repository,
        filter_substring=filter_substring,
        output_dir=output_dir,
    )
