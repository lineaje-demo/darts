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
ALLOWED_URL_SCHEMES = {"http", "https"}


class ReceiptError(Exception):
    pass


def receipts_dir() -> str:
    return current_app.config["RECEIPTS_DIR"]


def _unique_name(filename: str) -> str:
    return f"{uuid.uuid4().hex[:8]}-{secure_filename(filename) or 'receipt'}"


def _safe_receipt_path(name: str) -> str:
    """Resolve ``name`` inside the receipts directory, refusing to escape it.

    Receipt names come from uploads, zip entries or URL query strings and are
    attacker-controlled, so ``..`` segments, absolute paths, Windows drive
    prefixes and backslash separators must not let a name land outside
    ``receipts_dir()``.
    """
    if not name or "\x00" in name:
        raise ReceiptError("Invalid receipt name")
    base = os.path.realpath(receipts_dir())
    target = os.path.realpath(os.path.join(base, name.replace("\\", "/")))
    if not target.startswith(base + os.sep):
        raise ReceiptError("Invalid receipt name")
    return target


def save_upload(upload) -> str:
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")
    name = _unique_name(upload.filename)
    upload.save(_safe_receipt_path(name))
    return name


def _validate_fetch_url(url: str) -> None:
    """Allow only http(s) URLs whose host resolves exclusively to public addresses."""
    try:
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ReceiptError("That receipt link is not a valid URL") from exc
    if scheme not in ALLOWED_URL_SCHEMES or not hostname:
        raise ReceiptError("Receipt links must be http or https URLs")
    try:
        addresses = socket.getaddrinfo(hostname, port or (443 if scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ReceiptError(f"Could not download the receipt: {exc}") from exc
    if not addresses:
        raise ReceiptError("Could not download the receipt: the host could not be resolved")
    for family, _type, _proto, _canon, sockaddr in addresses:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise ReceiptError("That receipt link is not allowed") from exc
        if not ip.is_global:
            raise ReceiptError("That receipt link points to a private or local address")


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Apply the same URL policy to every redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_fetch_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_fetch_opener = urllib.request.build_opener(_CheckedRedirectHandler())


def fetch_from_url(url: str) -> str:
    """Download an e-receipt (ride share, airline, hotel folio...) and store it like an upload."""
    _validate_fetch_url(url)
    try:
        with _fetch_opener.open(url, timeout=10) as resp:
            data = resp.read(MAX_RECEIPT_BYTES + 1)
    except ReceiptError:
        raise
    except (OSError, ValueError) as exc:
        raise ReceiptError(f"Could not download the receipt: {exc}") from exc
    if len(data) > MAX_RECEIPT_BYTES:
        raise ReceiptError("That receipt is larger than 5 MB")

    filename = posixpath.basename(urllib.parse.urlparse(url).path) or "receipt.html"
    name = _unique_name(filename)
    with open(_safe_receipt_path(name), "wb") as fh:
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
            try:
                target = receipt_path(info.filename)
            except ReceiptError as exc:
                raise ReceiptError("That zip contains an unsafe file path") from exc
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            names.append(info.filename)
    return names


def receipt_path(name: str) -> str:
    """Resolve ``name`` inside the receipts directory, refusing anything outside it."""
    base = os.path.realpath(receipts_dir())
    try:
        path = os.path.realpath(os.path.join(base, name))
    except (OSError, ValueError) as exc:
        raise ReceiptError(f"{name!r} is not a valid receipt name") from exc
    if os.path.commonpath((base, path)) != base:
        raise ReceiptError(f"{name!r} is not a valid receipt name")
    return path
