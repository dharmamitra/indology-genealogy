/* Indology Lineages — one graph, five lenses (fields), four views: lineage network (canvas), chair timelines,
   map with a year slider, and a meta view of the fields. Colour always means field of study. */
(async function () {
const $ = id => document.getElementById(id);
$('panel').innerHTML = '<p class="note">Loading the graph…</p>';
// the build stamp is fetched uncached and appended to the data URLs: a rebuilt graph is never masked by a cached copy
const BUILD = await fetch('data/version.json', {cache: 'no-store'}).then(r => r.json()).catch(() => ({v: Date.now(), built: ''}));
const [DATA, WORLD] = await Promise.all([d3.json('data/graph.json?v=' + BUILD.v), d3.json('vendor/countries-50m.json')]);
let EV = null; // evidence quotes, loaded after the first paint
d3.json('data/evidence.json?v=' + BUILD.v).then(d => { EV = d; if (S.sel != null) renderPanel(); });

const N = DATA.nodes, E = DATA.edges;
const TYPES = [
  ['student_of', 'Teacher → student', true], ['succeeded', 'Successor in a chair', true],
  ['influenced_by', 'Influence', false], ['collaborated_with', 'Collaboration', false], ['other', 'Committees, kin, friends, feuds', false]];
const PP = new Set(TYPES.map(t => t[0]));
const FIELDS = [
  ['indology', 'Indology'], ['buddhist_studies', 'Buddhist studies'], ['tibetology', 'Tibetology'], ['computational', 'Computational'], ['sinology', 'Sinology'],
  ['japanology', 'Japanology'], ['iranian_central_asian', 'Iranian & Central Asian'], ['linguistics', 'Linguistics'],
  ['religious_studies', 'Religious studies'], ['philosophy', 'Philosophy'], ['history_archaeology', 'History & archaeology'], ['other', 'Other']];
const FLABEL = Object.fromEntries(FIELDS);
const has = (p, f) => (p.fields || []).includes(f);
const LENSES = {
  indology: ['Indology', p => has(p, 'indology')],
  buddhist: ['Buddhist studies', p => has(p, 'buddhist_studies')],
  tibetology: ['Tibetology', p => has(p, 'tibetology')],
  computational: ['Computational philology', p => has(p, 'computational')],
  jp_indology: ['Japan · Indology', p => p.japanese && has(p, 'indology')],
  jp_buddhist: ['Japan · Buddhist studies', p => p.japanese && has(p, 'buddhist_studies')],
  all: ['All fields', () => true]};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fcol = p => css('--f-' + ((p.fields || [])[0] || 'other'));

// ---------------------------------------------------------------- model
N.forEach((n, i) => { n.i = i; n.out = []; n.inc = []; });
E.forEach((e, i) => { e.i = i; e.S = N[e.s]; e.T = N[e.t]; e.S.out.push(e); e.T.inc.push(e); });
const people = N.filter(n => n.type === 'person'), insts = N.filter(n => n.type === 'institution');
for (const p of people) {
  p.nStud = p.inc.filter(e => e.type === 'student_of').length;
  p.deg = p.out.length + p.inc.length;
  p.linked = p.out.some(e => PP.has(e.type)) || p.inc.length > 0;
  p.r = 2.8 + 1.9 * Math.sqrt(p.nStud);
  p.yr = p.birth_year || null; p.est = !p.birth_year;
}
// without a birth year a scholar is placed by neighbours: teacher + 28, student − 28, first degree − 28, first post − 33
for (let pass = 0; pass < 3; pass++) for (const p of people) {
  if (p.yr) continue;
  const g = [];
  for (const e of p.out) { const o = e.T;
    if (e.type === 'student_of' && o.yr) g.push(o.yr + 28); else if (o.type === 'person' && o.yr) g.push(o.yr);
    const y = e.year_start || e.year_end; if (y) g.push(y - (e.type === 'position_at' ? 33 : 26)); }
  for (const e of p.inc) { const o = e.S; if (e.type === 'student_of' && o.yr) g.push(o.yr - 28); else if (o.yr) g.push(o.yr); }
  if (g.length) p.yr = Math.round(d3.mean(g));
}
// span of a post / period of study. Stated years are hard edges; "attested" years (publications that mention the
// affiliation) only prove presence, so such ends are drawn faded; a missing end is closed with a modelled guess.
for (const e of E) {
  if (e.type !== 'position_at' && e.type !== 'studied_at') continue;
  let a = e.year_start, b = e.year_end; const att = e.att; if (!a && !b && !att) continue;
  const p = e.S, sp = {openL: !a, openR: !b, attOnly: !a && !b};
  if (!a) a = att ? Math.min(att[0], b || 9999) : b - (e.type === 'studied_at' ? 3 : 6);
  if (!b) {
    if (sp.attOnly) b = att[1];
    else if (e.type === 'studied_at') b = Math.max(a + 3, att ? att[1] : 0);
    else { const next = p.out.filter(x => x.type === 'position_at' && x !== e && x.year_start > a).map(x => x.year_start);
      b = Math.max(att ? att[1] : 0, Math.min(...next, p.death_year || 9999, (p.birth_year || p.yr || a - 35) + 68, a + 35, 2025)); }
  }
  sp.a = a; sp.b = Math.max(a, b); e.span = sp;
}

const S = {view: 'lineages', lens: 'indology', types: new Set(TYPES.filter(t => t[2]).map(t => t[0])), source: 'all', sel: null,
  hl: null, lin: null, at: false, year: null, students: false, min2: true, L: new Set(), V: new Set()};
const srcOK = e => S.source === 'all' ? true : S.source === 'books' ? e.books : S.source === 'wd' ? e.wd
  : (e.src || []).some(s => /^(Windisch|Winternitz)/.test(s));
const edgeOn = e => S.types.has(e.type) && srcOK(e) && S.V.has(e.s) && S.V.has(e.t) && (S.L.has(e.s) || S.L.has(e.t));
const alive = (p, Y) => { const b = p.birth_year || p.yr; return (!b || Y >= b + 18) && (p.death_year ? Y <= p.death_year : (!b || Y <= b + 85)); };
const happened = (e, Y) => { if (e.year_start) return Y >= e.year_start; const b = Math.max(e.S.yr || 0, e.T.yr || 0); return !b || Y >= b + 22; };
const activeIn = (e, Y) => e.span && Y >= e.span.a && Y <= e.span.b;

function setLens(k) {
  S.lens = LENSES[k] ? k : 'indology';
  const test = LENSES[S.lens][1];
  S.L = new Set(people.filter(test).map(p => p.i));
  S.V = new Set(S.L);
  if (S.lens !== 'all') for (const e of E) if (e.type === 'student_of') { // teachers and pupils from neighbouring fields stay visible as context
    if (S.L.has(e.s)) S.V.add(e.t); if (S.L.has(e.t)) S.V.add(e.s); }
  $('lens').value = S.lens;
  layout(); select(S.sel, false); renderTools();
}

// ---------------------------------------------------------------- lineage graph (canvas)
const W = 3600, H = 1600, PAD = 70;
let xs = d3.scaleLinear(), XUND = W - PAD - 60, G = [], GE = [], qt = null, sim = null;
const canvas = $('graph'), ctx = canvas.getContext('2d'), stage = $('stage'), tip = $('tip');
let T = d3.zoomIdentity, vw = 800, vh = 600, raf = 0, hover = null, fitted = false;
const layouts = {};
function layout() {
  if (sim) sim.stop();
  G = people.filter(p => S.V.has(p.i) && p.linked && [...p.out, ...p.inc].some(e => PP.has(e.type) && S.V.has(e.s) && S.V.has(e.t) && (S.L.has(e.s) || S.L.has(e.t))));
  GE = E.filter(e => PP.has(e.type) && S.V.has(e.s) && S.V.has(e.t) && (S.L.has(e.s) || S.L.has(e.t)));
  const ys = G.map(p => p.yr).filter(Boolean).sort(d3.ascending);
  // time axis with elastic decades: a decade gets room in proportion to the square root of the scholars born in it,
  // so the crowded 20th century is not squeezed by the sparse 18th
  const yA = Math.floor((ys[0] || 1740) / 10) * 10, decs = d3.range(yA, 2001, 10), cnt = new Map();
  for (const y of ys) { const d = Math.min(1990, Math.floor(y / 10) * 10); cnt.set(d, (cnt.get(d) || 0) + 1); }
  const wts = decs.slice(0, -1).map(d => Math.max(1.2, Math.sqrt(cnt.get(d) || 0))), tot = d3.sum(wts);
  let acc = 0; const rng = [PAD]; for (const w of wts) { acc += w; rng.push(PAD + (W - 2 * PAD - 220) * acc / tot); }
  xs = d3.scaleLinear().domain(decs).range(rng).clamp(true);
  xs.ticksFrom = yA;
  const cache = layouts[S.lens];
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (const p of G) { p.tx = p.yr ? xs(p.yr) : XUND; p.x = p.tx; p.y = H / 2 + (rnd() - .5) * H * .9; }
  if (cache) { for (const p of G) if (cache[p.i]) [p.x, p.y] = cache[p.i]; index(); draw(); return; }
  const inG = new Set(G.map(p => p.i));
  const links = GE.filter(e => inG.has(e.s) && inG.has(e.t)).map(e => ({source: e.S, target: e.T, type: e.type}));
  const big = G.length > 2500;
  sim = d3.forceSimulation(G)
    .force('x', d3.forceX(d => d.tx).strength(.9)).force('y', d3.forceY(H / 2).strength(.004))
    .force('link', d3.forceLink(links).distance(70).strength(l => l.type === 'student_of' ? .1 : .02))
    .force('charge', d3.forceManyBody().strength(big ? -60 : -110).distanceMax(big ? 260 : 420).theta(1.1))
    .force('collide', d3.forceCollide(d => d.r + (big ? 5 : 9)).iterations(1)).stop();
  let ticks = 0; const total = big ? 160 : 300;
  $('yearnote').textContent = '';
  (function step() { // settle progressively so the page never blocks
    if (sim == null) return;
    const me = sim; for (let i = 0; i < (big ? 6 : 20) && ticks < total; i++, ticks++) me.tick();
    if (ticks >= total) { finish(); return; }
    index(); draw(`arranging ${G.length.toLocaleString()} scholars… ${Math.round(100 * ticks / total)}%`);
    requestAnimationFrame(() => { if (sim === me) step(); });
  })();
  function finish() {
    const yv = G.map(p => p.y).sort(d3.ascending), q0 = d3.quantileSorted(yv, .03), q1 = d3.quantileSorted(yv, .97);
    for (const p of G) p.y = Math.max(50, Math.min(H - 30, H * .08 + (p.y - q0) / ((q1 - q0) || 1) * H * .84));
    G.filter(p => !p.yr).sort((a, b) => a.y - b.y).forEach((p, i, a) => { p.x = XUND + (i % 3) * 30; p.y = 70 + (H - 110) * (i + .5) / a.length; });
    layouts[S.lens] = Object.fromEntries(G.map(p => [p.i, [p.x, p.y]]));
    index(); draw();
  }
}
function index() { qt = d3.quadtree().x(d => d.x).y(d => d.y).addAll(G); }
const zoom = d3.zoom().scaleExtent([.12, 16]).on('zoom', ev => { T = ev.transform; schedule(); });
d3.select(canvas).call(zoom).on('dblclick.zoom', null);
const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };
const go = (sel, z, t, ms = 450) => (reduced || !ms ? sel : sel.transition().duration(ms)).call(z.transform, t);
function fitGraph(ms) { const FW = W + 120, k = Math.min(vw / FW, vh / H) * .99; go(d3.select(canvas), zoom, d3.zoomIdentity.translate((vw - FW * k) / 2, (vh - H * k) / 2).scale(k), ms); }
function centre(d) { const k = Math.max(T.k, 1.6); go(d3.select(canvas), zoom, d3.zoomIdentity.translate(vw / 2 - d.x * k, vh / 2 - d.y * k).scale(k)); }
$('zin').onclick = () => d3.select(canvas).transition().call(zoom.scaleBy, 1.6);
$('zout').onclick = () => d3.select(canvas).transition().call(zoom.scaleBy, 1 / 1.6);
$('zfit').onclick = () => fitGraph();
function pick(ev) {
  if (!qt) return null; const r = canvas.getBoundingClientRect(), [x, y] = T.invert([ev.clientX - r.left, ev.clientY - r.top]);
  const d = qt.find(x, y, 14 / T.k); return d && Math.hypot(d.x - x, d.y - y) * T.k <= d.r + 6 ? d : null;
}
canvas.addEventListener('pointermove', ev => { const d = pick(ev);
  if (d !== hover) { hover = d; canvas.style.cursor = d ? 'pointer' : ''; schedule(); }
  if (d) showTip(ev, d.label + (d.birth_year || d.death_year ? `  ${d.birth_year || '?'}–${d.death_year || ''}` : '')); else tip.hidden = true; });
canvas.addEventListener('pointerleave', () => { hover = null; tip.hidden = true; schedule(); });
canvas.addEventListener('click', ev => { const d = pick(ev); select(d ? d.i : null); });
function showTip(ev, text) { tip.hidden = false; tip.textContent = text; moveTip(ev); }
function moveTip(ev) { const r = stage.getBoundingClientRect(); tip.style.left = (ev.clientX - r.left) + 'px'; tip.style.top = (ev.clientY - r.top) + 'px'; }

const EDGE_STYLE = {student_of: [1.1, []], succeeded: [1.1, [6, 3]], influenced_by: [1, [2, 3]], collaborated_with: [.7, []], other: [.7, [1, 4]]};
function draw(msg) {
  if (S.view !== 'lineages') return;
  const dpr = devicePixelRatio || 1, Y = S.year;
  if (canvas.width !== Math.round(vw * dpr) || canvas.height !== Math.round(vh * dpr)) { canvas.width = Math.round(vw * dpr); canvas.height = Math.round(vh * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, vw, vh);
  const ink = css('--ink'), muted = css('--muted'), rule = css('--rule'), halo = css('--halo'), grey = css('--grey'), hot = css('--madder');
  // time axis
  ctx.font = '11px "IBM Plex Sans", sans-serif'; ctx.textAlign = 'center'; ctx.lineWidth = 1;
  let lastX = -1e9;
  for (let y = xs.ticksFrom; y <= 2000; y += 10) { // decades are elastic, so labels are thinned by pixel distance
    const x = Math.round(T.applyX(xs(y))) + .5; if (x < -20 || x > vw + 20) continue;
    const major = y % 50 === 0, lab = x - lastX >= 44 || (major && x - lastX >= 30);
    if (!lab && !major) continue;
    ctx.strokeStyle = major ? grey : rule; ctx.globalAlpha = major ? .55 : 1; ctx.beginPath(); ctx.moveTo(x, 24); ctx.lineTo(x, vh); ctx.stroke();
    ctx.globalAlpha = 1; if (lab) { ctx.fillStyle = muted; ctx.fillText(y, x, 16); lastX = x; }
  }
  ctx.fillStyle = muted; ctx.font = 'italic 11px "IBM Plex Sans", sans-serif'; ctx.fillText('undated', T.applyX(XUND + 30), 16);
  if (Y && Y <= 2000) { const x = T.applyX(xs(Y)); ctx.strokeStyle = hot; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x, 24); ctx.lineTo(x, vh); ctx.stroke(); ctx.setLineDash([]); }
  const pos = new Map(); const onScreen = d => { const x = T.applyX(d.x), y = T.applyY(d.y); return x > -60 && x < vw + 60 && y > -40 && y < vh + 40; };
  const inG = new Set(G.map(p => p.i));
  const nodeDim = d => !!(S.hl && !S.hl.has(d.i)) || !!(Y && !alive(d, Y));
  // edges run from the elder party (teacher, predecessor, influencer) to the younger
  const dense = GE.length > 4000 && T.k < .6;
  for (const pass of [0, 1]) for (const e of GE) {
    const lit = !!(S.lin && S.lin.has(e.i)); if (lit !== !!pass) continue;
    if ((!S.types.has(e.type) && !lit) || !srcOK(e) || !inG.has(e.s) || !inG.has(e.t)) continue;
    const dim = !lit && (!!S.hl || !!(Y && !happened(e, Y)));
    if (dim && (dense || S.hl)) { if (S.hl && !dense && !pass) {/* fall through to faint stroke */} else continue; }
    const a = e.T, b = e.S; if (!onScreen(a) && !onScreen(b)) continue;
    const x1 = T.applyX(a.x), y1 = T.applyY(a.y), x2 = T.applyX(b.x), y2 = T.applyY(b.y);
    const dx = x2 - x1, dy = y2 - y1, mx = (x1 + x2) / 2 - dy * .13, my = (y1 + y2) / 2 + dx * .13;
    const st = EDGE_STYLE[e.type];
    ctx.globalAlpha = lit ? .95 : dim ? .05 : dense ? .13 : .32; ctx.strokeStyle = lit ? fcol(a) : ink; ctx.lineWidth = lit ? 1.9 : st[0]; ctx.setLineDash(st[1]);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(mx, my, x2, y2); ctx.stroke();
    if (lit || (!dim && T.k > 1.1 && e.type !== 'collaborated_with' && e.type !== 'other')) { // arrowhead at the younger party
      const ex = x2 - mx, ey = y2 - my, el = Math.hypot(ex, ey) || 1, ux = ex / el, uy = ey / el, bx = x2 - ux * (b.r + 2), by = y2 - uy * (b.r + 2);
      ctx.setLineDash([]); ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(bx - ux * 7 - uy * 3, by - uy * 7 + ux * 3); ctx.lineTo(bx - ux * 7 + uy * 3, by - uy * 7 - ux * 3); ctx.fill();
    }
  }
  ctx.setLineDash([]); ctx.globalAlpha = 1;
  const vis = G.filter(onScreen);
  for (const d of vis) {
    const x = T.applyX(d.x), y = T.applyY(d.y), dim = nodeDim(d), ctxNode = !S.L.has(d.i);
    ctx.globalAlpha = dim ? .12 : ctxNode ? .55 : 1; ctx.beginPath(); ctx.arc(x, y, d.r, 0, 6.2832);
    if (d.est) { ctx.fillStyle = halo; ctx.fill(); ctx.strokeStyle = fcol(d); ctx.lineWidth = 1.3; ctx.stroke(); }
    else { ctx.fillStyle = fcol(d); ctx.fill(); ctx.strokeStyle = halo; ctx.lineWidth = 1; ctx.stroke(); }
    if (d.i === S.sel || (S.at && S.hl && S.hl.has(d.i))) { ctx.globalAlpha = 1; ctx.strokeStyle = d.i === S.sel ? ink : hot; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(x, y, d.r + 3, 0, 6.2832); ctx.stroke(); }
  }
  // labels: greedy declutter, most important first
  const cand = vis.map(d => ({d, pr: (d === hover ? 1e6 : 0) + (d.i === S.sel ? 1e5 : 0) + (S.hl && S.hl.has(d.i) ? 1e3 : 0) + d.nStud * 12 + d.deg})).sort((a, b) => b.pr - a.pr);
  const boxes = []; let n = 0; ctx.textAlign = 'left'; ctx.lineJoin = 'round';
  for (const {d, pr} of cand) {
    if (nodeDim(d) && d !== hover && (T.k < 2.5 || Y)) continue;
    const big = d.nStud >= 6, fs = big ? 14 : 11.5, w = d.label.length * fs * .56 + 6, h = fs + 4, x = T.applyX(d.x) + d.r + 4, y = T.applyY(d.y) - h / 2;
    if (pr < 1e5 && boxes.some(b => x < b.x + b.w && x + w > b.x && y < b.y + b.h && y + h > b.y)) continue;
    boxes.push({x, y, w, h});
    ctx.font = big ? '500 14px Newsreader, Georgia, serif' : '11.5px "IBM Plex Sans", sans-serif'; ctx.globalAlpha = nodeDim(d) ? .25 : 1;
    ctx.strokeStyle = halo; ctx.lineWidth = 3.5; ctx.strokeText(d.label, x, y + h - 5); ctx.fillStyle = ink; ctx.fillText(d.label, x, y + h - 5);
    if (++n > 180) break;
  }
  ctx.globalAlpha = 1;
  if (msg) { ctx.font = '13px "IBM Plex Sans", sans-serif'; ctx.fillStyle = muted; ctx.textAlign = 'left'; ctx.fillText(msg, 16, vh - 14); }
  if (Y) $('yearnote').textContent = `${G.filter(p => (p.birth_year || p.yr) && alive(p, Y)).length.toLocaleString()} of these scholars active`;
}

// ---------------------------------------------------------------- chairs: one timeline per place
const surname = l => { const t = l.replace(/,.*$/, '').split(/\s+/); return t[t.length - 1]; };
function drawChairs() {
  if (S.view !== 'chairs') return;
  const wrap = $('chairs-scroll'), rows = [];
  for (const n of insts) {
    const posts = n.inc.filter(e => e.type === 'position_at' && e.span && srcOK(e) && S.L.has(e.s));
    if (posts.length < (S.min2 && n.i !== S.sel ? 2 : 1)) continue;
    rows.push({n, posts, study: S.students ? n.inc.filter(e => e.type === 'studied_at' && e.span && srcOK(e) && S.L.has(e.s)) : []});
  }
  rows.sort((a, b) => b.posts.length - a.posts.length || d3.min(a.posts, e => e.span.a) - d3.min(b.posts, e => e.span.a));
  const shown = rows.slice(0, S.allRows ? 1e9 : 80);
  const CY0 = Math.max(1700, Math.floor((d3.min(shown, r => d3.min(r.posts, e => e.span.a)) || 1800) / 10) * 10), CY1 = 2030;
  const w = Math.max(wrap.clientWidth - 4, (CY1 - CY0) * 6.5, 760), ML = 18, MR = 60;
  const x = d3.scaleLinear().domain([CY0, CY1]).range([ML, w - MR]).clamp(true);
  let y = 34; const bars = [];
  for (const r of shown) {
    r.y = y; y += 24;
    for (const [list, h, lab] of [[r.posts, 15, true], [r.study, 6, false]]) {
      const lanes = [];
      for (const e of [...list].sort((a, b) => a.span.a - b.span.a)) {
        const x0 = x(e.span.a), x1 = Math.max(x(e.span.b), x0 + 4), name = surname(e.S.label);
        const tw = name.length * 6.4 + 8, inside = lab && x1 - x0 >= tw, end = lab && !inside ? x1 + tw : x1;
        let li = lanes.findIndex(le => le + 5 <= x0); if (li < 0) { li = lanes.length; lanes.push(0); }
        lanes[li] = end; bars.push({e, x0, x1, y: y + li * (h + 4), h, name, inside, lab});
      }
      y += lanes.length * (h + 4);
    }
    y += 14;
  }
  const c = d3.select('#chairs').attr('width', w).attr('height', y + 40); c.selectAll('*').remove();
  const defs = c.append('defs');
  for (const [id, stops] of [['fadeR', [[0, 1], [.6, 1], [1, .08]]], ['fadeL', [[0, .08], [.4, 1], [1, 1]]], ['fadeLR', [[0, .08], [.3, 1], [.7, 1], [1, .08]]]]) {
    const g = defs.append('linearGradient').attr('id', 'g' + id);
    for (const [o, a] of stops) g.append('stop').attr('offset', o).attr('stop-color', '#fff').attr('stop-opacity', a);
    defs.append('mask').attr('id', id).attr('maskContentUnits', 'objectBoundingBox').append('rect').attr('width', 1).attr('height', 1).attr('fill', `url(#g${id})`);
  }
  const decades = d3.range(CY0, CY1 + 1, 10);
  c.append('g').attr('class', 'c-grid').selectAll('line').data(decades).join('line').attr('class', d => d % 50 === 0 ? 'major' : null)
    .attr('x1', d => x(d)).attr('x2', d => x(d)).attr('y1', 0).attr('y2', y + 40);
  const gr = c.append('g');
  for (const r of shown) {
    gr.append('line').attr('class', 'c-rule').attr('x1', 0).attr('x2', w).attr('y1', r.y - 8).attr('y2', r.y - 8);
    const t = gr.append('text').attr('class', 'c-head' + (r.n.i === S.sel ? ' sel' : '')).attr('x', ML).attr('y', r.y + 10).attr('data-row', r.n.i)
      .text(r.n.label).on('click', () => select(r.n.i));
    t.append('tspan').attr('class', 'c-sub').attr('dx', 8).text([r.n.city, `${r.posts.length} posts`].filter(Boolean).join(' · '));
  }
  if (rows.length > shown.length) gr.append('text').attr('class', 'c-head').attr('x', ML).attr('y', y + 16).text(`Show all ${rows.length} places…`).on('click', () => { S.allRows = true; drawChairs(); });
  const selP = S.sel != null && N[S.sel].type === 'person';
  const g = c.append('g').selectAll('g').data(bars).join('g').attr('class', 'bar-g').classed('dim', b => selP && b.e.s !== S.sel).attr('data-person', b => b.e.s);
  const model = b => b.e.ys_src === 'model' || (!b.e.ys_src && b.e.ye_src === 'model');
  g.append('rect').attr('class', b => 'bar-r' + (model(b) ? ' model' : '') + (b.lab ? '' : ' study'))
    .attr('fill', b => model(b) ? css('--halo') : fcol(b.e.S)).attr('stroke', b => model(b) ? fcol(b.e.S) : null)
    .attr('fill-opacity', b => b.e.span.attOnly ? .5 : (b.e.ys_src || b.e.ye_src) === 'wikidata' ? .78 : 1)
    .attr('x', b => b.x0).attr('y', b => b.y).attr('width', b => b.x1 - b.x0).attr('height', b => b.h).attr('rx', 1.5)
    .attr('mask', b => b.x1 - b.x0 > 12 && (b.e.span.openL || b.e.span.openR) ? `url(#fade${b.e.span.openL ? 'L' : ''}${b.e.span.openR ? 'R' : ''})` : null)
    .on('click', (ev, b) => select(b.e.s))
    .on('pointerenter', (ev, b) => showTip(ev, `${b.e.S.label} · ${(b.e.roles || [])[0] || (b.lab ? 'post' : 'student')} · ${b.e.span.attOnly ? 'attested ' + yrs(b.e.att[0], b.e.att[1]) : (b.e.span.openL ? '?' : b.e.span.a) + '–' + (b.e.span.openR ? '?' : b.e.span.b)}${model(b) ? ' (approx.)' : ''}`))
    .on('pointermove', moveTip).on('pointerleave', () => { tip.hidden = true; });
  g.filter(b => b.lab).append('text').attr('class', b => 'bar-t' + (b.inside && !model(b) ? ' in' : '')).attr('x', b => b.inside ? b.x0 + 4 : b.x1 + 4).attr('y', b => b.y + 11).text(b => b.name);
  const ax = c.append('g').attr('class', 'c-axis'); // the decade axis stays in view while the list scrolls
  ax.append('rect').attr('width', w).attr('height', 24).attr('fill', 'var(--ground)');
  ax.selectAll('text').data(decades).join('text').attr('x', d => x(d)).attr('y', 16).attr('text-anchor', 'middle').text(d => d);
  const stick = () => ax.attr('transform', `translate(0,${wrap.scrollTop})`); wrap.onscroll = stick; stick();
  if (!drawChairs.scrolled) { drawChairs.scrolled = true; wrap.scrollLeft = x(1850) - 40; }
}
function scrollChairs() {
  if (S.view !== 'chairs' || S.sel == null) return;
  const wrap = $('chairs-scroll'), el = wrap.querySelector(N[S.sel].type === 'institution' ? `[data-row="${S.sel}"]` : `[data-person="${S.sel}"]`);
  if (el) { const b = el.getBBox(); wrap.scrollTo({top: Math.max(0, b.y - 80), left: Math.max(0, b.x - 200), behavior: reduced ? 'auto' : 'smooth'}); }
}

// ---------------------------------------------------------------- map
const msvg = d3.select('#map'), gLand = msvg.append('g'), gPl = msvg.append('g'), gPlL = msvg.append('g');
const land = topojson.feature(WORLD, WORLD.objects.land), places = insts.filter(n => n.lat != null && n.lon != null);
let proj = d3.geoMercator(), MT = d3.zoomIdentity, mw = 800, mh = 600, mraf = 0;
const landPath = gLand.append('path').attr('class', 'land');
const mzoom = d3.zoom().scaleExtent([.08, 60]).on('zoom', ev => { MT = ev.transform; if (!mraf) mraf = requestAnimationFrame(() => { mraf = 0; drawMap(); }); });
msvg.call(mzoom).on('dblclick.zoom', null).on('click', () => select(null));
$('min').onclick = () => msvg.transition().call(mzoom.scaleBy, 1.8);
$('mout').onclick = () => msvg.transition().call(mzoom.scaleBy, 1 / 1.8);
$('mfit').onclick = () => toRegion(S.lens.startsWith('jp_') ? 'japan' : 'europe');
const REGIONS = {europe: [[-10, 36.5], [31, 60.5]], india: [[66, 6], [93, 35]], japan: [[128, 30.5], [146, 44]], america: [[-125, 25], [-66, 50]], world: [[-130, -12], [150, 64]]};
function toRegion(r, ms) { // zoom the fitted (Europe) projection so that region r fills the view
  const [[x0, y1], [x1, y0]] = [proj(REGIONS[r][0]), proj(REGIONS[r][1])], k = Math.min((mw - 48) / (x1 - x0), (mh - 48) / (y1 - y0));
  go(msvg, mzoom, d3.zoomIdentity.translate(mw / 2 - k * (x0 + x1) / 2, mh / 2 - k * (y0 + y1) / 2).scale(k), ms);
}
function layoutMap() {
  proj = d3.geoMercator().fitExtent([[24, 24], [mw - 24, mh - 24]], {type: 'MultiPoint', coordinates: REGIONS.europe});
  landPath.attr('d', d3.geoPath(proj)(land));
  for (const n of places) [n.px, n.py] = proj([n.lon, n.lat]);
}
function drawMap() {
  if (S.view !== 'map' || !places.length || places[0].px == null) return;
  const Y = S.year; gLand.attr('transform', MT);
  let posts = 0, active = 0;
  for (const n of places) {
    const P = n.inc.filter(e => e.type === 'position_at' && srcOK(e) && S.L.has(e.s)), St = n.inc.filter(e => e.type === 'studied_at' && srcOK(e) && S.L.has(e.s));
    n.nP = new Set((Y ? P.filter(e => activeIn(e, Y)) : P).map(e => e.s)).size;
    n.nS = new Set((Y ? St.filter(e => activeIn(e, Y)) : St).map(e => e.s)).size;
    n.any = P.length + St.length > 0; posts += n.nP; if (n.nP) active++;
  }
  const col = S.lens === 'all' ? css('--f-indology') : css('--f-' + ({buddhist: 'buddhist_studies', jp_buddhist: 'buddhist_studies', tibetology: 'tibetology'}[S.lens] || 'indology'));
  const vis = places.filter(n => n.any).sort((a, b) => b.nP - a.nP), rr = n => n.nP ? 2.5 + 2.6 * Math.sqrt(n.nP) : 2;
  gPl.selectAll('circle.study').data(vis.filter(n => n.nS), n => n.i).join('circle').attr('class', 'place study')
    .attr('cx', n => MT.applyX(n.px)).attr('cy', n => MT.applyY(n.py)).attr('r', n => rr(n) + 2 + 1.4 * Math.sqrt(n.nS));
  gPl.selectAll('circle.main').data(vis, n => n.i).join('circle').attr('class', n => 'place main' + (n.nP ? '' : ' idle') + (n.i === S.sel ? ' sel' : ''))
    .attr('fill', n => n.nP ? col : null).attr('cx', n => MT.applyX(n.px)).attr('cy', n => MT.applyY(n.py)).attr('r', rr)
    .on('click', (ev, n) => { ev.stopPropagation(); select(n.i); })
    .on('pointerenter', (ev, n) => showTip(ev, `${n.label} · ${n.nP} post${n.nP === 1 ? '' : 's'}${n.nS ? ` · ${n.nS} studying` : ''}`))
    .on('pointermove', moveTip).on('pointerleave', () => { tip.hidden = true; });
  const boxes = [], out = [];
  for (const n of vis) {
    if (!n.nP && n.i !== S.sel && MT.k < 6) continue;
    const label = /^University of /.test(n.label) ? n.label.replace('University of ', '') : n.label.replace(/ University$/, '');
    const w = label.length * 6.3 + 6, h = 15, x = MT.applyX(n.px) + rr(n) + 4, y = MT.applyY(n.py) - h / 2;
    if (x < 0 || x > mw || y < 0 || y > mh) continue;
    if (n.i !== S.sel && boxes.some(b => x < b.x + b.w && x + w > b.x && y < b.y + b.h && y + h > b.y)) continue;
    boxes.push({x, y, w, h}); out.push({n, x, y: y + 11, label}); if (out.length > 120) break;
  }
  gPlL.selectAll('text').data(out, o => o.n.i).join('text').attr('class', 'plbl').attr('x', o => o.x).attr('y', o => o.y).text(o => o.label);
  $('yearnote').textContent = Y ? `${posts.toLocaleString()} post-holders at ${active} places` : `${active} places with posts`;
}

// ---------------------------------------------------------------- fields (meta view)
function drawFields() {
  if (S.view !== 'fields') return;
  const el = $('fields'), w = Math.max(640, el.clientWidth - 40), keys = FIELDS.map(f => f[0]);
  // the meta view compares fields, so it looks past the single-field lenses: everybody, or everybody in Japan
  const jp = S.lens.startsWith('jp_'), inB = p => !jp || p.japanese, scope = jp ? 'Japan' : 'all fields';
  const P = people.filter(p => inB(p) && (p.birth_year || p.yr));
  const years = d3.range(1780, 2021, 5);
  const rows = years.map(Y => { const r = {Y}; for (const k of keys) r[k] = 0; for (const p of P) { const b = p.birth_year || p.yr; if (Y >= b + 25 && Y <= (p.death_year || b + 78)) r[(p.fields || ['other'])[0]]++; } return r; });
  const series = d3.stack().keys(keys)(rows), h = 300, m = {l: 44, r: 16, t: 14, b: 26};
  const x = d3.scaleLinear().domain([1780, 2020]).range([m.l, w - m.r]), y = d3.scaleLinear().domain([0, d3.max(series[series.length - 1], d => d[1]) || 1]).nice().range([h - m.b, m.t]);
  const area = d3.area().x(d => x(d.data.Y)).y0(d => y(d[0])).y1(d => y(d[1])).curve(d3.curveMonotoneX);
  // teacher field -> student field
  const M = {}, tot = {}; let mx = 1;
  for (const e of E) if (e.type === 'student_of' && srcOK(e) && (inB(e.S) || inB(e.T))) { const a = (e.T.fields || ['other'])[0], b = (e.S.fields || ['other'])[0]; const k = a + '>' + b; M[k] = (M[k] || 0) + 1; mx = Math.max(mx, M[k]); tot[a] = (tot[a] || 0) + 1; tot[b] = (tot[b] || 0) + 1; }
  const used = keys.filter(k => tot[k]), cs = Math.min(46, (w - 220) / Math.max(1, used.length)), mh2 = used.length * cs + 150;
  const byCountry = d3.rollups(people.filter(p => inB(p) && p.country), v => v.length, p => p.country).sort((a, b) => b[1] - a[1]).slice(0, 14);
  el.innerHTML = `<h2>The fields at a glance <span class="c-sub">· ${scope}</span></h2>
    <p class="note">Colour means field of study everywhere on this site. A scholar counts as active from age 25 to death (or 78); each is counted once, under the first field assigned.</p>
    <div class="legend inline">${FIELDS.map(([k, l]) => `<span class="key"><span class="dot" style="background:var(--f-${k})"></span>${l}</span>`).join('')}</div>
    <h3>Active scholars in the data, by field</h3><div class="scrollx"><svg id="f-area" width="${w}" height="${h}"></svg></div>
    <h3>Who taught whom across fields <span class="c-sub">rows: teacher’s field · columns: student’s field · number of documented teacher–student links</span></h3>
    <div class="scrollx"><svg id="f-mat" width="${w}" height="${mh2}"></svg></div>
    <h3>Where they worked</h3><ul class="rank" style="max-width:420px">${byCountry.map(([c, n]) => `<li><span>${esc(c)}</span><span class="n">${n.toLocaleString()}</span></li>`).join('')}</ul>`;
  const a = d3.select('#f-area');
  a.append('g').attr('class', 'c-grid').selectAll('line').data(y.ticks(5)).join('line').attr('x1', m.l).attr('x2', w - m.r).attr('y1', d => y(d)).attr('y2', d => y(d));
  a.append('g').selectAll('path').data(series).join('path').attr('d', area).attr('fill', d => `var(--f-${d.key})`).attr('stroke', 'var(--ground)').attr('stroke-width', .6)
    .on('pointermove', (ev, d) => { const Y = Math.round(x.invert(d3.pointer(ev)[0]) / 5) * 5, r = rows.find(r => r.Y === Y); if (r) showTip(ev, `${FLABEL[d.key]} · ${Y}: ${r[d.key]}`); }).on('pointerleave', () => { tip.hidden = true; });
  a.append('g').attr('class', 'c-axis').selectAll('text').data(d3.range(1780, 2021, 20)).join('text').attr('x', d => x(d)).attr('y', h - 8).attr('text-anchor', 'middle').text(d => d);
  a.append('g').attr('class', 'c-axis').selectAll('text').data(y.ticks(5)).join('text').attr('x', m.l - 6).attr('y', d => y(d) + 4).attr('text-anchor', 'end').text(d => d);
  const g = d3.select('#f-mat').append('g').attr('transform', 'translate(200,130)');
  const op = d3.scaleSqrt().domain([0, mx]).range([.06, 1]);
  used.forEach((ra, i) => used.forEach((cb, j) => { const v = M[ra + '>' + cb] || 0; if (!v) return;
    g.append('rect').attr('x', j * cs + 1).attr('y', i * cs + 1).attr('width', cs - 2).attr('height', cs - 2).attr('rx', 2).attr('fill', `var(--f-${ra})`).attr('fill-opacity', op(v));
    g.append('text').attr('class', 'm-num').attr('x', j * cs + cs / 2).attr('y', i * cs + cs / 2 + 4).attr('text-anchor', 'middle').text(v); }));
  used.forEach((k, i) => { g.append('text').attr('class', 'm-lab').attr('x', -8).attr('y', i * cs + cs / 2 + 4).attr('text-anchor', 'end').text(FLABEL[k]);
    g.append('text').attr('class', 'm-lab').attr('transform', `translate(${i * cs + cs / 2 + 4},-8) rotate(-45)`).text(FLABEL[k]); });
}

// ---------------------------------------------------------------- selection + panel
function lineage(i) { // academic ancestors and descendants over the visible teacher→student edges
  const ids = new Set([i]), es = new Set();
  const walk = (start, up) => { const st = [start]; while (st.length) { const n = N[st.pop()];
    for (const e of (up ? n.out : n.inc)) { if (e.type !== 'student_of' || !edgeOn(e)) continue; const o = up ? e.t : e.s; es.add(e.i); if (!ids.has(o)) { ids.add(o); st.push(o); } } } };
  walk(i, true); walk(i, false);
  // every personal link of the selected scholar is shown, whatever the link-type switches say
  for (const e of [...N[i].out, ...N[i].inc]) if (PP.has(e.type) && srcOK(e) && S.V.has(e.s) && S.V.has(e.t)) { es.add(e.i); ids.add(e.s); ids.add(e.t); }
  return {ids, es};
}
function select(i, move) {
  if (i != null && !N[i]) i = null;
  S.sel = i; S.at = false; S.hl = null; S.lin = null;
  if (i != null) { const n = N[i];
    if (n.type === 'person') { const l = lineage(i); S.hl = l.ids; S.lin = l.es; if (move && S.view === 'lineages' && n.x != null && G.includes(n)) centre(n); }
    else { S.at = true; S.hl = new Set(n.inc.filter(srcOK).map(e => e.s));
      if (move && S.view === 'map' && n.px != null) { const k = Math.max(MT.k, 5); go(msvg, mzoom, d3.zoomIdentity.translate(mw / 2 - n.px * k, mh / 2 - n.py * k).scale(k)); } } }
  renderPanel(); redraw(); if (move) scrollChairs(); writeHash();
  if (i != null && move && matchMedia('(max-width:860px)').matches) $('panel').scrollIntoView({behavior: reduced ? 'auto' : 'smooth'});
}
const yrs = (a, b) => a && b && a !== b ? `${a}–${b}` : a ? `${a}` : b ? `until ${b}` : '';
const ATT = '<span class="tag" title="Publications of these years mention the affiliation as current. They prove presence, not when it began or ended.">attested</span>';
function when(e) {
  const y = yrs(e.year_start, e.year_end);
  if (!y) return e.att ? yrs(e.att[0], e.att[1]) + ATT : '';
  const src = new Set([e.ys_src, e.ye_src]);
  if (e.att && !e.year_end && e.att[1] > (e.year_start || 0)) return (src.has('model') ? 'c. ' : '') + `${e.year_start}– (still in ${e.att[1]})` + (src.has('model') ? '<span class="tag model">model</span>' : '');
  return (src.has('model') ? 'c. ' : '') + y + (src.has('wikidata') ? '<span class="tag" title="Year from Wikidata">Wikidata</span>' : '') +
    (src.has('model') ? '<span class="tag model" title="Year recalled by the language model, not found in the publications or in Wikidata — approximate">model</span>' : '');
}
function item(e, o) {
  const ev = EV && EV[e.i] && EV[e.i][0];
  const meta = [esc((e.roles || []).join(' · ')), when(e), e.type !== 'position_at' && ev && ev.place ? esc(ev.place) : ''].filter(Boolean).join(', ');
  const q = e.books ? (ev ? `<span class="q">“${esc(ev.evidence)}” <span class="src">— ${esc(ev.source)}${e.n_ev > 1 ? ` +${e.n_ev - 1}` : ''}</span>${ev.explicit === false ? ' <span class="inferred">inferred</span>' : ''}${e.suspect ? ' <span class="inferred">check: teacher younger than student</span>' : ''}</span>` : '<span class="meta">…</span>')
    : `<span class="meta">Wikidata statement${e.S.qid ? ` · <a href="https://www.wikidata.org/wiki/${esc(e.S.qid)}" target="_blank" rel="noopener">source</a>` : ''}</span>`;
  return `<li class="${S.year && activeIn(e, S.year) ? 'now' : ''}"><button class="who${o.type === 'institution' ? ' inst' : ''}" data-i="${o.i}">${o.type === 'person' ? `<span class="dot" style="background:${fcol(o)}"></span>` : ''}${esc(o.label)}</button>` +
    (meta ? `<span class="meta">${meta}</span>` : '') + q + '</li>';
}
function section(title, es, pick, key) {
  es = es.filter(srcOK); if (!es.length) return '';
  const t0 = e => e.year_start || (e.att && e.att[0]) || e.year_end || 9999;
  es.sort((a, b) => t0(a) - t0(b) || b.n_ev - a.n_ev);
  const cap = S.open === key ? 1e9 : 60, more = es.length > cap ? `<button class="back more" data-open="${key}">Show all ${es.length}…</button>` : '';
  return `<h3><span>${title}</span><span>${es.length}</span></h3><ul class="items">${es.slice(0, cap).map(e => item(e, pick(e))).join('')}</ul>${more}`;
}
function colleagues(n) { // derived, not extracted: people whose posts at the same institution overlap in time
  const best = new Map();
  for (const e of n.out) { if (e.type !== 'position_at' || !e.span || !srcOK(e)) continue;
    for (const f of e.T.inc) { if (f.type !== 'position_at' || !f.span || f.S === n || !srcOK(f)) continue;
      const a = Math.max(e.span.a, f.span.a), b = Math.min(e.span.b, f.span.b); if (b - a < 1) continue;
      const cur = best.get(f.s); if (!cur || b - a > cur.b - cur.a) best.set(f.s, {o: f.S, at: e.T, a, b}); } }
  const rows = [...best.values()].sort((x, y) => (y.b - y.a) - (x.b - x.a) || y.o.deg - x.o.deg);
  if (!rows.length) return '';
  const cap = S.open === 'coll' ? 1e9 : 15;
  return `<h3><span>Colleagues at the same place <span class="c-sub" style="text-transform:none;letter-spacing:0">· derived from overlapping posts</span></span><span>${rows.length}</span></h3><ul class="items">` +
    rows.slice(0, cap).map(r => `<li><button class="who" data-i="${r.o.i}"><span class="dot" style="background:${fcol(r.o)}"></span>${esc(r.o.label)}</button><span class="meta">${esc(r.at.label)}, ${r.a}–${r.b}</span></li>`).join('') + '</ul>' +
    (rows.length > cap ? `<button class="back more" data-open="coll">Show all ${rows.length}…</button>` : '');
}
function renderPanel() {
  const el = $('panel'), n = S.sel != null && N[S.sel];
  if (!n) { el.innerHTML = overview(); return; }
  const back = '<button class="back" data-i="">← Overview</button>';
  const o = t => n.out.filter(e => e.type === t), i = t => n.inc.filter(e => e.type === t), src = e => e.S, tgt = e => e.T, either = e => e.S === n ? e.T : e.S;
  const links = [n.qid && `<a href="https://www.wikidata.org/wiki/${esc(n.qid)}" target="_blank" rel="noopener">Wikidata</a>`,
    n.enwiki && `<a href="https://en.wikipedia.org/wiki/${encodeURIComponent(n.enwiki)}" target="_blank" rel="noopener">Wikipedia</a>`,
    n.dewiki && !n.enwiki && `<a href="https://de.wikipedia.org/wiki/${encodeURIComponent(n.dewiki)}" target="_blank" rel="noopener">Wikipedia (de)</a>`,
    n.jawiki && `<a href="https://ja.wikipedia.org/wiki/${encodeURIComponent(n.jawiki)}" target="_blank" rel="noopener">Wikipedia (ja)</a>`].filter(Boolean).join('');
  if (n.type === 'person') {
    const life = n.birth_year || n.death_year ? `${n.dates_model ? 'c. ' : ''}${n.birth_year || '?'} – ${n.death_year || ''}${n.dates_model ? ' <span class="tag model" title="Life dates recalled by the language model, not confirmed by Wikidata or the texts">model</span>' : ''}` : '';
    const where = [n.birth_place && `b. ${n.birth_place}`, n.death_place && `d. ${n.death_place}`].filter(Boolean).join(' · ');
    const career = [...o('studied_at'), ...o('position_at')];
    el.innerHTML = back + `<div class="head">${n.image ? `<img alt="" loading="lazy" src="https://commons.wikimedia.org/wiki/Special:FilePath/${encodeURIComponent(n.image)}?width=180">` : ''}<div>` +
      `<h2>${esc(n.label)}</h2>${n.native ? `<div class="native">${esc(n.native)}</div>` : ''}<div class="dates">${[life, esc(where)].filter(Boolean).join('<br>')}</div>` +
      `<div class="field">${(n.fields || []).map(f => `<span class="chip"><span class="dot" style="background:var(--f-${f})"></span>${FLABEL[f] || f}</span>`).join('')}${n.country ? `<span class="chip">${esc(n.country)}</span>` : ''}</div>` +
      (links ? `<div class="links">${links}</div>` : '') + '</div></div>' +
      (n.summary ? `<p class="summary">${esc(n.summary)} <span class="tag" title="Written by Gemini from the facts listed below">auto-summary</span></p>` : '') +
      (!G.includes(n) ? '<p class="note">No teacher–student link in this lens, so this scholar is not on the lineage graph.</p>' : '') +
      ((n.variants || []).length > 1 || (n.variants || [])[0] !== n.label ? `<div class="aka">Also written: ${(n.variants || []).slice(0, 8).map(esc).join(' · ')}</div>` : '') +
      section('Career stations', career, tgt, 'career') + section('Studied under', o('student_of'), tgt, 'teachers') + section('Students', i('student_of'), src, 'students') +
      section('Succeeded', o('succeeded'), tgt, 'succ') + section('Succeeded by', i('succeeded'), src, 'succby') +
      section('Influenced by', o('influenced_by'), tgt, 'infl') + section('Influenced', i('influenced_by'), src, 'infld') +
      section('Worked with', [...o('collaborated_with'), ...i('collaborated_with')], either, 'collab') +
      colleagues(n) + section('Founded', o('founded'), tgt, 'founded') + section('Other links: thesis committees, kin, friends, opponents', [...o('other'), ...i('other')], either, 'other');
  } else {
    const inL = es => es.filter(e => S.L.has(e.s));
    el.innerHTML = back + `<h2>${esc(n.label)}</h2><div class="dates">${esc([n.city, n.country].filter(Boolean).join(', '))}${n.inception ? ` · founded ${n.inception}` : ''}</div>` +
      (links ? `<div class="links">${links}</div>` : '') + (S.year ? `<p class="note" style="margin-top:8px">● marks who was here in ${S.year}.</p>` : '') +
      section('Held posts here', inL(i('position_at')), src, 'posts') + section('Studied here', inL(i('studied_at')), src, 'studied') + section('Founded by', i('founded'), src, 'fby') +
      ((n.variants || []).length ? `<div class="aka" style="margin-top:18px">Also written: ${n.variants.slice(0, 8).map(esc).join(' · ')}</div>` : '');
  }
  el.scrollTop = 0;
}
function overview() {
  const Lp = people.filter(p => S.L.has(p.i));
  const top = [...Lp].sort((a, b) => b.nStud - a.nStud).slice(0, 14);
  const cnt = new Map(); for (const e of E) if ((e.type === 'position_at' || e.type === 'studied_at') && S.L.has(e.s)) { if (!cnt.has(e.t)) cnt.set(e.t, new Set()); cnt.get(e.t).add(e.s); }
  const pl = [...cnt].map(([t, s]) => [N[t], s.size]).sort((a, b) => b[1] - a[1]).slice(0, 16);
  const LE = E.filter(e => S.L.has(e.s) || S.L.has(e.t)), dated = LE.filter(e => e.year_start || e.year_end).length;
  return `<h2>Who taught whom, where, and when</h2>
  <p style="margin-top:10px">An academic genealogy of Indology, Buddhist studies and Tibetology — and their Japanese traditions — from the first Sanskritists to scholars working today. It is read out of two classic histories (Windisch, Winternitz), histories of Buddhist and Tibetan studies, obituaries, and above all the prefaces, acknowledgements, <i>Lebensläufe</i> and あとがき of several thousand books and dissertations, where scholars say who taught them.</p>
  <div class="stats"><div><b>${Lp.length.toLocaleString()}</b><span>scholars</span></div><div><b>${cnt.size.toLocaleString()}</b><span>places</span></div><div><b>${LE.length.toLocaleString()}</b><span>links</span></div><div><b>${dated.toLocaleString()}</b><span>dated</span></div></div>
  <p class="note">Lens: <b>${esc(LENSES[S.lens][0])}</b> — change it at the top. <b>Lineages</b> places everyone by year of birth, coloured by field. <b>Chairs</b> shows who held which post, place by place. <b>Map</b> plays the spread year by year. <b>Fields</b> compares the disciplines.</p>
  <h3><span>Largest schools</span><span>students named</span></h3>
  <ul class="rank">${top.map(p => `<li><button class="who" data-i="${p.i}"><span class="dot" style="background:${fcol(p)}"></span>${esc(p.label)}</button><span class="n">${p.nStud}</span></li>`).join('')}</ul>
  <h3><span>Places</span><span>scholars</span></h3>
  <ul class="rank">${pl.map(([n, c]) => `<li><button class="who inst" data-i="${n.i}">${esc(n.label)}</button><span class="n">${c}</span></li>`).join('')}</ul>
  <h3><span>How to read this</span></h3>
  <p class="note">Relations were extracted with Gemini and kept only when the quoted evidence was found verbatim in the source text. Name variants (including kanji and romanised forms) were merged automatically, matched to Wikidata, and checked again for duplicates; mistakes remain. Every extracted link was checked a second time by an independent model pass against its quote, and a year counts only if it is written in the quote. Years come in four grades: unmarked years are stated in a publication; <span class="tag">attested</span> means publications of those years mention the affiliation as current (presence, not start or end); <span class="tag">Wikidata</span> years come from dated Wikidata statements; <span class="tag model">model</span> years were recalled by the language model and are approximate. Bars that fade out have an unknown end. Hollow dots are scholars without a known birth year, placed by their neighbours. Fields and career summaries are assigned automatically. Faded dots in a lens are teachers or pupils from neighbouring fields.</p>
  <p class="note">Data build: ${esc(BUILD.built || '—')}. Code and data: <a href="https://github.com/dharmamitra/indology-genealogy">github.com/dharmamitra/indology-genealogy</a></p>`;
}
$('panel').addEventListener('click', ev => { const m = ev.target.closest('[data-open]'); if (m) { S.open = m.dataset.open; const t = $('panel').scrollTop; renderPanel(); $('panel').scrollTop = t; return; }
  const b = ev.target.closest('[data-i]'); if (b) { S.open = null; select(b.dataset.i === '' ? null : +b.dataset.i, true); } });

// ---------------------------------------------------------------- controls
function renderTools() {
  const t = $('tools');
  const sw = k => `<svg class="swatch-l" width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke="currentColor" stroke-width="${k === 'student_of' ? 2 : 1.4}" stroke-dasharray="${EDGE_STYLE[k][1].join(' ')}"/></svg>`;
  if (S.view === 'lineages') t.innerHTML = TYPES.map(([k, l]) => `<label class="tg"><input type="checkbox" id="tg-${k}" data-t="${k}" ${S.types.has(k) ? 'checked' : ''}>${sw(k)}${l}</label>`).join('') + '<span>· left to right is year of birth · colour is field</span>';
  else if (S.view === 'chairs') t.innerHTML = '<span class="key"><span class="swatch" style="background:var(--f-indology)"></span>years from publications</span><span class="key"><span class="swatch" style="background:var(--f-indology);opacity:.7"></span>from Wikidata</span><span class="key"><span class="swatch" style="background:var(--f-indology);opacity:.45"></span>attested only</span><span class="key"><span class="swatch model"></span>recalled by the model</span>' +
    `<label class="tg"><input type="checkbox" id="tg-students" ${S.students ? 'checked' : ''}><span class="swatch study"></span>students</label><label class="tg"><input type="checkbox" id="tg-min2" ${S.min2 ? 'checked' : ''}><span></span>only places with 2+ dated posts</label>`;
  else if (S.view === 'map') t.innerHTML = '<span class="key"><span class="dot" style="background:var(--f-indology)"></span>people holding a post</span><span class="key"><span class="dot ring"></span>people studying</span><span class="regions">' +
    Object.keys(REGIONS).map(r => `<button data-region="${r}">${{america: 'N. America'}[r] || r[0].toUpperCase() + r.slice(1)}</button>`).join('') + '</span>';
  else t.innerHTML = '<span>How the disciplines grew, and how they taught each other</span>';
  $('legend').innerHTML = FIELDS.filter(([k]) => people.some(p => S.V.has(p.i) && (p.fields || [])[0] === k)).map(([k, l]) => `<span class="key"><span class="dot" style="background:var(--f-${k})"></span>${l}</span>`).join('');
  $('yearbar').hidden = S.view === 'chairs' || S.view === 'fields';
}
$('tools').addEventListener('click', ev => { const b = ev.target.closest('[data-region]'); if (b) toRegion(b.dataset.region); });
$('tools').addEventListener('change', ev => { const i = ev.target;
  if (i.dataset.t) { i.checked ? S.types.add(i.dataset.t) : S.types.delete(i.dataset.t); select(S.sel, false); }
  else if (i.id === 'tg-students') { S.students = i.checked; drawChairs(); } else if (i.id === 'tg-min2') { S.min2 = i.checked; drawChairs(); } });
$('source').addEventListener('change', ev => { S.source = ev.target.value; select(S.sel, false); });
$('lens').innerHTML = Object.entries(LENSES).map(([k, [l]]) => `<option value="${k}">${l}</option>`).join('');
$('lens').addEventListener('change', ev => { drawChairs.scrolled = false; S.allRows = false; setLens(ev.target.value); writeHash(); if (S.view === 'map') toRegion(S.lens.startsWith('jp_') ? 'japan' : 'europe'); });
function redraw() { draw(); drawChairs(); drawMap(); drawFields(); }
function resize() {
  const v = $('view-' + S.view).getBoundingClientRect();
  if (S.view === 'lineages') { vw = v.width; vh = v.height; if (!fitted && vw > 50) { fitted = true; fitGraph(0); } }
  if (S.view === 'map') { const first = mw === 800 && mh === 600; mw = v.width; mh = v.height; msvg.attr('viewBox', `0 0 ${mw} ${mh}`); layoutMap(); if (first && S.lens.startsWith('jp_')) toRegion('japan', 0); }
  redraw();
}
function setView(v) {
  S.view = v;
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === v);
  for (const k of ['lineages', 'chairs', 'map', 'fields']) $('view-' + k).hidden = k !== v;
  renderTools(); resize(); scrollChairs(); writeHash();
  if (v === 'map' && !S.year && !setView.mapSeen) { setView.mapSeen = true; setYear(S.lens.startsWith('jp_') ? 1970 : S.lens === 'indology' ? 1900 : 1985); }
}
document.querySelector('.tabs').addEventListener('click', ev => { const b = ev.target.closest('[data-view]'); if (b) setView(b.dataset.view); });

