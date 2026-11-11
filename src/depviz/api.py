"""Expose dependency data and generated diagrams through a local HTTP API."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import subprocess
import sys
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

from .graph import DependencyGraph, build_graph
from .render import plantuml, svg
from .repository import DependencyRepository, RepositoryError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_BAD_GATEWAY = 502
PROCESS_SUCCESS = 0
DOWNLOAD_VALUES = {"1", "true", "yes"}
ALLOWED_GRAPH_ROUTES = {"/api/graph", "/api/graph.svg", "/api/graph.puml"}


def _filename(package: str, extension: str) -> str:
    """Create a filesystem-safe download name from the package coordinate."""
    safe_package = re.sub(r"[^A-Za-z0-9._-]+", "_", package).strip("._-")
    return f"{safe_package or 'graph'}.{extension}"


class DependencyAPIHandler(BaseHTTPRequestHandler):
    """Handle status, graph JSON, SVG, and PlantUML GET requests."""

    repository: DependencyRepository
    default_package: str
    filter_substring: str
    test_mode: bool

    server_version = "depviz-api/1.0"

    def log_message(self, format: str, *args) -> None:
        """Write a short record of each API request to the terminal."""
        message = format % args
        print(f"API: {self.address_string()} - {message}")

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        filename: str | None = None,
        disposition: str = "attachment",
    ) -> None:
        """Send a body with content type and optional file headers."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header(
                "Content-Disposition",
                f'{disposition}; filename="{filename}"',
            )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: dict) -> None:
        """Serialize a Python value as a UTF-8 JSON response."""
        body = json.dumps(value, ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
        self._send(status, body, "application/json; charset=utf-8")

    def _service_routes(self) -> dict[str, str]:
        """Build route examples using this run's default package."""
        package = self.default_package
        return {
            "health": "/api/health",
            "graph_json": f"/api/graph?package={package}",
            "svg_preview": f"/api/graph.svg?package={package}",
            "svg_download": (
                f"/api/graph.svg?package={package}&download=true"
            ),
            "plantuml_download": f"/api/graph.puml?package={package}",
            "reverse_dependencies": (
                f"/api/graph?package={package}&reverse=G"
            ),
        }

    def _handle_service_route(self, path: str) -> bool:
        """Send API or health information for service-level routes."""
        if path in {"/", "/api"}:
            payload = {
                "service": "depviz",
                "message": "API графа зависимостей.",
                "routes": self._service_routes(),
            }
            self._send_json(200, payload)
            return True
        if path == "/api/health":
            self._send_json(200, {"status": "ok", "service": "depviz"})
            return True
        return False

    def _load_graph(
        self,
        package: str,
    ) -> tuple[list[str], DependencyGraph] | None:
        """Read direct dependencies and build a graph or send an error."""
        try:
            direct = self.repository.dependencies(package)
            graph = build_graph(
                self.repository, package, self.filter_substring
            )
        except RepositoryError as exc:
            status = HTTP_NOT_FOUND if self.test_mode else HTTP_BAD_GATEWAY
            self._send_json(status, {"error": str(exc)})
            return None
        return direct, graph

    def _send_graph_file(
        self,
        path: str,
        query: dict[str, list[str]],
        package: str,
        graph: DependencyGraph,
    ) -> bool:
        """Send a generated SVG or PlantUML file for a package."""
        if path == "/api/graph.svg":
            self._send_svg(query, package, graph)
            return True
        if path == "/api/graph.puml":
            body = plantuml(graph).encode("utf-8")
            self._send(
                200,
                body,
                "text/plain; charset=utf-8",
                _filename(package, "puml"),
            )
            return True
        return False

    def _send_svg(
        self,
        query: dict[str, list[str]],
        package: str,
        graph: DependencyGraph,
    ) -> None:
        """Render the graph and return SVG inline or as an attachment."""
        body = svg(graph).encode("utf-8")
        download = query.get("download", [""])[0].lower()
        disposition = (
            "attachment" if download in DOWNLOAD_VALUES else "inline"
        )
        self._send(
            200,
            body,
            "image/svg+xml; charset=utf-8",
            _filename(package, "svg"),
            disposition,
        )

    def _graph_json(
        self,
        package: str,
        direct: list[str],
        graph: DependencyGraph,
        query: dict[str, list[str]],
    ) -> dict:
        """Return graph data and optional reverse dependencies."""
        result = {
            "package": package,
            "direct_dependencies": direct,
            "nodes": sorted(graph.nodes),
            "edges": graph.edges,
            "depths": graph.depths,
        }
        target = query.get("reverse", [""])[0].strip()
        if target:
            reverse = graph.reverse_dependencies(target)
            result["reverse_target"] = target
            result["reverse_dependencies"] = reverse
        return result

    def do_get(self) -> None:
        """Dispatch GET requests to the matching response helper."""
        request = urlsplit(self.path)
        if self._handle_service_route(request.path):
            return
        if request.path not in ALLOWED_GRAPH_ROUTES:
            self._send_json(
                404,
                {"error": "Неизвестный API-адрес."},
            )
            return
        self._handle_graph_request(request.path, parse_qs(request.query))

    def _handle_graph_request(
        self,
        path: str,
        query: dict[str, list[str]],
    ) -> None:
        """Validate query parameters and send data or a diagram file."""
        package = query.get("package", [self.default_package])[0].strip()
        if not package:
            self._send_json(
                400,
                {"error": "Параметр package пуст."},
            )
            return
        graph_data = self._load_graph(package)
        if graph_data is None:
            return
        direct, graph = graph_data
        if self._send_graph_file(path, query, package, graph):
            return
        self._send_json(
            200, self._graph_json(package, direct, graph, query)
        )


setattr(DependencyAPIHandler, "do_GET", DependencyAPIHandler.do_get)


def create_handler(
    repository: DependencyRepository,
    default_package: str,
    filter_substring: str,
    test_mode: bool,
) -> type[BaseHTTPRequestHandler]:
    """Bind one repository and its settings to an HTTP handler subclass."""
    settings = {
        "repository": repository,
        "default_package": default_package,
        "filter_substring": filter_substring,
        "test_mode": test_mode,
    }
    return type(
        "ConfiguredDependencyAPIHandler",
        (DependencyAPIHandler,),
        settings,
    )


def _open_download_url(url: str) -> bool:
    """Open the graph download URL in the default browser."""
    try:
        if sys.platform == "darwin":
            command = ["/usr/bin/open", url]
            return (
                subprocess.run(command, capture_output=True).returncode
                == PROCESS_SUCCESS
            )
        return webbrowser.open_new_tab(url)
    except (OSError, webbrowser.Error):
        return False


def serve_api(
    repository: DependencyRepository,
    default_package: str,
    filter_substring: str,
    test_mode: bool,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """Start the local API and optionally open the selected graph download."""
    handler = create_handler(
        repository, default_package, filter_substring, test_mode
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"HTTP API запущен: http://{host}:{server.server_port}")
    print("Маршруты API: /api/health, /api/graph, /api/graph.svg,")
    print("/api/graph.puml")
    query = urlencode({"package": default_package, "download": "true"})
    graph_url = f"http://{host}:{server.server_port}/api/graph.svg?{query}"
    if open_browser and not _open_download_url(graph_url):
        message = "Не удалось открыть браузер. "
        print(f"{message}Граф: {graph_url}")
    print("Остановить сервер: Ctrl+C")
    try:
        server.serve_forever()
    finally:
        server.server_close()
