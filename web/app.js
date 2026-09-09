/* ParentVeda Article Checker — client */

const $ = (s, r = document) => r.querySelector(s);
const esc = t => String(t ?? '').replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const TIERS = { must: 'Must fix', should: 'Should fix', polish: 'Polish' };
const VERDICTS = {
  publish: 'Publish', targeted_edits: 'Targeted edits', restructure: 'Restructure',
  merge_into_existing: 'Merge into existing', not_ready: 'Not ready'
};
const BAND = s => s >= 9 ? 'var(--good)' : s >= 8.5 ? 'var(--should)' : 'var(--must)';

let state = { articleId: null, runId: null, review: null, cursor: 0, filter: '', q: '' };

/* ---------------------------------------------------------------- plumbing */

function toast(msg, isError) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' err' : '');
  el.textContent = msg;
  document.body.append(el);
  setTimeout(() => el.remove(), isError ? 6000 : 2800);
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (session && session.access_token) headers.Authorization = 'Bearer ' + session.access_token;
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) { showGate('That sign-in has expired. Sign in again.'); throw new Error('Signed out'); }
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return r.json();
}

function go(key, label) {
  ['new', 'review', 'result', 'detail', 'experts'].forEach(k =>
    $('#s-' + k).classList.toggle('on', k === key));
  $('#crumb').textContent = label ||
    { new: 'New', review: 'Review', result: 'Result', detail: 'History', experts: 'Experts' }[key];
  $('.main').scrollTop = 0;
}

/* ---------------------------------------------------------------- sidebar */

const app = $('.app');
function toggleRail(force) {
  const c = force !== undefined ? force : !app.classList.contains('rail-collapsed');
  app.classList.toggle('rail-collapsed', c);
  try { localStorage.setItem('pv-rail', c ? '1' : '0'); } catch (e) {}
}
$('#railClose').onclick = () => toggleRail(true);
$('#railOpen').onclick = () => toggleRail(false);
try { if (localStorage.getItem('pv-rail') === '1') toggleRail(true); } catch (e) {}

async function loadList() {
  const p = new URLSearchParams();
  if (state.q) p.set('q', state.q);
  if (state.filter) p.set('status', state.filter);
  try {
    const rows = await api('/api/articles?' + p);
    const list = $('#articleList');
    if (!rows.length) {
      list.innerHTML = '<div class="empty">Nothing here yet.<br>Upload an article to begin.</div>';
      return;
    }
    list.innerHTML = rows.map(a => `
      <button class="item" data-id="${a.id}">
        <div class="item-top">
          <span class="item-title">${esc(a.title)}</span>
          ${a.score != null ? `<span class="item-score mono" style="color:${BAND(a.score)}">${a.score}</span>` : ''}
        </div>
        <div class="item-meta">
          <span class="dot" style="background:${a.score != null ? BAND(a.score) : 'var(--line-2)'}"></span>
          <span>${esc(VERDICTS[a.verdict] || a.status)}</span>
          ${a.words ? `<span>&middot;</span><span>${a.words.toLocaleString()} w</span>` : ''}
          <span>&middot;</span><span>${new Date(a.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}</span>
        </div>
      </button>`).join('');
    list.querySelectorAll('.item').forEach(b => b.onclick = () => {
      list.querySelectorAll('.item').forEach(i => i.removeAttribute('aria-current'));
      b.setAttribute('aria-current', 'true');
      openDetail(b.dataset.id);
    });
  } catch (e) { toast('Could not load articles: ' + e.message, true); }
}

let searchTimer;
$('#search').oninput = e => {
  clearTimeout(searchTimer);
  state.q = e.target.value.trim();
  searchTimer = setTimeout(loadList, 250);
};
$('#filters').querySelectorAll('.chip').forEach(c => c.onclick = () => {
  $('#filters').querySelectorAll('.chip').forEach(x => x.setAttribute('aria-pressed', 'false'));
  c.setAttribute('aria-pressed', 'true');
  state.filter = c.dataset.status;
  loadList();
});

/* ---------------------------------------------------------------- upload */