let timer = null;
function setYear(y) {
  S.year = y; $('allyears').checked = !y; $('yearbar').classList.toggle('off', !y);
  if (y) { $('year').value = y; $('yearout').textContent = y; } else $('yearnote').textContent = '';
  redraw(); if (S.sel != null && N[S.sel].type === 'institution') renderPanel();
}
$('year').addEventListener('input', ev => setYear(+ev.target.value));
$('allyears').addEventListener('change', ev => { stop(); setYear(ev.target.checked ? null : +$('year').value); });
function stop() { clearInterval(timer); timer = null; $('play').textContent = '▶'; }
$('play').onclick = () => { if (timer) return stop(); let y = S.year && S.year < 2025 ? S.year : (S.lens.startsWith('jp_') ? 1870 : 1780); $('play').textContent = '❚❚';
  timer = setInterval(() => { setYear(y); if (++y > 2025) stop(); }, 160); };

// search with suggestions (labels, native-script names and spelling variants)
const fold = s => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/ő/g, 'o').replace(/ű/g, 'u').replace(/ø/g, 'o').replace(/ł/g, 'l').replace(/ß/g, 'ss'); // search ignores diacritics: Koros finds Kőrös
const SEARCH = N.map(n => ({n, keys: [n.label, n.native, ...(n.variants || [])].filter(Boolean).map(fold)}));
const sug = $('suggest'), box = $('search');
function suggest() { const q = fold(box.value.trim()); if (q.length < 2) { sug.hidden = true; return; }
  const hits = []; for (const r of SEARCH) { const k = r.keys.find(k => k.includes(q)); if (k) hits.push([k.startsWith(q) ? 0 : 1, -(r.n.deg || r.n.inc.length), r.n]); if (hits.length > 400) break; }
  hits.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  sug.innerHTML = hits.slice(0, 12).map(([, , n]) => `<li><button data-i="${n.i}">${n.type === 'person' ? `<span class="dot" style="background:${fcol(n)}"></span>` : '<span class="dot sq"></span>'}${esc(n.label)}${n.native ? ` <span class="c-sub">${esc(n.native)}</span>` : ''}${n.birth_year ? ` <span class="c-sub">${n.birth_year}–${n.death_year || ''}</span>` : ''}</button></li>`).join('') || '<li class="none">No match</li>';
  sug.hidden = false; }
