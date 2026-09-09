"""Turn an uploaded file into plain text. Drafts arrive as .docx far more often than .md."""
import io
import re
import zipfile


def _unescape(t: str) -> str:
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    return t


def from_docx(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"</w:tr>", "\n", xml)
    xml = re.sub(r"</w:tc>", " | ", xml)
    text = _unescape(re.sub(r"<[^>]+>", "", xml))
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln and ln != "|")


def from_bytes(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return from_docx(data)
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace").strip()


def guess_title(body: str, fallback: str = "Untitled") -> str:
    """First substantial line, minus any markdown heading marks."""
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if 8 <= len(line) <= 160 and not line.startswith(("|", "-", "*")):
            return line
    return fallback
