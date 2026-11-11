"""Чтение INI-файла и понятная проверка пользовательских параметров."""

from configparser import ConfigParser, Error as IniError
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Ошибка конфигурации с сообщением, пригодным для пользователя."""


@dataclass(frozen=True)
class AppConfig:
    package: str
    repository_url: str
    test_mode: bool
    test_repository: Path
    filter_substring: str
    output_dir: Path


def _required(section: ConfigParser, key: str) -> str:
    value = section.get(key, fallback="").strip()
    if not value:
        raise ConfigError(f"Параметр '{key}' в секции [app] не задан или пуст.")
    return value


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise ConfigError(f"Файл конфигурации не найден: {config_path}")

    parser = ConfigParser()
    try:
        with config_path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, IniError) as exc:
        raise ConfigError(f"Не удалось прочитать INI-файл: {exc}") from exc
    if not parser.has_section("app"):
        raise ConfigError("В INI-файле отсутствует секция [app].")

    section = parser["app"]
    package = _required(section, "package")
    # В тестовом режиме разрешены простые имена A, B, ...; в обычном — Maven-координаты.
    test_mode_text = _required(section, "test_mode").lower()
    if test_mode_text not in {"true", "false"}:
        raise ConfigError("Параметр 'test_mode' должен быть true или false.")
    test_mode = test_mode_text == "true"
    if test_mode:
        if not package.isalpha() or not package.isascii() or not package.isupper():
            raise ConfigError("В тестовом режиме имя пакета должно состоять из больших латинских букв, например A.")
    else:
        parts = package.split(":")
        if len(parts) != 3 or any(not part.strip() for part in parts):
            raise ConfigError("Параметр 'package' должен иметь формат groupId:artifactId:version.")

    repository_url = _required(section, "repository_url")
    parsed_url = urlparse(repository_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ConfigError("Параметр 'repository_url' должен быть корректным URL с http:// или https://.")

    test_repository_raw = _required(section, "test_repository")
    test_repository = (config_path.parent / test_repository_raw).resolve()
    if test_mode and not test_repository.is_file():
        raise ConfigError(f"Файл тестового репозитория не найден: {test_repository}")
    if test_mode:
        try:
            import json

            with test_repository.open(encoding="utf-8") as stream:
                graph = json.load(stream)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ConfigError(f"Не удалось прочитать JSON тестового репозитория: {exc}") from exc
        if not isinstance(graph, dict) or any(
            not isinstance(node, str)
            or not node.isascii()
            or not node.isupper()
            or not node.isalpha()
            or not isinstance(children, list)
            or any(not isinstance(child, str) or not child.isascii() or not child.isalpha() or not child.isupper() for child in children)
            for node, children in graph.items()
        ):
            raise ConfigError("Тестовый репозиторий должен быть JSON-объектом вида {\"A\": [\"B\"]}; имена — большие латинские буквы.")

    if "filter_substring" not in section:
        raise ConfigError("Параметр 'filter_substring' отсутствует в секции [app] (можно указать пустое значение).")
    filter_substring = section.get("filter_substring", raw=True).strip()
    output_dir = Path(section.get("output_dir", "output").strip() or "output")
    if not output_dir.is_absolute():
        output_dir = config_path.parent / output_dir
    return AppConfig(package, repository_url.rstrip("/"), test_mode, test_repository, filter_substring, output_dir)

