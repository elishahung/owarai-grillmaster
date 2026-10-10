"""Offline stand-ins for the yt-dlp and platform-HTTP seams."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from grillmaster.sources.errors import SourceHttpError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from yt_dlp.postprocessor.common import PostProcessor


@dataclass(frozen=True, slots=True)
class YtDlpCall:
    url: str
    options: dict[str, Any]
    download: bool
    before_download: tuple[str, ...]


@dataclass
class FakeYtDlp:
    """A `YtDlp` returning `info`; `on_download(options)` plays the download.

    `fail_with` is raised by every call after it is recorded.
    """

    info: dict[str, Any] = field(default_factory=lambda: {"id": "x", "title": "t"})
    on_download: Callable[[Mapping[str, Any]], None] | None = None
    fail_with: Exception | None = None
    calls: list[YtDlpCall] = field(default_factory=list)

    def extract_info(
        self,
        url: str,
        options: Mapping[str, Any],
        *,
        download: bool,
        before_download: Sequence[PostProcessor] = (),
    ) -> dict[str, Any]:
        self.calls.append(
            YtDlpCall(
                url,
                dict(options),
                download,
                tuple(type(processor).__name__ for processor in before_download),
            )
        )
        if self.fail_with is not None:
            raise self.fail_with
        if download and self.on_download is not None:
            self.on_download(options)
        return dict(self.info)


@dataclass(frozen=True, slots=True)
class HttpRequest:
    url: str
    headers: dict[str, str]
    data: bytes | None


@dataclass
class FakeJsonHttp:
    """A `JsonHttp` answering from `routes`, keyed by URL without its query.

    A route holding an exception raises it; an unknown URL raises
    `SourceHttpError` like a 404 would.
    """

    routes: dict[str, object] = field(default_factory=dict)
    requests: list[HttpRequest] = field(default_factory=list)

    def request(
        self, url: str, *, headers: Mapping[str, str], data: bytes | None = None
    ) -> Any:
        self.requests.append(HttpRequest(url, dict(headers), data))
        route = url.split("?", 1)[0]
        if route not in self.routes:
            raise SourceHttpError(f"HTTP 404: {url}")
        response = self.routes[route]
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def urls(self) -> list[str]:
        return [request.url.split("?", 1)[0] for request in self.requests]
