"""Receipt file storage: uploads, e-receipt links and zip bundles."""

import os
import posixpath
import urllib.parse
import urllib.request
import uuid
import zipfile

from flask import current_app
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 100
MAX_BUNDLE_BYTES = 10 * MAX_RECEIPT_BYTES


class ReceiptError(Exception):
    pass


def receipts_dir() -> str:
    return current_app.config["RECEIPTS_DIR"]


def _unique_name(filename: str) -> str:
    return f"{uuid.uuid4().hex[:8]}-{secure_filename(filename) or 'receipt'}"


def _safe_target(filename: str) -> str | None:
    """Resolve ``filename`` to a path strictly inside the receipts directory.

    Returns None when the name is empty/dot-only or would escape the receipts
    directory (absolute paths, ``..`` components, symlinks), so callers never
    read or write outside it.
    """
    base = os.path.realpath(receipts_dir())
    target = os.path.realpath(os.path.join(base, filename))
    if target != base and os.path.commonpath((base, target)) == base:
        return target
    return None


def save_upload(upload) -> str:
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")
    name = _unique_name(upload.filename)
    upload.save(os.path.join(receipts_dir(), name))
    return name


def _validate_fetch_url(url: str) -> None:
    """Allow only public http(s) URLs, so receipt links cannot reach local,
    private, link-local or otherwise non-public hosts (or non-HTTP schemes)."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        raise ReceiptError("That receipt link is not a valid URL") from exc
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not parsed.hostname:
        raise ReceiptError("Receipt links must be http or https URLs")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ReceiptError("Could not resolve the receipt link host") from exc
    for _family, _type, _proto, _canonname, sockaddr in infos:
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise ReceiptError("Could not resolve the receipt link host") from exc
        if not addr.is_global:
            raise ReceiptError("That receipt link points at a local or private address")


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Apply the same URL policy to every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_fetch_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_CheckedRedirectHandler())


def fetch_from_url(url: str) -> str:
    """Download an e-receipt (ride share, airline, hotel folio...) and store it like an upload."""
    _validate_fetch_url(url)
    try:
        with _OPENER.open(url, timeout=10) as resp:
            data = resp.read(MAX_RECEIPT_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ReceiptError("Could not download the receipt") from exc
    if len(data) > MAX_RECEIPT_BYTES:
        raise ReceiptError("That receipt is larger than 5 MB")

    filename = posixpath.basename(urllib.parse.urlparse(url).path)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")
    name = _unique_name(filename)
    with open(os.path.join(receipts_dir(), name), "wb") as fh:
        fh.write(data)
    return name


def _discard(paths) -> None:
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
            ext = os.path.splitext(info.filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise ReceiptError(f"{ext or 'That file type'} is not a supported receipt format")
            name = _unique_name(os.path.basename(info.filename))
            with archive.open(info) as src, open(os.path.join(receipts_dir(), name), "wb") as dst:
                shutil.copyfileobj(src, dst)
            names.append(name)
    return names


def receipt_path(name: str) -> str:
    return os.path.join(receipts_dir(), secure_filename(name))
