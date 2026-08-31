from __future__ import annotations

import http.client
import http.server
import socketserver
import ssl
import threading
import urllib.parse
import zlib
from dataclasses import dataclass, field

from .measure import Exchange, UsageReader, inspect_request

# Preserve provider auth headers.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

# `read` would buffer SSE.
CHUNK_BYTES = 65_536
UPSTREAM_TIMEOUT_SECONDS = 900


@dataclass
class Session:
    exchanges: list = field(default_factory=list)
    errors: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _in_flight: int = 0
    _idle: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self._idle.set()

    def began(self) -> None:
        with self._lock:
            self._in_flight += 1
            self._idle.clear()

    def ended(self) -> None:
        with self._lock:
            self._in_flight -= 1
            if not self._in_flight:
                self._idle.set()

    def drain(self, timeout: float) -> bool:
        return self._idle.wait(timeout)

    def record(self, exchange: Exchange) -> None:
        with self._lock:
            self.exchanges.append(exchange)

    def failed(self) -> None:
        with self._lock:
            self.errors += 1


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    upstream_host = "api.anthropic.com"
    session: Session
    tls_context: ssl.SSLContext
    evaluate = None

    def log_message(self, *_args: object, **_kwargs: object) -> None:
        pass

    def _relay(self) -> None:
        self.session.began()
        try:
            self._forward()
        finally:
            self.session.ended()

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        path = urllib.parse.urlsplit(self.path).path
        exchange = Exchange(path=path, request_bytes=len(body))

        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP
        }
        headers["Host"] = self.upstream_host

        try:
            connection = http.client.HTTPSConnection(
                self.upstream_host,
                timeout=UPSTREAM_TIMEOUT_SECONDS,
                context=self.tls_context,
            )
            connection.request(self.command, self.path, body=body, headers=headers)
            self._measure(body, exchange)
            upstream = connection.getresponse()
        except Exception:
            self.session.failed()
            try:
                self.send_error(502, "upstream unreachable")
            except Exception:
                return
            return

        exchange.status = upstream.status
        try:
            self._stream(upstream, exchange)
        finally:
            connection.close()
            self.session.record(exchange)

    def _measure(self, body: bytes, exchange: Exchange) -> None:
        try:
            measured = inspect_request(body, self.evaluate)
        except Exception:
            exchange.measured = False
            return
        exchange.model = measured.model
        exchange.sections = measured.sections
        exchange.entities = measured.entities
        exchange.at_files = measured.at_files
        exchange.measured = measured.measured

    def _stream(self, upstream, exchange: Exchange) -> None:
        self.send_response(upstream.status)
        for name, value in upstream.getheaders():
            lowered = name.lower()
            # Stream without deriving a length.
            if lowered in HOP_BY_HOP or lowered == "content-length":
                continue
            self.send_header(name, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        reader = UsageReader()
        # Relay compressed bytes unchanged.
        encoding = (upstream.getheader("Content-Encoding") or "").lower()
        decoder = None
        if encoding == "gzip":
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding in ("deflate", ""):
            decoder = zlib.decompressobj() if encoding else None

        while True:
            try:
                chunk = upstream.read1(CHUNK_BYTES)
            except Exception:
                self.session.failed()
                exchange.usage = reader.usage
                self.close_connection = True
                return
            if not chunk:
                break
            try:
                self.wfile.write(b"%x\r\n" % len(chunk))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            try:
                if decoder is None:
                    reader.feed(chunk.decode("utf-8", "replace"))
                else:
                    decoded = decoder.decompress(chunk)
                    while decoder.eof and decoder.unused_data:
                        leftover = decoder.unused_data
                        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                        decoded += decoder.decompress(leftover)
                    reader.feed(decoded.decode("utf-8", "replace"))
            except Exception:
                decoder = None
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        exchange.usage = reader.usage

    do_POST = _relay
    do_GET = _relay
    do_PUT = _relay
    do_DELETE = _relay
    do_PATCH = _relay


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        pass


@dataclass
class Watch:
    port: int
    session: Session
    _server: _Server
    _thread: threading.Thread
    _stopped: bool = False

    DRAIN_SECONDS = 5.0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._server.shutdown()
        self.session.drain(self.DRAIN_SECONDS)
        self._server.server_close()
        self._thread.join(timeout=5)


def start(upstream: str = "api.anthropic.com", evaluate=None) -> Watch:
    session = Session()
    handler = type(
        "_BoundHandler",
        (_Handler,),
        {
            "upstream_host": upstream,
            "session": session,
            "tls_context": ssl.create_default_context(),
            "evaluate": staticmethod(evaluate) if evaluate else None,
        },
    )
    # Loopback carries live credentials.
    server = _Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return Watch(server.server_address[1], session, server, thread)


__all__ = ["CHUNK_BYTES", "HOP_BY_HOP", "Session", "Watch", "start"]
