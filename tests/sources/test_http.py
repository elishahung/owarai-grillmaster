from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest

from grillmaster.sources.errors import SourceHttpError
from grillmaster.sources.http import UrllibJsonHttp

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = {
            "/ok": b'{"a": 1}',
            "/truncated": b'{"a": 1',
            "/not-json": b"<html>",
        }[self.path]
        self.send_response(200)
        # The truncated route promises more than it sends.
        extra = 100 if self.path == "/truncated" else 0
        self.send_header("Content-Length", str(len(body) + extra))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host!s}:{port}"
    httpd.shutdown()
    httpd.server_close()


def test_decodes_json(server: str) -> None:
    assert UrllibJsonHttp().request(f"{server}/ok", headers={}) == {"a": 1}


@pytest.mark.parametrize("route", ["/truncated", "/not-json"])
def test_unusable_bodies_are_source_http_errors(server: str, route: str) -> None:
    with pytest.raises(SourceHttpError, match=f"GET {server}{route} failed"):
        UrllibJsonHttp().request(f"{server}{route}", headers={})
