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
ALLOWED_URL_SCHEMES = {"http", "https"}
MAX_RECEIPT_BYTES = 5 * 1024 * 1024


class ReceiptError(Exception):
    pass


def receipts_dir() -> str:
    return current_app.config["RECEIPTS_DIR"]


def _unique_name(filename: str) -> str:
    return f"{uuid.uuid4().hex[:8]}-{secure_filename(filename) or 'receipt'}"


def _resolve_in_receipts_dir(name: str) -> str | None:
    """Resolve *name* inside the receipts dir, or return None if it would escape it."""
    base = os.path.realpath(receipts_dir())
    try:
        path = os.path.realpath(os.path.join(base, name))
    except ValueError:
        return None
    if os.path.commonpath((base, path)) != base:
        return None
    return path


def save_upload(upload) -> str:
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")
    name = _unique_name(upload.filename)
    upload.save(os.path.join(receipts_dir(), name))
    return name


def fetch_from_url(url: str) -> str:
    """Download an e-receipt (ride share, airline, hotel folio...) and store it like an upload."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
    except ValueError as exc:
        raise ReceiptError(f"Could not download the receipt: {exc}") from exc
    if scheme not in ALLOWED_URL_SCHEMES or not hostname:
        raise ReceiptError("Receipt links must be http:// or https:// URLs")
    try:
        with urllib.request.urlopen(parsed.geturl(), timeout=10) as resp:
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


def _bundle_target(filename: str) -> str | None:
    """Resolve a zip member name inside the receipts dir; None if it would escape."""
    rel = posixpath.normpath(filename.replace("\\", "/"))
    parts = rel.split("/")
    if rel.startswith("/") or any(part in ("", ".", "..") or ":" in part for part in parts):
        return None
    return os.path.join(receipts_dir(), *parts)


def _discard(paths: list[str]) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


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
            target = _resolve_in_receipts_dir(info.filename)
            if target is None:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            names.append(info.filename)
    return names


def receipt_path(name: str) -> str:
    return _resolve_in_receipts_dir(name) or os.path.realpath(receipts_dir())
