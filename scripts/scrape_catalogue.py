"""Pull published ParentVeda articles for duplication detection + internal links."""
import urllib.request, re, html, json, io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = 'https://parentveda.in'
INDEX = BASE + '/reads/articles/'

def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'ParentVedaChecker/1.0'})
    return urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')

def clean(fragment):
    s = re.sub(r'<script.*?</script>|<style.*?</style>', '', fragment, flags=re.S)
    s = re.sub(r'<(h[1-6])[^>]*>', '\n\n## ', s)
    s = re.sub(r'</h[1-6]>', '\n', s)
    s = re.sub(r'</(p|li|tr|div|section)>', '\n', s)
    s = re.sub(r'<li[^>]*>', '- ', s)
    s = html.unescape(re.sub(r'<[^>]+>', ' ', s))
    s = re.sub(r'[ \t]+', ' ', s)
    return re.sub(r'\n\s*\n\s*\n+', '\n\n', s).strip()

idx = get(INDEX)
slugs = sorted(set(re.findall(r'/reads/articles/([a-z0-9-]+)/', idx)))
print(f'found {len(slugs)} slugs\n')

out = []
for i, slug in enumerate(slugs, 1):
    try:
        raw = get(f'{BASE}/reads/articles/{slug}/')
        m = re.search(r'<main[^>]*>(.*?)</main>', raw, re.S)
        body = clean(m.group(1) if m else raw)
        # trim trailing site chrome
        for cut in ['Want this gentle guidance', 'RELATED READS', 'Read more about Dr']:
            j = body.find(cut, 500)
            if j > 0:
                body = body[:j]
        tm = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
        title = html.unescape(tm.group(1)).split('·')[0].strip() if tm else slug
        dm = re.search(r'<meta name="description" content="([^"]*)"', raw)
        out.append({
            'slug': slug,
            'url': f'{BASE}/reads/articles/{slug}/',
            'title': title,
            'summary': html.unescape(dm.group(1)) if dm else '',
            'body': body,
            'words': len(body.split()),
        })
        print(f'{i:2}. {slug:<38} {len(body.split()):5} words')
        time.sleep(0.4)
    except Exception as e:
        print(f'{i:2}. {slug:<38} FAILED {e}')

json.dump(out, open('data/articles/published.json', 'w', encoding='utf-8'), indent=2)
tot = sum(a['words'] for a in out)
print(f'\nsaved {len(out)} articles, {tot} words total (~{tot*4//3} tokens if sent whole)')
