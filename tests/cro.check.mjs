// Behaviour of cro.js (Octave aggregate behaviour counters + A/B variants) in a fake browser.
// Portable, no dependency: node cro.check.mjs [path/to/cro.js]   (exit code 0 = ok). Vendored byte-identical.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

const file = process.argv[2] || fileURLToPath(new URL('./cro.js', import.meta.url));
const src = fs.readFileSync(file, 'utf8');
const CHROME = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';
const API = 'https://script.google.com/macros/s/TEST/exec';

class El {
  constructor(tag, { id = '', cls = [], sels = [], parent = null, attrs = {}, dataset = {}, ...rest } = {}) {
    Object.assign(this, { tagName: tag.toUpperCase(), id, parentElement: parent, dataset, attrs: { ...attrs }, ...rest });
    this.classList = Object.assign([...cls], { contains: (c) => cls.includes(c) });
    this.sels = new Set([tag, ...sels, ...(id ? ['#' + id, tag + '[id]'] : []), ...cls.map((c) => '.' + c), ...Object.keys(dataset).map((k) => '[data-' + k.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase()) + ']')]);
  }
  matches(sel) { return sel.split(',').some((s) => this.sels.has(s.trim())); }
  closest(sel) { for (let e = this; e; e = e.parentElement) if (e.matches(sel)) return e; return null; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
}

function browser({ ua = CHROME, webdriver = false, search = '', local = {}, session = {}, dnt = '', gpc = false, host = 'outils.vread.fr',
  path = '/candadopyme/', xp, prerendering = false, visibility = 'visible', now = Date.UTC(2026, 9, 13, 15), scrollHeight = 800, attrs = {} } = {}) {
  const sent = [], winL = {}, docL = {}, els = [], observed = [];
  const store = (o) => ({ getItem: (k) => (k in o ? o[k] : null), setItem: (k, v) => { o[k] = String(v); }, removeItem: (k) => { delete o[k]; } });
  const html = new El('html');
  const doc = {
    readyState: 'complete', prerendering, visibilityState: visibility, referrer: 'https://www.google.com/', forms: [], documentElement: Object.assign(html, { scrollHeight }),
    currentScript: { dataset: { site: 'candadopyme', api: API, ...attrs } },
    addEventListener: (k, f) => { (docL[k] ||= []).push(f); }, removeEventListener: (k, f) => { docL[k] = (docL[k] || []).filter((g) => g !== f); },
    querySelectorAll: (sel) => els.filter((e) => e.matches(sel)),
  };
  class IO { constructor(cb) { this.cb = cb; } observe(t) { observed.push({ io: this, t }); } unobserve(t) { const i = observed.findIndex((o) => o.t === t); if (i >= 0) observed.splice(i, 1); } }
  const perf = {};
  class PO { constructor(cb) { this.cb = cb; } observe({ type }) { perf[type] = (list) => this.cb({ getEntries: () => list }); } }
  const clock = { t: now };
  class FakeDate extends Date { constructor(...a) { super(...(a.length ? a : [clock.t])); } static now() { return clock.t; } }
  const win = {
    navigator: { userAgent: ua, webdriver, doNotTrack: dnt, globalPrivacyControl: gpc }, document: doc, location: { hostname: host, pathname: path, search },
    localStorage: store(local), sessionStorage: store(session), innerWidth: 1200, innerHeight: 800, scrollY: 0, URLSearchParams, JSON, Math, String, Intl, RegExp,
    Date: FakeDate, setTimeout: (f) => { f(); return 1; }, IntersectionObserver: IO, PerformanceObserver: PO, OCTAVE_XP: xp,
    addEventListener: (k, f) => { (winL[k] ||= []).push(f); },
    fetch: (u, o) => { assert.equal(u, API + '?action=track'); assert.equal(o.credentials, 'omit'); assert.equal(o.keepalive, true); sent.push(JSON.parse(o.body)); return Promise.resolve(); },
  };
  win.window = win;
  const el = (...a) => { const e = new El(...a); els.push(e); return e; };
  const fireWin = (k, ev = {}) => (winL[k] || []).slice().forEach((f) => f(ev));
  const fireDoc = (k, ev = {}) => (docL[k] || []).slice().forEach((f) => f(ev));
  const hide = () => { doc.visibilityState = 'hidden'; fireDoc('visibilitychange'); };
  const show = () => { doc.visibilityState = 'visible'; fireDoc('visibilitychange'); };
  const run = () => vm.runInNewContext(src, win);
  return { win, doc, html, sent, el, fireWin, fireDoc, hide, show, run, observed, perf, clock, local, session, events: () => sent.flatMap((b) => b.e) };
}
const has = (evs, t, k, v) => evs.some((e) => e[0] === t && (k === undefined || e[1] === k) && (v === undefined || e[2] === v));

