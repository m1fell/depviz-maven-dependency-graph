import json
import io
from http.server import ThreadingHTTPServer
import threading
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch
from pathlib import Path

from depviz.api import create_handler
from depviz.cli import _select_package
from depviz.config import ConfigError, load_config
from depviz.graph import build_graph
from depviz.render import plantuml, svg
from depviz.repository import (
    JsonTestRepository,
    MavenRepository,
    RepositoryError,
)

ROOT = Path(__file__).resolve().parents[1]


def _write_config(path, *lines):
    """Write INI lines to a temporary configuration file."""
    path.write_text("\n".join(lines), encoding="utf-8")


PARENT_POM = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>example</groupId>
  <artifactId>parent</artifactId>
  <version>1.0</version>
  <properties><shared.version>4.2</shared.version></properties>
  <dependencyManagement><dependencies>
    <dependency>
      <groupId>libs</groupId>
      <artifactId>managed</artifactId>
      <version>${shared.version}</version>
    </dependency>
  </dependencies></dependencyManagement>
  <dependencies><dependency>
    <groupId>libs</groupId>
    <artifactId>inherited</artifactId>
    <version>1.3</version>
  </dependency></dependencies>
</project>"""
CHILD_POM = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>example</groupId>
    <artifactId>parent</artifactId>
    <version>1.0</version>
  </parent>
  <artifactId>child</artifactId>
  <dependencies><dependency>
    <groupId>libs</groupId>
    <artifactId>managed</artifactId>
  </dependency></dependencies>
</project>"""
BOM_POM = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>example</groupId>
  <artifactId>platform</artifactId>
  <version>1.0</version>
  <dependencyManagement><dependencies><dependency>
    <groupId>libs</groupId>
    <artifactId>from-bom</artifactId>
    <version>3.7</version>
  </dependency></dependencies></dependencyManagement>
</project>"""
ROOT_BOM_POM = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>example</groupId>
  <artifactId>root</artifactId>
  <version>1.0</version>
  <dependencyManagement><dependencies><dependency>
    <groupId>example</groupId>
    <artifactId>platform</artifactId>
    <version>1.0</version>
    <type>pom</type>
    <scope>import</scope>
  </dependency></dependencies></dependencyManagement>
  <dependencies><dependency>
    <groupId>libs</groupId>
    <artifactId>from-bom</artifactId>
  </dependency></dependencies>
</project>"""


class ConfigTests(unittest.TestCase):
    def test_example_configuration_loads(self):
        config = load_config(ROOT / "config.ini")
        self.assertTrue(config.test_mode)
        self.assertEqual(config.package, "A")

    def test_missing_config_is_reported(self):
        with self.assertRaisesRegex(ConfigError, "не найден"):
            load_config(ROOT / "absent.ini")

    def test_bad_test_mode_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            _write_config(
                path,
                '[app]',
                'package=A',
                'repository_url=https://example.org',
                'test_mode=yes',
                'test_repository=x.json',
                'filter_substring=',
            )
            with self.assertRaisesRegex(ConfigError, "true или false"):
                load_config(path)

    def test_bad_maven_coordinates_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            _write_config(
                path,
                '[app]',
                'package=bad',
                'repository_url=https://example.org',
                'test_mode=false',
                'test_repository=x.json',
                'filter_substring=',
            )
            with self.assertRaisesRegex(
                ConfigError, "groupId:artifactId:version"
            ):
                load_config(path)

    def test_bad_repository_url_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            _write_config(
                path,
                '[app]',
                'package=A',
                'repository_url=not-a-url',
                'test_mode=true',
                'test_repository=x.json',
                'filter_substring=',
            )
            with self.assertRaisesRegex(
                ConfigError, "корректным URL"
            ):
                load_config(path)

    def test_invalid_test_package_name_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            _write_config(
                path,
                '[app]',
                'package=lower',
                'repository_url=https://example.org',
                'test_mode=true',
                'test_repository=graph.json',
                'filter_substring=',
            )
            with self.assertRaisesRegex(
                ConfigError, "больших латинских букв"
            ):
                load_config(path)

    def test_missing_test_repository_file_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            _write_config(
                path,
                '[app]',
                'package=A',
                'repository_url=https://example.org',
                'test_mode=true',
                'test_repository=missing.json',
                'filter_substring=',
            )
            with self.assertRaisesRegex(
                ConfigError, "Не найден тестовый файл"
            ):
                load_config(path)

    def test_missing_filter_setting_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            graph_path = Path(folder) / "graph.json"
            graph_path.write_text('{"A": []}', encoding="utf-8")
            path = Path(folder) / "config.ini"
            _write_config(
                path,
                '[app]',
                'package=A',
                'repository_url=https://example.org',
                'test_mode=true',
                'test_repository=graph.json',
            )
            with self.assertRaisesRegex(ConfigError, "filter_substring"):
                load_config(path)

    def test_malformed_test_repository_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            graph_path = Path(folder) / "graph.json"
            graph_path.write_text('{"A": ["not uppercase"]}', encoding="utf-8")
            path = Path(folder) / "config.ini"
            _write_config(
                path,
                '[app]',
                'package=A',
                'repository_url=https://example.org',
                'test_mode=true',
                'test_repository=graph.json',
                'filter_substring=',
            )
            with self.assertRaisesRegex(ConfigError, "JSON-объектом"):
                load_config(path)