$('#pickFile').onclick = () => $('#fileInput').click();
$('#fileInput').onchange = e => { if (e.target.files[0]) submit({ file: e.target.files[0] }); };

const dz = $('#dropZone');
['dragover', 'dragenter'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.style.borderColor = 'var(--accent)';
}));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.style.borderColor = '';
}));
dz.addEventListener('drop', e => { if (e.dataTransfer.files[0]) submit({ file: e.dataTransfer.files[0] }); });

$('#pasteBox').oninput = e => {
  const n = e.target.value.trim().split(/\s+/).filter(Boolean).length;
  $('#pasteCount').textContent = n.toLocaleString() + ' words' + (n && n < 120 ? ' — too short to review' : '');
};
$('#reviewPaste').onclick = () => {
  const text = $('#pasteBox').value.trim();
  if (text.split(/\s+/).length < 120) return toast('That is too short to review.', true);
  submit({ text });
};

async function submit({ file, text }) {
  go('review');
  $('#s-review').innerHTML =
    `<div class="working"><span class="spinner"></span>
     Reading against the ParentVeda Bible &mdash; usually 45&ndash;90 seconds.</div>`;
  const fd = new FormData();
  if (file) fd.append('file', file); else fd.append('text', text);
  try {
    const res = await api('/api/review', { method: 'POST', body: fd });
    state.articleId = res.article_id;
    state.runId = res.run_id;
    state.review = res.review;
    renderReview();
    loadList();
  } catch (e) {
    $('#s-review').innerHTML =
      `<div class="blocker"><span style="color:var(--must)">&#9888;</span>
       <div><div class="blocker-t">Review failed</div>
       <div style="font-size:12.5px; color:var(--ink-2)">${esc(e.message)}</div></div></div>
       <button class="btn" onclick="location.reload()">Start again</button>`;
  }
}

/* ---------------------------------------------------------------- review */

function renderReview() {
  const r = state.review, s = r.overall;
  const groups = ['must', 'should', 'polish']
    .map(t => [t, r.feedback.filter(f => f.tier === t)]).filter(([, f]) => f.length);

  $('#s-review').innerHTML = `
    <div class="score-head">
      <div class="score-num" style="color:${BAND(s)}">${s.toFixed(1)}<span class="score-of">/10</span></div>
      <div class="score-side">
        <h1 class="art-title" id="revTitle"></h1>
        <div class="badges">
          <span class="badge ${s >= 9 ? 'b-good' : 'b-must'}">${esc(VERDICTS[r.verdict] || r.verdict)}</span>
          <span class="badge b-neutral">${esc(r.article_type.replace(/_/g, ' '))}</span>
          <span class="badge b-neutral mono">${esc(r.usage.model.replace('claude-', ''))} &middot; $${r.cost.toFixed(3)}</span>
        </div>
      </div>
    </div>

    ${r.blockers.length ? `<div class="blocker"><span aria-hidden="true" style="color:var(--must)">&#9888;</span>
      <div><div class="blocker-t">Blocked &mdash; cannot be exported for publication</div>
      <ul>${r.blockers.map(b => `<li>${esc(b)}</li>`).join('')}</ul></div></div>` : ''}

    <details class="params">
      <summary><span class="caret">&rsaquo;</span> Why ${s.toFixed(1)}? &mdash; the twelve parameters</summary>
      <table class="ptable">
        <thead><tr><th>Parameter</th><th>Weight</th><th>Score</th><th></th><th>Note</th></tr></thead>
        <tbody>${r.scores.map(p => `<tr>
          <td>${esc(p.label)}</td><td>${(p.weight * 100).toFixed(1).replace('.0', '')}%</td>
          <td style="color:${p.score < 8.5 ? 'var(--must)' : 'inherit'}">${p.score}</td>
          <td><div class="bar"><span style="width:${p.score * 10}%"></span></div></td>
          <td class="note">${esc(p.justification)}</td></tr>`).join('')}
        </tbody>
      </table>
    </details>

    <div class="sec-head">
      <h2 class="sec-title">${r.feedback.length} findings</h2>
      <span class="kbd-hint"><kbd>A</kbd> accept &middot; <kbd>R</kbd> reject &middot; <kbd>J</kbd><kbd>K</kbd> move</span>
    </div>

    ${groups.map(([tier, items]) => `
      <div class="tier-group g-${tier}">
        <div class="tier-head">
          <span class="tier-pip"></span><span class="tier-name">${TIERS[tier]}</span>
          <span class="tier-count">${items.length}</span>
        </div>
        ${items.map(f => `
          <article class="finding" data-id="${f.id}" data-tier="${f.tier}">
            <div class="f-num">${f.position + 1}</div>
            <div class="f-body">
              <div class="f-sum">${esc(f.summary)}</div>
              <div class="f-param">${esc(f.parameter)} &middot; ${esc(f.kind)}</div>
              ${f.quote ? `<blockquote class="quote">${esc(f.quote)}</blockquote>` : ''}
              <div class="proposed"><b>Change to</b>${esc(f.proposed)}</div>
              ${f.rationale ? `<div class="f-why">${esc(f.rationale)}</div>` : ''}
            </div>
            <div class="f-acts">
              <button class="act yes" aria-pressed="false" title="Accept">&#10003;</button>
              <button class="act no" aria-pressed="false" title="Reject">&#10005;</button>
            </div>
          </article>`).join('')}
      </div>`).join('')}

    <div class="footbar">
      <span class="foot-status" id="tally"></span>
      <div class="topbar-actions">
        <button class="btn" id="chooseAll">Choose for me</button>
        <button class="btn btn-primary" id="applyBtn">Apply &amp; rewrite &rarr;</button>
      </div>
    </div>`;

  $('#revTitle').textContent = r.article_level.title_note ? document.title : '';
  fetch('/api/articles/' + state.articleId).then(x => x.json())
    .then(d => { $('#revTitle').textContent = d.article.title; });

  wireFindings();
}

