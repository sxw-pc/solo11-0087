"""Loopback-only HTTP access with an audit hook."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

import requests

from .errors import FetchError, explain
from .normalize import sha256_bytes

AuditSink = Callable[[str, str, str], None]
LOOPBACK_NAMES = frozenset({"localhost"})
CHARSET_RE = re.compile(r"charset=([A-Za-z0-9._-]+)", re.IGNORECASE)


def is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower().strip("[]")
    if normalized in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ParsedTarget:
    url: str
    scheme: str
    host: str
    port: int
    path: str

    @property
    def origin(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


class LoopbackPolicy:
    """Validates every URL before a socket is opened."""

    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        cleaned = tuple(sorted({host.strip().lower() for host in allowed_hosts if host}))
        if not cleaned:
            raise FetchError(
                "CONFIG_INVALID", "host 白名单不能为空", hint=explain("CONFIG_HOST_FORBIDDEN")
            )
        for host in cleaned:
            if not is_loopback_host(host):
                raise FetchError(
                    "FETCH_HOST_BLOCKED",
                    f"白名单包含非 loopback host：{host}",
                    hint=explain("FETCH_HOST_BLOCKED"),
                    host=host,
                )
        self.allowed_hosts = cleaned

    def validate(self, url: str) -> ParsedTarget:
        parsed = urlsplit((url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            raise FetchError(
                "FETCH_SCHEME_BLOCKED",
                f"不支持的 URL scheme：{parsed.scheme or '(empty)'}",
                hint=explain("FETCH_SCHEME_BLOCKED"),
                url=url[:200],
            )
        if parsed.username or parsed.password:
            raise FetchError(
                "FETCH_URL_INVALID",
                "URL 不得包含用户名或密码",
                hint=explain("FETCH_URL_INVALID"),
                url=url[:200],
            )
        host = (parsed.hostname or "").lower()
        if not host or host not in self.allowed_hosts or not is_loopback_host(host):
            raise FetchError(
                "FETCH_HOST_BLOCKED",
                f"目标 host 不允许访问：{host or '(empty)'}",
                hint=explain("FETCH_HOST_BLOCKED"),
                host=host,
            )
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise FetchError(
                "FETCH_URL_INVALID", "URL 端口不合法", url=url[:200]
            ) from exc
        return ParsedTarget(
            url=url,
            scheme=parsed.scheme,
            host=host,
            port=port,
            path=parsed.path or "/",
        )

    def resolve(self, base_url: str, reference: str) -> str:
        base = self.validate(base_url)
        candidate = urljoin(base.url, reference)
        target = self.validate(candidate)
        if target.scheme != base.scheme:
            raise FetchError(
                "FETCH_SCHEME_BLOCKED",
                "禁止切换到不同 scheme",
                hint=explain("FETCH_SCHEME_BLOCKED"),
                url=candidate[:200],
            )
        return candidate


@dataclass(frozen=True)
class FetchedResponse:
    url: str
    status: int
    content_type: str
    content: bytes
    sha256: str
    charset: str

    @property
    def text(self) -> str:
        return self.content.decode(self.charset)


class SafeFetcher:
    """A requests session that refuses anything except configured loopback hosts."""

    def __init__(
        self,
        policy: LoopbackPolicy,
        *,
        timeout_seconds: float = 5.0,
        max_bytes: int = 1_048_576,
        user_agent: str = "tender-anchor-local/1.0",
        audit: AuditSink | None = None,
    ) -> None:
        self.policy = policy
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.audit = audit
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"}
        )

    def _emit(self, event_type: str, target: str, detail: str) -> None:
        if self.audit is None:
            return
        try:
            self.audit(event_type, target[:500], detail[:500])
        except Exception:  # auditing must never mask the fetch reason
            pass

    def fetch(self, url: str) -> FetchedResponse:
        try:
            target = self.policy.validate(url)
        except FetchError as exc:
            self._emit("FETCH_BLOCKED", url, f"{exc.code}: {exc.message}")
            raise
        try:
            response = self.session.get(
                target.url,
                timeout=self.timeout_seconds,
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout as exc:
            self._emit("FETCH_ERROR", url, "FETCH_TIMEOUT")
            raise FetchError(
                "FETCH_TIMEOUT", f"请求超时：{url}", hint=explain("FETCH_TIMEOUT")
            ) from exc
        except requests.RequestException as exc:
            self._emit("FETCH_ERROR", url, "FETCH_CONNECTION_ERROR")
            raise FetchError(
                "FETCH_CONNECTION_ERROR",
                f"连接失败：{url}",
                hint=explain("FETCH_CONNECTION_ERROR"),
                detail=str(exc)[:160],
            ) from exc

        with response:
            if 300 <= response.status_code < 400:
                self._emit("FETCH_BLOCKED", url, "FETCH_REDIRECT_BLOCKED")
                raise FetchError(
                    "FETCH_REDIRECT_BLOCKED",
                    f"服务端返回重定向，已拒绝跟随：HTTP {response.status_code}",
                    hint="mock 门户不应使用重定向；如确需跳转请显式配置。",
                    location=response.headers.get("Location", ""),
                )
            if response.status_code >= 400:
                self._emit("FETCH_ERROR", url, f"FETCH_HTTP_ERROR:{response.status_code}")
                raise FetchError(
                    "FETCH_HTTP_ERROR",
                    f"HTTP {response.status_code}：{url}",
                    hint="目标资源不存在或服务不可用。",
                    status=str(response.status_code),
                )
            declared = response.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > self.max_bytes:
                self._emit("FETCH_ERROR", url, "FETCH_TOO_LARGE")
                raise FetchError(
                    "FETCH_TOO_LARGE",
                    f"响应超过 {self.max_bytes} 字节限制",
                    hint=explain("FETCH_TOO_LARGE"),
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.max_bytes:
                    self._emit("FETCH_ERROR", url, "FETCH_TOO_LARGE")
                    raise FetchError(
                        "FETCH_TOO_LARGE",
                        f"响应超过 {self.max_bytes} 字节限制",
                        hint=explain("FETCH_TOO_LARGE"),
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            charset_match = CHARSET_RE.search(content_type)
            charset = charset_match.group(1) if charset_match else "utf-8"
            try:
                content.decode(charset)
            except (LookupError, UnicodeDecodeError) as exc:
                self._emit("FETCH_ERROR", url, "FETCH_DECODE_ERROR")
                raise FetchError(
                    "FETCH_DECODE_ERROR",
                    f"响应无法按 {charset} 解码",
                    hint=explain("FETCH_DECODE_ERROR"),
                ) from exc
            return FetchedResponse(
                url=url,
                status=response.status_code,
                content_type=content_type,
                content=content,
                sha256=sha256_bytes(content),
                charset=charset,
            )

    def fetch_text(self, url: str) -> tuple[str, FetchedResponse]:
        response = self.fetch(url)
        return response.text, response

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "SafeFetcher":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
