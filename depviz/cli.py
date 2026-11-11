"""Этап 1: загрузка настроек из INI и вывод их значений."""

import argparse
from .config import ConfigError, load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description="Визуализатор зависимостей Maven")
    parser.add_argument("--config", default="config.ini", help="путь к INI-конфигурации")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        parser.error(str(exc))
    print("Параметры конфигурации:")
    print(f"package = {config.package}")
    print(f"repository_url = {config.repository_url}")
    print(f"test_mode = {str(config.test_mode).lower()}")
    print(f"test_repository = {config.test_repository}")
    print(f"filter_substring = {config.filter_substring}")
    print(f"output_dir = {config.output_dir}")
    return 0
