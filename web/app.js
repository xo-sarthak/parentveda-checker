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
const mmss = s => s == null ? '' :
  (s >= 60 ? Math.floor(s / 60) + 'm ' + String(Math.round(s % 60)).padStart(2, '0') + 's'
           : Math.round(s) + 's');

let state = { articleId: null, runId: null, review: null, estimates: null,
              batchNav: null, cursor: 0, filter: '', q: '' };

/* Money the intern sees. Estimates come from the server per engine; the
   real figure is recorded afterwards from usage. */
const RATE = () => (state.estimates && state.estimates.inr_rate) || 95;
const inr = usd => {
  const r = usd * RATE();
  if (r < 0.005) return 'free';
  if (r < 1) return 'under \u20b91';
  return '\u20b9' + (r < 10 ? r.toFixed(1).replace(/\.0$/, '') : String(Math.round(r)));
};
/* "~₹11" for a real number; "under ₹1" is already approximate, no tilde. */
const approx = usd => { const t = inr(usd); return t.startsWith('\u20b9') ? '~' + t : t; };
const est = job => (state.estimates && state.estimates[job]) || { usd: 0, inr: 0 };
/* The queue is ChatGPT-only, so anything priced on a queue screen uses the
   ChatGPT estimates whatever engine the browser has selected. */
const qEst = job => (((window.PV_CONFIG || {}).estimates || {}).openai || {})[job] || est(job);

/* Which provider scores, rewrites and drafts the sheet. Per browser, so two
   people can compare engines on the same article without touching config. */
const ENGINE_LABEL = { claude: 'Claude', openai: 'ChatGPT' };
let engine = 'claude';
try { engine = localStorage.getItem('pv-engine') || engine; } catch (e) {}
function setEngine(e) {
  engine = e;
  try { localStorage.setItem('pv-engine', e); } catch (e2) {}
  document.querySelectorAll('#engineSwitch .chip').forEach(c =>
    c.setAttribute('aria-pressed', c.dataset.engine === e ? 'true' : 'false'));
  if (typeof syncModeWithEngine === 'function') syncModeWithEngine();
}
/* When to review. The queue is ChatGPT's Batch API — half price, results
   later. Claude has no queue here, so choosing Claude forces "now". */
let prefMode = 'queue';                         // what the person chose
try { prefMode = localStorage.getItem('pv-mode') || prefMode; } catch (e) {}
let mode = prefMode;                            // what applies right now
function setMode(m, chosen = false) {
  mode = m;
  if (chosen) { prefMode = m; try { localStorage.setItem('pv-mode', m); } catch (e2) {} }
  document.querySelectorAll('#whenSwitch .when-opt').forEach(b =>
    b.setAttribute('aria-pressed', b.dataset.mode === m ? 'true' : 'false'));
  if (typeof renderPicked === 'function') renderPicked();
}
function syncModeWithEngine() {
  const q = document.querySelector('#whenSwitch .when-opt[data-mode="queue"]');
  if (!q) return;
  const canQueue = engine === 'openai' && !!(window.PV_CONFIG && window.PV_CONFIG.queue);
  q.disabled = !canQueue;
  q.title = canQueue ? '' : (engine !== 'openai' ? 'Batching is available with the ChatGPT engine'
                                                 : 'Batching is not set up on this server');
  setMode(canQueue ? prefMode : 'now');
  /* Price the two options for the engine in use; a review's estimate is the
     "rescore" shape, and the queue is half of it. */
  const ests = window.PV_CONFIG && window.PV_CONFIG.estimates && window.PV_CONFIG.estimates[engine];
  const usd = ests ? ests.rescore.usd : 0;
  if (!state.estimates && ests) state.estimates = ests;
  $('#queuePrice').textContent = canQueue ? approx(usd / 2) + ' per article' : 'ChatGPT only';
  $('#nowPrice').textContent = approx(usd) + ' per article';
}
function shortModel(m) { return (m || '').replace('claude-', ''); }

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
  ['new', 'review', 'result', 'detail', 'experts', 'batch', 'batches'].forEach(k =>
    $('#s-' + k).classList.toggle('on', k === key));
  $('#crumb').textContent = label ||
    { new: 'New', review: 'Review', result: 'Result', detail: 'History', experts: 'Experts',
      batch: 'Batch', batches: 'Batches' }[key];
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
    const hhmm = t => new Date(t).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true });
    const inQueue = a => a.score == null && ['queued', 'submitted'].includes(a.queue_status);
    const queueFailed = a => a.score == null && a.queue_status === 'failed' && !/instantly/.test(a.queue_error || '');
    list.innerHTML = rows.map(a => `
      <button class="item" data-id="${a.id}">
        <div class="item-top">
          <span class="item-title">${esc(a.title)}</span>
          ${a.score != null ? `<span class="item-score mono" style="color:${BAND(a.score)}">${a.score}</span>`
            : inQueue(a) ? '<span class="item-queued" title="In the queue" aria-hidden="true">&#9203;</span>' : ''}
        </div>
        <div class="item-meta">
          <span class="dot" style="background:${a.score != null ? BAND(a.score) : inQueue(a) ? 'var(--should)' : 'var(--line-2)'}"></span>
          <span>${inQueue(a) ? (a.queue_status === 'submitted' ? 'Reviewing' : 'Sending') + ' &middot; Batch #' + (a.queue_seq || '?')
                  : queueFailed(a) ? 'Failed in Batch #' + (a.queue_seq || '?')
                  : esc(VERDICTS[a.verdict] || a.status)}</span>
          ${a.score != null && a.batch != null ? `<span class="item-tag ${a.batch ? 't-queue' : 't-now'}" title="${a.batch ? 'Reviewed in Batch #' + (a.queue_seq || '?') + ' (half price)' : 'Reviewed instantly, outside any batch'}">${a.batch ? 'batch #' + (a.queue_seq || '?') : 'instant'}</span>` : ''}
          ${a.words ? `<span>&middot;</span><span>${a.words.toLocaleString()} w</span>` : ''}
          <span>&middot;</span><span>${new Date(a.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}</span>
        </div>
      </button>`).join('');
    /* While anything is in the queue, check back every minute so the row
       flips to a score without anyone reloading. */
    clearTimeout(listTimer);
    if (rows.some(inQueue)) listTimer = setTimeout(loadList, 60000);
    list.querySelectorAll('.item').forEach(b => b.onclick = () => {
      list.querySelectorAll('.item').forEach(i => i.removeAttribute('aria-current'));
      b.setAttribute('aria-current', 'true');
      openDetail(b.dataset.id);
    });
  } catch (e) { toast('Could not load articles: ' + e.message, true); }
  loadBatchesRail();
}

/* The batches an intern is most likely to be waiting on, above the articles.
   Real rows, not headings: click one to open it. */
