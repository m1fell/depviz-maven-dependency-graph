"""Read Maven POM metadata or a local test graph without a package manager."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import re
import xml.etree.ElementTree as element_tree

COORDINATE_PARTS = 3
PROPERTY_PASSES = 30
DEFAULT_TIMEOUT = 15.0
DEFAULT_SCOPE = "compile"
HTTP_NOT_FOUND = 404
INCLUDED_SCOPES = {"compile", "runtime"}
DEPENDENCY_FIELDS = (
    "groupId",
    "artifactId",
    "version",
    "scope",
    "optional",
    "type",
    "classifier",
)


class RepositoryError(RuntimeError):
    """Describe an error while loading a repository or parsing a POM file."""


class DependencyRepository:
    """Define the shared interface implemented by dependency data sources."""

    def dependencies(self, package: str) -> list[str]:
        """Return the direct dependencies of one package."""
        raise NotImplementedError


@dataclass
class EffectivePom:
    """Hold Maven metadata after resolving inheritance and managed versions."""

    group_id: str
    artifact_id: str
    version: str
    properties: dict[str, str]
    dependencies: dict[tuple[str, str, str, str], dict[str, str]]
    dependency_management: dict[tuple[str, str, str, str], dict[str, str]]


@dataclass
class PomDocument:
    """Keep the parsed XML root and namespace details together."""

    root: element_tree.Element
    prefix: str
    namespaces: dict[str, str]


class JsonTestRepository(DependencyRepository):
    """Read package relationships from the simple JSON test graph."""

    def __init__(self, path: Path):
        """Load the test graph from disk or report a readable file error."""
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            message = f"Ошибка JSON-графа: {exc}"
            raise RepositoryError(message) from exc
        self.graph: Mapping[str, list[str]] = graph

    def dependencies(self, package: str) -> list[str]:
        """Return a test package's direct edges or report a missing node."""
        if package not in self.graph:
            message = f"Пакет {package} отсутствует."
            raise RepositoryError(message)
        return list(self.graph[package])


