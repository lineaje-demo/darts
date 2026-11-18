"""Receipt file storage: uploads, e-receipt links and zip bundles."""

import http.client
import io
import os
import posixpath
import shutil
import socket
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

from flask import current_app
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT = 10  # per-socket-operation timeout (connect, individual reads)
FETCH_TOTAL_SECONDS = 15  # wall-clock budget for the whole receipt transfer


class ReceiptError(Exception):
    pass


def receipts_dir() -> str:
    return current_app.config["RECEIPTS_DIR"]


def _contained_path(name: str) -> str:
    """Resolve *name* inside the receipts directory, rejecting anything that escapes it."""
    base = os.path.realpath(receipts_dir())
    target = os.path.realpath(os.path.join(base, name))
    if not target.startswith(base + os.sep):
        raise ReceiptError("That receipt file name is not allowed")
    return target


def _unique_name(filename: str) -> str:
    return f"{uuid.uuid4().hex[:8]}-{secure_filename(filename) or 'receipt'}"


def _discard(paths) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def save_upload(upload) -> str:
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")
    name = _unique_name(upload.filename)
    upload.save(os.path.join(receipts_dir(), name))
    return name


class _DeadlineReader(io.RawIOBase):
    """Raw read adapter over a socket that fails once a wall-clock deadline passes."""

    def __init__(self, sock, deadline):
        self._sock = sock
        self._deadline = deadline

    def readable(self):
        return True

    def readinto(self, b):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("timed out")
        self._sock.settimeout(min(FETCH_TIMEOUT, remaining))
        return self._sock.recv_into(b)

    def close(self):
        try:
            self._sock.close()
        finally:
            super().close()


class _DeadlineSocket:
    """Socket facade giving one fetch a single wall-clock deadline.

    urllib's ``timeout`` only bounds each individual socket operation, so a
    server that drip-feeds one byte per interval satisfies it forever while
    the request stays blocked. This facade makes every read or write fail
    with ``socket.timeout`` once the deadline passes, no matter which phase
    of the fetch (headers, redirects, body) is in progress.
    """

    def __init__(self, sock, deadline):
        self._sock = sock
        self._deadline = deadline

    def _remaining(self):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("timed out")
        return remaining

    def sendall(self, data):
        self._sock.settimeout(min(FETCH_TIMEOUT, self._remaining()))
        self._sock.sendall(data)

    def makefile(self, mode, *args, **kwargs):
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline))

    def close(self):
        pass  # urllib closes the connection early; the response reader owns the real socket

    def __getattr__(self, name):
        return getattr(self._sock, name)


def _deadline_connection(base, deadline):
    class DeadlineConnection(base):
        def connect(self):
            super().connect()
            if self.sock is not None:
                self.sock = _DeadlineSocket(self.sock, deadline)

    return DeadlineConnection


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline):
        super().__init__()
        self._deadline = deadline

    def http_open(self, req):
        return self.do_open(_deadline_connection(http.client.HTTPConnection, self._deadline), req)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, deadline):
        super().__init__()
        self._deadline = deadline

    def https_open(self, req):
        return self.do_open(_deadline_connection(http.client.HTTPSConnection, self._deadline), req)


def fetch_from_url(url: str) -> str:
    """Download an e-receipt (ride share, airline, hotel folio...) and store it like an upload."""
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ReceiptError("Receipt links must be http:// or https:// URLs")
    deadline = time.monotonic() + FETCH_TOTAL_SECONDS
    opener = urllib.request.build_opener(
        _DeadlineHTTPHandler(deadline), _DeadlineHTTPSHandler(deadline)
    )
    try:
        with opener.open(url, timeout=FETCH_TIMEOUT) as resp:
            data = resp.read(MAX_RECEIPT_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ReceiptError(f"Could not download the receipt: {exc}") from exc
    if len(data) > MAX_RECEIPT_BYTES:
        raise ReceiptError("That receipt is larger than 5 MB")

    filename = posixpath.basename(urllib.parse.urlparse(url).path) or "receipt.html"
    name = _unique_name(filename)
    with open(os.path.join(receipts_dir(), name), "wb") as fh:
        fh.write(data)
    return name


def extract_bundle(fileobj) -> list[str]:
    """Unpack a zip of receipts (e.g. exported from a phone's photo library)."""
    try:
        archive = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile as exc:
        raise ReceiptError("That file is not a zip archive") from exc

    os.makedirs(receipts_dir(), exist_ok=True)
    names = []
    targets = []
    total_bytes = 0
    max_mb = MAX_RECEIPT_BYTES // (1024 * 1024)
    try:
        with archive:
            for info in archive.infolist():
                if info.is_dir() or info.filename.startswith("__MACOSX"):
                    continue
                base = posixpath.basename(info.filename.replace("\\", "/"))
                if not base or not secure_filename(base):
                    continue
                if len(names) >= MAX_BUNDLE_ENTRIES:
                    raise ReceiptError(f"That bundle holds more than {MAX_BUNDLE_ENTRIES} files")
                if info.file_size > MAX_RECEIPT_BYTES:
                    raise ReceiptError(f"{secure_filename(base)} is larger than {max_mb} MB")
                name = _unique_name(base)
                target = os.path.join(receipts_dir(), name)
                targets.append(target)
                with archive.open(info) as src, open(target, "wb") as dst:
                    file_bytes = 0
                    while True:
                        chunk = src.read(256 * 1024)
                        if not chunk:
                            break
                        file_bytes += len(chunk)
                        total_bytes += len(chunk)
                        if file_bytes > MAX_RECEIPT_BYTES:
                            raise ReceiptError(f"{secure_filename(base)} is larger than {max_mb} MB")
                        if total_bytes > MAX_BUNDLE_TOTAL_BYTES:
                            raise ReceiptError(
                                f"That bundle unpacks to more than "
                                f"{MAX_BUNDLE_TOTAL_BYTES // (1024 * 1024)} MB"
                            )
                        dst.write(chunk)
                names.append(name)
    except ReceiptError:
        _discard(targets)
        raise
    except (zipfile.BadZipFile, RuntimeError) as exc:
        _discard(targets)
        raise ReceiptError("That zip archive is corrupt, truncated or encrypted") from exc
    return names


def receipt_path(name: str) -> str:
    return _resolve_in_receipts(name)
