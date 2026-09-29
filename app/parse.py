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


# Headings that mark the end of the article and the start of internal
# production material. Everything from the first of these onward is for the
# team, never for a parent, and is not reviewed.
INTERNAL_MARKERS = (
    "paste-safe table source",
    "change log",
    "changelog",
    "seo pack",
    "doctor validation table",
    "clinician validation",
    "publishing checklist",
    "visual experience recommendations",
    "internal linking suggestions",
    "quality report",
    "auto-gate",
    "auto gate",
    "handoff",
    "hand-off",
    "notes for the editor",
    "editor notes",
    "revert note",
)


# Headings that open the image or visual plan for an article. They are
# internal (not article text) but the judge must see them: an image prompt
# becomes an image, and a bad prompt is a bad visual.
VISUAL_HEADINGS = ("visual experience recommendation", "visual recommendation",
                   "visual plan", "visuals", "image prompt", "image prompts",
                   "image brief", "image briefs", "cover image", "images",
                   "illustration", "illustrations", "infographic")


def _heading(raw: str) -> str | None:
    line = raw.strip().lstrip("#").strip().strip("*_").rstrip(":").strip()
    return line.lower() if line and len(line) <= 70 else None


def extract_visuals(internal: str) -> str:
    """The image prompts and visual plan from an article's internal sections,
    as one block. Empty if the draft has none."""
    if not internal:
        return ""
    lines, out, keep = internal.splitlines(), [], False
    for raw in lines:
        h = _heading(raw)
        if h is not None:
            if any(h.startswith(v) for v in VISUAL_HEADINGS):
                keep = True
            elif any(h.startswith(m) for m in INTERNAL_MARKERS):
                keep = False
        if keep:
            out.append(raw)
    return "\n".join(out).strip()


def split_internal(body: str) -> tuple[str, str]:
    """Return (what a reader would see, what is internal).

    Cuts at the first internal heading. A heading is a short line — anything
    long enough to be a sentence is prose that happens to mention the words.
    """
    lines = body.splitlines()
    for i, raw in enumerate(lines):
        low = _heading(raw)
        if low is None:
            continue
        if any(low.startswith(m) or low == m for m in INTERNAL_MARKERS + VISUAL_HEADINGS[:6]):
            return "\n".join(lines[:i]).rstrip(), "\n".join(lines[i:]).strip()
    return body, ""


def guess_title(body: str, fallback: str = "Untitled") -> str:
    """First substantial line, minus any markdown heading marks."""
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if 8 <= len(line) <= 160 and not line.startswith(("|", "-", "*")):
            return line
    return fallback