class MavenRepository(DependencyRepository):
    """Fetch Maven POM files and extract their compile/runtime dependencies."""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT):
        """Set a Maven repository URL and initialize metadata caches."""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cache: dict[str, list[str]] = {}
        self._pom_cache: dict[str, element_tree.Element] = {}
        self._effective_cache: dict[str, EffectivePom] = {}

    def _split_coordinates(self, coordinate: str) -> tuple[str, str, str]:
        """Validate Maven coordinates and return their three components."""
        parts = coordinate.split(":")
        if len(parts) != COORDINATE_PARTS or any(not part for part in parts):
            raise RepositoryError(
                f"Неверные Maven-координаты '{coordinate}'. "
                "Нужен формат "
                "groupId:artifactId:version."
            )
        if any(".." in part or "/" in part or "\\" in part for part in parts):
            message = (
                f"Неверные координаты: {coordinate}"
            )
            raise RepositoryError(message)
        return parts[0], parts[1], parts[2]

    def _pom_url(self, coordinate: str) -> str:
        """Convert Maven coordinates to the corresponding POM file URL."""
        group, artifact, version = self._split_coordinates(coordinate)
        group_path = group.replace(".", "/")
        filename = f"{artifact}-{version}.pom"
        return f"{self.base_url}/{group_path}/{artifact}/{version}/{filename}"

    @staticmethod
    def _text(element: element_tree.Element | None) -> str:
        """Return trimmed XML text or an empty string for a missing element."""
        return (element.text or "").strip() if element is not None else ""

    @staticmethod
    def _namespace(
        root: element_tree.Element,
    ) -> tuple[str, dict[str, str]]:
        """Build XML namespace values for Maven POM queries."""
        if not root.tag.startswith("{"):
            return "", {}
        uri = root.tag.split("}", 1)[0].lstrip("{")
        return f"{{{uri}}}", {"m": uri}

    @classmethod
    def _element_text(
        cls,
        parent: element_tree.Element | None,
        name: str,
        prefix: str,
        namespaces: dict[str, str],
    ) -> str:
        """Find a named XML child and return its trimmed text value."""
        if parent is None:
            return ""
        return cls._text(parent.find(f"{prefix}{name}", namespaces))

    @staticmethod
    def _interpolate(value: str, properties: Mapping[str, str]) -> str:
        """Resolve Maven property references such as ${project.version}."""
        pattern = re.compile(r"\$\{([^}]+)\}")
        for _ in range(PROPERTY_PASSES):
            resolved = pattern.sub(
                lambda match: properties.get(match.group(1), match.group(0)),
                value,
            )
            if resolved == value:
                return value
            value = resolved
        return value

    @staticmethod
    def _project_properties(
        group_id: str,
        artifact_id: str,
        version: str,
        parent: tuple[str, str, str] | None = None,
    ) -> dict[str, str]:
        """Create built-in Maven properties for a project and its parent."""
        values = {
            "project.groupId": group_id,
            "pom.groupId": group_id,
            "project.artifactId": artifact_id,
            "pom.artifactId": artifact_id,
            "project.version": version,
            "pom.version": version,
        }
        if parent is None:
            return values
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

    def _load_pom(self, coordinate: str) -> element_tree.Element:
        """Download, parse, and cache one XML POM document."""
        if coordinate in self._pom_cache:
            return self._pom_cache[coordinate]
        url = self._pom_url(coordinate)
        request = Request(
            url, headers={"User-Agent": "depviz-student-project/1.0"}
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                root = element_tree.fromstring(response.read())
        except HTTPError as exc:
            self._raise_http_error(exc, coordinate, url)
        except (URLError, TimeoutError, OSError) as exc:
            message = f"Нет соединения с {url}: {exc}"
            raise RepositoryError(message) from exc
        except element_tree.ParseError as exc:
            message = (
                f"POM '{coordinate}': некорректный XML: {exc}"
            )
            raise RepositoryError(message) from exc
        self._pom_cache[coordinate] = root
        return root

    @staticmethod
    def _raise_http_error(
        error: HTTPError,
        coordinate: str,
        url: str,
    ) -> None:
        """Translate HTTP failures into readable error messages."""
        if error.code == HTTP_NOT_FOUND:
            message = f"POM '{coordinate}' не найден: {url}"
        else:
            message = f"HTTP {error.code} от репозитория: {url}"
        raise RepositoryError(message) from error

    @staticmethod
    def _dependency_values(
        element: element_tree.Element,
        prefix: str,
        namespaces: dict[str, str],
        properties: Mapping[str, str],
    ) -> dict[str, str]:
        """Read dependency fields and resolve Maven properties."""
        values = {}
        for name in DEPENDENCY_FIELDS:
            value = MavenRepository._element_text(
                element, name, prefix, namespaces
            )
            if value:
                values[name] = MavenRepository._interpolate(value, properties)
        values.setdefault("type", "jar")
        values.setdefault("classifier", "")
        return values

    @staticmethod
    def _dependency_key(
        values: Mapping[str, str],
    ) -> tuple[str, str, str, str]:
        """Create a key for matching managed dependency entries."""
        return (
            values.get("groupId", ""),
            values.get("artifactId", ""),
            values.get("type", "jar"),
            values.get("classifier", ""),
        )

    def _parent_model(
        self,
        root: element_tree.Element,
        coordinate: str,
        prefix: str,
        namespaces: dict[str, str],
        raw_properties: dict[str, str],
        resolving: tuple[str, ...],
    ) -> tuple[EffectivePom | None, tuple[str, str, str] | None]:
        """Resolve and load the parent POM referenced by the current POM."""
        parent = root.find(f"{prefix}parent", namespaces)
        if parent is None:
            return None, None
        current = self._split_coordinates(coordinate)
        context = self._project_properties(*current)
        context.update(raw_properties)
        parent_coordinates = self._read_parent_coordinates(
            parent, prefix, namespaces, context, coordinate
        )
        parent_key = ":".join(parent_coordinates)
        return self._effective_pom(parent_key, resolving), parent_coordinates

    def _read_parent_coordinates(
        self,
        parent: element_tree.Element,
        prefix: str,
        namespaces: dict[str, str],
        context: Mapping[str, str],
        coordinate: str,
    ) -> tuple[str, str, str]:
        """Read parent coordinates and resolve current properties."""
        names = ("groupId", "artifactId", "version")
        values = [
            self._interpolate(
                self._element_text(parent, name, prefix, namespaces), context
            )
            for name in names
        ]
        if any(not value or "${" in value for value in values):
            message = (
                "Не удалось определить Maven parent "
                f"для '{coordinate}'."
            )
            raise RepositoryError(message)
        return values[0], values[1], values[2]

    @staticmethod
    def _raw_properties(
        root: element_tree.Element,
        prefix: str,
        namespaces: dict[str, str],
    ) -> dict[str, str]:
        """Read all custom property values from a POM's properties element."""
        properties = {}
        element = root.find(f"{prefix}properties", namespaces)
        if element is None:
            return properties
        for child in element:
            name = child.tag.split("}", 1)[-1]
            properties[name] = MavenRepository._text(child)
        return properties

    def _resolved_properties(
        self,
        coordinate: tuple[str, str, str],
        parent_coordinate: tuple[str, str, str] | None,
        parent_model: EffectivePom | None,
        raw_properties: dict[str, str],
        pom: PomDocument,
    ) -> tuple[dict[str, str], dict[str, str], str, str, str]:
        """Resolve project identity and inherited properties for one POM."""
        properties = self._inherited_properties(parent_model, raw_properties)
        builtins = self._project_properties(*coordinate, parent_coordinate)
        builtins.update(properties)
        identity = self._project_identity(
            coordinate,
            parent_model,
            pom.root,
            pom.prefix,
            pom.namespaces,
            builtins,
        )
        group_id, artifact_id, version = identity
        builtins.update(
            self._project_properties(
                group_id, artifact_id, version, parent_coordinate
            )
        )
        resolved = self._resolve_property_values(properties, builtins)
        return resolved, builtins, group_id, artifact_id, version

    @staticmethod
    def _inherited_properties(
        parent_model: EffectivePom | None,
        raw_properties: dict[str, str],
    ) -> dict[str, str]:
        """Combine parent properties with the values declared by this POM."""
        properties = dict(parent_model.properties) if parent_model else {}
        properties.update(raw_properties)
        return properties

    def _project_identity(
        self,
        coordinate: tuple[str, str, str],
        parent_model: EffectivePom | None,
        root: element_tree.Element,
        prefix: str,
        namespaces: dict[str, str],
        builtins: Mapping[str, str],
    ) -> tuple[str, str, str]:
        """Resolve group, artifact, and version for this POM."""
        requested_group, requested_artifact, requested_version = coordinate
        default_group = (
            parent_model.group_id if parent_model else requested_group
        )
        default_version = (
            parent_model.version if parent_model else requested_version
        )
        values = (
            self._project_value(
                root, "groupId", prefix, namespaces, default_group
            ),
            self._project_value(
                root, "artifactId", prefix, namespaces, requested_artifact
            ),
            self._project_value(
                root, "version", prefix, namespaces, default_version
            ),
        )
        return tuple(self._interpolate(value, builtins) for value in values)

    @classmethod
    def _project_value(
        cls,
        root: element_tree.Element,
        name: str,
        prefix: str,
        namespaces: dict[str, str],
        fallback: str,
    ) -> str:
        """Return one project coordinate from XML or its inherited fallback."""
        return cls._element_text(root, name, prefix, namespaces) or fallback

    @classmethod
    def _resolve_property_values(
        cls,
        properties: dict[str, str],
        builtins: Mapping[str, str],
    ) -> dict[str, str]:
        """Resolve chained properties until their values become stable."""
        resolved = dict(properties)
        for _ in range(PROPERTY_PASSES):
            context = dict(builtins)
            context.update(resolved)
            updated = {
                name: cls._interpolate(value, context)
                for name, value in resolved.items()
            }
            if updated == resolved:
                return resolved
            resolved = updated
        return resolved

    def _dependency_management(
        self,
        root: element_tree.Element,
        prefix: str,
        namespaces: dict[str, str],
        properties: Mapping[str, str],
        parent_model: EffectivePom | None,
        resolving: tuple[str, ...],
    ) -> dict[tuple[str, str, str, str], dict[str, str]]:
        """Combine inherited, imported, and local managed entries."""
        imported, direct = self._read_managed_entries(
            root, prefix, namespaces, properties, resolving
        )
        inherited = (
            dict(parent_model.dependency_management) if parent_model else {}
        )
        inherited.update(imported)
        inherited.update(direct)
        return inherited

    def _read_managed_entries(
        self,
        root: element_tree.Element,
        prefix: str,
        namespaces: dict[str, str],
        properties: Mapping[str, str],
        resolving: tuple[str, ...],
    ) -> tuple[
        dict[tuple[str, str, str, str], dict[str, str]],
        dict[tuple[str, str, str, str], dict[str, str]],
    ]:
        """Read local management entries and expand imported Maven BOMs."""
        imported = {}
        direct = {}
        pom = PomDocument(root, prefix, namespaces)
        path = f"{prefix}dependencyManagement/{prefix}dependencies"
        element = root.find(path, namespaces)
        if element is None:
            return imported, direct
        for dependency in element.findall(f"{prefix}dependency", namespaces):
            self._add_managed_entry(
                dependency,
                pom,
                properties,
                resolving,
                imported,
                direct,
            )
        return imported, direct

    def _add_managed_entry(
        self,
        dependency: element_tree.Element,
        pom: PomDocument,
        properties: Mapping[str, str],
        resolving: tuple[str, ...],
        imported: dict[tuple[str, str, str, str], dict[str, str]],
        direct: dict[tuple[str, str, str, str], dict[str, str]],
    ) -> None:
        """Add one local managed dependency or import its BOM entries."""
        values = self._dependency_values(
            dependency, pom.prefix, pom.namespaces, properties
        )
        if not values.get("groupId") or not values.get("artifactId"):
            return
        if self._is_bom_import(values):
            self._import_bom(values, resolving, imported)
            return
        version = values.get("version", "")
        if version and "${" in version:
            return
        direct[self._dependency_key(values)] = values

    @staticmethod
    def _is_bom_import(values: Mapping[str, str]) -> bool:
        """Tell whether a management entry imports a Maven BOM POM."""
        return values.get("type") == "pom" and values.get("scope") == "import"

    def _import_bom(
        self,
        values: Mapping[str, str],
        resolving: tuple[str, ...],
        imported: dict[tuple[str, str, str, str], dict[str, str]],
    ) -> None:
        """Load a BOM and add its entries when the version is known."""
        version = values.get("version", "")
        if not version or "${" in version:
            return
        bom = ":".join((values["groupId"], values["artifactId"], version))
        bom_model = self._effective_pom(bom, resolving)
        for key, managed_values in bom_model.dependency_management.items():
            imported.setdefault(key, dict(managed_values))

    def _dependencies_with_management(
        self,
        root: element_tree.Element,
        prefix: str,
        namespaces: dict[str, str],
        properties: Mapping[str, str],
        management: dict[tuple[str, str, str, str], dict[str, str]],
        parent_model: EffectivePom | None,
    ) -> dict[tuple[str, str, str, str], dict[str, str]]:
        """Resolve direct dependencies, including parent and managed values."""
        dependencies = dict(parent_model.dependencies) if parent_model else {}
        element = root.find(f"{prefix}dependencies", namespaces)
        if element is None:
            return dependencies
        for dependency in element.findall(f"{prefix}dependency", namespaces):
            values = self._dependency_values(
                dependency, prefix, namespaces, properties
            )
            self._apply_managed_values(values, management)
            if values.get("groupId") and values.get("artifactId"):
                dependencies[self._dependency_key(values)] = values
        return dependencies

    def _apply_managed_values(
        self,
        values: dict[str, str],
        management: Mapping[tuple[str, str, str, str], dict[str, str]],
    ) -> None:
        """Fill missing fields from managed dependencies."""
        managed = management.get(self._dependency_key(values), {})
        for field in ("version", "scope", "optional"):
            if not values.get(field) and managed.get(field):
                values[field] = managed[field]

    def _effective_pom(
        self,
        coordinate: str,
        resolving: tuple[str, ...] = (),
    ) -> EffectivePom:
        """Build POM metadata with parent and BOM inheritance."""
        if coordinate in self._effective_cache:
            return self._effective_cache[coordinate]
        self._check_pom_cycle(coordinate, resolving)
        model = self._build_effective_pom(coordinate, resolving)
        self._effective_cache[coordinate] = model
        return model

    @staticmethod
    def _check_pom_cycle(
        coordinate: str,
        resolving: tuple[str, ...],
    ) -> None:
        """Raise a readable error when a parent or BOM cycle is found."""
        if coordinate in resolving:
            chain = " -> ".join((*resolving, coordinate))
            raise RepositoryError(
                f"Обнаружен цикл parent/BOM в Maven POM: {chain}"
            )

    def _build_effective_pom(
        self,
        coordinate: str,
        resolving: tuple[str, ...],
    ) -> EffectivePom:
        """Resolve parent, properties, managed versions, and dependencies."""
        root = self._load_pom(coordinate)
        prefix, namespaces = self._namespace(root)
        pom = PomDocument(root, prefix, namespaces)
        next_resolving = (*resolving, coordinate)
        raw = self._raw_properties(root, prefix, namespaces)
        parent, parent_coordinate = self._parent_model(
            root, coordinate, prefix, namespaces, raw, next_resolving
        )
        identity = self._split_coordinates(coordinate)
        resolved = self._resolved_properties(
            identity, parent_coordinate, parent, raw, pom
        )
        properties, builtins, group, artifact, version = resolved
        context = dict(builtins)
        context.update(properties)
        management = self._dependency_management(
            root, prefix, namespaces, context, parent, next_resolving
        )
        dependencies = self._dependencies_with_management(
            root, prefix, namespaces, context, management, parent
        )
        model = EffectivePom(
            group, artifact, version, properties, dependencies, management
        )
        return model

    @staticmethod
    def _is_included_dependency(values: Mapping[str, str]) -> bool:
        """Keep only resolved, non-optional compile or runtime dependencies."""
        scope = values.get("scope", DEFAULT_SCOPE) or DEFAULT_SCOPE
        optional = values.get("optional", "false").lower() == "true"
        coordinates = (
            values.get("groupId"),
            values.get("artifactId"),
            values.get("version"),
        )
        unresolved = any(not value or "${" in value for value in coordinates)
        return scope in INCLUDED_SCOPES and not optional and not unresolved

    def dependencies(self, package: str) -> list[str]:
        """Return resolved direct dependencies from a Maven POM."""
        if package in self._cache:
            return self._cache[package]
        model = self._effective_pom(package)
        dependencies = self._package_dependencies(model)
        self._cache[package] = dependencies
        return dependencies

    @classmethod
    def _package_dependencies(cls, model: EffectivePom) -> list[str]:
        """Format eligible direct dependency metadata as Maven coordinates."""
        dependencies = []
        for values in model.dependencies.values():
            if not cls._is_included_dependency(values):
                continue
            coordinate = ":".join(
                (values["groupId"], values["artifactId"], values["version"])
            )
            if coordinate not in dependencies:
                dependencies.append(coordinate)
        return dependencies