// 1. A normal visit: nothing until the tab is hidden, then one batch with only whitelisted fields.
let b = browser();
b.run();
assert.equal(b.sent.length, 0, 'nothing sent while the page is open');
b.hide();
assert.equal(b.sent.length, 1, 'one batch when the tab is hidden');
assert.deepEqual(Object.keys(b.sent[0]).sort(), ['action', 'device', 'e', 'kind', 'page', 'site', 'v']);
assert.equal(b.sent[0].kind, 'cro'); assert.equal(b.sent[0].v, 2); assert.equal(b.sent[0].site, 'candadopyme');
let ev = b.events();
assert.ok(has(ev, 'pv') && ['25', '50', '75', '100'].every((s) => has(ev, 'scroll', '', s)), 'page view and scroll buckets (short page = 100 %)');
assert.ok(has(ev, 'cwv', 'cls', 'good'), 'CLS bucket');
assert.ok(!JSON.stringify(b.sent).includes('Chrome') && !JSON.stringify(b.sent).includes('google.com'), 'neither user agent nor referrer');
b.hide(); b.fireWin('pagehide');
assert.equal(b.sent.length, 1, 'nothing new, nothing sent');

// 2. Scroll depth on a long page, sections, CWV buckets.
b = browser({ scrollHeight: 4800 });
const hero = b.el('section', { id: 'hero' }), offer = b.el('section', { id: 'offer' }), faqSec = b.el('div', { dataset: { section: 'faq' } });
b.run();
assert.equal(b.observed.length, 3, 'sections with an id or data-section are observed');
b.observed[0].io.cb([{ isIntersecting: true, target: hero }, { isIntersecting: false, target: offer }]);
b.observed[0].io.cb([{ isIntersecting: true, target: faqSec }]);
b.win.scrollY = 2100; b.fireWin('scroll');
b.perf['largest-contentful-paint']([{ startTime: 3100 }]);
b.perf['event']([{ interactionId: 0, duration: 900 }, { interactionId: 7, duration: 120 }]);
b.perf['layout-shift']([{ startTime: 2000, value: 0.2, hadRecentInput: false }, { startTime: 2300, value: 0.1, hadRecentInput: true }]);
b.hide();
ev = b.events();
assert.ok(has(ev, 'scroll', '', '25') && has(ev, 'scroll', '', '50') && !has(ev, 'scroll', '', '75'), 'scroll buckets reached only');
assert.ok(has(ev, 'section', 'hero') && has(ev, 'section', 'faq') && !has(ev, 'section', 'offer'), 'sections reached');
assert.ok(has(ev, 'cwv', 'lcp', 'ni') && has(ev, 'cwv', 'inp', 'good') && has(ev, 'cwv', 'cls', 'ni'), 'LCP / INP / CLS bucketed');

// 2b. Loaded in <head> (DOM still loading): sections are observed once the DOM is ready.
b = browser();
b.doc.readyState = 'loading';
b.run();
assert.equal(b.observed.length, 0);
b.el('section', { id: 'hero' }); b.doc.readyState = 'interactive'; b.fireWin('DOMContentLoaded');
assert.equal(b.observed.length, 1, 'observed after DOMContentLoaded');

