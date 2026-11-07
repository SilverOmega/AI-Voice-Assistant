"""Attached documents: the text a model can read, from PDF, Word, Excel, PowerPoint,
OpenDocument and plain-text files, and fetching a document from a cloud share link.

Every failure is a DocumentError whose message can be shown to the user as is.
"""

import asyncio
import base64
import csv
import io
import ipaddress
import re
import socket
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

import httpx
from defusedxml import ElementTree

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 5
MAX_TEXT_CHARS = 400_000  # kept per file; what reaches the model is capped per request
MAX_PDF_PAGES = 500
MAX_SHEET_ROWS = 5_000
_MAX_XML_BYTES = 60 * 1024 * 1024  # uncompressed, per part of a .docx/.xlsx/...: zip bomb guard


class DocumentError(ValueError):
    """A file or link that can't be used, with a message for the user."""


@dataclass(frozen=True)
class Extracted:
    media_type: str
    text: str
    pages: int | None = None  # PDF pages, slides or sheets


_TEXT_TYPES = {
    ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv",
    ".tsv": "text/tab-separated-values", ".json": "application/json", ".xml": "application/xml",
    ".yaml": "text/yaml", ".yml": "text/yaml", ".log": "text/plain", ".ini": "text/plain",
    ".toml": "text/plain", ".sql": "text/plain", ".py": "text/x-python", ".js": "text/javascript",
    ".ts": "text/plain", ".tsx": "text/plain", ".jsx": "text/plain", ".java": "text/plain",
    ".c": "text/plain", ".h": "text/plain", ".cpp": "text/plain", ".cs": "text/plain",
    ".go": "text/plain", ".rs": "text/plain", ".rb": "text/plain", ".php": "text/plain",
    ".sh": "text/plain", ".ps1": "text/plain", ".css": "text/css", ".srt": "text/plain",
    ".vtt": "text/vtt",
}
_HTML_TYPES = {".html": "text/html", ".htm": "text/html"}
_OFFICE_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}
MEDIA_TYPES = {**_TEXT_TYPES, **_HTML_TYPES, **_OFFICE_TYPES}
_LEGACY = {".doc": "Word", ".xls": "Excel", ".ppt": "PowerPoint", ".rtf": "Rich Text"}
_LEGACY_SAVE_AS = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx", ".rtf": ".docx"}


def extension(name: str) -> str:
    return PurePosixPath(name.lower()).suffix