async function loadBatchesRail() {
  const label = $('#batchLabel'), nav = $('#batchList');
  try {
    const gs = await api('/api/batches?limit=8');
    if (!gs.length) { label.hidden = true; nav.hidden = true; return; }
    label.hidden = false; nav.hidden = false;
    nav.innerHTML = gs.slice(0, 6).map(g => `
      <button class="item b-item" data-id="${g.id}">
        <div class="item-top">
          <span class="item-title mono">Batch #${g.seq}</span>
          <span class="b-count">${g.n} article${g.n === 1 ? '' : 's'}</span>
        </div>
        <div class="item-meta">
          ${batchStatusLine(g)}
        </div>
      </button>`).join('') + (gs.length > 6 ? '<button class="item-more" id="allBatches">All batches &rarr;</button>' : '');
    nav.querySelectorAll('.b-item').forEach(b => b.onclick = () => openBatch(b.dataset.id));
    const more = $('#allBatches'); if (more) more.onclick = openBatches;
  } catch (e) { /* the rail is a convenience; the Batches screen still works */ }
}

const BATCH_STATE = {
  waiting:       ['Sending', 'var(--should)'],
  reviewing:     ['Reviewing', 'var(--should)'],
  reviewed:      ['Reviewed', 'var(--good)'],
  failed:        ['Failed', 'var(--must)'],
  partly_failed: ['Partly failed', 'var(--must)'],
};
function batchStatusLine(g) {
  const [word, colour] = BATCH_STATE[g.status] || BATCH_STATE.waiting;
  const detail = g.status === 'reviewing' ? `${g.done} of ${g.n} done`
    : g.status === 'partly_failed' ? `${g.done} reviewed, ${g.failed} failed`
    : g.status === 'failed' ? 'send again'
    : new Date(g.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  return `<span class="dot" style="background:${colour}"></span>
          <span style="color:${colour}">${word}</span><span>&middot;</span><span>${detail}</span>`;
}

let searchTimer, listTimer;
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

/* What the intern has picked so far. Files and pasted texts sit in one list;
   nothing is sent until they press the button. */
let picked = [];

$('#pickFile').onclick = () => $('#fileInput').click();
$('#fileInput').onchange = e => { addFiles(e.target.files); e.target.value = ''; };

const dz = $('#dropZone');
['dragover', 'dragenter'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.style.borderColor = 'var(--accent)';
}));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.style.borderColor = '';
}));
dz.addEventListener('drop', e => addFiles(e.dataTransfer.files));

function addFiles(files) {
  const ok = /\.(docx|md|txt)$/i;
  let skipped = 0, added = 0, dupes = 0;
  [...files].forEach(f => {
    if (!ok.test(f.name)) return skipped++;
    if (picked.some(x => x.file && x.file.name === f.name && x.file.size === f.size)) return dupes++;
    picked.push({ file: f, name: f.name }); added++;
  });
  /* Say what happened, with numbers, every time — nothing is sent yet. */
  const parts = [];
  if (added) parts.push(`${added} article${added === 1 ? '' : 's'} added`);
  if (dupes) parts.push(`${dupes} already in the list`);
  if (skipped) parts.push(`${skipped} skipped (only .docx, .md or .txt)`);
  parts.push(`${picked.length} ready to send`);
  toast(parts.join(' · '), !!skipped);
  renderPicked();
  if (added) $('#pickedPanel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

$('#pasteBox').oninput = e => {
  const n = e.target.value.trim().split(/\s+/).filter(Boolean).length;
  $('#pasteCount').textContent = n.toLocaleString() + ' words' + (n && n < 120 ? ' — too short to review' : '');
};
$('#addPaste').onclick = () => {
  const text = $('#pasteBox').value.trim();
  const n = text.split(/\s+/).filter(Boolean).length;
  if (n < 120) return toast('That is too short to review.', true);
  const first = text.split('\n').find(l => l.trim()) || 'Pasted text';
  picked.push({ text, name: first.trim().slice(0, 70), words: n });
  $('#pasteBox').value = ''; $('#pasteCount').textContent = '0 words';
  toast(`Pasted text added · ${picked.length} ready to send`);
  renderPicked();
};
document.querySelectorAll('#whenSwitch .when-opt').forEach(b =>
  b.onclick = () => { if (!b.disabled) setMode(b.dataset.mode, true); });
$('#clearPicked').onclick = () => { picked = []; confirming = false; renderPicked(); };

/* The button asks once more, with the count and the price, before anything
   is sent. Files can still be added or removed while it is asking. */
let confirming = false;
$('#sendBtn').onclick = () => {
  if (!picked.length) return;
  if (mode === 'now') return sendPicked();          // one article, straight away
  confirming = true; renderPicked();
};
$('#sendYes').onclick = () => { confirming = false; sendPicked(); };
$('#sendNo').onclick = () => { confirming = false; renderPicked(); };

function renderPicked() {
  const panel = $('#pickedPanel');
  if (!panel) return;
  panel.hidden = !picked.length;
  if (!picked.length) return;
  $('#pickedList').innerHTML = picked.map((x, i) => `
    <div class="pick-row">
      <span class="pick-n mono">${i + 1}</span>
      <span class="pick-name">${esc(x.name)}</span>
      <span class="pick-meta">${x.file ? (x.file.size / 1024).toFixed(0) + ' KB' : x.words.toLocaleString() + ' words'}</span>
      <button class="pick-x" data-i="${i}" title="Remove">&#10005;</button>
    </div>`).join('');
  $('#pickedList').querySelectorAll('.pick-x').forEach(b =>
    b.onclick = () => { picked.splice(+b.dataset.i, 1); renderPicked(); });

  const n = picked.length;
  const canQueue = engine === 'openai' && !!(window.PV_CONFIG && window.PV_CONFIG.queue);
  const each = mode === 'queue' && canQueue ? qEst('rescore').usd / 2 : est('rescore').usd;
  const total = each * n;
  const nowOpt = document.querySelector('#whenSwitch .when-opt[data-mode="now"]');
  nowOpt.disabled = n > 1;
  nowOpt.title = n > 1 ? 'Review now takes one article at a time — send these as a batch, or remove all but one' : '';
  if (n > 1 && mode === 'now' && canQueue) setMode('queue');

  $('#pickedTotal').innerHTML = `<b>${n} article${n === 1 ? '' : 's'}</b> &middot; ${approx(each)} each &middot; total <b>${approx(total)}</b>`
    + (mode === 'queue' ? '' : ' &middot; reviewed straight away');
  $('#sendBtn').innerHTML = mode === 'queue'
    ? `Send batch &middot; ${n} article${n === 1 ? '' : 's'} &middot; ${approx(total)} &rarr;`
    : `Review now &middot; ${approx(total)} &rarr;`;
  $('#sendBtn').disabled = mode === 'now' && n !== 1;
  $('#sendBtn').hidden = confirming;
  $('#confirmRow').hidden = !confirming;
  if (confirming) $('#confirmText').innerHTML =
    `Send <b>${n} article${n === 1 ? '' : 's'}</b> as one batch for about <b>${approx(total)}</b>?`;
}

async function sendPicked() {
  if (!picked.length) return;
  if (mode !== 'queue' || engine !== 'openai') {
    if (picked.length !== 1) return toast('Review now takes one article at a time.', true);
    const x = picked[0]; picked = []; renderPicked();
    return submit(x.file ? { file: x.file } : { text: x.text });
  }
  const fd = new FormData();
  picked.forEach(x => x.file ? fd.append('files', x.file) : fd.append('texts', x.text));
  const n = picked.length;
  go('batch', 'Batch');
  $('#s-batch').innerHTML = `<div class="working"><span class="spinner"></span>
    Uploading ${n} article${n === 1 ? '' : 's'} and sending the batch&hellip;</div>`;
  try {
    const g = await api('/api/batches', { method: 'POST', body: fd });
    picked = []; renderPicked();
    if (g.problems && g.problems.length) toast('Not sent: ' + g.problems.join(' · '), true);
    loadList();
    openBatch(g.id);
  } catch (e) {
    $('#s-batch').innerHTML =
      `<div class="blocker"><span style="color:var(--must)">&#9888;</span>
       <div><div class="blocker-t">Could not send the batch</div>
       <div style="font-size:12.5px; color:var(--ink-2)">${esc(e.message)}</div></div></div>
       <button class="btn" onclick="go('new')">Back</button>`;
  }
}

/* One article, reviewed straight away. */
async function submit({ file, text }) {
  state.batchNav = null;
  go('review');
  $('#s-review').innerHTML =
    `<div class="working"><span class="spinner"></span>
       <div>
         <div id="workPhase">Reading against the ParentVeda Bible&hellip;</div>
         <div class="worknote">${ENGINE_LABEL[engine]} reads the whole article against twelve parameters before
           it writes anything. Two to three minutes is normal &mdash;
           <span id="workClock" class="mono">0:00</span> elapsed.</div>
       </div>
     </div>`;
  startClock();
  const fd = new FormData();
  if (file) fd.append('file', file); else fd.append('text', text);
  fd.append('engine', engine);
  try {
    const res = await api('/api/review', { method: 'POST', body: fd });
    stopClock();
    state.articleId = res.article_id;
    state.runId = res.run_id;
    state.review = res.review;
    state.estimates = res.estimates;
    renderReview();
    loadList();
  } catch (e) {
    stopClock();
    $('#s-review').innerHTML =
      `<div class="blocker"><span style="color:var(--must)">&#9888;</span>
       <div><div class="blocker-t">Review failed</div>
       <div style="font-size:12.5px; color:var(--ink-2)">${esc(e.message)}</div></div></div>
       <button class="btn" onclick="location.reload()">Start again</button>`;
  }
}

/* Pull an article out of a batch and review it instantly. Only works while
   it is still waiting or after it failed — once sent to OpenAI it is on its way. */
async function reviewQueuedNow(articleId) {
  state.batchNav = null;
  go('review');
  $('#s-review').innerHTML =
    `<div class="working"><span class="spinner"></span>
       <div><div id="workPhase">Reading against the ParentVeda Bible&hellip;</div>
       <div class="worknote">About 90 seconds &mdash; <span id="workClock" class="mono">0:00</span> elapsed.</div></div>
     </div>`;
  startClock();
  try {
    const res = await api('/api/queue/now', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ article_id: articleId })
    });
    stopClock();
    state.articleId = res.article_id; state.runId = res.run_id;
    state.review = res.review; state.estimates = res.estimates;
    renderReview(); loadList();
  } catch (e) {
    stopClock();
    $('#s-review').innerHTML =
      `<div class="blocker"><span style="color:var(--must)">&#9888;</span>
       <div><div class="blocker-t">Could not review it now</div>
       <div style="font-size:12.5px; color:var(--ink-2)">${esc(e.message)}</div></div></div>`;
  }
}

