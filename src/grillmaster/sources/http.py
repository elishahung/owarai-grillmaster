"""The JSON-over-HTTP seam the platform metadata clients use.

TVer and ABEMA publish talent and on-air metadata through web APIs yt-dlp
does not read. Platforms take a `JsonHttp` so tests pass a fake;
`UrllibJsonHttp` is the real one.
"""

from __future__ import annotations

import json
from http.client import HTTPException
from typing import TYPE_CHECKING, Any, Protocol
from urllib.request import Request, urlopen

from grillmaster.sources.errors import SourceHttpError

if TYPE_CHECKING:
    from collections.abc import Mapping

_TIMEOUT_S = 20.0

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


class JsonHttp(Protocol):
    def request(
        self, url: str, *, headers: Mapping[str, str], data: bytes | None = None
    ) -> Any:
        """GET `url` (POST when `data` is given) and return the decoded JSON.

        Raises `SourceHttpError` on a network failure, an HTTP error status
        or a body that is not JSON.
        """
        ...


class UrllibJsonHttp:
    """`JsonHttp` over the standard library."""

    def request(
        self, url: str, *, headers: Mapping[str, str], data: bytes | None = None
    ) -> Any:
        method = "GET" if data is None else "POST"
        request = Request(url, data=data, headers=dict(headers), method=method)  # noqa: S310 - fixed https API URLs
        try:
            with urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310 - fixed https API URLs
                body = response.read().decode("utf-8")
            return json.loads(body)
        # `OSError` covers `URLError`, timeouts and resets during the read;
        # `HTTPException` a truncated body; `ValueError` bad UTF-8 or JSON.
        except (OSError, HTTPException, ValueError) as error:
            raise SourceHttpError(f"{method} {url} failed: {error}") from error