box.addEventListener('input', suggest); box.addEventListener('focus', suggest);
box.addEventListener('keydown', ev => { if (ev.key === 'Enter') { const b = sug.querySelector('[data-i]'); if (b) b.click(); } if (ev.key === 'Escape') sug.hidden = true; });
sug.addEventListener('click', ev => { const b = ev.target.closest('[data-i]'); if (!b) return; const n = N[+b.dataset.i]; sug.hidden = true; box.value = n.label; box.blur();
  if (n.type === 'person' && !S.V.has(n.i)) { const k = Object.keys(LENSES).find(k => k !== 'all' && LENSES[k][1](n)) || 'all'; setLens(k); }
  select(n.i, true); });
document.addEventListener('click', ev => { if (!ev.target.closest('.search')) sug.hidden = true; });

// shareable state: #lens=tibetology&view=chairs&sel=Rudolf von Roth
const byLabel = new Map(N.map(n => [n.id, n.i]));
function writeHash() { const h = new URLSearchParams(); if (S.lens !== 'indology') h.set('lens', S.lens); if (S.view !== 'lineages') h.set('view', S.view); if (S.sel != null) h.set('sel', N[S.sel].id);
  history.replaceState(null, '', h.toString() ? '#' + h : location.pathname + location.search); }
const h0 = new URLSearchParams(location.hash.slice(1));
new ResizeObserver(resize).observe(stage);
S.sel = h0.get('sel') && byLabel.has(h0.get('sel')) ? byLabel.get(h0.get('sel')) : null;
const v0 = ['lineages', 'chairs', 'map', 'fields'].includes(h0.get('view')) ? h0.get('view') : 'lineages';
S.view = v0; setLens(h0.get('lens')); setView(v0); if (S.sel != null) select(S.sel, true);
new MutationObserver(redraw).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redraw);
})();
