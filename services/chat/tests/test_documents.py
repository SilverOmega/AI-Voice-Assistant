import io
import zipfile

import httpx
import pytest
from openpyxl import Workbook

from app.chat_service import with_documents
from app.config import Settings
from app.documents import MAX_FILE_BYTES, DocumentError, direct_url, extract, fetch_link
from app.llm import LLMProvider, ProviderRegistry
from app.llm.ollama import OllamaProvider
from app.main import create_app


# ---- building real files in memory -------------------------------------------------


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page ('' = a page without text)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    for text in pages:
        page_no = len(objects) + 1
        kids.append(f"{page_no} 0 R")
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {page_no + 1} 0 R "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_zip(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in parts.items():
            zf.writestr(name, content)
    return buf.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
DOCX = make_zip({
    "word/document.xml": f"""<w:document {W}><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Quarterly report</w:t></w:r></w:p>
      <w:p><w:r><w:t xml:space="preserve">Revenue grew </w:t></w:r><w:r><w:t>12%.</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Region</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Sales</w:t></w:r></w:p></w:tc></w:tr>
             <w:tr><w:tc><w:p><w:r><w:t>North</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>420</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    </w:body></w:document>""",
})
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
PPTX = make_zip({
    "ppt/slides/slide2.xml": f"<p:sld xmlns:p='p' {A}><a:p><a:r><a:t>Next steps</a:t></a:r></a:p></p:sld>",
    "ppt/slides/slide10.xml": f"<p:sld xmlns:p='p' {A}><a:p><a:r><a:t>Thanks</a:t></a:r></a:p></p:sld>",
    "ppt/slides/slide1.xml": f"<p:sld xmlns:p='p' {A}><a:p><a:r><a:t>Welcome</a:t></a:r></a:p></p:sld>",
})
ODT = make_zip({
    "content.xml": """<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
      xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>
      <text:h>Minutes</text:h><text:p>Decided to <text:span>ship</text:span> on Friday.</text:p>
    </office:text></office:body></office:document-content>""",
})


