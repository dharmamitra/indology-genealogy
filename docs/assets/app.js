/* Indology Lineages — three views over one graph: lineage network, chair timelines, map with a year slider. */
(async function () {
const [DATA, WORLD] = await Promise.all([d3.json('data/graph.json'), d3.json('vendor/countries-50m.json')]);

const TYPES = [
  ['student_of', 'Teacher → student', true], ['succeeded', 'Successor in a chair', true],
  ['influenced_by', 'Influence', true], ['collaborated_with', 'Collaboration', false], ['other', 'Kin, friends, feuds', false]];
const PP = new Set(TYPES.map(t => t[0])), ARROWS = new Set(['student_of', 'influenced_by', 'succeeded']);
const byId = new Map(DATA.nodes.map(n => [n.id, n]));
const people = DATA.nodes.filter(n => n.type === 'person'), insts = DATA.nodes.filter(n => n.type === 'institution');
const ppE = DATA.edges.filter(e => PP.has(e.type));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = id => document.getElementById(id);

// ---------------------------------------------------------------- model
for (const n of DATA.nodes) { n.out = []; n.inc = []; }
for (const e of DATA.edges) { byId.get(e.source).out.push(e); byId.get(e.target).inc.push(e); }
for (const p of people) {
  p.nStud = p.inc.filter(e => e.type === 'student_of').length;
  p.deg = p.out.length + p.inc.length;
  p.r = 3.2 + 2.1 * Math.sqrt(p.nStud);
  p.yr = p.birth_year || null; p.est = !p.birth_year;
}
// scholars without a birth year are placed by their neighbours (teacher + 25, student − 25, first post − 32)
for (let pass = 0; pass < 3; pass++) for (const p of people) {
  if (p.yr) continue;
  const g = [];
  for (const e of p.out) { const o = byId.get(e.target);
    if (e.type === 'student_of' && o.yr) g.push(o.yr + 25); else if (o.type === 'person' && o.yr) g.push(o.yr);
    if (e.year_start) g.push(e.year_start - 32); }
  for (const e of p.inc) { const o = byId.get(e.source);
    if (e.type === 'student_of' && o.yr) g.push(o.yr - 25); else if (o.yr) g.push(o.yr); }
  if (g.length) p.yr = Math.round(d3.mean(g));
}
// time span of a post or a period of study; open ends are closed with a guess and drawn faded
for (const e of DATA.edges) {
  if (e.type !== 'position_at' && e.type !== 'studied_at') continue;
  let a = e.year_start, b = e.year_end;
  if (!a && !b) continue;
  const p = byId.get(e.source), sp = {openL: !a, openR: !b};
  if (e.type === 'studied_at') { if (!a) a = b - 3; if (!b) b = a + 3; }
  else {
    if (!a) a = b - 6;
    if (!b) { const next = p.out.filter(x => x.type === 'position_at' && x !== e && x.year_start > a).map(x => x.year_start);
      b = Math.min(...next, p.death_year || 9999, a + 35); }
  }
  sp.a = a; sp.b = Math.max(a, b);
  e.span = sp;
}

const S = {view: 'lineages', types: new Set(TYPES.filter(t => t[2]).map(t => t[0])), source: 'all', sel: null, hl: null,
  lin: null, at: null, year: null, students: false, min2: true};
const srcOK = e => S.source === 'all' ? true : S.source === 'books' ? e.sources.some(s => s !== 'Wikidata')
  : e.sources.some(s => s.startsWith(S.source));
const edgeOn = e => S.types.has(e.type) && srcOK(e);
const alive = (p, Y) => { const b = p.birth_year || p.yr; return (!b || Y >= b + 15) && (p.death_year ? Y <= p.death_year : (!b || Y <= b + 85)); };
const happened = (e, Y) => { if (e.year_start) return Y >= e.year_start;
  const s = byId.get(e.source), t = byId.get(e.target), b = Math.max(s.yr || 0, t.yr || 0); return !b || Y >= b + 20; };
const activeIn = (e, Y) => e.span && Y >= e.span.a && Y <= e.span.b;

// ---------------------------------------------------------------- lineage graph
const W = 3400, H = 1500, PAD = 70, XUND = W - PAD - 60;
const xs = d3.scaleLinear().domain([1600, 1740, 1900]).range([PAD, PAD + 330, W - PAD - 220]).clamp(true);
{
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (const p of people) { p.tx = p.yr ? xs(p.yr) : XUND; p.x = p.tx; p.y = H / 2 + (rnd() - .5) * H * .85; }
  const links = ppE.map(e => ({source: e.source, target: e.target, type: e.type}));
  const sim = d3.forceSimulation(people)
    .force('x', d3.forceX(d => d.tx).strength(.85)).force('y', d3.forceY(H / 2).strength(.007))
    .force('link', d3.forceLink(links).id(d => d.id).distance(80).strength(l => l.type === 'student_of' ? .1 : .02))
    .force('charge', d3.forceManyBody().strength(-110).distanceMax(420))
    .force('collide', d3.forceCollide(d => d.r + 10)).stop();
  for (let i = 0; i < 340; i++) sim.tick();
  const ys = people.map(p => p.y).sort(d3.ascending), q0 = d3.quantileSorted(ys, .05), q1 = d3.quantileSorted(ys, .95);
  for (const p of people) p.y = Math.max(50, Math.min(H - 30, H * .12 + (p.y - q0) / (q1 - q0) * H * .76));
  people.filter(p => !p.yr).sort((a, b) => a.y - b.y).forEach((p, i, a) => { p.x = XUND + (i % 2 ? 38 : 0); p.y = 70 + (H - 110) * (i + .5) / a.length; });
}
const svg = d3.select('#graph'), stage = $('stage'), tip = $('tip');
const gAxis = svg.append('g'), gE = svg.append('g'), gN = svg.append('g'), gL = svg.append('g');
const eSel = gE.selectAll('path').data(ppE).join('path').attr('class', e => 'edge ' + e.type);
let hover = null, T = d3.zoomIdentity, vw = 800, vh = 600, raf = 0;
const nSel = gN.selectAll('circle').data(people).join('circle').attr('class', d => 'node' + (d.est ? ' est' : '')).attr('r', d => d.r)
  .on('click', (ev, d) => { ev.stopPropagation(); select(d.id); })
  .on('pointerenter', (ev, d) => { hover = d; showTip(ev, d.label + (d.birth_year || d.death_year ? `  ${d.birth_year || '?'}–${d.death_year || '?'}` : '')); drawGraph(); })
  .on('pointermove', ev => moveTip(ev)).on('pointerleave', () => { hover = null; tip.hidden = true; drawGraph(); });
function showTip(ev, text) { tip.hidden = false; tip.textContent = text; moveTip(ev); }
function moveTip(ev) { const r = stage.getBoundingClientRect(); tip.style.left = (ev.clientX - r.left) + 'px'; tip.style.top = (ev.clientY - r.top) + 'px'; }
const zoom = d3.zoom().scaleExtent([.15, 14]).on('zoom', ev => { T = ev.transform; if (!raf) raf = requestAnimationFrame(() => { raf = 0; drawGraph(); }); });
svg.call(zoom).on('dblclick.zoom', null).on('click', () => select(null));
const go = (sel, z, t, ms = 450) => (reduced || !ms ? sel : sel.transition().duration(ms)).call(z.transform, t);
function fitGraph(ms) { const FW = W + 170, k = Math.min(vw / FW, vh / H) * .99; go(svg, zoom, d3.zoomIdentity.translate((vw - FW * k) / 2, (vh - H * k) / 2).scale(k), ms); }
function centre(d) { const k = Math.max(T.k, 1.5); go(svg, zoom, d3.zoomIdentity.translate(vw / 2 - d.x * k, vh / 2 - d.y * k).scale(k)); }
$('zin').onclick = () => svg.transition().call(zoom.scaleBy, 1.6);
$('zout').onclick = () => svg.transition().call(zoom.scaleBy, 1 / 1.6);
$('zfit').onclick = () => fitGraph();

function drawGraph() {
  if (S.view !== 'lineages') return;
  const Y = S.year;
  const gap = T.k * (xs(1810) - xs(1800)), step = gap >= 46 ? 10 : gap * 2 >= 46 ? 20 : 50;
  const yrs = []; for (let y = 1600; y <= 1900; y += 10) if (y % step === 0 && (y >= 1740 || y % 50 === 0)) yrs.push(y);
  gAxis.selectAll('g.tick').data(yrs, d => d).join(en => { const g = en.append('g').attr('class', 'tick'); g.append('line'); g.append('text').attr('y', 16).attr('text-anchor', 'middle'); return g; })
    .classed('major', d => d % 50 === 0).attr('transform', d => `translate(${T.applyX(xs(d))},0)`)
    .call(g => g.select('line').attr('y1', 24).attr('y2', vh)).call(g => g.select('text').text(d => d));
  gAxis.selectAll('g.undated').data([0]).join(en => { const g = en.append('g').attr('class', 'undated'); g.append('text').attr('y', 16).attr('text-anchor', 'middle').text('undated'); return g; })
    .attr('transform', `translate(${T.applyX(XUND)},0)`);
  gAxis.selectAll('line.nowline').data(Y && Y <= 1900 ? [Y] : []).join('line').attr('class', 'nowline')
    .attr('x1', d => T.applyX(xs(d))).attr('x2', d => T.applyX(xs(d))).attr('y1', 24).attr('y2', vh);
  // edges run from the elder party (teacher, predecessor, influencer) to the younger
  eSel.attr('display', e => edgeOn(e) ? null : 'none').attr('d', e => {
    const a = byId.get(e.target), b = byId.get(e.source);
    const x1 = T.applyX(a.x), y1 = T.applyY(a.y), x2 = T.applyX(b.x), y2 = T.applyY(b.y);
    const dx = x2 - x1, dy = y2 - y1, c = .13, mx = (x1 + x2) / 2 - dy * c, my = (y1 + y2) / 2 + dx * c;
    const ex = x2 - mx, ey = y2 - my, el = Math.hypot(ex, ey) || 1, back = b.r + 3;
    return `M${x1},${y1}Q${mx},${my} ${x2 - ex / el * back},${y2 - ey / el * back}`;
  }).each(function (e) {
    const dim = !!(S.hl && !(S.hl.has(e.source) && S.hl.has(e.target) && (S.lin ? S.lin.has(e) : true))) || !!(Y && !happened(e, Y));
    // markers do not inherit stroke-opacity, so dimmed edges drop their arrowheads
    d3.select(this).classed('dim', dim).classed('hl', !dim && !!(S.lin && S.lin.has(e)))
      .attr('marker-end', !dim && ARROWS.has(e.type) ? `url(#m-${e.type})` : null);
  });
  const nodeDim = d => !!(S.hl && !S.hl.has(d.id)) || !!(Y && !alive(d, Y));
  nSel.attr('cx', d => T.applyX(d.x)).attr('cy', d => T.applyY(d.y))
    .classed('dim', nodeDim).classed('hl', d => !!(S.hl && S.hl.has(d.id) && !S.at))
    .classed('at', d => !!(S.at && S.hl.has(d.id))).classed('sel', d => d.id === S.sel);
  // labels: greedy declutter, most important first
  const cand = people.filter(d => { const x = T.applyX(d.x), y = T.applyY(d.y); return x > -40 && x < vw + 40 && y > 20 && y < vh + 20; })
    .map(d => ({d, pr: (d === hover ? 1e6 : 0) + (d.id === S.sel ? 1e5 : 0) + (S.hl && S.hl.has(d.id) ? 1e3 : 0) + d.nStud * 12 + d.deg}))
    .sort((a, b) => b.pr - a.pr);
  const boxes = [], out = [];
  for (const {d, pr} of cand) {
    if (nodeDim(d) && d !== hover && (T.k < 2.5 || Y)) continue;
    const big = d.nStud >= 5, fs = big ? 14 : 11.5, w = d.label.length * fs * .56 + 6, h = fs + 4;
    const x = T.applyX(d.x) + d.r + 4, y = T.applyY(d.y) - h / 2;
    if (pr < 1e5 && boxes.some(b => x < b.x + b.w && x + w > b.x && y < b.y + b.h && y + h > b.y)) continue;
    boxes.push({x, y, w, h}); out.push({d, x, y: y + h - 5, big}); if (out.length > 170) break;
  }
  gL.selectAll('text').data(out, o => o.d.id).join('text').attr('class', o => 'lbl' + (o.big ? ' big' : ''))
    .classed('dim', o => nodeDim(o.d)).attr('x', o => o.x).attr('y', o => o.y).text(o => o.d.label);
  if (Y) $('yearnote').textContent = `${people.filter(p => (p.birth_year || p.yr) && alive(p, Y)).length} of these scholars active`;
}

// ---------------------------------------------------------------- chairs: one timeline per place
const CY0 = 1770, CY1 = 1925;
const surname = l => { const t = l.replace(/,.*$/, '').split(/\s+/); return t[t.length - 1]; };
function drawChairs() {
  if (S.view !== 'chairs') return;
  const wrap = $('chairs-scroll'), w = Math.max(760, wrap.clientWidth - 4), ML = 18, MR = 40;
  const x = d3.scaleLinear().domain([CY0, CY1]).range([ML, w - MR]).clamp(true);
  const rows = [];
  for (const n of insts) {
    const posts = n.inc.filter(e => e.type === 'position_at' && e.span && srcOK(e) && e.span.b >= CY0);
    if (posts.length < (S.min2 && n.id !== S.sel ? 2 : 1)) continue;
    const study = S.students ? n.inc.filter(e => e.type === 'studied_at' && e.span && srcOK(e) && e.span.b >= CY0) : [];
    rows.push({n, posts, study});
  }
  rows.sort((a, b) => b.posts.length - a.posts.length || d3.min(a.posts, e => e.span.a) - d3.min(b.posts, e => e.span.a));
  let y = 34; const bars = [];
  for (const r of rows) {
    r.y = y; y += 24;
    for (const [list, h, lab] of [[r.posts, 15, true], [r.study, 6, false]]) {
      const lanes = [];
      for (const e of [...list].sort((a, b) => a.span.a - b.span.a)) {
        const x0 = x(e.span.a), x1 = Math.max(x(e.span.b), x0 + 4), name = surname(byId.get(e.source).label);
        const tw = name.length * 6.4 + 8, inside = lab && x1 - x0 >= tw, end = lab && !inside ? x1 + tw : x1;
        let li = lanes.findIndex(le => le + 5 <= x0); if (li < 0) { li = lanes.length; lanes.push(0); }
        lanes[li] = end;
        bars.push({e, x0, x1, y: y + li * (h + 4), h, name, inside, lab});
      }
      y += lanes.length * (h + 4);
    }
    y += 14; r.y1 = y;
  }
  const c = d3.select('#chairs').attr('width', w).attr('height', y + 20);
  c.selectAll('*').remove();
  const defs = c.append('defs');
  for (const [id, stops] of [['fadeR', [[0, 1], [.6, 1], [1, .08]]], ['fadeL', [[0, .08], [.4, 1], [1, 1]]], ['fadeLR', [[0, .08], [.3, 1], [.7, 1], [1, .08]]]]) {
    const g = defs.append('linearGradient').attr('id', 'g' + id);
    for (const [o, a] of stops) g.append('stop').attr('offset', o).attr('stop-color', '#fff').attr('stop-opacity', a);
    defs.append('mask').attr('id', id).attr('maskContentUnits', 'objectBoundingBox').append('rect').attr('width', 1).attr('height', 1).attr('fill', `url(#g${id})`);
  }
  const decades = d3.range(CY0, CY1 + 1, 10);
  c.append('g').attr('class', 'c-grid').selectAll('line').data(decades).join('line').attr('class', d => d % 50 === 0 ? 'major' : null)
    .attr('x1', d => x(d)).attr('x2', d => x(d)).attr('y1', 0).attr('y2', y + 20);
  const gr = c.append('g');
  for (const r of rows) {
    gr.append('line').attr('class', 'c-rule').attr('x1', 0).attr('x2', w).attr('y1', r.y - 8).attr('y2', r.y - 8);
    const t = gr.append('text').attr('class', 'c-head' + (r.n.id === S.sel ? ' sel' : '')).attr('x', ML).attr('y', r.y + 10).attr('data-row', r.n.id)
      .text(r.n.label).on('click', () => select(r.n.id));
    t.append('tspan').attr('class', 'c-sub').attr('dx', 8).text([r.n.city, `${r.posts.length} posts`].filter(Boolean).join(' · '));
  }
  const selPeople = S.sel && byId.get(S.sel).type === 'person' ? S.hl : null;
  const g = c.append('g').selectAll('g').data(bars).join('g').attr('class', 'bar-g')
    .classed('dim', b => !!(selPeople && b.e.source !== S.sel)).attr('data-person', b => b.e.source);
  g.append('rect').attr('class', b => 'bar-r ' + (b.lab ? (b.e.ys_src === 'model' || (!b.e.ys_src && b.e.ye_src === 'model') ? 'model' : (b.e.ys_src || b.e.ye_src) === 'wikidata' ? 'wikidata' : 'text') : 'study'))
    .attr('x', b => b.x0).attr('y', b => b.y).attr('width', b => b.x1 - b.x0).attr('height', b => b.h).attr('rx', 1.5)
    .attr('mask', b => b.x1 - b.x0 > 12 && (b.e.span.openL || b.e.span.openR) ? `url(#fade${b.e.span.openL ? 'L' : ''}${b.e.span.openR ? 'R' : ''})` : null)
    .on('click', (ev, b) => select(b.e.source))
    .on('pointerenter', (ev, b) => showTip(ev, `${byId.get(b.e.source).label} · ${b.e.roles[0] || (b.lab ? 'post' : 'student')} · ${b.e.span.openL ? '?' : b.e.span.a}–${b.e.span.openR ? '?' : b.e.span.b}`))
    .on('pointermove', moveTip).on('pointerleave', () => { tip.hidden = true; });
  g.filter(b => b.lab).append('text').attr('class', b => 'bar-t' + (b.inside && !(b.e.ys_src === 'model') ? ' in' : ''))
    .attr('x', b => b.inside ? b.x0 + 4 : b.x1 + 4).attr('y', b => b.y + 11).text(b => b.name);
  // the decade axis stays in view while the list scrolls
  const ax = c.append('g').attr('class', 'c-axis');
  ax.append('rect').attr('width', w).attr('height', 24).attr('fill', 'var(--ground)');
  ax.selectAll('text').data(decades).join('text').attr('x', d => x(d)).attr('y', 16).attr('text-anchor', 'middle').text(d => d);
  const stick = () => ax.attr('transform', `translate(0,${wrap.scrollTop})`);
  wrap.onscroll = stick; stick();
  drawChairs.rows = rows;
}
function scrollChairs() {
  if (S.view !== 'chairs' || !S.sel) return;
  const wrap = $('chairs-scroll'), n = byId.get(S.sel);
  const el = n.type === 'institution' ? wrap.querySelector(`[data-row="${CSS.escape(S.sel)}"]`) : wrap.querySelector(`[data-person="${CSS.escape(S.sel)}"]`);
  if (el) wrap.scrollTo({top: Math.max(0, el.getBBox().y - 80), behavior: reduced ? 'auto' : 'smooth'});
}

// ---------------------------------------------------------------- map
const msvg = d3.select('#map'), gLand = msvg.append('g'), gPl = msvg.append('g'), gPlL = msvg.append('g');
const land = topojson.feature(WORLD, WORLD.objects.land), places = insts.filter(n => n.lat != null && n.lon != null);
let proj = d3.geoMercator(), MT = d3.zoomIdentity, mw = 800, mh = 600, mraf = 0;
const landPath = gLand.append('path').attr('class', 'land');
const mzoom = d3.zoom().scaleExtent([.1, 40]).on('zoom', ev => { MT = ev.transform; if (!mraf) mraf = requestAnimationFrame(() => { mraf = 0; drawMap(); }); });
msvg.call(mzoom).on('dblclick.zoom', null).on('click', () => select(null));
$('min').onclick = () => msvg.transition().call(mzoom.scaleBy, 1.8);
$('mout').onclick = () => msvg.transition().call(mzoom.scaleBy, 1 / 1.8);
$('mfit').onclick = () => go(msvg, mzoom, d3.zoomIdentity);
const REGIONS = {europe: [[-10, 36.5], [31, 60.5]], india: [[66, 6], [93, 35]], world: [[-125, -10], [145, 62]]};
function toRegion(r) { // zoom the fitted (Europe) projection so that region r fills the view
  const [[x0, y1], [x1, y0]] = [proj(REGIONS[r][0]), proj(REGIONS[r][1])];
  const k = Math.min((mw - 48) / (x1 - x0), (mh - 48) / (y1 - y0));
  go(msvg, mzoom, d3.zoomIdentity.translate(mw / 2 - k * (x0 + x1) / 2, mh / 2 - k * (y0 + y1) / 2).scale(k));
}
function layoutMap() {
  proj = d3.geoMercator().fitExtent([[24, 24], [mw - 24, mh - 24]], {type: 'MultiPoint', coordinates: REGIONS.europe});
  landPath.attr('d', d3.geoPath(proj)(land));
  for (const n of places) [n.px, n.py] = proj([n.lon, n.lat]);
}
function drawMap() {
  if (S.view !== 'map') return;
  const Y = S.year;
  gLand.attr('transform', MT);
  let posts = 0, active = 0;
  for (const n of places) {
    const P = n.inc.filter(e => e.type === 'position_at' && srcOK(e)), St = n.inc.filter(e => e.type === 'studied_at' && srcOK(e));
    n.nP = new Set((Y ? P.filter(e => activeIn(e, Y)) : P).map(e => e.source)).size;
    n.nS = new Set((Y ? St.filter(e => activeIn(e, Y)) : St).map(e => e.source)).size;
    n.any = P.length + St.length > 0; posts += n.nP; if (n.nP) active++;
  }
  const vis = places.filter(n => n.any).sort((a, b) => b.nP - a.nP);
  const rr = n => n.nP ? 3 + 3.2 * Math.sqrt(n.nP) : 2.5;
  gPl.selectAll('circle.study').data(vis.filter(n => n.nS), n => n.id).join('circle').attr('class', 'place study')
    .attr('cx', n => MT.applyX(n.px)).attr('cy', n => MT.applyY(n.py)).attr('r', n => rr(n) + 2 + 1.6 * Math.sqrt(n.nS));
  gPl.selectAll('circle.main').data(vis, n => n.id).join('circle').attr('class', n => 'place main' + (n.nP ? '' : ' idle') + (n.id === S.sel ? ' sel' : ''))
    .attr('cx', n => MT.applyX(n.px)).attr('cy', n => MT.applyY(n.py)).attr('r', rr)
    .on('click', (ev, n) => { ev.stopPropagation(); select(n.id); })
    .on('pointerenter', (ev, n) => showTip(ev, `${n.label} · ${n.nP} post${n.nP === 1 ? '' : 's'}${n.nS ? ` · ${n.nS} studying` : ''}`))
    .on('pointermove', moveTip).on('pointerleave', () => { tip.hidden = true; });
  const boxes = [], out = [];
  for (const n of vis) {
    if (!n.nP && n.id !== S.sel && MT.k < 5) continue;
    const label = n.city && /^University of /.test(n.label) ? n.label.replace('University of ', '') : n.label;
    const w = label.length * 6.3 + 6, h = 15, x = MT.applyX(n.px) + rr(n) + 4, y = MT.applyY(n.py) - h / 2;
    if (x < 0 || x > mw || y < 0 || y > mh) continue;
    if (n.id !== S.sel && boxes.some(b => x < b.x + b.w && x + w > b.x && y < b.y + b.h && y + h > b.y)) continue;
    boxes.push({x, y, w, h}); out.push({n, x, y: y + 11, label});
  }
  gPlL.selectAll('text').data(out, o => o.n.id).join('text').attr('class', 'plbl').attr('x', o => o.x).attr('y', o => o.y).text(o => o.label);
  $('yearnote').textContent = Y ? `${posts} post-holders at ${active} places` : `${active} places with posts`;
}

// ---------------------------------------------------------------- selection + panel
function lineage(id) { // all academic ancestors and descendants over the teacher→student edges currently shown
  const ids = new Set([id]), es = new Set();
  const walk = (start, up) => { const st = [start]; while (st.length) { const n = byId.get(st.pop());
    for (const e of (up ? n.out : n.inc)) { if (e.type !== 'student_of' || !edgeOn(e)) continue;
      const o = up ? e.target : e.source; es.add(e); if (!ids.has(o)) { ids.add(o); st.push(o); } } } };
  walk(id, true); walk(id, false);
  const n = byId.get(id);
  for (const e of [...n.out, ...n.inc]) if (PP.has(e.type) && edgeOn(e)) { es.add(e); ids.add(e.source); ids.add(e.target); }
  return {ids, es};
}
function select(id, move) {
  if (id && !byId.has(id)) id = null;
  S.sel = id; S.at = null; S.hl = null; S.lin = null;
  if (id) { const n = byId.get(id);
    if (n.type === 'person') { const l = lineage(id); S.hl = l.ids; S.lin = l.es; if (move && S.view === 'lineages') centre(n); }
    else { S.at = id; S.hl = new Set(n.inc.filter(srcOK).map(e => e.source));
      if (move && S.view === 'map' && n.px != null) { const k = Math.max(MT.k, 4); go(msvg, mzoom, d3.zoomIdentity.translate(mw / 2 - n.px * k, mh / 2 - n.py * k).scale(k)); } }
  }
  renderPanel(); redraw(); if (move) scrollChairs(); writeHash();
  if (id && move && matchMedia('(max-width:860px)').matches) $('panel').scrollIntoView({behavior: reduced ? 'auto' : 'smooth'});
}
const yrs = (a, b) => a && b && a !== b ? `${a}–${b}` : a ? `${a}` : b ? `until ${b}` : '';
function when(e) {
  const y = yrs(e.year_start, e.year_end); if (!y) return '';
  const src = new Set([e.ys_src, e.ye_src]);
  return (src.has('model') ? 'c. ' : '') + y + (src.has('wikidata') ? '<span class="tag" title="Year from Wikidata">Wikidata</span>' : '') +
    (src.has('model') ? '<span class="tag model" title="Year recalled by the language model, not found in the books or in Wikidata — treat as approximate">model</span>' : '');
}
function item(e, otherId, self) {
  const o = byId.get(otherId), ev = e.evidence[0];
  const meta = [esc(e.roles.join(' · ')), when(e), e.type !== 'position_at' && ev && ev.place ? esc(ev.place) : ''].filter(Boolean).join(', ');
  const q = ev ? `<span class="q">“${esc(ev.evidence)}” <span class="src">— ${esc(ev.source)}${e.evidence.length > 1 ? ` +${e.evidence.length - 1}` : ''}</span>${e.explicit ? '' : ' <span class="inferred">inferred</span>'}</span>`
    : `<span class="meta">Wikidata statement${self.qid || o.qid ? ` · <a href="https://www.wikidata.org/wiki/${esc((byId.get(e.source).qid) || o.qid)}" target="_blank" rel="noopener">source</a>` : ''}</span>`;
  return `<li class="${S.year && activeIn(e, S.year) ? 'now' : ''}"><button class="who${o.type === 'institution' ? ' inst' : ''}" data-id="${esc(o.id)}">${esc(o.label)}</button>` +
    (meta ? `<span class="meta">${meta}</span>` : '') + q + '</li>';
}
function section(title, es, pick, self) {
  es = es.filter(srcOK); if (!es.length) return '';
  es.sort((a, b) => (a.year_start || a.year_end || 9999) - (b.year_start || b.year_end || 9999));
  return `<h3><span>${title}</span><span>${es.length}</span></h3><ul class="items">${es.map(e => item(e, pick(e), self)).join('')}</ul>`;
}
function renderPanel() {
  const el = $('panel'), n = S.sel && byId.get(S.sel);
  if (!n) { el.innerHTML = overview(); return; }
  const back = '<button class="back" data-id="">← Overview</button>';
  const o = t => n.out.filter(e => e.type === t), i = t => n.inc.filter(e => e.type === t), src = e => e.source, tgt = e => e.target;
  const either = e => e.source === n.id ? e.target : e.source;
  const links = [n.qid && `<a href="https://www.wikidata.org/wiki/${esc(n.qid)}" target="_blank" rel="noopener">Wikidata</a>`,
    n.enwiki && `<a href="https://en.wikipedia.org/wiki/${encodeURIComponent(n.enwiki)}" target="_blank" rel="noopener">Wikipedia</a>`,
    n.dewiki && `<a href="https://de.wikipedia.org/wiki/${encodeURIComponent(n.dewiki)}" target="_blank" rel="noopener">Wikipedia (de)</a>`].filter(Boolean).join('');
  if (n.type === 'person') {
    const life = n.birth_year || n.death_year ? `${n.birth_year || '?'} – ${n.death_year || '?'}` : 'dates unknown';
    const where = [n.birth_place && `b. ${n.birth_place}`, n.death_place && `d. ${n.death_place}`].filter(Boolean).join(' · ');
    el.innerHTML = back + `<div class="head">${n.image ? `<img alt="" loading="lazy" src="https://commons.wikimedia.org/wiki/Special:FilePath/${encodeURIComponent(n.image)}?width=180">` : ''}<div>` +
      `<h2>${esc(n.label)}</h2><div class="dates">${life}${where ? `<br>${esc(where)}` : ''}</div>` +
      (n.field ? `<div class="field">${esc(n.field)}</div>` : '') + (links ? `<div class="links">${links}</div>` : '') + '</div></div>' +
      (n.variants.length > 1 || n.variants[0] !== n.label ? `<div class="aka">In the books as: ${n.variants.map(esc).join(' · ')}</div>` : '') +
      section('Studied under', o('student_of'), tgt, n) + section('Students', i('student_of'), src, n) +
      section('Posts held', o('position_at'), tgt, n) + section('Studied at', o('studied_at'), tgt, n) +
      section('Succeeded', o('succeeded'), tgt, n) + section('Succeeded by', i('succeeded'), src, n) +
      section('Influenced by', o('influenced_by'), tgt, n) + section('Influenced', i('influenced_by'), src, n) +
      section('Worked with', [...o('collaborated_with'), ...i('collaborated_with')], either, n) +
      section('Founded', o('founded'), tgt, n) + section('Kin, friends, opponents', [...o('other'), ...i('other')], either, n);
  } else {
    el.innerHTML = back + `<h2>${esc(n.label)}</h2><div class="dates">${esc([n.city, n.country].filter(Boolean).join(', '))}${n.inception ? ` · founded ${n.inception}` : ''}</div>` +
      (links ? `<div class="links">${links}</div>` : '') + (S.year ? `<p class="note" style="margin-top:8px">● marks who was here in ${S.year}.</p>` : '') +
      section('Held posts here', i('position_at'), src, n) + section('Studied here', i('studied_at'), src, n) + section('Founded by', i('founded'), src, n);
  }
  el.scrollTop = 0;
}
function overview() {
  const top = [...people].sort((a, b) => b.nStud - a.nStud).slice(0, 14);
  const cnt = n => new Set(n.inc.filter(e => e.type !== 'founded').map(e => e.source)).size;
  const pl = insts.map(n => [n, cnt(n)]).sort((a, b) => b[1] - a[1]).slice(0, 16);
  const dated = DATA.edges.filter(e => e.year_start || e.year_end).length;
  return `<h2>Who taught whom, where, and when</h2>
  <p style="margin-top:10px">An academic genealogy of Sanskrit philology and its neighbours, read out of two books: Ernst Windisch’s <i>Geschichte der Sanskrit-Philologie und indischen Altertumskunde</i> (1917–20) and the biographical asides in Moriz Winternitz’s <i>Geschichte der indischen Litteratur</i> (1908–20). Every link from the books carries the sentence it rests on; dates the books leave out come from Wikidata.</p>
  <div class="stats"><div><b>${people.length}</b><span>scholars</span></div><div><b>${insts.length}</b><span>places</span></div><div><b>${DATA.edges.length}</b><span>links</span></div><div><b>${dated}</b><span>dated</span></div></div>
  <p class="note"><b>Lineages</b> places everyone by year of birth and draws teacher → student lines. <b>Chairs</b> shows who held which post, place by place. <b>Map</b> plays the spread of the field year by year.</p>
  <h3><span>Largest schools</span><span>students named</span></h3>
  <ul class="rank">${top.map(p => `<li><button class="who" data-id="${esc(p.id)}">${esc(p.label)}</button><span class="n">${p.nStud}</span></li>`).join('')}</ul>
  <h3><span>Places</span><span>scholars</span></h3>
  <ul class="rank">${pl.map(([n, c]) => `<li><button class="who inst" data-id="${esc(n.id)}">${esc(n.label)}</button><span class="n">${c}</span></li>`).join('')}</ul>
  <h3><span>How to read this</span></h3>
  <p class="note">Relations were extracted from the OCR text chunk by chunk with Gemini and kept only when the quoted evidence was found verbatim in the text. Name variants were merged automatically and matched to Wikidata by name and life dates. Years come in three grades: unmarked years are stated in the books; <span class="tag">Wikidata</span> years come from dated Wikidata statements; <span class="tag model">model</span> years were recalled by the language model where both are silent, and are approximate. Bars that fade out have an unknown end. Hollow dots are scholars without a known birth year, placed by their neighbours. “Inferred” marks links the text implies rather than states. The books end around 1920, and so does the picture; Wikidata adds a few later posts.</p>
  <p class="note">Code and data: <a href="https://github.com/dharmamitra/indology-genealogy">github.com/dharmamitra/indology-genealogy</a></p>`;
}
$('panel').addEventListener('click', ev => { const b = ev.target.closest('[data-id]'); if (b) select(b.dataset.id || null, true); });

// ---------------------------------------------------------------- controls
function renderTools() {
  const t = $('tools');
  if (S.view === 'lineages') t.innerHTML = TYPES.map(([k, l]) => `<label class="tg"><input type="checkbox" id="tg-${k}" data-t="${k}" ${S.types.has(k) ? 'checked' : ''}><span class="swatch line ${k}"></span>${l}</label>`).join('') +
    '<span>· left to right is year of birth · click a scholar for the whole lineage</span>';
  else if (S.view === 'chairs') t.innerHTML = '<span class="key"><span class="swatch text"></span>years from the books</span><span class="key"><span class="swatch wikidata"></span>from Wikidata</span><span class="key"><span class="swatch model"></span>recalled by the model</span>' +
    `<label class="tg"><input type="checkbox" id="tg-students" ${S.students ? 'checked' : ''}><span class="swatch study"></span>students</label>` +
    `<label class="tg"><input type="checkbox" id="tg-min2" ${S.min2 ? 'checked' : ''}><span></span>only places with 2+ dated posts</label>`;
  else t.innerHTML = '<span class="key"><span class="swatch text" style="border-radius:50%;width:10px;height:10px"></span>people holding a post</span><span class="key"><span class="swatch" style="border:2px solid var(--turmeric);border-radius:50%;width:10px;height:10px"></span>people studying</span><span>· press ▶ to watch the field spread ·</span><span class="regions">' +
    ['europe', 'india', 'world'].map(r => `<button data-region="${r}">${r[0].toUpperCase() + r.slice(1)}</button>`).join('') + '</span>';
  $('yearbar').hidden = S.view === 'chairs';
}
$('tools').addEventListener('click', ev => { const b = ev.target.closest('[data-region]'); if (b) toRegion(b.dataset.region); });
$('tools').addEventListener('change', ev => {
  const i = ev.target;
  if (i.dataset.t) { i.checked ? S.types.add(i.dataset.t) : S.types.delete(i.dataset.t); select(S.sel, false); }
  else if (i.id === 'tg-students') { S.students = i.checked; drawChairs(); }
  else if (i.id === 'tg-min2') { S.min2 = i.checked; drawChairs(); }
});
$('source').addEventListener('change', ev => { S.source = ev.target.value; select(S.sel, false); });
function redraw() { drawGraph(); drawChairs(); drawMap(); }
function resize() {
  const v = $('view-' + S.view).getBoundingClientRect();
  if (S.view === 'lineages') { const first = vw === 800 && vh === 600; vw = v.width; vh = v.height; svg.attr('viewBox', `0 0 ${vw} ${vh}`); if (first) fitGraph(0); }
  if (S.view === 'map') { mw = v.width; mh = v.height; msvg.attr('viewBox', `0 0 ${mw} ${mh}`); layoutMap(); }
  redraw();
}
function setView(v) {
  S.view = v;
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === v);
  for (const k of ['lineages', 'chairs', 'map']) $('view-' + k).hidden = k !== v;
  renderTools(); resize(); scrollChairs(); writeHash();
  if (v === 'map' && !S.year && !setView.mapSeen) { setView.mapSeen = true; setYear(+$('year').value); }
}
document.querySelector('.tabs').addEventListener('click', ev => { const b = ev.target.closest('[data-view]'); if (b) setView(b.dataset.view); });

let timer = null;
function setYear(y) {
  S.year = y; $('allyears').checked = !y; $('yearbar').classList.toggle('off', !y);
  if (y) { $('year').value = y; $('yearout').textContent = y; } else $('yearnote').textContent = '';
  redraw(); if (S.sel && byId.get(S.sel).type === 'institution') renderPanel();
}
$('year').addEventListener('input', ev => setYear(+ev.target.value));
$('allyears').addEventListener('change', ev => { stop(); setYear(ev.target.checked ? null : +$('year').value); });
function stop() { clearInterval(timer); timer = null; $('play').textContent = '▶'; }
$('play').onclick = () => {
  if (timer) return stop();
  let y = S.year && S.year < 1920 ? S.year : 1780; $('play').textContent = '❚❚';
  timer = setInterval(() => { setYear(y); if (++y > 1920) stop(); }, 220);
};

const names = new Map(DATA.nodes.map(n => [n.label.toLowerCase(), n.id]));
for (const n of DATA.nodes) for (const v of n.variants || []) if (!names.has(v.toLowerCase())) names.set(v.toLowerCase(), n.id);
$('names').innerHTML = [...DATA.nodes].sort((a, b) => a.label.localeCompare(b.label)).map(n => `<option value="${esc(n.label)}">`).join('');
function doSearch() { const q = $('search').value.trim().toLowerCase(); if (!q) return;
  let id = names.get(q); if (!id) { const k = [...names.keys()].find(k => k.includes(q)); id = k && names.get(k); }
  if (id) { select(id, true); $('search').blur(); } }
$('search').addEventListener('change', doSearch); $('search').addEventListener('keydown', ev => { if (ev.key === 'Enter') doSearch(); });

// shareable state: #view=chairs&sel=P:Rudolf von Roth
function writeHash() { const h = new URLSearchParams(); if (S.view !== 'lineages') h.set('view', S.view); if (S.sel) h.set('sel', S.sel);
  history.replaceState(null, '', h.toString() ? '#' + h : location.pathname + location.search); }
const h0 = new URLSearchParams(location.hash.slice(1));
new ResizeObserver(resize).observe(stage);
setView(['lineages', 'chairs', 'map'].includes(h0.get('view')) ? h0.get('view') : 'lineages');
if (h0.get('sel')) select(h0.get('sel'), true); else renderPanel();
})();
