"""Forward every byte unchanged, and read a copy on the way past.

The whole risk of this feature is in one sentence: a hook that breaks fails
open and the agent keeps working, but a proxy that breaks fails closed and the
agent cannot reach the model at all. So the forwarding path has no opinions.
It does not retry, does not rewrite, does not decide, and does not invent a
response — an upstream error is relayed as the provider sent it (PRD-09 R7).

Measurement runs beside the copy, never in front of it. Every call into
`measure` is guarded, and a measurement that raises loses its numbers rather
than the request.

Standard library only, per R9: `http.server` and `http.client` are already
present everywhere the package installs, and the zipapp has nothing to vendor.
"""

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

#: Headers that describe one hop and must not be copied to the next.
#: Everything else is forwarded verbatim — R3 turns on this, because
#: `anthropic-beta` and `anthropic-version` carry an OAuth capability for
#: subscription sign-ins and a request without them fails with 401.
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

#: Upper bound on one relayed read, not a target. The read below is `read1`,
#: which returns whatever has arrived; plain `read(n)` blocks until it has all
#: `n` bytes, which for a server-sent-event stream means blocking until the
#: model has finished. That turns a streaming response into a buffered one and
#: destroys time to first token — the failure `test_the_stream_is_relayed_in_
#: pieces_rather_than_at_the_end` exists to catch.
CHUNK_BYTES = 65_536
UPSTREAM_TIMEOUT_SECONDS = 900


@dataclass
class Session:
    """Everything the report is built from. Holds numbers, never traffic."""

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
            self._in_flight = max(0, self._in_flight - 1)
            if not self._in_flight:
                self._idle.set()

    def drain(self, timeout: float) -> bool:
        """Wait for handlers still running. Returns False if any remain.

        A record is written after the last byte has been flushed to the client,
        so the client can finish a request microseconds before its numbers are
        counted. Without this the final request of a session — often the most
        expensive one — would be missing from the report.
        """
        return self._idle.wait(timeout)

    def record(self, exchange: Exchange) -> None:
        with self._lock:
            self.exchanges.append(exchange)

    def failed(self) -> None:
        with self._lock:
            self.errors += 1


class _Handler(http.server.BaseHTTPRequestHandler):
    # Keep-alive matters: the client opens one connection and streams many
    # requests down it, and closing after each would add a TLS handshake to
    # every turn.
    protocol_version = "HTTP/1.1"

    upstream_host = "api.anthropic.com"
    session: Session
    evaluate = None

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Stay silent. The client owns the terminal while it is running."""

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
                context=ssl.create_default_context(),
            )
            # Send first, measure second. The request is on its way before a
            # single byte of it is examined, so nothing here can sit between
            # the user and the model — the work overlaps the provider's own
            # thinking time instead of being added to it.
            connection.request(self.command, self.path, body=body, headers=headers)
            self._measure(body, exchange)
            upstream = connection.getresponse()
        except Exception:
            # R7: the provider's own failure shape is not ours to imitate, but
            # something has to be said or the client hangs. A 502 is the honest
            # answer to "the hop after me did not answer".
            self.session.failed()
            try:
                self.send_error(502, "upstream unreachable")
            except Exception:
                pass
            return

        exchange.status = upstream.status
        try:
            self._stream(upstream, exchange)
        finally:
            connection.close()
            self.session.record(exchange)

    def _measure(self, body: bytes, exchange: Exchange) -> None:
        """Fill in the request-side numbers. Never raises."""
        try:
            measured = inspect_request(body, self.evaluate)
        except Exception:
            exchange.measured = False
            return
        measured.path = exchange.path
        exchange.model = measured.model
        exchange.sections = measured.sections
        exchange.entities = measured.entities
        exchange.at_files = measured.at_files
        exchange.measured = measured.measured

    def _stream(self, upstream, exchange: Exchange) -> None:
        """Relay the response as it arrives, reading a decoded copy alongside."""
        self.send_response(upstream.status)
        for name, value in upstream.getheaders():
            lowered = name.lower()
            # Length is re-derived: the body is re-framed as chunked so that
            # nothing has to be held back to count it.
            if lowered in HOP_BY_HOP or lowered == "content-length":
                continue
            self.send_header(name, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        reader = UsageReader()
        # The stream arrives gzipped. The client is given the compressed bytes
        # exactly as they came, and a second, incremental decompressor feeds
        # the reader — so nothing is re-encoded and nothing is buffered.
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
                break
            if not chunk:
                break
            try:
                self.wfile.write(b"%x\r\n" % len(chunk))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                # The client hung up. Nothing left to forward to.
                return
            try:
                if decoder is None:
                    reader.feed(chunk.decode("utf-8", "replace"))
                else:
                    decoded = decoder.decompress(chunk)
                    # A member can end mid-stream when something upstream
                    # compressed per chunk rather than continuously. Carry on
                    # with a fresh decompressor instead of going quiet.
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
    # A client that vanishes mid-turn must not take the proxy down with it.
    allow_reuse_address = True

    def handle_error(self, request: object, client_address: object) -> None:
        """Swallow per-connection errors instead of printing a traceback.

        The client owns the terminal; a stack trace interleaved with its output
        looks like the agent broke.
        """


@dataclass
class Watch:
    """A running proxy and the numbers it has collected."""

    port: int
    session: Session
    _server: _Server
    _thread: threading.Thread
    _stopped: bool = False

    #: How long to wait for a request that is still being forwarded. Bounded,
    #: because handler threads are daemons on purpose: a wedged upstream must
    #: not be able to stop the command from exiting.
    DRAIN_SECONDS = 5.0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        """Stop serving, then let in-flight requests finish being counted."""
        if self._stopped:
            return
        self._stopped = True
        self._server.shutdown()
        self.session.drain(self.DRAIN_SECONDS)
        self._server.server_close()
        self._thread.join(timeout=5)


def start(upstream: str = "api.anthropic.com", evaluate=None) -> Watch:
    """Bind a proxy to a free loopback port and serve it on a background thread.

    Loopback only: this forwards a live credential, and binding it to anything
    reachable would hand that credential to the network. Port 0 asks the kernel
    for a free one, so two sessions never collide.

    Raises `OSError` if it cannot bind. `shim watch` must fail here, before the
    client starts, rather than leave a client pointed at a dead proxy (R7).
    """
    session = Session()
    handler = type(
        "_BoundHandler",
        (_Handler,),
        {
            "upstream_host": upstream,
            "session": session,
            "evaluate": staticmethod(evaluate) if evaluate else None,
        },
    )
    server = _Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return Watch(server.server_address[1], session, server, thread)


__all__ = ["CHUNK_BYTES", "HOP_BY_HOP", "Session", "Watch", "start"]
