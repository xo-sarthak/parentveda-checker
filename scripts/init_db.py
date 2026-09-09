import sys, io, os, json, re, hashlib
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import psycopg
from app import config

url = os.environ['DATABASE_URL']
sql = (config.ROOT / 'app' / 'schema.sql').read_text(encoding='utf-8')

with psycopg.connect(url, connect_timeout=30) as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print('schema applied')

    # corpus version
    ruleset = config.RULESET.read_text(encoding='utf-8')
    ver = 'v1-' + hashlib.sha256(ruleset.encode()).hexdigest()[:8]
    with conn.cursor() as cur:
        cur.execute(
            "insert into corpus_versions (version, ruleset, note) values (%s,%s,%s) "
            "on conflict (version) do nothing", (ver, ruleset, 'compiled from Bible folder'))
    conn.commit()
    print('corpus version:', ver)

    # published catalogue
    arts = json.load(open('data/articles/published.json', encoding='utf-8'))
    with conn.cursor() as cur:
        for a in arts:
            heads = re.findall(r'^## (.+)$', a['body'], re.M)
            cur.execute(
                "insert into published (slug,url,title,summary,body,word_count,headings) "
                "values (%s,%s,%s,%s,%s,%s,%s) on conflict (slug) do update set "
                "body=excluded.body, word_count=excluded.word_count, "
                "headings=excluded.headings, fetched_at=now()",
                (a['slug'], a['url'], a['title'], a['summary'], a['body'],
                 a['words'], json.dumps([h.strip() for h in heads])))
    conn.commit()

    with conn.cursor() as cur:
        for t in ['articles','versions','runs','scores','feedback','decisions',
                  'verification','published','corpus_versions']:
            n = cur.execute(f'select count(*) from {t}').fetchone()[0]
            print(f'  {t:<18} {n:>4} rows')