class GraphTests(unittest.TestCase):
    def set_up(self):
        self.repository = JsonTestRepository(
            ROOT / "examples/test_repository.json"
        )

    def test_bfs_visits_each_node_once_despite_cycle(self):
        graph = build_graph(self.repository, "A")
        self.assertEqual(graph.nodes, set("ABCDEFGH"))
        self.assertEqual(graph.depths["A"], 0)
        self.assertEqual(graph.edges["G"], ["B"])

    def test_filter_removes_matching_nodes_and_their_edges(self):
        graph = build_graph(self.repository, "A", "E")
        self.assertNotIn("E", graph.nodes)
        self.assertEqual(graph.edges["B"], ["D"])

    def test_reverse_dependencies_are_found_with_bfs(self):
        graph = build_graph(self.repository, "A")
        self.assertEqual(
            set(graph.reverse_dependencies("G")), {"D", "E", "B", "C", "A"}
        )

    def test_unknown_test_package_is_reported(self):
        with self.assertRaises(RepositoryError):
            self.repository.dependencies("Z")

    def test_diagrams_have_expected_formats_and_cycle_edge(self):
        graph = build_graph(self.repository, "A")
        self.assertIn("@startuml", plantuml(graph))
        self.assertIn("@enduml", plantuml(graph))
        self.assertIn("<svg", svg(graph))
        self.assertIn("#d97706", svg(graph))


setattr(GraphTests, "setUp", GraphTests.set_up)


