"""Compact catalogue: what a human would scan to spot overlap. Titles, summaries, headings."""
import json, re, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
arts = json.load(open('data/articles/published.json', encoding='utf-8'))
lines = []
for a in sorted(arts, key=lambda x: x['slug']):
    heads = re.findall(r'^## (.+)$', a['body'], re.M)
    heads = [h.strip() for h in heads if 3 < len(h.strip()) < 90][:9]
    lines.append(f"### {a['title']}")
    lines.append(f"url: {a['url']} · {a['words']} words")
    if a['summary']:
        lines.append(a['summary'])
    if heads:
        lines.append("covers: " + " | ".join(heads))
    lines.append("")
digest = "\n".join(lines)
open('data/articles/catalogue-digest.md', 'w', encoding='utf-8').write(digest)
print(f'{len(arts)} articles · {len(digest)} chars · ~{len(digest)//4} tokens')
