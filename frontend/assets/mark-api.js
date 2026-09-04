/* ═══════════════════════════════════════════════════════════════════════
   mark-api.js — the one backend client the interior pages share.

   budget.html grew its own fetch wrapper before there were other pages; this
   is that wrapper's rules, extracted so schedule / rates / teardown don't each
   reinvent them (and drift):

     · base URL from ?api=, then localStorage, then the deployed backend
     · the backend sleeps on Railway's free tier, so warm it once per page
       load and retry a network-level failure exactly once
     · FastAPI returns errors as {detail}, so surface that rather than a
       TypeError that says nothing

   No build step, no modules — a plain script tag, same as the rest of the site.
   ═══════════════════════════════════════════════════════════════════════ */

const MARK_API = (() => {
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get('api');
  if (fromQuery) localStorage.setItem('mark_api', fromQuery);
  return localStorage.getItem('mark_api')
    || (location.hostname === 'localhost' || location.hostname === '127.0.0.1'
        ? 'http://localhost:8000'
        : 'https://backend-production-6ea4.up.railway.app');
})();

const MARK_API_KEY = localStorage.getItem('mark_api_key_header') || '';

let _warmed = false;
async function warmBackend() {
  if (_warmed) return;
  _warmed = true;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 4000);
    await fetch(`${MARK_API}/health`, { method: 'GET', signal: ctl.signal });
    clearTimeout(t);
  } catch { /* the retry in apiPost covers a cold start */ }
}

function _headers(isForm) {
  const h = {};
  if (!isForm) h['Content-Type'] = 'application/json';
  if (MARK_API_KEY) h['X-API-Key'] = MARK_API_KEY;
  return h;
}

async function apiPost(path, body, { timeout = 120000, retry = true } = {}) {
  await warmBackend();
  const isForm = body instanceof FormData;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  try {
    const resp = await fetch(`${MARK_API}${path}`, {
      method: 'POST',
      headers: _headers(isForm),
      body: isForm ? body : JSON.stringify(body || {}),
      signal: ctl.signal,
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const j = await resp.json();
        detail = j.detail || j.error || detail;
      } catch { /* non-JSON error body */ }
      if (retry && [502, 503, 504].includes(resp.status)) {
        await new Promise(r => setTimeout(r, 1200));
        return apiPost(path, body, { timeout, retry: false });
      }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return await resp.json();
  } catch (err) {
    if (retry && (err.name === 'TypeError' || err.name === 'AbortError')) {
      await new Promise(r => setTimeout(r, 1200));
      return apiPost(path, body, { timeout, retry: false });
    }
    if (err.name === 'AbortError') throw new Error('The backend did not answer in time. Try again.');
    if (err.name === 'TypeError') throw new Error(`Could not reach the backend at ${MARK_API}.`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/* ── formatting ──────────────────────────────────────────────────────────
   Indian digit grouping is not a nicety. ₹1,23,45,678 and ₹12,345,678 are
   the same number and only one of them is readable to the person signing it. */
const CURRENCY_SYMBOLS = { INR: '₹', USD: '$', GBP: '£', EUR: '€' };

function fmtMoney(value, code = 'INR', { compact = false } = {}) {
  const n = Number(value || 0);
  const symbol = CURRENCY_SYMBOLS[code] || `${code} `;
  if (compact && code === 'INR') {
    if (Math.abs(n) >= 1e7) return `${symbol}${(n / 1e7).toFixed(2)} Cr`;
    if (Math.abs(n) >= 1e5) return `${symbol}${(n / 1e5).toFixed(2)} L`;
  }
  const locale = code === 'INR' ? 'en-IN' : 'en-US';
  return symbol + n.toLocaleString(locale, { maximumFractionDigits: 0 });
}

function fmtPct(value, { signed = true } = {}) {
  if (value === null || value === undefined) return '—';
  const pct = Number(value) * 100;
  const sign = signed && pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(pct % 1 === 0 ? 0 : 1)}%`;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function download(filename, data, mime = 'application/octet-stream') {
  const blob = data instanceof Blob ? data : new Blob([data], { type: mime });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

function b64ToBlob(b64, mime) {
  const bytes = atob(b64);
  const buf = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i);
  return new Blob([buf], { type: mime });
}

/* Status line shared by every page: one element, three states, no toast
   library. A failure has to be readable and stay on screen. */
function status(el, message, kind = 'info') {
  if (!el) return;
  el.textContent = message || '';
  el.dataset.kind = kind;
  el.hidden = !message;
}
