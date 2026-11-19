"""Receipt file storage: uploads, e-receipt links and zip bundles."""

import http.client
import os
import posixpath
import shutil
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

from flask import current_app
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
INLINE_MIMETYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
}
MAX_RECEIPT_BYTES = 5 * 1024 * 1024


class ReceiptError(Exception):
    pass


def receipts_dir() -> str:
    return current_app.config["RECEIPTS_DIR"]


def _unique_name(filename: str) -> str:
    return f"{uuid.uuid4().hex[:8]}-{secure_filename(filename) or 'receipt'}"


def _checked_ext(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")
    return ext


def save_upload(upload) -> str:
    _checked_ext(upload.filename)
    name = _unique_name(upload.filename)
    upload.save(os.path.join(receipts_dir(), name))
    return name


def _response_socket(resp):
    """Best effort: the socket behind an urllib response, None when there isn't one."""
    fp = getattr(resp, "fp", None)  # buffered file (or the HTTPResponse on older Pythons)
    for candidate in (fp, getattr(fp, "fp", None)):
        raw = getattr(candidate, "raw", None)  # socket.SocketIO inside the buffered file
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            return sock
    return None


def _read_with_deadline(resp, max_bytes: int, deadline: float) -> bytes:
    """Read a response body, aborting once the monotonic deadline has passed.

    urllib's timeout only bounds each individual socket operation, so a peer
    that trickles one byte at a time can keep read() busy indefinitely. Reading
    in chunks (one underlying socket read per chunk via read1) and shrinking
    the socket timeout to the time remaining bounds the total transfer time.
    """
    read_chunk = resp.read1 if hasattr(resp, "read1") else resp.read
    chunks = []
    remaining = max_bytes + 1
    while remaining > 0:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("download timed out")
        sock = _response_socket(resp)
        if sock is not None:
            sock.settimeout(left)
        chunk = read_chunk(min(_READ_CHUNK, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def fetch_from_url(url: str) -> str:
    """Download an e-receipt (ride share, airline, hotel folio...) and store it like an upload."""
    try:
        deadline = time.monotonic() + FETCH_TIMEOUT
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
            data = _read_with_deadline(resp, MAX_RECEIPT_BYTES, deadline)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise ReceiptError(f"Could not download the receipt: {exc}") from exc
    if len(data) > MAX_RECEIPT_BYTES:
        raise ReceiptError("That receipt is larger than 5 MB")

    name = _unique_name(filename)
    with open(os.path.join(receipts_dir(), name), "wb") as fh:
        fh.write(data)
    return name


def _extract_member(archive, info, target: str) -> int:
    """Write one zip member to target, capping decompressed bytes at MAX_RECEIPT_BYTES."""
    written = 0
    with archive.open(info) as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(65536)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_RECEIPT_BYTES:
                break
            dst.write(chunk)
    if written > MAX_RECEIPT_BYTES:
        os.unlink(target)
        raise ReceiptError(f"{os.path.basename(info.filename)} is larger than 5 MB")
    return written


def extract_bundle(fileobj) -> list[str]:
    """Unpack a zip of receipts (e.g. exported from a phone's photo library)."""
    try:
        archive = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile as exc:
        raise ReceiptError("That file is not a zip archive") from exc

    names = []
    with archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename.startswith("__MACOSX"):
                continue
            ext = os.path.splitext(info.filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS or info.file_size > MAX_RECEIPT_BYTES:
                continue  # bundles hold the same receipt types as a direct upload
            name = _unique_name(info.filename)
            with archive.open(info) as src, open(os.path.join(receipts_dir(), name), "wb") as dst:
                shutil.copyfileobj(src, dst)
            names.append(name)
    return names


def receipt_path(name: str) -> str:
    """Absolute path of a stored receipt, confined to the receipts directory."""
    root = os.path.realpath(receipts_dir())
    path = os.path.realpath(os.path.join(root, name))
    if os.path.commonpath((root, path)) != root:
        raise ReceiptError("Unknown receipt")
    return path