function wireFindings() {
  const cards = [...document.querySelectorAll('#s-review .finding')];
  state.cursor = 0;
  const focus = i => { state.cursor = i; cards.forEach((c, n) => c.classList.toggle('cursor', n === i)); };
  const nextOpen = from => {
    for (let i = from + 1; i < cards.length; i++) if (!cards[i].classList.contains('done')) return i;
    for (let i = 0; i < cards.length; i++) if (!cards[i].classList.contains('done')) return i;
    return -1;
  };
  const tally = () => {
    const done = cards.filter(c => c.classList.contains('done')).length;
    const left = cards.filter(c => !c.classList.contains('done') && c.dataset.tier !== 'polish').length;
    $('#tally').textContent = `${done} of ${cards.length} decided · ${left} remaining above polish`;
  };
  const decide = async (card, yes, advance) => {
    card.querySelectorAll('.act').forEach(a => a.setAttribute('aria-pressed', 'false'));
    card.querySelector(yes ? '.yes' : '.no').setAttribute('aria-pressed', 'true');
    card.classList.add('done');
    tally();
    try {
      await api('/api/decide', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feedback_id: card.dataset.id, outcome: yes ? 'accepted' : 'rejected' })
      });
    } catch (e) { toast('Could not save that decision: ' + e.message, true); }
    if (advance) {
      const n = nextOpen(cards.indexOf(card));
      if (n === -1) return cards.forEach(c => c.classList.remove('cursor'));
      focus(n); cards[n].scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  };

  cards.forEach((c, i) => {
    c.querySelector('.yes').onclick = () => { focus(i); decide(c, true, false); };
    c.querySelector('.no').onclick = () => { focus(i); decide(c, false, false); };
  });
  focus(0); tally();

  $('#chooseAll').onclick = async () => {
    await api('/api/decide-all', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: state.runId, tiers: ['must', 'should'] })
    });
    cards.forEach(c => {
      const yes = c.dataset.tier !== 'polish';
      c.querySelectorAll('.act').forEach(a => a.setAttribute('aria-pressed', 'false'));
      c.querySelector(yes ? '.yes' : '.no').setAttribute('aria-pressed', 'true');
      c.classList.add('done'); c.classList.remove('cursor');
    });
    tally();
    toast('Must-fix and should-fix accepted');
  };

  $('#applyBtn').onclick = async () => {
    const decided = cards.filter(c => c.classList.contains('done')).length;
    if (!decided) return toast('Decide at least one finding first.', true);
    const before = state.review.overall;
    go('result');
    $('#s-result').innerHTML =
      `<div class="working"><span class="spinner"></span>
       Applying the accepted changes, then re-scoring &mdash; about two minutes.</div>`;
    try {
      const res = await api('/api/rewrite', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: state.runId })
      });
      renderResult(res, before);
      loadList();
    } catch (e) {
      $('#s-result').innerHTML =
        `<div class="blocker"><span style="color:var(--must)">&#9888;</span>
         <div><div class="blocker-t">Rewrite failed</div>
         <div style="font-size:12.5px; color:var(--ink-2)">${esc(e.message)}</div></div></div>`;
    }
  };

  document.onkeydown = e => {
    if ((e.ctrlKey || e.metaKey) && e.key === '\\') { e.preventDefault(); return toggleRail(); }
    if (!$('#s-review').classList.contains('on')) return;
    if (['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
    const k = e.key.toLowerCase();
    if (k === 'j') { focus(Math.min(state.cursor + 1, cards.length - 1)); cards[state.cursor].scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    if (k === 'k') { focus(Math.max(state.cursor - 1, 0)); cards[state.cursor].scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    if (k === 'a' && cards[state.cursor]) decide(cards[state.cursor], true, true);
    if (k === 'r' && cards[state.cursor]) decide(cards[state.cursor], false, true);
  };
}

/* ---------------------------------------------------------------- result */

function renderResult(res, before) {
  const r = res.review, after = r.overall, d = res.diff;
  const up = after >= before;
  $('#s-result').innerHTML = `
    <h1 class="art-title" style="margin-bottom:18px">Rewritten</h1>

    <div class="panel">
      <h3>Result</h3>
      <div class="delta">
        <span class="from">${before.toFixed(1)}</span>
        <span class="arrow">&rarr;</span>
        <span class="to" style="color:${BAND(after)}">${after.toFixed(1)}</span>
      </div>
      <div class="diffnote">
        ${res.applied} change${res.applied === 1 ? '' : 's'} applied &middot;
        ${esc(VERDICTS[r.verdict] || r.verdict)} &middot;
        ${d.words_before.toLocaleString()} &rarr; ${d.words_after.toLocaleString()} words &middot;
        ${esc(res.edit_usage.model.replace('claude-', ''))} + ${esc(r.usage.model.replace('claude-', ''))}
        &middot; $${res.total_cost.toFixed(3)}
        ${up ? '' : ' &middot; score did not improve'}
      </div>
    </div>

    ${r.blockers.length ? `<div class="blocker"><span aria-hidden="true" style="color:var(--must)">&#9888;</span>
      <div><div class="blocker-t">Still blocked</div>
      <ul>${r.blockers.map(b => `<li>${esc(b)}</li>`).join('')}</ul></div></div>` : ''}

    <div class="panel">
      <h3>What changed &mdash; ${d.changes.length} passage${d.changes.length === 1 ? '' : 's'}</h3>
      ${d.changes.length ? `<div class="diff">${d.changes.map(c => `
        <div class="drow d-${c.kind}">
          <div class="dkind">${c.kind}</div>
          <div>
            ${c.before ? `<div class="dbefore">${esc(c.before)}</div>` : ''}
            ${c.after ? `<div class="dafter">${esc(c.after)}</div>` : ''}
          </div>
        </div>`).join('')}</div>`
        : '<div class="empty">No paragraphs changed.</div>'}
    </div>

    <div class="panel">
      <h3>Final article</h3>
      <div class="finalbody" id="finalBody"></div>
      <div class="footbar" style="position:static; border:0; padding-top:14px">
        <span class="foot-status">Export, or send for clinical verification below.</span>
        <div class="topbar-actions">
          <button class="btn" id="copyFinal">Copy text</button>
          <button class="btn" id="dlDocx">Download DOCX</button>
          <button class="btn" id="dlPdf">Print / PDF</button>
          <button class="btn btn-primary" id="reviewAgain">Review again</button>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3>Clinical verification</h3>
      <p style="margin:0 0 13px; font-size:13px; color:var(--ink-2)">
        Generates a one-page sheet of every claim, number and red flag, so a
        clinician can sign off without reading the article.
      </p>
      <div id="sheetArea">
        <button class="btn btn-primary" id="makeSheet">Generate verification sheet</button>
      </div>
    </div>`;

  $('#finalBody').textContent = res.body;
  $('#copyFinal').onclick = () => {
    navigator.clipboard.writeText(res.body)
      .then(() => toast('Article copied'))
      .catch(() => toast('Could not copy — select the text instead', true));
  };
  const aid = state.articleId;
  $('#dlDocx').onclick = () => { location.href = `/api/export/${aid}.docx`; };
  $('#dlPdf').onclick = () => {
    const w = window.open(`/api/export/${aid}.html`, '_blank');
    if (w) setTimeout(() => w.print(), 900); else toast('Allow pop-ups to print', true);
  };
  $('#makeSheet').onclick = () => makeSheet(aid);
  $('#reviewAgain').onclick = () => {
    state.runId = res.run_id;
    state.review = r;
    go('review');
    renderReview();
  };
}

/* ------------------------------------------------------- verification */

async function makeSheet(articleId) {
  const area = $('#sheetArea');
  area.innerHTML = '<div class="working"><span class="spinner"></span> Extracting claims, numbers and red flags&hellip;</div>';
  try {
    const res = await api('/api/doctor-sheet', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ article_id: articleId })
    });
    const experts = res.experts || [];
    area.innerHTML = `
      <div class="badges" style="margin-bottom:13px">
        <span class="badge b-neutral">Specialty needed: <b>${esc(res.specialty)}</b></span>
        <span class="badge b-neutral mono">$${res.cost.toFixed(3)}</span>
      </div>

      <details class="params" style="border-top:0; margin-bottom:14px">
        <summary><span class="caret">&rsaquo;</span> Preview the sheet</summary>
        <div class="finalbody" id="sheetBody" style="max-height:340px"></div>
      </details>

      ${experts.length ? `
        <div class="tbl-wrap">
          <table class="grid">
            <thead><tr><th></th><th>Expert</th><th>Designation</th><th>Practice</th></tr></thead>
            <tbody>${experts.map(e => `
              <tr><td><input type="checkbox" class="pick" value="${e.id}" aria-label="Send to ${esc(e.name)}"></td>
                  <td>${esc(e.name)}</td><td>${esc(e.designation || '—')}</td>
                  <td>${esc(e.practice || '—')}</td></tr>`).join('')}
            </tbody>
          </table>
        </div>`
        : `<div class="nomatch"><span aria-hidden="true" style="color:var(--should)">!</span>
           <div><div style="font-size:13px"><b>Nobody on the roster holds ${esc(res.specialty)}</b></div>
           <div style="font-size:12.5px; color:var(--ink-2)">Add an expert with this specialty, or download
           the sheet and send it yourself.</div></div></div>`}

      <div class="footbar" style="position:static; border:0; padding-top:14px">
        <span class="foot-status">The article is held at “awaiting doctor” until someone marks it verified.</span>
        <div class="topbar-actions">
          <button class="btn" id="dlSheet">Download sheet</button>
          ${experts.length ? '<button class="btn btn-primary" id="sendSheet">Send to selected</button>' : ''}
        </div>
      </div>`;

    $('#sheetBody').textContent = res.sheet;
    $('#dlSheet').onclick = () => { location.href = `/api/sheet/${res.verification_id}.docx`; };
    const send = $('#sendSheet');
    if (send) send.onclick = async () => {
      const ids = [...document.querySelectorAll('.pick:checked')].map(c => c.value);
      if (!ids.length) return toast('Pick at least one expert.', true);
      try {
        const out = await api('/api/send-verification', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ verification_id: res.verification_id, expert_ids: ids })
        });
        toast('Sent to ' + out.sent_to.join(', '));
        loadList();
      } catch (e) { toast('Could not send: ' + e.message, true); }
    };
  } catch (e) {
    area.innerHTML = `<div class="empty">Could not generate the sheet: ${esc(e.message)}</div>`;
  }
}

