"""Receipt file storage: uploads, e-receipt links and zip bundles."""

import os
import posixpath
import urllib.parse
import urllib.request
import uuid
import zipfile

from flask import current_app
from werkzeug.utils import safe_join, secure_filename

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 200
MAX_BUNDLE_BYTES = 20 * 1024 * 1024


class ReceiptError(Exception):
    pass


def receipts_dir() -> str:
    return current_app.config["RECEIPTS_DIR"]


def _unique_name(filename: str) -> str:
    return f"{uuid.uuid4().hex[:8]}-{secure_filename(filename) or 'receipt'}"


def save_upload(upload) -> str:
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")
    name = _unique_name(upload.filename)
    upload.save(os.path.join(receipts_dir(), name))
    return name


def _read_body(resp, deadline: float) -> bytes:
    """Read a response body, enforcing a deadline on the total transfer time.

    urlopen's timeout only bounds each individual blocking socket operation,
    so a server that trickles one byte at a time would otherwise keep the
    request open almost indefinitely. read1() performs at most one raw socket
    read per call, so the deadline is re-checked whenever data arrives.
    """
    read = getattr(resp, "read1", resp.read)
    chunks = []
    total = 0
    while True:
        if time.monotonic() >= deadline:
            raise ReceiptError("The receipt server took too long to respond")
        chunk = read(_READ_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RECEIPT_BYTES:
            raise ReceiptError("That receipt is larger than 5 MB")
    return b"".join(chunks)


def fetch_from_url(url: str) -> str:
    """Download an e-receipt (ride share, airline, hotel folio...) and store it like an upload."""
    try:
        deadline = time.monotonic() + FETCH_TIMEOUT
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
            data = _read_body(resp, deadline)
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

    names = []
    paths = []
    total_bytes = 0
    try:
        with archive:
            for info in archive.infolist():
                if info.is_dir() or info.filename.startswith("__MACOSX"):
                    continue
                label = os.path.basename(info.filename) or "receipt"
                if len(names) >= MAX_BUNDLE_MEMBERS:
                    raise ReceiptError(f"That bundle holds more than {MAX_BUNDLE_MEMBERS} files")
                if info.file_size > MAX_RECEIPT_BYTES:
                    raise ReceiptError(f"{label} is larger than the 5 MB receipt limit")
                target = os.path.join(receipts_dir(), info.filename)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                paths.append(target)
                member_bytes = 0
                with archive.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        member_bytes += len(chunk)
                        total_bytes += len(chunk)
                        if member_bytes > MAX_RECEIPT_BYTES:
                            raise ReceiptError(f"{label} is larger than the 5 MB receipt limit")
                        if total_bytes > MAX_BUNDLE_BYTES:
                            raise ReceiptError("That bundle is too large once unzipped")
                        dst.write(chunk)
                names.append(info.filename)
    except ReceiptError:
        _remove_extracted(paths)
        raise
    except zipfile.BadZipFile as exc:
        _remove_extracted(paths)
        raise ReceiptError("That zip archive is corrupt") from exc
    return names


def _remove_extracted(paths) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def receipt_path(name: str) -> str:
    """Resolve ``name`` to a path inside the receipts directory.

    Raises ReceiptError if ``name`` is absolute or otherwise resolves to a
    location outside the receipts directory.
    """
    base = os.path.realpath(receipts_dir())
    try:
        path = os.path.realpath(os.path.join(base, name))
        contained = os.path.commonpath((base, path)) == base
    except ValueError:
        # Embedded null byte or otherwise unresolvable name.
        contained = False
    if not contained:
        raise ReceiptError(f"{name!r} is not a valid receipt name")
    return path
