"""Would a `git add .` leak anything? Run before the first push.

Mirrors git's own ignore semantics closely enough to be trusted: directory
patterns, path patterns and bare names all behave the way git treats them.
"""
import fnmatch
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

lines = [l.rstrip('\n').strip() for l in open('.gitignore', encoding='utf-8')]
lines = [l for l in lines if l and not l.startswith('#')]
allow = {l[1:] for l in lines if l.startswith('!')}
pats = [l for l in lines if not l.startswith('!')]

# Split so this file does not trip its own scan.
MARKERS = [b'sk-' + b'ant-', b'sk-' + b'proj-', b'sb_' + b'publishable', b'sb_' + b'secret',
           b'postgresql://' + b'postgres', b'eyJhbGciOiJI' + b'UzI1']


def ignored(rel: str) -> bool:
    if rel in allow:
        return False
    segs = rel.split('/')
    for raw in pats:
        p = raw.rstrip('/')
        if '/' in p:
            # a path pattern matches the file or any directory above it
            if fnmatch.fnmatch(rel, p) or rel.startswith(p + '/'):
                return True
        elif any(fnmatch.fnmatch(s, p) for s in segs):
            return True
    return False


keep, risky = [], []
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs
               if d not in ('.git', '__pycache__', 'node_modules', '.venv', 'venv')]
    for f in files:
        rel = os.path.relpath(os.path.join(root, f), '.').replace(os.sep, '/')
        if ignored(rel):
            continue
        keep.append(rel)
        try:
            blob = open(os.path.join(root, f), 'rb').read(20000)
        except OSError:
            continue
        for m in MARKERS:
            if m in blob and 'example' not in rel:
                risky.append((rel, m.decode()))
                break

print(f'{len(keep)} files would be committed\n')
if risky:
    print('!! SECRETS FOUND — do not push until these are fixed:')
    for r, m in risky:
        print(f'   {r}  ->  {m}')
else:
    print('OK — no secrets in anything that would be committed')

print('\nheld back (as intended):')
for f in ('.env', 'hello.env', 'corpus/compiled/parentveda-ruleset.md',
          'app/prompts/judge.md', 'data/articles/published.json'):
    if os.path.exists(f):
        print(f'   {"ignored " if ignored(f) else "!! WOULD BE PUSHED "} {f}')

print('\nlargest files going up:')
for size, f in sorted(((os.path.getsize(f), f) for f in keep), reverse=True)[:5]:
    print(f'   {size / 1024:7.1f} KB  {f}')