class APITests(unittest.TestCase):
    @classmethod
    def set_up_class(cls):
        cls.repository = JsonTestRepository(
            ROOT / "examples/test_repository.json"
        )
        handler = create_handler(cls.repository, "A", "", True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tear_down_class(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health_endpoint(self):
        with urlopen(f"{self.base_url}/api/health") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(
                json.loads(response.read()),
                {"status": "ok", "service": "depviz"},
            )

    def test_root_endpoint_lists_available_api_routes(self):
        with urlopen(f"{self.base_url}/") as response:
            result = json.loads(response.read())
        self.assertEqual(result["service"], "depviz")
        self.assertEqual(
            result["routes"]["svg_preview"], "/api/graph.svg?package=A"
        )
        self.assertEqual(
            result["routes"]["svg_download"],
            "/api/graph.svg?package=A&download=true",
        )

    def test_graph_endpoint_returns_edges_and_reverse_dependencies(self):
        with urlopen(
            f"{self.base_url}/api/graph?package=A&reverse=G"
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(result["direct_dependencies"], ["B", "C"])
        self.assertEqual(result["edges"]["G"], ["B"])
        self.assertEqual(
            set(result["reverse_dependencies"]), {"A", "B", "C", "D", "E"}
        )

    def test_svg_endpoint_displays_graph_inline_in_browser(self):
        with urlopen(f"{self.base_url}/api/graph.svg?package=C") as response:
            content = response.read().decode("utf-8")
            self.assertEqual(
                response.headers.get_content_type(), "image/svg+xml"
            )
            self.assertEqual(response.headers.get_filename(), "C.svg")
            self.assertEqual(
                response.headers.get_content_disposition(), "inline"
            )
        self.assertIn("<svg", content)
        self.assertIn(
            "Граф зависимостей пакета C", content
        )

    def test_svg_endpoint_can_download_image_as_separate_file(self):
        with urlopen(
            f"{self.base_url}/api/graph.svg?package=C&download=true"
        ) as response:
            self.assertEqual(response.headers.get_filename(), "C.svg")
            self.assertEqual(
                response.headers.get_content_disposition(), "attachment"
            )

    def test_plantuml_endpoint_downloads_source(self):
        with urlopen(f"{self.base_url}/api/graph.puml?package=A") as response:
            content = response.read().decode("utf-8")
            self.assertEqual(response.headers.get_filename(), "A.puml")
        self.assertIn("@startuml", content)
        self.assertIn("@enduml", content)

    def test_unknown_test_package_returns_json_404(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base_url}/api/graph?package=Z")
        self.assertEqual(error.exception.code, 404)
        try:
            self.assertIn(
                "отсутствует",
                error.exception.read().decode("utf-8"),
            )
        finally:
            error.exception.close()


setattr(APITests, "setUpClass", APITests.set_up_class)
setattr(APITests, "tearDownClass", APITests.tear_down_class)


class PackageSelectionTests(unittest.TestCase):
    def set_up(self):
        self.repository = JsonTestRepository(
            ROOT / "examples/test_repository.json"
        )

    @patch("builtins.input", return_value="3")
    def test_selects_package_by_number(self, _input):
        self.assertEqual(_select_package(self.repository, "A", None), "C")

    @patch("builtins.input", return_value="F")
    def test_selects_package_by_name(self, _input):
        self.assertEqual(_select_package(self.repository, "A", None), "F")

    @patch("builtins.input", return_value="")
    def test_enter_keeps_configured_package_as_default(self, _input):
        self.assertEqual(_select_package(self.repository, "C", None), "C")

    def test_package_argument_skips_interactive_selection(self):
        self.assertEqual(_select_package(self.repository, "A", "H"), "H")


setattr(PackageSelectionTests, "setUp", PackageSelectionTests.set_up)


class MavenParsingTests(unittest.TestCase):
    def test_maven_pom_url(self):
        repo = MavenRepository("https://repo.maven.apache.org/maven2/")
        self.assertEqual(
            repo._pom_url("org.apache.commons:commons-lang3:3.14.0"),
            (
                "https://repo.maven.apache.org/maven2/org/apache/commons/"
                + "commons-lang3/3.14.0/commons-lang3-3.14.0.pom"
            ),
        )

    def test_parses_direct_compile_and_runtime_dependencies_only(self):
        pom = b"""<?xml version="1.0"?>
          <project xmlns="http://maven.apache.org/POM/4.0.0">
          <dependencies>
            <dependency>
          <groupId>example</groupId>
          <artifactId>compile-lib</artifactId>
          <version>1.0</version>
          </dependency>
            <dependency>
          <groupId>example</groupId>
          <artifactId>runtime-lib</artifactId>
          <version>2.0</version>
          <scope>runtime</scope>
          </dependency>
            <dependency>
          <groupId>example</groupId>
          <artifactId>test-lib</artifactId>
          <version>3.0</version>
          <scope>test</scope>
          </dependency>
            <dependency>
          <groupId>example</groupId>
          <artifactId>optional-lib</artifactId>
          <version>4.0</version>
          <optional>true</optional>
          </dependency>
          </dependencies>
          </project>"""
        response = io.BytesIO(pom)
        with patch("depviz.repository.urlopen", return_value=response):
            dependencies = MavenRepository(
                "https://repo.example.test"
            ).dependencies("example:root:1.0")
        self.assertEqual(
            dependencies,
            ["example:compile-lib:1.0", "example:runtime-lib:2.0"],
        )

    def test_resolves_properties_and_dependency_management_versions(self):
        repo = MavenRepository("https://repo.example.test")
        coordinate = "example:root:1.0"
        pom = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
          <groupId>example</groupId>
          <artifactId>root</artifactId>
          <version>1.0</version>
          <properties>
          <shared.version>2.4.1</shared.version>
          </properties>
          <dependencyManagement>
          <dependencies>
            <dependency>
          <groupId>libs</groupId>
          <artifactId>managed</artifactId>
          <version>${shared.version}</version>
          </dependency>
          </dependencies>
          </dependencyManagement>
          <dependencies>
          <dependency>
          <groupId>libs</groupId>
          <artifactId>managed</artifactId>
          </dependency>
          </dependencies>
        </project>"""
        with patch("depviz.repository.urlopen", return_value=io.BytesIO(pom)):
            self.assertEqual(
                repo.dependencies(coordinate), ["libs:managed:2.4.1"]
            )

    def test_inherits_parent_properties_dependencies_and_dependency_management(
        self,
    ):
        repo = MavenRepository("https://repo.example.test")
        parent = "example:parent:1.0"
        child = "example:child:1.0"
        poms = {
            repo._pom_url(parent): PARENT_POM,
            repo._pom_url(child): CHILD_POM,
        }

        def fetch(request, timeout):
            return io.BytesIO(poms[request.full_url])

        with patch("depviz.repository.urlopen", side_effect=fetch):
            result = repo.dependencies(child)
        self.assertEqual(result, ["libs:inherited:1.3", "libs:managed:4.2"])

    def test_imports_bom_dependency_management(self):
        repo = MavenRepository("https://repo.example.test")
        bom = "example:platform:1.0"
        root = "example:root:1.0"
        poms = {
            repo._pom_url(bom): BOM_POM,
            repo._pom_url(root): ROOT_BOM_POM,
        }

        def fetch(request, timeout):
            return io.BytesIO(poms[request.full_url])

        with patch("depviz.repository.urlopen", side_effect=fetch):
            self.assertEqual(
                repo.dependencies(root), ["libs:from-bom:3.7"]
            )


if __name__ == "__main__":
    unittest.main()
