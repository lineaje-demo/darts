"""Receipt file storage: uploads, e-receipt links and zip bundles."""

import ipaddress
import os
import posixpath
import shutil
import socket
import urllib.parse
import urllib.request
import uuid
import zipfile

from flask import current_app
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
ALLOWED_FETCH_SCHEMES = {"http", "https"}


class ReceiptError(Exception):
    pass


def receipts_dir() -> str:
    return current_app.config["RECEIPTS_DIR"]


def _validate_fetch_url(url: str) -> None:
    """Receipt links must be public http(s) URLs: no file:/ftp:/... schemes and no
    local, private, link-local or otherwise non-public addresses."""
    try:
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ReceiptError("That receipt link is not a valid URL.") from exc
    if scheme not in ALLOWED_FETCH_SCHEMES or not host:
        raise ReceiptError("Receipt links must be http:// or https:// URLs.")
    try:
        addrinfos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError) as exc:
        raise ReceiptError(f"Could not resolve the receipt host: {exc}") from exc
    for info in addrinfos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ReceiptError("That receipt link points to a local or private address.")


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Apply the same URL policy to every redirect hop, so a validated URL cannot
    bounce the fetch to a file: URL or an internal address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_fetch_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_fetch_opener = urllib.request.build_opener(_CheckedRedirectHandler())


def _unique_name(filename: str) -> str:
    return f"{uuid.uuid4().hex[:8]}-{secure_filename(filename) or 'receipt'}"


def _check_extension(filename: str) -> None:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")


def save_upload(upload) -> str:
    _check_extension(upload.filename)
    name = _unique_name(upload.filename)
    upload.save(os.path.join(receipts_dir(), name))
    return name


def fetch_from_url(url: str) -> str:
    """Download an e-receipt (ride share, airline, hotel folio...) and store it like an upload."""
    _validate_fetch_url(url)
    try:
        with _fetch_opener.open(url, timeout=10) as resp:
            data = resp.read(MAX_RECEIPT_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ReceiptError(f"Could not download the receipt: {exc}") from exc
    if len(data) > MAX_RECEIPT_BYTES:
        raise ReceiptError("That receipt is larger than 5 MB")

    filename = posixpath.basename(urllib.parse.urlparse(url).path) or "receipt"
    _check_extension(filename)
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
    with archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename.startswith("__MACOSX"):
                continue
            _check_extension(info.filename)
            target = os.path.join(receipts_dir(), info.filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            names.append(info.filename)
    return names


def receipt_path(name: str) -> str:
    """Resolve ``name`` inside the receipts directory.

    Raises ReceiptError if ``name`` is empty or resolves outside the
    receipts directory (absolute path, ``..`` traversal, symlink escape).
    """
    if not name or "\x00" in name:
        raise ReceiptError("Invalid receipt file name")
    base = os.path.realpath(receipts_dir())
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(base + os.sep):
        raise ReceiptError("Invalid receipt file name")
    return path
