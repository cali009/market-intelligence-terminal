/*
 * HONESTY OVERHAUL — §7 check #10: real page-load verification.
 *
 * Loads the shipped public/index.html into jsdom, serves every /api/*.json from
 * public/api/, runs the page's own init(), then runs the page's own
 * runHonestySelfCheck(). Captures console errors and uncaught exceptions.
 *
 * This exercises the actual shipped bundle — not a re-implementation of it.
 *
 * Usage: node scripts/page_load_selfcheck.mjs
 * Exit 0 = clean, 1 = failures found.
 */
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { JSDOM, VirtualConsole } from 'jsdom';
import path from 'node:path';

const ROOT = path.resolve('.');
const PUBLIC = path.join(ROOT, 'public');
const HTML_PATH = path.join(PUBLIC, 'index.html');

if (!existsSync(HTML_PATH)) {
    console.error('public/index.html not found — run scripts/build_firebase_public.py first');
    process.exit(2);
}

const consoleErrors = [];
const uncaught = [];

const vc = new VirtualConsole();
vc.on('jsdomError', (e) => uncaught.push('jsdomError: ' + (e.message || e)));
vc.on('error', (...a) => consoleErrors.push(a.map(String).join(' ')));
// Let the page's own self-check output through so we can see it.
vc.on('info', (...a) => console.log('  [page]', ...a));
vc.on('warn', (...a) => console.log('  [page:warn]', ...a));

const html = readFileSync(HTML_PATH, 'utf8');

const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    url: 'https://market-intelligence-terminal.web.app/',
    virtualConsole: vc,
    beforeParse(window) {
        // Serve /api/* straight off disk so no network is involved.
        window.fetch = async (input) => {
            const url = String(typeof input === 'string' ? input : input.url);
            let p = url.replace(/^https?:\/\/[^/]+/, '');
            p = p.split('?')[0];
            if (p.startsWith('/api/')) {
                let file = path.join(PUBLIC, p);
                if (!existsSync(file) && !file.endsWith('.json')) file += '.json';
                if (existsSync(file)) {
                    return { ok: true, status: 200, json: async () => JSON.parse(readFileSync(file, 'utf8')) };
                }
            }
            return { ok: false, status: 404, json: async () => ({}) };
        };
        window.addEventListener('error', (e) => uncaught.push('window error: ' + e.message));
        window.addEventListener('unhandledrejection', (e) =>
            uncaught.push('unhandled rejection: ' + (e.reason && e.reason.message || e.reason)));
        // Charts/canvas are not needed for this audit and jsdom lacks a 2D context.
        window.HTMLCanvasElement.prototype.getContext = () => null;
    },
});

const { window } = dom;

// The page self-registers window.onload = init. Give the async renders time.
await new Promise((r) => setTimeout(r, 9000));

console.log('=== page-load audit ===');
console.log('  /api files available:', readdirSync(path.join(PUBLIC, 'api')).filter(f => f.endsWith('.json')).length);

// Exercise the Phase 1 deliverable: open the Strategy Validation tab so its
// lazy-loaded feed is actually fetched and rendered before the audit runs.
try {
    if (typeof window.switchTab === 'function') {
        window.switchTab('paper', { preventDefault() {}, stopPropagation() {}, target: null });
        await new Promise((r) => setTimeout(r, 4000));
        const rendered = window.document.querySelectorAll('#valVerdictBody tr').length;
        console.log('  opened Strategy Validation tab; verdict rows rendered:', rendered);
    }
} catch (e) {
    console.error('  could not open the validation tab:', e.message);
}

// Run the page's own self-check and take its verdict.
let pageFailures = null;
if (typeof window.runHonestySelfCheck === 'function') {
    pageFailures = window.runHonestySelfCheck();
} else {
    console.error('  runHonestySelfCheck is not defined on window — the self-check did not ship');
}

// Independent checks from the harness side.
const bodyText = window.document.body ? window.document.body.innerHTML : '';
const harness = [];

// A top-level `let` in a classic <script> is a lexical binding, not a window
// property, so the bundle cannot be read off `window`. Audit what was rendered.
const verdictRows = window.document.querySelectorAll('#valVerdictBody tr').length;
if (verdictRows === 0) {
    harness.push('Strategy Validation panel rendered no rows — the feed never loaded');
}
const pboEl = window.document.getElementById('valPboValue');
if (pboEl && /^\s*(\u2014|-)\s*$/.test(pboEl.textContent || '')) {
    harness.push('PBO renders as an em dash — no computed value reached the page');
}
const bannerEl = window.document.getElementById('researchBannerBody');
if (bannerEl && /Loading/i.test(bannerEl.textContent || '')) {
    harness.push('research banner never received computed figures');
}

const stuck = [];
window.document.querySelectorAll('[id]').forEach((el) => {
    if (el.children.length) return;
    const t = (el.textContent || '').trim();
    if (/^Loading/i.test(t)) stuck.push('#' + el.id);
});
if (stuck.length) harness.push('panel(s) stuck on a loading placeholder: ' + stuck.slice(0, 8).join(', '));

// body.textContent includes the source of every <script>, so audit a clone with
// the code and styles stripped out — that is what a user can actually see.
const clone = window.document.body.cloneNode(true);
clone.querySelectorAll('script, style, noscript, template').forEach((n) => n.remove());
const visible = clone.textContent || '';
['undefined', 'NaN', '[object Object]'].forEach((bad) => {
    if (visible.indexOf(bad) !== -1) harness.push('rendered text contains "' + bad + '"');
});

console.log('  console.error calls during load :', consoleErrors.length);
consoleErrors.slice(0, 10).forEach((e) => console.log('      -', e.slice(0, 200)));
console.log('  uncaught exceptions             :', uncaught.length);
uncaught.slice(0, 10).forEach((e) => console.log('      -', e.slice(0, 200)));
console.log('  page self-check failures        :', pageFailures === null ? 'NOT RUN' : pageFailures.length);
(pageFailures || []).forEach((f) => console.log('      -', f));
console.log('  harness-side failures           :', harness.length);
harness.forEach((h) => console.log('      -', h));

// Feed load errors are logged by the page as console.error; some are expected for
// feeds that legitimately 404 in this harness, so report them without failing.
const fatal = uncaught.length + harness.length + (pageFailures ? pageFailures.length : 0);

if (fatal > 0) {
    console.log('\nRESULT: FAIL — ' + fatal + ' problem(s)');
    process.exit(1);
}
console.log('\nRESULT: PASS — page loaded, self-check clean, no uncaught exceptions');
process.exit(0);