// 3. Forms: start, last field before abandon (name only, never a value), submit.
b = browser();
const form = b.el('form', { id: 'preventa' }); b.doc.forms.push(form);
const email = b.el('input', { form, name: 'email', type: 'email', value: 'ana@empresa.cl' });
const rut = b.el('input', { form, name: 'rut', type: 'text', value: '12.345.678-5' });
const hp = b.el('input', { form, name: 'website', type: 'text' });
b.run();
b.fireWin('focusin', { target: email }); b.fireWin('change', { target: email }); b.fireWin('focusin', { target: rut }); b.fireWin('focusin', { target: hp });
b.hide();
ev = b.events();
assert.ok(has(ev, 'form', 'preventa', 'start') && has(ev, 'abandon', 'preventa', 'rut'), 'started, abandoned at the last field touched');
assert.ok(!JSON.stringify(b.sent).includes('ana@') && !JSON.stringify(b.sent).includes('12.345'), 'never a value');
b.show(); b.fireWin('focusin', { target: email }); b.fireWin('submit', { target: form }); b.hide();
ev = b.sent[1].e;
assert.ok(has(ev, 'form', 'preventa', 'submit') && !has(ev, 'abandon'), 'submitted; one abandon at most per form');

// 4. CTA timing, outbound domain, rage clicks, FAQ items, JS errors (hash only, capped).
b = browser();
const cta = b.el('a', { dataset: { cta: '' }, sels: ['a[href]'], hostname: 'outils.vread.fr' });
const out = b.el('a', { sels: ['a[href]'], hostname: 'www.sii.cl' });
const btn = b.el('button', { id: 'calc', cls: ['big'] });
b.run();
b.clock.t += 14_000; b.fireWin('click', { target: cta });
b.fireWin('click', { target: out });
b.fireWin('click', { target: btn }); b.clock.t += 300; b.fireWin('click', { target: btn }); b.clock.t += 300; b.fireWin('click', { target: btn });
const det = b.el('details', { id: 'q-precio', open: true }); b.fireWin('toggle', { target: det });
const det2 = b.el('details', { open: true }); b.fireWin('toggle', { target: det2 });
for (let i = 0; i < 8; i++) b.fireWin('error', { message: 'TypeError: x' + i + ' is undefined' });
b.hide();
ev = b.events();
assert.ok(has(ev, 'cta_time', '', '10-30'), 'first CTA after 14 s');
assert.ok(has(ev, 'out', 'sii.cl'), 'outbound by domain');
assert.ok(has(ev, 'rage', 'button#calc'), 'rage click by selector');
assert.ok(has(ev, 'faq', 'q-precio') && has(ev, 'faq', 'q1'), 'FAQ item by id, else by position');
assert.equal(ev.filter((e) => e[0] === 'err').length, 5, 'JS errors capped at 5 per page');
assert.ok(ev.filter((e) => e[0] === 'err').every((e) => /^[0-9a-f]{1,8}$/.test(e[1])) && !JSON.stringify(b.sent).includes('undefined'), 'hash only');

// 5. Never sent: DNT, GPC, other host, our tests, automated browsers, crawlers. Variants still apply.
const XP = [{ id: 'price', variants: ['a', 'b'], mode: 'day', tz: 'America/Santiago', param: 'precio' }];
for (const o of [{ dnt: '1' }, { gpc: true }, { host: 'example.com' }, { search: '?octave_notrack=1' }, { local: { octave_notrack: '1' } }, { webdriver: true },
  { ua: CHROME.replace('Chrome/', 'HeadlessChrome/') }, { ua: 'Googlebot/2.1 (+http://www.google.com/bot.html)' }, { attrs: { api: '' } }]) {
  b = browser({ ...o, xp: XP });
  b.run(); b.fireWin('click', { target: new El('a', { dataset: { cta: '' } }) }); b.hide(); b.fireWin('pagehide');
  assert.equal(b.sent.length, 0, 'nothing sent for ' + JSON.stringify(o));
  assert.match(b.html.getAttribute('data-xp-price'), /^[ab]$/, 'the variant is still applied');
}
b = browser({ search: '?octave_notrack=0', local: { octave_notrack: '1' } }); b.run(); b.hide();
assert.equal(b.sent.length, 1, '?octave_notrack=0 counts again');