def make_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Item", "Cost"])
    ws.append(["Laptop", 1200])
    ws.append([None, None])
    ws.append(["Total", "=SUM(B2:B2)"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- reading -----------------------------------------------------------------------


def test_reads_each_format():
    pdf = extract("report.pdf", make_pdf(["Hello from page one", "And page two"]))
    assert pdf.pages == 2 and pdf.media_type == "application/pdf"
    assert "[Page 1]\nHello from page one" in pdf.text and "[Page 2]\nAnd page two" in pdf.text

    docx = extract("report.docx", DOCX).text
    assert docx.startswith("# Quarterly report\nRevenue grew 12%.")
    assert "| Region | Sales |\n| North | 420 |" in docx

    xlsx = extract("budget.xlsx", make_xlsx())
    assert xlsx.pages == 1
    assert xlsx.text.startswith("[Sheet: Budget]\nItem,Cost\nLaptop,1200\nTotal,")

    pptx = extract("deck.pptx", PPTX)
    assert pptx.pages == 3
    assert pptx.text == "[Slide 1]\nWelcome\n\n[Slide 2]\nNext steps\n\n[Slide 3]\nThanks"

    assert extract("minutes.odt", ODT).text == "Minutes\nDecided to ship on Friday."
    assert extract("notes.md", "# Title\r\n\r\n\r\n\r\nBody".encode()).text == "# Title\n\nBody"
    assert extract("old.txt", "café".encode("cp1252")).text == "café"
    page = "<html><head><title>T</title><style>p{}</style></head><body><p>One</p><script>x()</script><p>Two</p></body></html>"
    assert extract("page.html", page.encode()).text == "One\n\nTwo"


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("scan.pdf", make_pdf(["", ""]), "no text layer"),
        ("fake.pdf", b"hello", "isn't a valid PDF"),
        ("letter.doc", b"\xd0\xcf\x11\xe0", "old Word format. Open it, save it as .docx"),
        ("sheet.xls", b"\xd0\xcf\x11\xe0", "old Excel format"),
        ("photo.heic", b"x", "can't be read"),
        ("blob.txt", b"\x00\x01" * 100, "binary"),
        ("empty.txt", b"", "empty"),
        ("broken.docx", b"PK\x03\x04garbage", "couldn't be read as a .docx"),
        ("notzip.xlsx", b"hello", "isn't a valid .xlsx"),
        ("big.txt", b"a" * (MAX_FILE_BYTES + 1), "larger than 20 MB"),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 40 else "",
)
def test_unreadable_files_explain_why(name, data, message):
    with pytest.raises(DocumentError, match=message):
        extract(name, data)


def test_xml_bombs_are_refused():
    bomb = '<!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><d>&b;</d>'
    with pytest.raises(DocumentError):
        extract("bomb.odt", make_zip({"content.xml": bomb}))


# ---- cloud links -------------------------------------------------------------------


def test_share_links_become_downloads():
    assert direct_url("https://docs.google.com/document/d/AbC_1-x/edit?usp=sharing") == (
        "https://docs.google.com/document/d/AbC_1-x/export?format=docx"
    )
    assert direct_url("https://docs.google.com/spreadsheets/d/S1/edit#gid=0").endswith("/S1/export?format=xlsx")
    assert direct_url("https://docs.google.com/presentation/d/P1/edit").endswith("/P1/export/pptx")
    assert direct_url("https://drive.google.com/file/d/F1/view?usp=drive_link") == (
        "https://drive.usercontent.google.com/download?id=F1&export=download&confirm=t"
    )
    assert "id=F2" in direct_url("https://drive.google.com/open?id=F2")
    assert direct_url("https://www.dropbox.com/scl/fi/x/plan.pdf?rlkey=k&dl=0") == (
        "https://www.dropbox.com/scl/fi/x/plan.pdf?rlkey=k&dl=1"
    )
    assert direct_url("https://1drv.ms/w/s!Abc").startswith("https://api.onedrive.com/v1.0/shares/u!aHR0cHM6Ly8xZHJ2Lm1zL3cvcyFBYmM/")
    assert direct_url("https://contoso.sharepoint.com/:w:/g/doc?e=1").endswith("?e=1&download=1")
    assert direct_url("https://example.com/a.pdf") == "https://example.com/a.pdf"


def fake_dns(table: dict[str, str]):
    async def resolve(host: str) -> list[str]:
        if host not in table:
            raise OSError("no such host")
        return [table[host]]

    return resolve


async def fetch(url: str, handler, dns=None) -> tuple[str, bytes]:
    dns = dns or {"files.example.com": "93.184.216.34", "docs.google.com": "142.250.1.1", "www.dropbox.com": "162.125.1.18"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await fetch_link(url, client, resolve=fake_dns(dns))


async def test_fetches_a_shared_file():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/export"):
            return httpx.Response(302, headers={"location": "https://files.example.com/dl"})
        return httpx.Response(
            200,
            content=DOCX,
            headers={"content-type": "application/octet-stream", "content-disposition": "attachment; filename*=UTF-8''B%C3%A1o%20c%C3%A1o.docx"},
        )

    name, data = await fetch("https://docs.google.com/document/d/D1/edit", handler)
    assert (name, data) == ("Báo cáo.docx", DOCX)
    assert seen == ["https://docs.google.com/document/d/D1/export?format=docx", "https://files.example.com/dl"]


async def test_a_web_page_link_is_read_as_a_page():
    page = b"<html><head><title>Release notes: v2</title></head><body><p>Fixed</p></body></html>"
    handler = lambda r: httpx.Response(200, content=page, headers={"content-type": "text/html; charset=utf-8"})
    name, data = await fetch("https://files.example.com/notes", handler)
    assert name == "Release notes v2.html" and data == page


@pytest.mark.parametrize(
    ("url", "handler", "message"),
    [
        ("http://192.168.1.1/admin", lambda r: httpx.Response(200), "local network"),
        ("http://localhost:8001/api/sessions", lambda r: httpx.Response(200), "local network"),
        ("ftp://files.example.com/a.pdf", lambda r: httpx.Response(200), "https://"),
        ("https://nowhere.invalid/a.pdf", lambda r: httpx.Response(200), "Couldn't find nowhere.invalid"),
        # A public link that redirects into the local network is stopped at the redirect.
        ("https://files.example.com/a", lambda r: httpx.Response(302, headers={"location": "http://10.0.0.5/x"}), "local network"),
        ("https://docs.google.com/document/d/D1/edit", lambda r: httpx.Response(200, content=b"<html>Sign in</html>", headers={"content-type": "text/html"}), "Anyone with the link"),
        ("https://docs.google.com/document/d/D1/edit", lambda r: httpx.Response(403), "Anyone with the link"),
        ("https://files.example.com/a", lambda r: httpx.Response(302, headers={"location": "/a"}), "redirects too many times"),
        ("https://files.example.com/big", lambda r: httpx.Response(200, content=b"x" * (MAX_FILE_BYTES + 10)), "larger than 20 MB"),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 60 else "",
)
async def test_bad_links_are_refused(url, handler, message):
    dns = {"files.example.com": "93.184.216.34", "docs.google.com": "142.250.1.1", "192.168.1.1": "192.168.1.1",
           "localhost": "127.0.0.1", "10.0.0.5": "10.0.0.5"}
    with pytest.raises(DocumentError, match=message):
        await fetch(url, handler, dns)


# ---- into the conversation ------------------------------------------------------------


def test_documents_go_before_the_question_within_a_budget():
    text, left = with_documents("What changed?", [("a.txt", "x" * 10), ("b <1>.txt", "y" * 10)], budget=15)
    assert left == 0
    assert text.startswith('<document name="a.txt">\nxxxxxxxxxx\n</document>\n\n<document name="b &lt;1&gt;.txt">\nyyyyy\n[Cut here: the remaining 5 characters')
    assert text.endswith("</document>\n\nWhat changed?")
    text, _ = with_documents("Hi", [("c.txt", "z")], budget=0)
    assert "[Not included" in text
    assert with_documents("Plain", [], budget=5) == ("Plain", 5)


def test_ollama_context_grows_for_long_prompts():
    ollama = OllamaProvider("http://x", "m", max_ctx=16384)
    short = [{"content": "hi"}]
    assert ollama._context_options(short, 4096) == {}  # ordinary chats keep Ollama's default
    assert ollama._context_options([{"content": "x" * 12_000}], 4096) == {"num_ctx": 8192}
    assert ollama._context_options([{"content": "x" * 30_000}], 4096) == {"num_ctx": 16384}
    assert ollama._context_options([{"content": "x" * 900_000}], 4096) == {"num_ctx": 16384}
    assert OllamaProvider("http://x", "m", max_ctx=0)._context_options([{"content": "x" * 90_000}], 4096) == {}


class RecordingProvider(LLMProvider):
    name = "ollama"
    default_model = "fake-model"

    def __init__(self):
        self.turns = []

    async def stream(self, turns, params):
        self.turns.append(turns)
        yield "Read it."

    async def complete_json(self, turns, params, schema):
        self.turns.append(turns)
        return {"title": "Notes", "content": "# Notes"}

    async def check(self, model):
        return True, "ok"


@pytest.fixture
async def env(tmp_path):
    provider = RecordingProvider()
    settings = Settings(database_path=str(tmp_path / "t.db"), llm_provider="ollama", document_max_chars=1000)
    app = create_app(settings, ProviderRegistry({"ollama": provider}))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c, provider


async def upload(client, name: str, data: bytes) -> httpx.Response:
    return await client.post("/api/files", params={"name": name}, content=data)


async def test_attach_a_document_and_ask_about_it(env):
    client, provider = env
    up = await upload(client, "report.pdf", make_pdf(["Revenue grew 12%"]))
    assert up.status_code == 201
    doc = up.json()
    assert (doc["name"], doc["media_type"], doc["pages"]) == ("report.pdf", "application/pdf", 1)
    assert doc["chars"] > 0 and doc["size"] > 0

    sid = (await client.post("/api/sessions", json={})).json()["id"]
    sent = await client.post(f"/api/sessions/{sid}/messages", json={"content": "", "files": [doc["id"]]})
    assert sent.status_code == 200, sent.text
    user = sent.json()["user_message"]
    assert user["files"] == [{"id": doc["id"], "name": "report.pdf", "media_type": "application/pdf", "size": doc["size"]}]
    (turn,) = provider.turns[-1]
    assert turn.content == '<document name="report.pdf">\n[Page 1]\nRevenue grew 12%\n</document>'

    session = (await client.get(f"/api/sessions/{sid}")).json()
    assert session["title"] == "report.pdf"
    assert session["messages"][0]["files"][0]["id"] == doc["id"]

    # The next question still sees the document.
    await client.post(f"/api/sessions/{sid}/messages", json={"content": "And costs?"})
    assert provider.turns[-1][0].content.startswith('<document name="report.pdf">')
    assert provider.turns[-1][-1].content == "And costs?"

    # So does a task drafted from the chat.
    await client.post(f"/api/sessions/{sid}/actions", json={"kind": "document", "instruction": "Notes please", "now": "2026-11-04T10:00"})
    assert "Revenue grew 12%" in provider.turns[-1][0].content

    download = await client.get(f"/api/files/{doc['id']}")
    assert download.content.startswith(b"%PDF")
    assert download.headers["content-disposition"] == "attachment; filename*=UTF-8''report.pdf"
    assert download.headers["content-security-policy"] == "sandbox"

    # Sent documents can't be attached a second time or removed.
    again = await client.post(f"/api/sessions/{sid}/messages", json={"content": "x", "files": [doc["id"]]})
    assert again.status_code == 400
    assert (await client.delete(f"/api/files/{doc['id']}")).status_code == 404


async def test_upload_rules(env):
    client, _ = env
    bad = await upload(client, "letter.doc", b"\xd0\xcf\x11\xe0")
    assert bad.status_code == 422 and "save it as .docx" in bad.json()["detail"]
    too_big = await upload(client, "big.txt", b"a" * (MAX_FILE_BYTES + 1))
    assert too_big.status_code == 413
    # Path parts in the name are dropped.
    odd = await upload(client, "..\\..\\secret/notes.txt", b"hi")
    assert odd.json()["name"] == "notes.txt"
    assert (await client.delete(f"/api/files/{odd.json()['id']}")).status_code == 204
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    gone = await client.post(f"/api/sessions/{sid}/messages", json={"content": "x", "files": [odd.json()["id"]]})
    assert gone.status_code == 400
    too_many = await client.post(f"/api/sessions/{sid}/messages", json={"content": "x", "files": ["a"] * 6})
    assert too_many.status_code == 422


async def test_long_documents_are_cut_to_the_budget(env):
    client, provider = env
    first = (await upload(client, "old.txt", b"o" * 900)).json()
    second = (await upload(client, "new.txt", b"n" * 900)).json()
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(f"/api/sessions/{sid}/messages", json={"content": "first", "files": [first["id"]]})
    await client.post(f"/api/sessions/{sid}/messages", json={"content": "second", "files": [second["id"]]})
    turns = provider.turns[-1]
    # Budget 1000: the newest document is whole, the older one gets the last 100 characters' room.
    assert "n" * 900 in turns[-1].content
    assert "o" * 100 + "\n[Cut here: the remaining 800 characters" in turns[0].content
