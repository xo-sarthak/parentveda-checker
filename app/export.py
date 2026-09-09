"""DOCX and PDF export. Plain, readable, ready to hand on."""
import io
import re
from datetime import date

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

INK = RGBColor(0x19, 0x1A, 0x18)
MUTED = RGBColor(0x63, 0x66, 0x5F)


def _style(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.25


def _blocks(text: str):
    """Yield (kind, content) for markdown-ish source."""
    for raw in re.split(r"\n\s*\n", text):
        block = raw.strip()
        if not block:
            continue
        h = re.match(r"^(#{1,4})\s+(.*)$", block)
        if h:
            yield ("h", len(h.group(1)), h.group(2).strip())
            continue
        if all(re.match(r"^\s*([-*]|\d+\.)\s+", ln) for ln in block.splitlines()):
            for ln in block.splitlines():
                yield ("li", 0, re.sub(r"^\s*([-*]|\d+\.)\s+", "", ln).strip())
            continue
        yield ("p", 0, block)


def _emphasis(par, text: str) -> None:
    """Render **bold** without dragging in a markdown library."""
    for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not chunk:
            continue
        run = par.add_run(chunk)
        run.bold = i % 2 == 1


def to_docx(title: str, body: str, *, subtitle: str | None = None,
            footer: str | None = None) -> bytes:
    doc = Document()
    _style(doc)

    head = doc.add_paragraph()
    run = head.add_run(title)
    run.bold = True
    run.font.size = Pt(19)
    head.paragraph_format.space_after = Pt(4)

    if subtitle:
        sub = doc.add_paragraph()
        srun = sub.add_run(subtitle)
        srun.font.size = Pt(9.5)
        srun.font.color.rgb = MUTED
        sub.paragraph_format.space_after = Pt(16)

    first = True
    for kind, level, content in _blocks(body):
        if kind == "h":
            if first and content.strip().lower() == title.strip().lower():
                first = False
                continue
            p = doc.add_paragraph()
            r = p.add_run(content)
            r.bold = True
            r.font.size = Pt(14 if level <= 2 else 12)
            p.paragraph_format.space_before = Pt(14)
            p.paragraph_format.space_after = Pt(4)
        elif kind == "li":
            p = doc.add_paragraph(style="List Bullet")
            _emphasis(p, content)
        else:
            p = doc.add_paragraph()
            _emphasis(p, content)
        first = False

    if footer:
        doc.add_paragraph()
        f = doc.add_paragraph()
        fr = f.add_run(footer)
        fr.font.size = Pt(8.5)
        fr.font.color.rgb = MUTED
        f.alignment = WD_ALIGN_PARAGRAPH.LEFT

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def to_html(title: str, body: str, *, subtitle: str | None = None) -> str:
    """Printable HTML — the browser's own Save-as-PDF is better than a bundled engine."""
    parts = []
    for kind, level, content in _blocks(body):
        safe = (content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        if kind == "h":
            parts.append(f"<h{min(level + 1, 4)}>{safe}</h{min(level + 1, 4)}>")
        elif kind == "li":
            parts.append(f"<li>{safe}</li>")
        else:
            parts.append(f"<p>{safe}</p>")
    html_body = re.sub(r"(<li>.*?</li>)(?!\s*<li>)", r"<ul>\1</ul>",
                       "".join(parts), flags=re.S)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>
@page {{ margin: 22mm 20mm; }}
body {{ font: 11.5pt/1.6 Georgia, 'Times New Roman', serif; color:#191A18;
        max-width: 46em; margin: 0 auto; padding: 24px; }}
h1 {{ font-size: 20pt; margin: 0 0 4px; line-height:1.2 }}
h2 {{ font-size: 14pt; margin: 26px 0 6px }}
h3 {{ font-size: 12pt; margin: 20px 0 5px }}
p, li {{ margin: 0 0 9px }}
ul {{ padding-left: 20px }}
.sub {{ color:#63665F; font-size:9.5pt; font-family: system-ui, sans-serif;
        margin: 0 0 22px }}
@media print {{ body {{ padding:0 }} }}
</style></head><body>
<h1>{title}</h1>
{f'<div class="sub">{subtitle}</div>' if subtitle else ''}
{html_body}
</body></html>"""


def sheet_footer(specialty: str) -> str:
    return (f"Verification sheet · {specialty} · generated "
            f"{date.today().strftime('%d %b %Y')} · "
            "Approve ☐   Approve with changes ☐   Needs discussion ☐   "
            "Name ____________________   Date __________")