// 6. Prerendered page: counted only once opened.
b = browser({ prerendering: true }); b.run(); b.hide();
assert.equal(b.sent.length, 0);
b.doc.prerendering = false; b.show(); b.fireDoc('prerenderingchange'); b.hide();
assert.equal(b.sent.length, 1);

// 7. A/B: day alternation (no storage), like candadopyme's priceVariant (even day number in Santiago = a).
const santiagoDay = (t) => { const [y, m, d] = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Santiago', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(t)).split('-').map(Number); return Date.UTC(y, m - 1, d) / 864e5; };
for (const t of [Date.UTC(2026, 9, 13, 2), Date.UTC(2026, 9, 13, 15), Date.UTC(2026, 9, 14, 15)]) {
  b = browser({ xp: XP, now: t }); b.run();
  assert.equal(b.win.octaveCro.v('price'), santiagoDay(t) % 2 === 0 ? 'a' : 'b');
  assert.equal(Object.keys(b.session).filter((k) => k === 'oxp_price').length, 0, 'day mode stores no variant');
}
b = browser({ xp: XP, search: '?precio=b', now: Date.UTC(2026, 9, 14, 15) }); b.run();
assert.equal(b.win.octaveCro.v('price'), 'b', 'forced by its URL parameter');

// 8. A/B: tab mode, swaps, exposure once per tab, conversions once per tab, page scope, goals.
const XP2 = [{ id: 'hero', variants: ['a', 'b'], swap: { b: [['#hero h1', 'text', 'Nuevo titular'], ['a.cta', 'href', '/b/']] } },
  { id: 'price', variants: ['a149', 'b249'], param: 'p', pages: '/oferta' }];
const session = {};
b = browser({ xp: XP2, session, search: '?xp_hero=b' });
const h1 = b.el('h1', { sels: ['#hero h1'], textContent: 'Viejo' }), ca = b.el('a', { sels: ['a.cta'] });
b.run();
assert.equal(h1.textContent, 'Nuevo titular'); assert.equal(ca.getAttribute('href'), '/b/');
assert.equal(b.html.getAttribute('data-xp-hero'), 'b'); assert.equal(session.oxp_hero, 'b');
assert.match(session.oxp_price, /^(a149|b249)$/, 'drawn once per tab');
b.win.octaveCro.goal('Preventa'); b.hide();
ev = b.events();
assert.ok(has(ev, 'xp', 'hero', 'b.n') && has(ev, 'xp', 'hero', 'b.view') && has(ev, 'xp', 'hero', 'b.preventa'), 'exposure, view, goal');
assert.ok(!has(ev, 'xp', 'price'), 'not exposed outside its pages');
const price = session.oxp_price;
b = browser({ xp: XP2, session, path: '/candadopyme/oferta/' });
const c2 = b.el('a', { dataset: { cta: '' } });
b.run(); b.fireWin('click', { target: c2 }); b.win.octaveCro.goal('preventa'); b.hide();
ev = b.events();
assert.equal(b.win.octaveCro.v('hero'), 'b', 'same variant on the next page of the tab');
assert.ok(!has(ev, 'xp', 'hero', 'b.n') && has(ev, 'xp', 'hero', 'b.view') && has(ev, 'xp', 'hero', 'b.cta'), 'exposed once per tab');
assert.ok(!has(ev, 'xp', 'hero', 'b.preventa'), 'a goal counts once per tab');
assert.ok(has(ev, 'xp', 'price', price + '.n') && has(ev, 'xp', 'price', price + '.cta') && has(ev, 'xp', 'price', price + '.preventa'));
b = browser({ xp: XP2, search: '?p=b' }); b.run();
assert.equal(b.win.octaveCro.v('price'), 'b249', 'a forced value may be a prefix of the variant');

// 9. Size budget (the minified build): under 3 KB over the wire.
const gz = gzipSync(src, { level: 9 }).length;
if (!/cro\.src\.js$/.test(file)) assert.ok(gz <= 3000 && Buffer.byteLength(src) < 6000, 'cro.js stays small: ' + gz + ' bytes gzipped');
console.log('cro.js: ok (' + Buffer.byteLength(src) + ' bytes, ' + gz + ' gzipped)');