/* ---------------------------------------------------------------- detail */

async function openDetail(id) {
  go('detail');
  $('#s-detail').innerHTML = '<div class="working"><span class="spinner"></span> Loading history&hellip;</div>';
  try {
    const d = await api('/api/articles/' + id);
    const last = d.runs[d.runs.length - 1];
    const dec = d.decisions.reduce((a, t) => ({
      accepted: a.accepted + Number(t.accepted), rejected: a.rejected + Number(t.rejected),
      total: a.total + Number(t.total)
    }), { accepted: 0, rejected: 0, total: 0 });
    const cost = d.runs.reduce((a, r) => a + Number(r.cost_usd || 0), 0);
    const when = t => new Date(t).toLocaleString('en-IN',
      { day: 'numeric', month: 'short', year: 'numeric',
        hour: 'numeric', minute: '2-digit', hour12: true });

    $('#s-detail').innerHTML = `
      <h1 class="art-title">${esc(d.article.title)}</h1>
      <div class="badges" style="margin:9px 0 22px">
        <span class="badge ${last && last.overall >= 9 ? 'b-good' : 'b-neutral'}">${esc(d.article.status.replace(/_/g, ' '))}</span>
        ${d.article.article_type ? `<span class="badge b-neutral">${esc(d.article.article_type.replace(/_/g, ' '))}</span>` : ''}
        ${last ? `<span class="badge b-neutral mono">${last.word_count.toLocaleString()} words</span>` : ''}
        ${d.article.author ? `<span class="badge b-neutral">Author: ${esc(d.article.author)}</span>` : ''}
      </div>

      <div class="panel">
        <h3>At a glance</h3>
        <dl class="kv">
          <dt>Current score</dt><dd class="mono" style="color:${last ? BAND(last.overall) : 'inherit'}">
            <b>${last ? Number(last.overall).toFixed(1) : '—'}</b> — ${last ? esc(VERDICTS[last.verdict] || last.verdict) : '—'}</dd>
          <dt>Versions</dt><dd>${new Set(d.runs.map(r => r.version_no)).size}</dd>
          <dt>Findings</dt><dd>${dec.total} &middot; ${dec.accepted} accepted, ${dec.rejected} rejected</dd>
          <dt>Sent to</dt><dd>${d.verification.length ? esc(d.verification.map(v => v.doctor_name || v.specialty).join(', ')) : 'Not yet sent'}</dd>
          <dt>Total API cost</dt><dd class="mono">$${cost.toFixed(3)}</dd>
        </dl>
      </div>

      ${d.verification.length ? `
      <div class="panel">
        <h3>Clinical verification</h3>
        <div class="tbl-wrap">
          <table class="grid">
            <thead><tr><th>Expert</th><th>Specialty</th><th>Sent</th><th>Verified</th><th></th></tr></thead>
            <tbody>${d.verification.map(v => `
              <tr>
                <td>${esc(v.doctor_name || '—')}</td>
                <td>${esc(v.specialty || '—')}</td>
                <td>${v.sent_at ? when(v.sent_at) : '<span style="color:var(--ink-3)">not sent</span>'}</td>
                <td>${v.verified_at
                      ? `<span class="badge b-good">${when(v.verified_at)}</span>${
                          v.verified_by ? `<div style="font-size:11px; color:var(--ink-3); margin-top:3px">marked by ${esc(v.verified_by)}</div>` : ''}`
                      : '<span class="badge b-warn">Awaiting</span>'}</td>
                <td style="white-space:nowrap">
                  <button class="btn" style="padding:3px 9px; font-size:12px"
                          onclick="location.href='/api/sheet/${v.id}.docx'">Sheet</button>
                  ${v.verified_at ? '' :
                    `<button class="btn btn-primary markver" data-id="${v.id}"
                             style="padding:3px 9px; font-size:12px">Mark verified</button>`}
                </td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>` : ''}

      <div class="panel">
        <h3>Everything that happened</h3>
        <div class="timeline">
          ${d.runs.map(r => `
            <div class="tl">
              <div class="tl-when">${when(r.created_at)}</div>
              <div class="tl-what">${r.kind === 'review' ? 'Reviewed' : 'Re-scored'} —
                <b class="mono" style="color:${BAND(r.overall)}">${Number(r.overall).toFixed(1)}</b>,
                ${esc(VERDICTS[r.verdict] || r.verdict)}</div>
              <div class="tl-detail">v${r.version_no} &middot; ${r.word_count.toLocaleString()} words &middot;
                ${esc(r.model.replace('claude-', ''))} &middot; ${esc(r.effort)} &middot; $${Number(r.cost_usd).toFixed(3)}
                ${(r.blockers || []).length ? ` &middot; ${r.blockers.length} blocker(s)` : ''}
                ${r.actor_email ? `<br><span style="color:var(--ink-3)">run by ${esc(r.actor_email)}</span>` : ''}</div>
            </div>`).join('')}
        </div>
      </div>`;
    document.querySelectorAll('.markver').forEach(b => b.onclick = async () => {
      const notes = prompt('Any note from the reviewer? (optional)') || null;
      try {
        await api('/api/mark-verified', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ verification_id: b.dataset.id, notes })
        });
        toast('Marked verified'); openDetail(id); loadList();
      } catch (e) { toast('Could not update: ' + e.message, true); }
    });
  } catch (e) { toast('Could not load that article: ' + e.message, true); }
}

/* ---------------------------------------------------------------- experts */

async function openExperts() {
  go('experts');
  $('#s-experts').innerHTML = '<div class="working"><span class="spinner"></span> Loading roster&hellip;</div>';
  try {
    const rows = await api('/api/experts');
    $('#s-experts').innerHTML = `
      <div class="sec-head" style="margin-top:0">
        <h1 class="art-title" style="font-size:22px">Expert roster</h1>
        <div class="topbar-actions"><button class="btn btn-primary" id="addExpert">+ Add expert</button></div>
      </div>
      <div class="panel" id="expertForm" hidden>
        <h3>Add an expert</h3>
        <div style="display:grid; grid-template-columns:repeat(2,1fr); gap:12px 16px">
          <label style="display:grid; gap:4px"><span style="font-size:12px; color:var(--ink-3)">Name</span>
            <input class="fld" id="xName" placeholder="Dr Priya Nair"></label>
          <label style="display:grid; gap:4px"><span style="font-size:12px; color:var(--ink-3)">Designation</span>
            <input class="fld" id="xDesig" placeholder="Obstetrician (MBBS, MS)"></label>
          <label style="display:grid; gap:4px"><span style="font-size:12px; color:var(--ink-3)">Specialty — comma separated</span>
            <input class="fld" id="xSpec" placeholder="Obs-gynae, High-risk pregnancy"></label>
          <label style="display:grid; gap:4px"><span style="font-size:12px; color:var(--ink-3)">Practice</span>
            <input class="fld" id="xPrac" placeholder="Fortis, Delhi"></label>
          <label style="display:grid; gap:4px"><span style="font-size:12px; color:var(--ink-3)">Email</span>
            <input class="fld" id="xMail" placeholder="name@practice.in"></label>
          <label style="display:grid; gap:4px"><span style="font-size:12px; color:var(--ink-3)">Status</span>
            <select class="fld" id="xOn"><option value="true">Onboarded</option><option value="false">Not signed yet</option></select></label>
        </div>
        <div class="footbar" style="position:static; border:0; padding-top:14px">
          <span class="foot-status">Saved to the roster immediately — no code change needed.</span>
          <div class="topbar-actions">
            <button class="btn" id="cancelExpert">Cancel</button>
            <button class="btn btn-primary" id="saveExpert">Save expert</button>
          </div>
        </div>
      </div>
      <div class="tbl-wrap">
        <table class="grid">
          <thead><tr><th>Expert</th><th>Designation</th><th>Specialty</th><th>Practice</th><th>Status</th></tr></thead>
          <tbody>${rows.map(x => `
            <tr${x.onboarded ? '' : ' style="opacity:.6"'}>
              <td>${esc(x.name)}</td><td>${esc(x.designation || '—')}</td>
              <td><div class="tags">${(x.specialties || []).map(s => `<span class="tag">${esc(s)}</span>`).join('')}</div></td>
              <td>${esc(x.practice || '—')}</td>
              <td><span class="badge ${x.onboarded ? 'b-good' : 'b-warn'}">${x.onboarded ? 'Onboarded' : 'Not signed yet'}</span></td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
      <p class="diffnote">Experts not yet signed are shown but cannot be sent work. Nobody is deleted — past verifications keep their attribution.</p>`;

    const form = $('#expertForm');
    $('#addExpert').onclick = () => { form.hidden = false; $('#xName').focus(); };
    $('#cancelExpert').onclick = () => { form.hidden = true; };
    $('#saveExpert').onclick = async () => {
      const name = $('#xName').value.trim();
      if (!name) return toast('A name is required.', true);
      try {
        await api('/api/experts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name, designation: $('#xDesig').value.trim() || null,
            specialties: $('#xSpec').value.split(',').map(s => s.trim()).filter(Boolean),
            practice: $('#xPrac').value.trim() || null,
            email: $('#xMail').value.trim() || null,
            onboarded: $('#xOn').value === 'true'
          })
        });
        toast('Saved to the roster'); openExperts();
      } catch (e) { toast('Could not save: ' + e.message, true); }
    };
  } catch (e) { toast('Could not load the roster: ' + e.message, true); }
}