/* ------------------------------------------------------------ one batch */

let batchTimer;
const whenFull = t => new Date(t).toLocaleString('en-IN',
  { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', hour12: true });

async function openBatch(id) {
  go('batch', 'Batch');
  clearTimeout(batchTimer);
  if (!$('#s-batch').querySelector('.batch-head'))
    $('#s-batch').innerHTML = '<div class="working"><span class="spinner"></span> Loading the batch&hellip;</div>';
  let g;
  try { g = await api('/api/batches/' + id); }
  catch (e) { return $('#s-batch').innerHTML = `<div class="empty">Could not load the batch: ${esc(e.message)}</div>`; }
  $('#crumb').textContent = 'Batch #' + g.seq;

  const [word, colour] = BATCH_STATE[g.status] || BATCH_STATE.waiting;
  const each = qEst('rescore').usd / 2;
  const busy = g.status === 'waiting' || g.status === 'reviewing';
  const done = g.items.filter(i => i.status === 'done');
  const failed = g.items.filter(i => i.status === 'failed');
  const spent = done.length * each;

  const row = (it, i) => {
    const st = it.status;
    const right = st === 'done' && it.overall != null
      ? `<b class="mono" style="color:${BAND(it.overall)}">${it.overall.toFixed(2)}</b>`
      : st === 'submitted' ? '<span class="bi-state" style="color:var(--should)">Reviewing&hellip;</span>'
      : st === 'queued' ? '<span class="bi-state" style="color:var(--should)">Sending&hellip;</span>'
      : '<span class="bi-state" style="color:var(--must)">Failed</span>';
    return `
      <button class="b-row${st === 'done' ? '' : ' is-off'}" data-run="${it.run_id || ''}" data-i="${i}" ${st === 'done' ? '' : 'disabled'}>
        <span class="b-n mono">${i + 1}</span>
        <span class="b-title">${esc(it.title)}</span>
        <span class="b-words mono">${(it.word_count || 0).toLocaleString()} w</span>
        <span class="b-right">${right}</span>
        ${st === 'failed' && it.error ? `<span class="b-err">${esc(it.error)}</span>` : ''}
      </button>`;
  };

  let panel = '';
  if (busy) {
    panel = `
      <div class="queued-panel">
        <span aria-hidden="true" style="font-size:22px">&#9203;</span>
        <div>
          <div class="qp-t">${g.status === 'waiting' ? 'Sending to the reviewer' : `Reviewing ${g.n} article${g.n === 1 ? '' : 's'}`}
            ${g.done ? ` &mdash; ${g.done} of ${g.n} done` : ''}</div>
          <p>Usually reviewed within the hour, always by tomorrow morning. This page updates itself
             &mdash; you can close it and come back. Reviewed articles open as soon as they are ready.</p>
        </div>
      </div>`;
  } else if (g.status === 'failed') {
    panel = `
      <div class="queued-panel" style="border-color:var(--must); background:var(--must-bg)">
        <span aria-hidden="true" style="font-size:22px; color:var(--must)">&#9888;</span>
        <div>
          <div class="qp-t" style="color:var(--must)">The whole batch failed &mdash; nothing was charged</div>
          <p>${esc(failed[0] && failed[0].error || 'No result came back.')}</p>
        </div>
        <div class="qp-acts">
          <button class="btn btn-primary" id="bResend">Send the whole batch again &middot; ${g.n} article${g.n === 1 ? '' : 's'} &middot; ${approx(each * g.n)}</button>
        </div>
      </div>`;
  } else if (g.status === 'partly_failed') {
    panel = `
      <div class="queued-panel" style="border-color:var(--must); background:var(--must-bg)">
        <span aria-hidden="true" style="font-size:22px; color:var(--must)">&#9888;</span>
        <div>
          <div class="qp-t" style="color:var(--must)">${done.length} reviewed &middot; ${failed.length} failed</div>
          <p>The reviewed ones are ready below. The failed ones were not charged and can go again.</p>
        </div>
        <div class="qp-acts">
          <button class="btn btn-primary" id="bResend">Send the ${failed.length} failed again &middot; ${approx(each * failed.length)}</button>
        </div>
      </div>`;
  } else {
    panel = `
      <div class="queued-panel" style="border-color:var(--good); background:var(--good-bg)">
        <span aria-hidden="true" style="font-size:22px; color:var(--good)">&#10003;</span>
        <div>
          <div class="qp-t" style="color:var(--good)">All ${g.n} reviewed &middot; ${inr(spent)} total</div>
          <p>Open any article below to go through its findings. Prev / Next at the top of each
             review moves through the batch.</p>
        </div>
        <div class="qp-acts">
          <button class="btn btn-primary" id="bStart">Start with the first &rarr;</button>
        </div>
      </div>`;
  }

  const list = g.status === 'partly_failed'
    ? `<div class="b-sec"><h3>Reviewed &middot; ${done.length}</h3>${g.items.map((it, i) => it.status === 'done' ? row(it, i) : '').join('')}</div>
       <div class="b-sec"><h3 style="color:var(--must)">Failed &middot; ${failed.length}</h3>${g.items.map((it, i) => it.status === 'failed' ? row(it, i) : '').join('')}</div>`
    : `<div class="b-sec">${g.items.map(row).join('')}</div>`;

  $('#s-batch').innerHTML = `
    <div class="batch-head">
      <h1 class="art-title">Batch #${g.seq}</h1>
      <div class="badges" style="margin:8px 0 16px">
        <span class="badge" style="color:${colour}; border-color:${colour}">${word}</span>
        <span class="badge b-neutral">${g.n} article${g.n === 1 ? '' : 's'}</span>
        <span class="badge b-neutral">sent ${whenFull(g.created_at)}</span>
        ${g.created_by ? `<span class="badge b-neutral">${esc(g.created_by)}</span>` : ''}
      </div>
    </div>
    ${panel}
    <div class="panel">${list}</div>`;

  const nav = (i) => ({ id: g.id, seq: g.seq, items: g.items, index: i });
  $('#s-batch').querySelectorAll('.b-row:not([disabled])').forEach(b =>
    b.onclick = () => openRun(b.dataset.run, nav(+b.dataset.i)));
  const st = $('#bStart');
  if (st) st.onclick = () => { const i = g.items.findIndex(x => x.status === 'done'); openRun(g.items[i].run_id, nav(i)); };
  const rs = $('#bResend');
  if (rs) rs.onclick = async () => {
    rs.disabled = true;
    try {
      const out = await api('/api/batches/' + g.id + '/resend', { method: 'POST' });
      toast(`${out.resent} sent again`); loadList(); openBatch(g.id);
    } catch (e) { rs.disabled = false; toast('Could not send again: ' + e.message, true); }
  };

  if (busy) batchTimer = setTimeout(() => {
    if ($('#s-batch').classList.contains('on')) openBatch(g.id);
  }, 30000);
}

/* ----------------------------------------------------------- all batches */

async function openBatches() {
  go('batches');
  $('#s-batches').innerHTML = '<div class="working"><span class="spinner"></span> Loading batches&hellip;</div>';
  try {
    const gs = await api('/api/batches?limit=100');
    $('#s-batches').innerHTML = `
      <h1 class="art-title" style="margin-bottom:6px">Batches</h1>
      <p style="margin:0 0 18px; font-size:13px; color:var(--ink-2)">
        Everything sent as a batch, newest first. A batch is whatever was uploaded together;
        each article in it is reviewed and priced on its own.</p>
      ${gs.length ? gs.map(g => `
        <button class="b-card" data-id="${g.id}">
          <div class="b-card-top">
            <b class="mono">Batch #${g.seq}</b>
            <span>${g.n} article${g.n === 1 ? '' : 's'}</span>
            <span>&middot; ${whenFull(g.created_at)}</span>
            ${g.created_by ? `<span>&middot; ${esc(g.created_by)}</span>` : ''}
            <span class="b-card-state">${batchStatusLine(g)}</span>
          </div>
          <div class="b-card-titles">${g.titles.map(t => esc(t)).join(' &middot; ')}${g.n > 3 ? ' &middot; &hellip;' : ''}</div>
        </button>`).join('')
        : '<div class="empty">No batches yet. Drop a few articles on the New article screen and send them together.</div>'}`;
    $('#s-batches').querySelectorAll('.b-card').forEach(b => b.onclick = () => openBatch(b.dataset.id));
  } catch (e) {
    $('#s-batches').innerHTML = `<div class="empty">Could not load batches: ${esc(e.message)}</div>`;
  }
}

/* ------------------------------------------------------------- progress */

let clockTimer = null;
const PHASES = [
  [0,   'Reading against the ParentVeda Bible&hellip;'],
  [35,  'Checking the twelve parameters&hellip;'],
  [80,  'Comparing against your published articles&hellip;'],
  [125, 'Writing up the findings&hellip;'],
  [200, 'Still working &mdash; long articles take longer.'],
];

function startClock(phases = PHASES) {
  const t0 = Date.now();
  stopClock();
  clockTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    const el = $('#workClock');
    if (!el) return stopClock();
    el.textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    const phase = [...phases].reverse().find(([at]) => s >= at);
    const p = $('#workPhase');
    if (p && phase) p.innerHTML = phase[1];
  }, 1000);
}
function stopClock() {
  if (clockTimer) { clearInterval(clockTimer); clockTimer = null; }
}