def extract(name: str, data: bytes) -> Extracted:
    """The readable text of a document. Raises DocumentError."""
    ext = extension(name)
    if ext in _LEGACY:
        raise DocumentError(
            f"{name} is in the old {_LEGACY[ext]} format. Open it, save it as {_LEGACY_SAVE_AS[ext]} "
            "(or export a PDF), and attach that."
        )
    if ext not in MEDIA_TYPES:
        raise DocumentError(
            f"{name}: this kind of file can't be read. Attach a PDF, Word, Excel, PowerPoint, "
            "OpenDocument or text file."
        )
    if not data:
        raise DocumentError(f"{name} is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    media_type = MEDIA_TYPES[ext]
    if ext == ".pdf":
        text, pages = _pdf(name, data)
    elif ext in _OFFICE_TYPES:
        text, pages = _office(name, ext, data)
    elif ext in _HTML_TYPES:
        text, pages = html_text(_decode(name, data)), None
    else:
        text, pages = _decode(name, data), None
    text = _tidy(text)
    if not text:
        raise DocumentError(f"{name} has no text in it.")
    return Extracted(media_type, text[:MAX_TEXT_CHARS], pages)


def _tidy(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode(name: str, data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data[:4096].count(b"\x00") > 8:
        raise DocumentError(f"{name} looks like a binary file, not text.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# ---- PDF -------------------------------------------------------------------------


def _pdf(name: str, data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if b"%PDF" not in data[:1024]:
        raise DocumentError(f"{name} isn't a valid PDF file.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError(f"{name} is password-protected. Remove the password and attach it again.")
        total = len(reader.pages)
        texts = [(reader.pages[i].extract_text() or "").strip() for i in range(min(total, MAX_PDF_PAGES))]
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise DocumentError(f"{name} couldn't be read as a PDF ({exc}).") from None
    if not any(texts):
        raise DocumentError(
            f"{name} has no text layer: it's probably a scan. Send the pages as pictures to a vision "
            "model instead, or run it through OCR first."
        )
    parts = [f"[Page {number}]\n{text}" for number, text in enumerate(texts, start=1)]
    if total > MAX_PDF_PAGES:
        parts.append(f"[The PDF continues for {total - MAX_PDF_PAGES} more pages, not read.]")
    return "\n\n".join(parts), total


# ---- Office Open XML and OpenDocument ---------------------------------------------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
DRAW_NS = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"


def _office(name: str, ext: str, data: bytes) -> tuple[str, int | None]:
    if not data.startswith(b"PK\x03\x04"):
        raise DocumentError(f"{name} isn't a valid {ext} file.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if ext == ".docx":
                return _docx(zf), None
            if ext == ".pptx":
                return _pptx(zf)
            if ext == ".xlsx":
                return _xlsx(data)
            return _odf(zf, ext)
    except DocumentError:
        raise
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError, OSError) as exc:
        raise DocumentError(f"{name} couldn't be read as a {ext} file ({exc}).") from None


def _xml(zf: zipfile.ZipFile, part: str):
    info = zf.getinfo(part)
    if info.file_size > _MAX_XML_BYTES:
        raise DocumentError("The document is too large to read.")
    with zf.open(info) as f:
        raw = f.read(_MAX_XML_BYTES + 1)
    if len(raw) > _MAX_XML_BYTES:
        raise DocumentError("The document is too large to read.")
    return ElementTree.fromstring(raw)


def _docx(zf: zipfile.ZipFile) -> str:
    body = _xml(zf, "word/document.xml").find(f"{W}body")
    lines: list[str] = []
    for block in body if body is not None else []:
        if block.tag == f"{W}p":
            lines.append(_docx_paragraph(block))
        elif block.tag == f"{W}tbl":
            for row in block.iter(f"{W}tr"):
                cells = [" ".join(_docx_paragraph(p) for p in cell.iter(f"{W}p")).strip() for cell in row.iter(f"{W}tc")]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    return "\n".join(lines)


def _docx_paragraph(p) -> str:
    text = "".join(
        node.text or "" if node.tag == f"{W}t" else "\t" if node.tag == f"{W}tab" else "\n"
        for node in p.iter()
        if node.tag in (f"{W}t", f"{W}tab", f"{W}br", f"{W}cr")
    )
    style = p.find(f"{W}pPr/{W}pStyle")
    level = style.get(f"{W}val", "") if style is not None else ""
    if match := re.fullmatch(r"Heading([1-6])", level):
        return "#" * int(match.group(1)) + " " + text
    if level == "Title":
        return "# " + text
    return text


def _pptx(zf: zipfile.ZipFile) -> tuple[str, int]:
    slides = sorted(
        (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
    )
    parts = []
    for number, part in enumerate(slides, start=1):
        paragraphs = ["".join(t.text or "" for t in p.iter(f"{A}t")) for p in _xml(zf, part).iter(f"{A}p")]
        parts.append(f"[Slide {number}]\n" + "\n".join(p for p in paragraphs if p.strip()))
    return "\n\n".join(parts), len(slides)


def _xlsx(data: bytes) -> tuple[str, int]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        parts = []
        for sheet in workbook.worksheets:
            out = io.StringIO()
            writer = csv.writer(out, lineterminator="\n")
            rows = 0
            for row in sheet.iter_rows(values_only=True):
                if rows >= MAX_SHEET_ROWS:
                    out.write(f"[The sheet continues past {MAX_SHEET_ROWS} rows, not read.]\n")
                    break
                if any(v not in (None, "") for v in row):
                    writer.writerow(["" if v is None else v for v in row])
                    rows += 1
            parts.append(f"[Sheet: {sheet.title}]\n{out.getvalue()}")
        return "\n\n".join(parts), len(workbook.worksheets)
    finally:
        workbook.close()


def _odf(zf: zipfile.ZipFile, ext: str) -> tuple[str, int | None]:
    root = _xml(zf, "content.xml")
    lines: list[str] = []
    count = 0

    def walk(node) -> None:
        nonlocal count
        if node.tag == f"{TABLE_NS}table" and ext == ".ods":
            count += 1
            lines.append(f"\n[Sheet: {node.get(f'{TABLE_NS}name', count)}]")
        if node.tag == f"{DRAW_NS}page":
            count += 1
            lines.append(f"\n[Slide {count}]")
        if node.tag == f"{TABLE_NS}table-row":
            cells = ["".join(c.itertext()).strip() for c in node if c.tag == f"{TABLE_NS}table-cell"]
            if any(cells):
                lines.append("| " + " | ".join(cells) + " |")
            return
        if node.tag in (f"{TEXT_NS}p", f"{TEXT_NS}h"):
            lines.append("".join(node.itertext()))
            return
        for child in node:
            walk(child)

    walk(root)
    return "\n".join(lines), count or None


# ---- HTML ------------------------------------------------------------------------


class _HTMLText(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _BREAKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"[ \t]{2,}", " ", text)


def html_title(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html[:200_000])
    return " ".join(parser.title.split())


# ---- cloud share links -----------------------------------------------------------


def direct_url(url: str) -> str:
    """The download address behind a share link. Other links are returned unchanged.

    Google Docs, Sheets and Slides are exported as Word, Excel and PowerPoint files; Drive,
    Dropbox and OneDrive links point at the file itself. The file must be shared with
    "anyone with the link": the assistant can't sign in to your account.
    """
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    path = parts.path
    if host == "docs.google.com":
        for kind, export in (
            ("document", "export?format=docx"),
            ("spreadsheets", "export?format=xlsx"),
            ("presentation", "export/pptx"),
        ):
            if match := re.match(rf"/{kind}/d/([\w-]+)", path):
                return f"https://docs.google.com/{kind}/d/{match.group(1)}/{export}"
    if host == "drive.google.com":
        file_id = None
        if match := re.match(r"/file/d/([\w-]+)", path):
            file_id = match.group(1)
        elif path in ("/open", "/uc"):
            file_id = (parse_qs(parts.query).get("id") or [None])[0]
        if file_id:
            return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    if host in ("www.dropbox.com", "dropbox.com"):
        query = {k: v for k, v in parse_qs(parts.query).items() if k != "dl"}
        query["dl"] = ["1"]
        return urlunparse(parts._replace(query=urlencode(query, doseq=True)))
    if host in ("1drv.ms", "onedrive.live.com"):
        # OneDrive's sharing API takes the share link itself, base64url-encoded.
        token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        return f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content"
    if host.endswith(".sharepoint.com") and "download=1" not in parts.query:
        query = f"{parts.query}&download=1" if parts.query else "download=1"
        return urlunparse(parts._replace(query=query))
    return url


_CLOUD_HOSTS = ("docs.google.com", "drive.google.com", "drive.usercontent.google.com", "dropbox.com",
                "1drv.ms", "onedrive.live.com", "api.onedrive.com", "sharepoint.com")

Resolver = Callable[[str], Awaitable[list[str]]]


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def _check_public(url: str, resolve: Resolver) -> None:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise DocumentError("Paste a link that starts with https://.")
    try:
        addresses = await resolve(parts.hostname)
    except OSError:
        raise DocumentError(f"Couldn't find {parts.hostname}. Check the link.") from None
    # The assistant runs next to your other devices and its own services: links into this
    # computer or the local network are not fetched (someone could slip one into a document).
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise DocumentError("Links to this computer or your local network can't be fetched. Download the file and attach it instead.")


def _filename(resp: httpx.Response, url: str) -> str:
    header = resp.headers.get("content-disposition", "")
    if header:
        msg = Message()
        msg["content-disposition"] = header
        if name := msg.get_filename():
            return PurePosixPath(name.replace("\\", "/")).name
    return unquote(PurePosixPath(urlparse(url).path).name)


_EXT_FOR_TYPE = {
    "application/pdf": ".pdf", "text/html": ".html", "text/plain": ".txt", "text/csv": ".csv",
    "text/markdown": ".md", "application/json": ".json", **{v: k for k, v in _OFFICE_TYPES.items()},
}


async def fetch_link(
    url: str, client: httpx.AsyncClient, resolve: Resolver = _resolve
) -> tuple[str, bytes]:
    """(file name, bytes) of the document behind a link. Raises DocumentError."""
    url = url.strip()
    target = direct_url(url)
    cloud = any((urlparse(url).hostname or "").endswith(h) for h in _CLOUD_HOSTS)
    # The link plus up to 5 redirects, each checked before it's followed. (A host that changes
    # its DNS answer between the check and the request could still slip through; the user
    # pastes these links themselves, so that's an accepted gap.)
    for _ in range(6):
        await _check_public(target, resolve)
        try:
            async with client.stream("GET", target, follow_redirects=False) as resp:
                if resp.is_redirect and "location" in resp.headers:
                    target = urljoin(target, resp.headers["location"])
                    continue
                if resp.status_code in (401, 403, 404):
                    raise DocumentError(
                        "The file couldn't be opened from that link. Share it as \"Anyone with the link\" "
                        "(or download it and attach it from your device)."
                        if cloud
                        else f"The link answered {resp.status_code}: the file isn't public or doesn't exist."
                    )
                if resp.status_code >= 400:
                    raise DocumentError(f"The link answered with an error ({resp.status_code}).")
                size = int(resp.headers.get("content-length") or 0)
                if size > MAX_FILE_BYTES:
                    raise DocumentError(f"That file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
                chunks, total = [], 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise DocumentError(f"That file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
                    chunks.append(chunk)
                data = b"".join(chunks)
                media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                name = _filename(resp, target)
        except httpx.TimeoutException:
            raise DocumentError("The link took too long to answer.") from None
        except httpx.HTTPError as exc:
            raise DocumentError(f"The link couldn't be opened ({exc.__class__.__name__}).") from None
        break
    else:
        raise DocumentError("The link redirects too many times.")

    if media_type == "text/html":
        if cloud:
            # A sign-in or "can't scan for viruses" page instead of the file.
            raise DocumentError(
                "The cloud service showed a web page instead of the file. Share it as \"Anyone with the "
                "link\", or download it and attach it from your device."
            )
        title = html_title(_decode("page", data)) or urlparse(url).hostname or "page"
        name = " ".join(re.sub(r'[\\/:*?"<>|]+', " ", title).split())[:80] + ".html"
    elif extension(name) not in MEDIA_TYPES and media_type in _EXT_FOR_TYPE:
        name = (name or "document") + _EXT_FOR_TYPE[media_type]
    return name or "document", data