/* ---------------------------------------------------------------- boot */

document.querySelectorAll('[data-goto]').forEach(b => b.onclick = () => {
  if (b.dataset.goto === 'experts') return openExperts();
  go(b.dataset.goto);
});
document.onkeydown = e => {
  if ((e.ctrlKey || e.metaKey) && e.key === '\\') { e.preventDefault(); toggleRail(); }
};

/* ---------------------------------------------------------------- sign-in */

let sb = null, session = null;

function showGate(note) {
  document.getElementById('gate').hidden = false;
  document.getElementById('shell').hidden = true;
  if (note) document.getElementById('gateNote').textContent = note;
}

function showApp(email) {
  document.getElementById('gate').hidden = true;
  document.getElementById('shell').hidden = false;
  const who = document.getElementById('whoBtn');
  who.textContent = email;
  who.onclick = async () => {
    if (!confirm('Sign out of the Article Checker?')) return;
    if (sb) await sb.auth.signOut();
    session = null;
    showGate('Signed out.');
  };
  loadList();
}

async function boot() {
  let cfg;
  try {
    cfg = await (await fetch('/api/config')).json();
  } catch (e) {
    return showGate('Cannot reach the server.');
  }

  if (!cfg.require_auth) {
    const me = await (await fetch('/api/me')).json();
    return showApp(me.email + '  ·  local');
  }

  if (!cfg.supabase_url || !cfg.anon_key) {
    return showGate('Google sign-in is not configured on the server yet.');
  }

  sb = window.supabase.createClient(cfg.supabase_url, cfg.anon_key, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
  });

  sb.auth.onAuthStateChange((_evt, s) => {
    session = s;
    if (s && s.user) showApp(s.user.email);
  });

  const { data } = await sb.auth.getSession();
  session = data.session;
  if (session && session.user) showApp(session.user.email);
  else showGate();

  document.getElementById('googleBtn').onclick = async () => {
    const { error } = await sb.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin }
    });
    if (error) showGate('Sign-in failed: ' + error.message);
  };
}

boot();