/* ---------------------------------------------------------------- reopen */

async function openRun(runId, nav) {
  state.batchNav = nav || null;
  go('review');
  $('#s-review').innerHTML =
    '<div class="working"><span class="spinner"></span> Reopening&hellip;</div>';
  try {
    const res = await api('/api/runs/' + runId + '?engine=' + engine);
    state.articleId = res.article_id;
    state.runId = res.run_id;
    state.review = res.review;
    state.estimates = res.estimates;
    renderReview();
  } catch (e) {
    $('#s-review').innerHTML =
      `<div class="empty">Could not reopen that review: ${esc(e.message)}</div>`;
  }
}

/* ---------------------------------------------------------------- review */

function renderReview() {
  const r = state.review, s = r.overall;
  const groups = ['must', 'should', 'polish']
    .map(t => [t, r.feedback.filter(f => f.tier === t)]).filter(([, f]) => f.length);

  const nav = state.batchNav;
  const bar = nav ? (() => {
    const doneIdx = nav.items.map((x, i) => x.status === 'done' ? i : -1).filter(i => i >= 0);
    const pos = doneIdx.indexOf(nav.index);
    const prev = pos > 0 ? doneIdx[pos - 1] : -1, next = pos < doneIdx.length - 1 ? doneIdx[pos + 1] : -1;
    return `
    <div class="batch-bar">
      <button class="btn btn-sm" id="bbBack">&larr; Batch #${nav.seq}</button>
      <span class="bb-pos">Article <b>${nav.index + 1}</b> of ${nav.items.length}</span>
      <span class="bb-nav">
        <button class="btn btn-sm" id="bbPrev" ${prev < 0 ? 'disabled' : ''} data-i="${prev}">&larr; Prev</button>
        <button class="btn btn-sm" id="bbNext" ${next < 0 ? 'disabled' : ''} data-i="${next}">Next &rarr;</button>
      </span>
    </div>`;
  })() : '';

  $('#s-review').innerHTML = bar + `
    <div class="score-head">
      <div class="score-num" style="color:${BAND(s)}">${s.toFixed(1)}<span class="score-of">/10</span></div>
      <div class="score-side">
        <h1 class="art-title" id="revTitle"></h1>
        <div class="badges">
          <span class="badge ${s >= 9 ? 'b-good' : 'b-must'}">${esc(VERDICTS[r.verdict] || r.verdict)}</span>
          <span class="badge b-neutral">${esc(r.article_type.replace(/_/g, ' '))}</span>
          <span class="badge b-neutral mono">${esc(shortModel(r.usage.model))}${r.usage.batch ? ' &middot; via batch' : ' &middot; instant'} &middot; ${inr(r.cost)}${r.duration_s && !r.usage.batch ? ' &middot; ' + mmss(r.duration_s) : ''}</span>
        </div>
      </div>
    </div>

    ${r.reopened && !nav ? `<div class="reopened">Reopened from history — no new review was run.
      Decisions are saved as you make them.</div>` : ''}

    <div class="anchor">
      <div class="anchor-job">${esc(r.article_level.job || '')}</div>
      <div class="anchor-scope">${esc(r.article_level.scope || '')}</div>
      <div class="anchor-meta">
        ${r.article_level.length_note ? `<span>${esc(r.article_level.length_note)}</span>` : ''}
        ${r.article_level.leave_alone ? `<span class="anchor-keep"><b>Leave alone:</b> ${esc(r.article_level.leave_alone)}</span>` : ''}
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
          <article class="finding${f.headline ? ' is-headline' : ''}${f.outcome && f.outcome !== 'pending' ? ' done ' + (f.outcome === 'rejected' ? 'rejected' : 'accepted') : ''}" data-id="${f.id}" data-tier="${f.tier}" data-kind="${f.free ? 'line' : 'structural'}">
            <div class="f-num">${f.position + 1}</div>
            <div class="f-body">
              ${f.headline ? '<div class="f-flag">Start here &mdash; the biggest issue</div>' : ''}
              <div class="f-sum">${esc(f.summary)}</div>
              <div class="f-param">
                ${f.free
                  ? '<span class="f-cost free" title="The replacement sentence is already written; applying it is an exact swap">Swap &middot; free</span>'
                  : `<span class="f-cost paid" title="${f.kind === 'line' ? 'The proposal is an instruction rather than the sentence itself' : "A change to the article's shape"}; ${esc(shortModel(est('rewrite').model || ''))} writes it">Rewrite &middot; ${approx(est('rewrite').usd)}</span>`}
                ${esc(f.parameter)}${f.needs_validation ? ' &middot; <span class="f-val">clinician to confirm</span>' : ''}
              </div>
              <div class="ba">
                ${f.quote
                  ? `<div class="ba-now"><b>What it says now</b><span>${esc(f.quote)}</span></div>`
                  : `<div class="ba-now ba-none"><b>What it says now</b>
                     <span>Nothing to replace &mdash; this is a change to the article's
                     shape rather than to a sentence.</span></div>`}
                <div class="ba-arrow" aria-hidden="true">&darr;</div>
                <div class="ba-new"><b>${f.kind === 'line' ? 'Change it to' : 'What to do'}</b><span>${esc(f.proposed)}</span></div>
              </div>
              ${f.rationale ? `<div class="f-why">${esc(f.rationale)}</div>` : ''}
            </div>
            <div class="f-acts">
              <button class="act yes" aria-pressed="${['accepted','edited'].includes(f.outcome) ? 'true' : 'false'}" title="Accept">&#10003;</button>
              <button class="act no" aria-pressed="${f.outcome === 'rejected' ? 'true' : 'false'}" title="Reject">&#10005;</button>
            </div>
          </article>`).join('')}
      </div>`).join('')}

    <div class="footbar">
      <span class="foot-status" id="tally"></span>
      <div class="topbar-actions">
        <button class="btn" id="chooseAll">Choose for me</button>
        <button class="btn btn-primary" id="applyBtn">Apply &rarr;</button>
      </div>
    </div>`;

  if (nav) {
    $('#bbBack').onclick = () => openBatch(nav.id);
    ['bbPrev', 'bbNext'].forEach(k => { const b = $('#' + k); if (b && !b.disabled)
      b.onclick = () => openRun(nav.items[+b.dataset.i].run_id, { ...nav, index: +b.dataset.i }); });
  }
  $('#revTitle').textContent = '';
  api('/api/articles/' + state.articleId)
    .then(d => { $('#revTitle').textContent = d.article.title; })
    .catch(() => {});

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
  const accepted = () => cards.filter(c => c.querySelector('.yes').getAttribute('aria-pressed') === 'true');
  const tally = () => {
    const done = cards.filter(c => c.classList.contains('done')).length;
    const left = cards.filter(c => !c.classList.contains('done') && c.dataset.tier !== 'polish').length;
    const acc = accepted();
    const swaps = acc.filter(c => c.dataset.kind === 'line').length;
    const rewrites = acc.length - swaps;
    const cost = rewrites ? `${approx(est('rewrite').usd)}` : 'free';
    $('#tally').innerHTML = `${done} of ${cards.length} decided &middot; ${left} remaining above polish`
      + (acc.length ? `<br><b>${acc.length} accepted</b> &middot; ${swaps} swap${swaps === 1 ? '' : 's'} (free)`
        + (rewrites ? ` &middot; ${rewrites} rewrite${rewrites === 1 ? '' : 's'}` : '')
        + ` &middot; apply cost <b>${cost}</b>` : '');
    const btn = $('#applyBtn');
    if (btn) btn.innerHTML = !acc.length ? 'Apply &rarr;'
      : rewrites ? `Apply &mdash; ${rewrites} rewrite${rewrites === 1 ? '' : 's'}, ${approx(est('rewrite').usd)} &rarr;`
      : 'Apply &mdash; free &rarr;';
  };
  /* Clicking the already-pressed tick or cross undoes it: back to undecided. */
  const decide = async (card, yes, advance) => {
    const btn = card.querySelector(yes ? '.yes' : '.no');
    const undo = btn.getAttribute('aria-pressed') === 'true';
    card.querySelectorAll('.act').forEach(a => a.setAttribute('aria-pressed', 'false'));
    card.classList.remove('done', 'accepted', 'rejected');
    if (!undo) {
      btn.setAttribute('aria-pressed', 'true');
      card.classList.add('done', yes ? 'accepted' : 'rejected');
    }
    tally();
    try {
      await api('/api/decide', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feedback_id: card.dataset.id,
                               outcome: undo ? 'pending' : yes ? 'accepted' : 'rejected' })
      });
    } catch (e) { toast('Could not save that decision: ' + e.message, true); }
    if (undo) return;
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
      c.classList.remove('cursor', 'accepted', 'rejected');
      c.classList.add('done', yes ? 'accepted' : 'rejected');
    });
    tally();
    toast('Must-fix and should-fix accepted');
  };

  $('#applyBtn').onclick = async () => {
    const acc = accepted();
    if (!acc.length) return toast('Accept at least one finding first.', true);
    const rewrites = acc.filter(c => c.dataset.kind !== 'line').length;
    go('result');
    $('#s-result').innerHTML =
      `<div class="working"><span class="spinner"></span>
         <div>
           <div id="workPhase">Applying the accepted changes&hellip;</div>
           <div class="worknote">${rewrites
             ? `Sentence swaps go in instantly; ${esc(shortModel(est('rewrite').model || ''))} is writing the
                ${rewrites} structural change${rewrites === 1 ? '' : 's'}. Under a minute is normal &mdash;`
             : 'Exact sentence swaps only &mdash; this takes a moment &mdash;'}
             <span id="workClock" class="mono">0:00</span> elapsed.</div>
         </div>
       </div>`;
    startClock([[0, 'Applying the accepted changes&hellip;'],
                [45, 'Still writing &mdash; a long new section takes longer.']]);
    try {
      const res = await api('/api/rewrite', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: state.runId, engine })
      });
      stopClock();
      state.estimates = res.estimates || state.estimates;
      renderResult(res, state.review);
      loadList();
    } catch (e) {
      stopClock();
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

function renderResult(res, review) {
  const d = res.diff, score = review.overall;
  const total = review.feedback.length;
  const paid = res.edit_usage != null;
  $('#s-result').innerHTML = `
    <h1 class="art-title" style="margin-bottom:18px">Applied</h1>

    <div class="panel">
      <h3>Result</h3>
      <div class="delta">
        <span class="to" style="color:${BAND(score)}">${score.toFixed(1)}</span>
        <span class="applied-of">${res.applied} of ${total} finding${total === 1 ? '' : 's'} applied</span>
      </div>
      <div class="diffnote">
        ${res.swapped.length} sentence swap${res.swapped.length === 1 ? '' : 's'} (free)
        ${res.rewritten.length ? ` &middot; ${res.rewritten.length} rewrite${res.rewritten.length === 1 ? '' : 's'} by ${esc(shortModel(res.edit_usage.model))} &middot; ${inr(res.edit_cost)}` : ''}
        &middot; ${d.words_before.toLocaleString()} &rarr; ${d.words_after.toLocaleString()} words
        ${res.duration_s ? ' &middot; ' + mmss(res.duration_s) : ''}
      </div>
      <div class="rescore-note">
        The score is the review's. Nothing was re-read &mdash; the reviewer wrote these
        changes, so the article now reads as it asked.
      </div>
    </div>

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
        <span class="foot-status">Export, brief the images, or send for clinical verification below.</span>
        <div class="topbar-actions">
          <button class="btn" id="copyFinal">Copy text</button>
          <button class="btn" id="dlDocx">Word</button>
          <button class="btn" id="dlPdf">PDF</button>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3>Images</h3>
      <p style="margin:0 0 13px; font-size:13px; color:var(--ink-2)">
        A brief for the cover and for up to two visuals inside the article &mdash;
        prompts a designer or an image tool can use as they are. Prompts only; no
        images are generated.
      </p>
      <div id="imagesArea">
        <button class="btn btn-primary" id="makeImages">Write image briefs &middot; ${approx(est('images').usd)}</button>
      </div>
    </div>

    <div class="panel">
      <h3>Clinical verification</h3>
      <p style="margin:0 0 13px; font-size:13px; color:var(--ink-2)">
        Generates a one-page sheet of every claim, number and red flag, so a
        clinician can sign off without reading the article.
      </p>
      <div id="sheetArea">
        <button class="btn btn-primary" id="makeSheet">Generate verification sheet &middot; ${approx(est('sheet').usd)}</button>
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
  $('#dlPdf').onclick = () => { location.href = `/api/export/${aid}.pdf`; };
  $('#makeSheet').onclick = () => makeSheet(aid);
  $('#makeImages').onclick = () => makeImages(aid);
}

