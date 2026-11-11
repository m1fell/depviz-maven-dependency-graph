"""Загрузка метаданных Maven напрямую из POM или из учебного JSON-графа."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import re
import xml.etree.ElementTree as ET


class RepositoryError(RuntimeError):
    """Ошибка чтения репозитория или POM-файла."""


class DependencyRepository:
    def dependencies(self, package: str) -> list[str]:
        raise NotImplementedError


@dataclass
class _EffectivePom:
    group_id: str
    artifact_id: str
    version: str
    properties: dict[str, str]
    dependencies: dict[tuple[str, str, str, str], dict[str, str]]
    dependency_management: dict[tuple[str, str, str, str], dict[str, str]]


class JsonTestRepository(DependencyRepository):
    def __init__(self, path: Path):
        try:
            self.graph: Mapping[str, list[str]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RepositoryError(f"Не удалось открыть тестовый репозиторий: {exc}") from exc

    def dependencies(self, package: str) -> list[str]:
        if package not in self.graph:
            raise RepositoryError(f"Пакет '{package}' отсутствует в тестовом репозитории.")
        return list(self.graph[package])


class MavenRepository(DependencyRepository):
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cache: dict[str, list[str]] = {}
        self._pom_cache: dict[str, ET.Element] = {}
        self._effective_cache: dict[str, _EffectivePom] = {}

    def _pom_url(self, coordinate: str) -> str:
        parts = coordinate.split(":")
        if len(parts) != 3 or any(not part for part in parts):
            raise RepositoryError(f"Неверные Maven-координаты '{coordinate}'. Нужен формат groupId:artifactId:version.")
        group, artifact, version = parts
        if any(".." in part or "/" in part or "\\" in part for part in parts):
            raise RepositoryError(f"Недопустимые символы в Maven-координатах '{coordinate}'.")
        group_path = group.replace(".", "/")
        return f"{self.base_url}/{group_path}/{artifact}/{version}/{artifact}-{version}.pom"

    @staticmethod
    def _text(element: ET.Element | None) -> str:
        return (element.text or "").strip() if element is not None else ""

    @staticmethod
    def _namespace(root: ET.Element) -> tuple[str, dict[str, str]]:
        uri = root.tag.split("}", 1)[0].lstrip("{") if root.tag.startswith("{") else ""
        return (f"{{{uri}}}" if uri else "", {"m": uri} if uri else {})

    @classmethod
    def _element_text(cls, parent: ET.Element | None, name: str, prefix: str, ns: dict[str, str]) -> str:
        if parent is None:
            return ""
        return cls._text(parent.find(f"{prefix}{name}", ns))

    @staticmethod
    def _interpolate(value: str, properties: Mapping[str, str]) -> str:
        pattern = re.compile(r"\$\{([^}]+)\}")
        for _ in range(30):
            resolved = pattern.sub(lambda match: properties.get(match.group(1), match.group(0)), value)
            if resolved == value:
                break
            value = resolved
        return value

    @staticmethod
    def _project_properties(
        group_id: str,
        artifact_id: str,
        version: str,
        parent: tuple[str, str, str] | None = None,
    ) -> dict[str, str]:
        values = {
            "project.groupId": group_id,
            "pom.groupId": group_id,
            "project.artifactId": artifact_id,
            "pom.artifactId": artifact_id,
            "project.version": version,
            "pom.version": version,
        }
        if parent:
            parent_group, parent_artifact, parent_version = parent
            values.update(
                {
                    "project.parent.groupId": parent_group,
                    "pom.parent.groupId": parent_group,
                    "project.parent.artifactId": parent_artifact,
                    "pom.parent.artifactId": parent_artifact,
                    "project.parent.version": parent_version,
                    "pom.parent.version": parent_version,
                }
            )
        return values

    def _load_pom(self, coordinate: str) -> ET.Element:
        if coordinate in self._pom_cache:
            return self._pom_cache[coordinate]
        url = self._pom_url(coordinate)
        request = Request(url, headers={"User-Agent": "depviz-student-project/1.0"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                root = ET.fromstring(response.read())
        except HTTPError as exc:
            if exc.code == 404:
                raise RepositoryError(f"POM для '{coordinate}' не найден в репозитории: {url}") from exc
            raise RepositoryError(f"Репозиторий вернул HTTP {exc.code} для {url}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RepositoryError(f"Не удалось подключиться к репозиторию {url}: {exc}") from exc
        except ET.ParseError as exc:
            raise RepositoryError(f"POM для '{coordinate}' содержит некорректный XML: {exc}") from exc
        self._pom_cache[coordinate] = root
        return root

    @staticmethod
    def _dependency_values(
        element: ET.Element,
        prefix: str,
        ns: dict[str, str],
        properties: Mapping[str, str],
    ) -> dict[str, str]:
        values: dict[str, str] = {}
        for name in ("groupId", "artifactId", "version", "scope", "optional", "type", "classifier"):
            value = MavenRepository._element_text(element, name, prefix, ns)
            if value:
                values[name] = MavenRepository._interpolate(value, properties)
        values.setdefault("type", "jar")
        values.setdefault("classifier", "")
        return values

    @staticmethod
    def _dependency_key(values: Mapping[str, str]) -> tuple[str, str, str, str]:
        return (values.get("groupId", ""), values.get("artifactId", ""), values.get("type", "jar"), values.get("classifier", ""))

    def _effective_pom(self, coordinate: str, resolving: frozenset[str] = frozenset()) -> _EffectivePom:
        if coordinate in self._effective_cache:
            return self._effective_cache[coordinate]
        if coordinate in resolving:
            chain = " -> ".join((*resolving, coordinate))
            raise RepositoryError(f"Обнаружен цикл parent/BOM в Maven POM: {chain}")
        parts = coordinate.split(":")
        if len(parts) != 3:
            raise RepositoryError(f"Неверные Maven-координаты '{coordinate}'. Нужен формат groupId:artifactId:version.")
        requested_group, requested_artifact, requested_version = parts
        root = self._load_pom(coordinate)
        prefix, ns = self._namespace(root)
        next_resolving = resolving | {coordinate}

        raw_properties: dict[str, str] = {}
        properties_element = root.find(f"{prefix}properties", ns)
        if properties_element is not None:
            for child in properties_element:
                name = child.tag.split("}", 1)[-1]
                raw_properties[name] = self._text(child)

        parent_element = root.find(f"{prefix}parent", ns)
        parent_model: _EffectivePom | None = None
        parent_coordinate: tuple[str, str, str] | None = None
        if parent_element is not None:
            initial_context = self._project_properties(requested_group, requested_artifact, requested_version)
            initial_context.update(raw_properties)
            parent_group = self._interpolate(self._element_text(parent_element, "groupId", prefix, ns), initial_context)
            parent_artifact = self._interpolate(self._element_text(parent_element, "artifactId", prefix, ns), initial_context)
            parent_version = self._interpolate(self._element_text(parent_element, "version", prefix, ns), initial_context)
            if not parent_group or not parent_artifact or not parent_version or "${" in parent_group + parent_artifact + parent_version:
                raise RepositoryError(f"Не удалось определить Maven-координаты parent для '{coordinate}'.")
            parent_coordinate = (parent_group, parent_artifact, parent_version)
            parent_model = self._effective_pom(":".join(parent_coordinate), next_resolving)

        inherited_properties = dict(parent_model.properties) if parent_model else {}
        inherited_properties.update(raw_properties)
        effective_group = self._element_text(root, "groupId", prefix, ns) or (parent_model.group_id if parent_model else requested_group)
        effective_artifact = self._element_text(root, "artifactId", prefix, ns) or requested_artifact
        effective_version = self._element_text(root, "version", prefix, ns) or (parent_model.version if parent_model else requested_version)
        builtins = self._project_properties(requested_group, requested_artifact, requested_version, parent_coordinate)
        builtins.update(inherited_properties)
        effective_group = self._interpolate(effective_group, builtins)
        effective_artifact = self._interpolate(effective_artifact, builtins)
        effective_version = self._interpolate(effective_version, builtins)
        builtins.update(self._project_properties(effective_group, effective_artifact, effective_version, parent_coordinate))

        resolved_properties = dict(inherited_properties)
        for _ in range(30):
            context = dict(builtins)
            context.update(resolved_properties)
            updated = {name: self._interpolate(value, context) for name, value in resolved_properties.items()}
            if updated == resolved_properties:
                break
            resolved_properties = updated
        context = dict(builtins)
        context.update(resolved_properties)

        inherited_management = dict(parent_model.dependency_management) if parent_model else {}
        imported_management: dict[tuple[str, str, str, str], dict[str, str]] = {}
        direct_management: dict[tuple[str, str, str, str], dict[str, str]] = {}
        management_element = root.find(f"{prefix}dependencyManagement/{prefix}dependencies", ns)
        if management_element is not None:
            for dependency in management_element.findall(f"{prefix}dependency", ns):
                values = self._dependency_values(dependency, prefix, ns, context)
                if not values.get("groupId") or not values.get("artifactId"):
                    continue
                if values.get("type") == "pom" and values.get("scope") == "import":
                    version = values.get("version", "")
                    bom_parts = (values["groupId"], values["artifactId"], version)
                    if version and "${" not in version:
                        bom_model = self._effective_pom(":".join(bom_parts), next_resolving)
                        for key, managed_values in bom_model.dependency_management.items():
                            imported_management.setdefault(key, dict(managed_values))
                    continue
                if values.get("version") and "${" in values["version"]:
                    continue
                direct_management[self._dependency_key(values)] = values

        inherited_management.update(imported_management)
        inherited_management.update(direct_management)

        dependencies = dict(parent_model.dependencies) if parent_model else {}
        dependency_elements = root.find(f"{prefix}dependencies", ns)
        if dependency_elements is not None:
            for dependency in dependency_elements.findall(f"{prefix}dependency", ns):
                values = self._dependency_values(dependency, prefix, ns, context)
                if not values.get("groupId") or not values.get("artifactId"):
                    continue
                managed = inherited_management.get(self._dependency_key(values), {})
                for field in ("version", "scope", "optional"):
                    if not values.get(field) and managed.get(field):
                        values[field] = managed[field]
                dependencies[self._dependency_key(values)] = values

        model = _EffectivePom(
            effective_group,
            effective_artifact,
            effective_version,
            resolved_properties,
            dependencies,
            inherited_management,
        )
        self._effective_cache[coordinate] = model
        return model

    def dependencies(self, package: str) -> list[str]:
        if package in self._cache:
            return self._cache[package]
        model = self._effective_pom(package)
        deps: list[str] = []
        for values in model.dependencies.values():
            scope = values.get("scope", "compile") or "compile"
            optional = values.get("optional", "false").lower() == "true"
            group = values.get("groupId", "")
            artifact = values.get("artifactId", "")
            version = values.get("version", "")
            if scope not in {"compile", "runtime"} or optional or not (group and artifact and version):
                continue
            if "${" in group + artifact + version:
                continue
            coordinate = f"{group}:{artifact}:{version}"
            if coordinate not in deps:
                deps.append(coordinate)
        self._cache[package] = deps
        return deps