/* ------------------------------------------------------------- images */

function briefCard(b, label) {
  return `
    <div class="brief">
      <div class="brief-head">
        <b>${esc(label)}</b>
        <span class="brief-type mono">${esc(b.type)}</span>
        ${b.placement && b.type !== 'cover' ? `<span class="brief-place">under &ldquo;${esc(b.placement)}&rdquo;</span>` : ''}
      </div>
      <div class="brief-purpose">${esc(b.purpose)}</div>
      <div class="brief-prompt"><span>${esc(b.prompt)}</span>
        <button class="btn btn-sm copy-prompt" data-text="${esc(b.prompt)}">Copy prompt</button></div>
      <div class="brief-meta"><b>Alt text</b> ${esc(b.alt_text)}</div>
      <div class="brief-meta"><b>Avoid</b> ${esc(b.avoid)}</div>
    </div>`;
}

function renderBriefs(area, res) {
  area.innerHTML = `
    ${res.cost != null ? `<div class="badges" style="margin-bottom:13px"><span class="badge b-neutral mono">${inr(res.cost)}</span></div>` : ''}
    ${briefCard(res.cover, 'Cover')}
    ${(res.visuals || []).map((v, i) => briefCard(v, 'Visual ' + (i + 1))).join('')}
    ${res.note ? `<div class="brief-note">${esc(res.note)}</div>` : ''}`;
  area.querySelectorAll('.copy-prompt').forEach(b => b.onclick = () =>
    navigator.clipboard.writeText(b.dataset.text).then(() => toast('Prompt copied')));
}

async function makeImages(articleId) {
  const area = $('#imagesArea');
  area.innerHTML = '<div class="working"><span class="spinner"></span> Writing the briefs&hellip;</div>';
  try {
    const res = await api('/api/image-briefs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ article_id: articleId, engine })
    });
    renderBriefs(area, res);
  } catch (e) {
    area.innerHTML = `<div class="empty">Could not write the briefs: ${esc(e.message)}</div>`;
  }
}

/* ------------------------------------------------------- verification */

async function makeSheet(articleId) {
  const area = $('#sheetArea');
  area.innerHTML = '<div class="working"><span class="spinner"></span> Extracting claims, numbers and red flags&hellip;</div>';
  try {
    const res = await api('/api/doctor-sheet', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ article_id: articleId, engine })
    });
    const experts = res.experts || [];
    area.innerHTML = `
      <div class="badges" style="margin-bottom:13px">
        <span class="badge b-neutral">Specialty needed: <b>${esc(res.specialty)}</b></span>
        <span class="badge b-neutral mono">${inr(res.cost)}</span>
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
          <button class="btn" id="dlSheetDocx">Sheet &mdash; Word</button>
          <button class="btn" id="dlSheet">Sheet &mdash; PDF</button>
          ${experts.length ? '<button class="btn btn-primary" id="sendSheet">Send to selected</button>' : ''}
        </div>
      </div>`;

    $('#sheetBody').textContent = res.sheet;
    $('#dlSheet').onclick = () => { location.href = `/api/sheet/${res.verification_id}.pdf`; };
    const sd = $('#dlSheetDocx');
    if (sd) sd.onclick = () => { location.href = `/api/sheet/${res.verification_id}.docx`; };
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

let detailTimer;
async function openDetail(id) {
  go('detail');
  $('#s-detail').innerHTML =
    '<div class="working"><span class="spinner"></span> Loading history&hellip;</div>';
  try {
    const d = await api('/api/articles/' + id + '/trail');
    const when = t => new Date(t).toLocaleString('en-IN',
      { day: 'numeric', month: 'short', year: 'numeric',
        hour: 'numeric', minute: '2-digit', hour12: true });
    const day = t => new Date(t).toLocaleDateString('en-IN',
      { day: 'numeric', month: 'short' });

    const runs = d.versions.flatMap(v => v.runs);
    const last = runs[runs.length - 1];
    const cost = runs.reduce((a, r) => a + Number(r.cost_usd || 0), 0);
    const decided = runs.reduce((a, r) => ({
      acc: a.acc + r.accepted, rej: a.rej + r.rejected,
      all: a.all + r.feedback.length
    }), { acc: 0, rej: 0, all: 0 });

    const OUTCOME = {
      accepted: ['Accepted', 'var(--good)'],
      edited:   ['Edited then accepted', 'var(--good)'],
      rejected: ['Rejected', 'var(--ink-3)'],
      pending:  ['Not decided', 'var(--should)'],
    };

    const findingRow = f => {
      const [label, colour] = OUTCOME[f.outcome] || OUTCOME.pending;
      return `
        <div class="tr-find">
          <div class="tr-find-top">
            <span class="tr-tier tr-${f.tier}">${TIERS[f.tier]}</span>
            <span class="tr-sum">${esc(f.summary)}</span>
            <span class="tr-outcome" style="color:${colour}">${label}</span>
          </div>
          ${f.quote ? `<div class="tr-was">${esc(f.quote)}</div>` : ''}
          <div class="tr-became">${esc(f.edited_text || f.proposed)}</div>
        </div>`;
    };

    const versionBlock = (v, i) => {
      const r = v.runs[v.runs.length - 1];
      const prev = i > 0 ? d.versions[i - 1] : null;
      const prevRun = prev && prev.runs[prev.runs.length - 1];
      const delta = (prevRun && r)
        ? `<span class="tr-delta">${Number(prevRun.overall).toFixed(1)} &rarr;
             <b style="color:${BAND(r.overall)}">${Number(r.overall).toFixed(1)}</b></span>`
        : (r ? `<span class="tr-delta"><b style="color:${BAND(r.overall)}">${Number(r.overall).toFixed(1)}</b></span>` : '');

      const allFindings = v.runs.flatMap(x => x.feedback);

      return `
      <details class="tr-ver"${i === d.versions.length - 1 ? ' open' : ''}>
        <summary>
          <span class="caret">&rsaquo;</span>
          <span class="tr-vno mono">v${v.version_no}</span>
          <span class="tr-what">${v.source === 'rewrite' ? 'Applied accepted changes' + (v.note ? ' &mdash; ' + esc(v.note) : '') : 'Uploaded'}</span>
          <span class="tr-meta mono">${v.word_count.toLocaleString()} w</span>
          <span class="tr-meta">${day(v.created_at)}</span>
          ${delta}
        </summary>
        <div class="tr-body">
          ${v.source === 'rewrite' ? `
            <div class="tr-run">
              <b>Applied</b> ${when(v.created_at)}
              ${v.created_by ? `&middot; ${esc(v.created_by)}` : ''}
              &middot; ${Number(v.cost_usd || 0) ? inr(Number(v.cost_usd)) : 'free'}
            </div>` : ''}
          ${v.image_briefs ? `
            <details class="tr-sub">
              <summary><span class="caret">&rsaquo;</span> Image briefs &mdash; cover + ${(v.image_briefs.visuals || []).length} visual${(v.image_briefs.visuals || []).length === 1 ? '' : 's'}</summary>
              <div class="tr-briefs"></div>
            </details>` : ''}
          ${v.runs.map(x => `
            <div class="tr-run">
              <b>${x.kind === 'review' ? 'Reviewed' : 'Re-scored'}</b> ${when(x.created_at)}
              &middot; ${esc(shortModel(x.model))} ${esc(x.effort)}${x.batch ? ' &middot; Batch #' + (d.queue && d.queue.seq || '?') + ' (half price)' : ' &middot; instant'}
              &middot; ${inr(Number(x.cost_usd))}
              ${x.duration_s && !x.batch ? '&middot; ' + mmss(Number(x.duration_s)) : ''}
              &middot; ${esc(VERDICTS[x.verdict] || x.verdict)}
              ${x.actor_email ? `&middot; ${esc(x.actor_email)}` : ''}
              ${(x.blockers || []).length ? `<div class="tr-block">${x.blockers.map(b => esc(b)).join('<br>')}</div>` : ''}
            </div>`).join('')}

          ${v.runs.some(x => x.feedback.some(f => !f.outcome || f.outcome === 'pending')) ? `
            <div class="tr-continue">
              <span>${v.runs.reduce((n, x) => n + x.feedback.filter(f => !f.outcome || f.outcome === 'pending').length, 0)} findings still undecided</span>
              <button class="btn btn-primary tr-resume" data-run="${v.runs[v.runs.length - 1].id}"
                      style="padding:4px 11px; font-size:12.5px">Continue deciding</button>
            </div>` : ''}

          ${allFindings.length ? `
            <details class="tr-sub">
              <summary><span class="caret">&rsaquo;</span>
                ${allFindings.length} findings &mdash;
                ${allFindings.filter(f => ['accepted','edited'].includes(f.outcome)).length} accepted,
                ${allFindings.filter(f => f.outcome === 'rejected').length} rejected
              </summary>
              <div class="tr-finds">${allFindings.map(findingRow).join('')}</div>
            </details>` : ''}

          <details class="tr-sub">
            <summary><span class="caret">&rsaquo;</span> Read the article as it was at v${v.version_no}</summary>
            <div class="finalbody tr-text"></div>
          </details>
        </div>
      </details>`;
    };

    const qs = d.queue && d.queue.status;
    const qWaiting = qs === 'queued' || qs === 'submitted';
    const qFailed = qs === 'failed' && !/instantly/.test(d.queue.error || '') && !last;
    const bseq = d.queue && d.queue.seq;
    const queuePanel = qWaiting ? `
      <div class="queued-panel">
        <span aria-hidden="true" style="font-size:20px">&#9203;</span>
        <div>
          <div class="qp-t">Reviewing &middot; part of Batch #${bseq}</div>
          <p>Sent ${when(d.queue.sent_at || d.queue.created_at)}${d.queue.batch_size > 1 ? ` with ${d.queue.batch_size - 1} other article${d.queue.batch_size === 2 ? '' : 's'}` : ''}.
             Usually reviewed within the hour, always by tomorrow morning. This page updates on its own.</p>
        </div>
        <div class="qp-acts">
          <button class="btn" id="qOpenBatch">Open Batch #${bseq}</button>
          ${qs === 'queued' ? `<button class="btn" id="qNowDetail">Review this one now &middot; ${approx(qEst('rescore').usd)}</button>` : ''}
        </div>
      </div>` : qFailed ? `
      <div class="queued-panel" style="border-color:var(--must); background:var(--must-bg)">
        <span aria-hidden="true" style="font-size:20px; color:var(--must)">&#9888;</span>
        <div>
          <div class="qp-t" style="color:var(--must)">Not reviewed &mdash; ${d.queue.batch_failed > 1 ? `${d.queue.batch_failed} articles in Batch #${bseq} failed` : `Batch #${bseq} failed`}</div>
          <p>${esc(d.queue.error || 'No result came back.')} Nothing was charged.</p>
        </div>
        <div class="qp-acts">
          <button class="btn btn-primary" id="qResendDetail">Send ${d.queue.batch_failed > 1 ? 'all ' + d.queue.batch_failed : 'it'} again &middot; ${approx(qEst('rescore').usd / 2)}${d.queue.batch_failed > 1 ? ' each' : ''}</button>
          <button class="btn" id="qOpenBatch">Open Batch #${bseq}</button>
        </div>
      </div>` : '';

    $('#s-detail').innerHTML = `
      <h1 class="art-title">${esc(d.article.title)}</h1>
      ${queuePanel}
      <div class="badges" style="margin:9px 0 22px">
        <span class="badge ${last && last.overall >= 9 ? 'b-good' : 'b-neutral'}">${esc(d.article.status.replace(/_/g, ' '))}</span>
        ${d.article.article_type ? `<span class="badge b-neutral">${esc(d.article.article_type.replace(/_/g, ' '))}</span>` : ''}
        ${d.article.created_by ? `<span class="badge b-neutral">${esc(d.article.created_by)}</span>` : ''}
      </div>

      <div class="panel">
        <h3>At a glance</h3>
        <dl class="kv">
          <dt>Current score</dt><dd class="mono" style="color:${last ? BAND(last.overall) : 'inherit'}">
            <b>${last ? Number(last.overall).toFixed(1) : '—'}</b> — ${last ? esc(VERDICTS[last.verdict] || last.verdict) : '—'}</dd>
          <dt>Versions</dt><dd>${d.versions.length}</dd>
          <dt>Findings</dt><dd>${decided.all} &middot; ${decided.acc} accepted, ${decided.rej} rejected</dd>
          <dt>Sent to</dt><dd>${d.verification.length ? esc(d.verification.map(v => v.doctor_name || v.specialty).join(', ')) : 'Not yet sent'}</dd>
          <dt>Total API cost</dt><dd class="mono">${inr(cost + d.versions.reduce((a, v) => a + Number(v.cost_usd || 0), 0))}</dd>
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
                      ? `<span class="badge b-good">${when(v.verified_at)}</span>${v.verified_by ? `<div style="font-size:11px; color:var(--ink-3); margin-top:3px">marked by ${esc(v.verified_by)}</div>` : ''}`
                      : '<span class="badge b-warn">Awaiting</span>'}</td>
                <td style="white-space:nowrap">
                  <button class="btn" style="padding:3px 9px; font-size:12px"
                          onclick="location.href='/api/sheet/${v.id}.pdf'">Sheet</button>
                  ${v.verified_at ? '' : `<button class="btn btn-primary markver" data-id="${v.id}"
                             style="padding:3px 9px; font-size:12px">Mark verified</button>`}
                </td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>` : ''}

      <div class="panel">
        <h3>History</h3>
        <div class="trail">${d.versions.map(versionBlock).join('')}</div>
      </div>`;

    // article text set as textContent, never parsed as HTML
    $('#s-detail').querySelectorAll('.tr-text').forEach((el, i) => {
      el.textContent = d.versions[i].body;
    });
    const qn = $('#qNowDetail');
    if (qn) qn.onclick = () => reviewQueuedNow(id);
    const qr = $('#qResendDetail');
    if (qr) qr.onclick = async () => {
      qr.disabled = true;
      try {
        const out = await api('/api/batches/' + d.queue.group_id + '/resend', { method: 'POST' });
        toast(`${out.resent} sent again`); loadList(); openBatch(d.queue.group_id);
      } catch (e) { qr.disabled = false; toast('Could not send again: ' + e.message, true); }
    };
    const qo = $('#qOpenBatch');
    if (qo) qo.onclick = () => openBatch(d.queue.group_id);
    clearTimeout(detailTimer);
    if (qWaiting) detailTimer = setTimeout(() => {
      if ($('#s-detail').classList.contains('on')) openDetail(id);
    }, 60000);
    $('#s-detail').querySelectorAll('.tr-briefs').forEach(el => {
      const i = [...document.querySelectorAll('#s-detail .tr-ver')].indexOf(el.closest('.tr-ver'));
      renderBriefs(el, d.versions[i].image_briefs);
    });

    document.querySelectorAll('.tr-resume').forEach(b =>
      b.onclick = () => openRun(b.dataset.run));

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
        <div class="topbar-actions">
          <button class="btn" id="reloadRules" title="Pull the latest ruleset and prompts from the database">Reload ruleset</button>
          <button class="btn btn-primary" id="addExpert">+ Add expert</button>
        </div>
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

    $('#reloadRules').onclick = async () => {
      const b = $('#reloadRules');
      b.disabled = true; b.textContent = 'Reloading…';
      try {
        const r = await api('/api/refresh-content', { method: 'POST' });
        toast(Array.isArray(r.changed) && r.changed.length
          ? 'Reloaded — ' + r.changed.join(', ')
          : 'Already current — nothing changed');
      } catch (e) { toast('Could not reload: ' + e.message, true); }
      b.disabled = false; b.textContent = 'Reload ruleset';
    };

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
  if (b.dataset.goto === 'batches') return openBatches();
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
  setEngine(engine);
  document.querySelectorAll('#engineSwitch .chip').forEach(c =>
    c.onclick = () => setEngine(c.dataset.engine));
  syncModeWithEngine();
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

  window.PV_CONFIG = cfg;
  try { if (!localStorage.getItem('pv-engine') && cfg.engine) engine = cfg.engine; } catch (e) {}

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
