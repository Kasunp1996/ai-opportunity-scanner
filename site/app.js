/* AI Opportunity Scanner — static dashboard. Reads JSON produced by the nightly pipeline. */
(() => {
"use strict";

// ---------------------------------------------------------------------------------------------
// utilities
// ---------------------------------------------------------------------------------------------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const isNum = (x) => typeof x === "number" && isFinite(x);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 0, sign = false) => isNum(x) ? `${sign && x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–";
const usd = (x, d = 2) => isNum(x) ? `$${x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}` : "–";
const big = (x) => {
  if (!isNum(x)) return "–";
  const a = Math.abs(x), s = x < 0 ? "-" : "";
  if (a >= 1e12) return `${s}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(0)}M`;
  return `${s}$${a.toFixed(0)}`;
};
const mult = (x) => isNum(x) ? `${x.toFixed(1)}x` : "–";
const cls = (x) => isNum(x) ? (x > 0 ? "pos" : x < 0 ? "neg" : "") : "";
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
};
const watch = {
  list() { return store.get("scanner-watchlist", []); },
  has(t) { return this.list().includes(t); },
  toggle(t) { const l = this.list(); const i = l.indexOf(t); i >= 0 ? l.splice(i, 1) : l.push(t); store.set("scanner-watchlist", l); },
};

const cache = {};
async function getJSON(path) {
  if (cache[path] !== undefined) return cache[path];
  try {
    const r = await fetch(`data/${path}`, { cache: "no-cache" });
    cache[path] = r.ok ? await r.json() : null;
  } catch (e) { cache[path] = null; }
  return cache[path];
}
async function getText(path) {
  try { const r = await fetch(`data/${path}`, { cache: "no-cache" }); return r.ok ? await r.text() : null; } catch (e) { return null; }
}

function tierBadge(t) {
  const c = { "Tier 1": "tier-1", "Tier 2": "tier-2", "Tier 3": "tier-3", "Avoid": "tier-avoid" }[t] || "tier-na";
  return `<span class="badge ${c}">${esc(t)}</span>`;
}
function scoreBar(s) {
  if (!isNum(s)) return "–";
  return `<span class="scorebar"><i><b style="width:${Math.max(0, Math.min(100, s))}%"></b></i><span class="num">${s.toFixed(0)}</span></span>`;
}
function flagChips(row, details) {
  const fl = details || {};
  const names = row ? row.flags || [] : Object.keys(fl);
  const high = new Set(row ? row.high_flags || [] : Object.keys(fl).filter((k) => fl[k].severity === "high"));
  return names.map((f) => `<span class="flag ${high.has(f) ? "high" : "medium"}" title="${esc(fl[f]?.detail || "")}">${esc(f.replace(/_/g, " "))}</span>`).join("");
}
function sparkline(vals) {
  if (!vals || vals.length < 2) return "";
  const w = 84, h = 24, lo = Math.min(...vals), hi = Math.max(...vals), r = hi - lo || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - 2 - ((v - lo) / r) * (h - 4)}`).join(" ");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="var(--ink-2)" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

// ---------------------------------------------------------------------------------------------
// tooltip + charts (SVG, hover layer by default)
// ---------------------------------------------------------------------------------------------
const tip = $("#tip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.style.display = "block";
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}
const hideTip = () => { tip.style.display = "none"; };
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || Math.abs(hi) || 1;
  const step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n + 0.5) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toPrecision(12));
  return out;
}
const mon = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", year: "2-digit" });

function lineChart(host, o) {
  host.innerHTML = "";
  const W = Math.max(280, host.clientWidth || 600), H = o.height || 240;
  const m = { l: 52, r: o.rightPad ?? 86, t: 12, b: 26 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.label || "chart" }, host);
  const all = o.series.flatMap((s) => s.pts.map((p) => p.y)).concat((o.hlines || []).map((h) => h.y));
  if (o.band) all.push(o.band.y0, o.band.y1);
  let lo = Math.min(...all.filter(isNum)), hi = Math.max(...all.filter(isNum));
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.1 || 1;
  lo -= pad; hi += pad;
  if (o.zero && lo > 0) lo = 0;
  const xs = o.series[0].pts.map((p) => p.x);
  const x = (i) => m.l + (i / Math.max(1, xs.length - 1)) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  for (const t of niceTicks(lo, hi, 4)) {
    el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: "var(--grid)", "stroke-width": 1 }, svg);
    const tx = el("text", { x: m.l - 8, y: y(t) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--muted)", class: "num" }, svg);
    tx.textContent = o.yFmt ? o.yFmt(t) : t;
  }
  const nX = Math.min(6, xs.length);
  for (let k = 0; k < nX; k++) {
    const i = Math.round((k / Math.max(1, nX - 1)) * (xs.length - 1));
    const tx = el("text", { x: x(i), y: H - 6, "text-anchor": k === 0 ? "start" : k === nX - 1 ? "end" : "middle", "font-size": 11, fill: "var(--muted)" }, svg);
    tx.textContent = o.xFmt ? o.xFmt(xs[i]) : xs[i];
  }
  if (o.band && isNum(o.band.y0) && isNum(o.band.y1)) {
    const a = y(Math.max(o.band.y0, o.band.y1)), b = y(Math.min(o.band.y0, o.band.y1));
    el("rect", { x: m.l, y: a, width: W - m.l - m.r, height: Math.max(1, b - a), fill: "var(--band)" }, svg);
    const t = el("text", { x: W - m.r + 6, y: (a + b) / 2 + 4, "font-size": 11, fill: "var(--accent-ink)" }, svg);
    t.textContent = o.band.label;
  }
  for (const h of o.hlines || []) {
    if (!isNum(h.y)) continue;
    el("line", { x1: m.l, x2: W - m.r, y1: y(h.y), y2: y(h.y), stroke: h.color || "var(--ink-2)", "stroke-width": 1.25, "stroke-dasharray": h.dash ? "4 4" : "" }, svg);
    const t = el("text", { x: W - m.r + 6, y: y(h.y) + 4, "font-size": 11, fill: "var(--ink-2)" }, svg);
    t.textContent = h.label;
  }
  o.series.forEach((s) => {
    const d = s.pts.map((p, i) => isNum(p.y) ? `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.y).toFixed(1)}` : "").join("");
    el("path", { d, fill: "none", stroke: s.color || "var(--s1)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    if (o.series.length > 1) {
      const last = s.pts[s.pts.length - 1];
      const t = el("text", { x: W - m.r + 6, y: y(last.y) + 4, "font-size": 11, fill: "var(--ink-2)" }, svg);
      t.textContent = s.name;
    }
  });
  // hover layer
  const cross = el("line", { y1: m.t, y2: H - m.b, stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" }, svg);
  const dots = o.series.map((s) => el("circle", { r: 4, fill: s.color || "var(--s1)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }, svg));
  const hit = el("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" }, svg);
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(xs.length - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (xs.length - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    let html = `<b>${esc(o.tipX ? o.tipX(xs[i]) : xs[i])}</b>`;
    o.series.forEach((s, k) => {
      const v = s.pts[i]?.y;
      if (isNum(v)) { dots[k].setAttribute("cx", x(i)); dots[k].setAttribute("cy", y(v)); dots[k].setAttribute("visibility", "visible"); }
      html += `<br>${esc(s.name)}: <b>${o.yFmt ? o.yFmt(v) : v}</b>`;
    });
    (o.tipExtra || []).forEach((t) => { html += `<br><span class="muted">${esc(t)}</span>`; });
    showTip(html, ev.clientX, ev.clientY);
  };
  hit.addEventListener("mousemove", move);
  hit.addEventListener("touchstart", (e) => move(e.touches[0]), { passive: true });
  hit.addEventListener("mouseleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); });
}

function barChart(host, o) {
  host.innerHTML = "";
  const W = Math.max(280, host.clientWidth || 600), H = o.height || 220;
  const m = { l: 52, r: 8, t: 10, b: 26 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.label || "bar chart" }, host);
  const vals = o.vals.map((v) => (isNum(v) ? v : 0));
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  hi += (hi - lo) * 0.06;
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo || 1)) * (H - m.t - m.b);
  for (const t of niceTicks(lo, hi, 4)) {
    el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: "var(--grid)", "stroke-width": 1 }, svg);
    const tx = el("text", { x: m.l - 8, y: y(t) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--muted)" }, svg);
    tx.textContent = o.fmt(t);
  }
  const n = vals.length, slot = (W - m.l - m.r) / n, bw = Math.max(4, Math.min(34, slot - 2));
  vals.forEach((v, i) => {
    const cx = m.l + slot * i + slot / 2, x0 = cx - bw / 2, y0 = y(0), y1 = y(v), r = Math.min(4, bw / 2, Math.abs(y0 - y1));
    const top = Math.min(y0, y1), h = Math.abs(y0 - y1);
    const d = v >= 0
      ? `M${x0},${y0}V${top + r}Q${x0},${top} ${x0 + r},${top}H${x0 + bw - r}Q${x0 + bw},${top} ${x0 + bw},${top + r}V${y0}Z`
      : `M${x0},${y0}V${top + h - r}Q${x0},${top + h} ${x0 + r},${top + h}H${x0 + bw - r}Q${x0 + bw},${top + h} ${x0 + bw},${top + h - r}V${y0}Z`;
    el("path", { d, fill: v >= 0 ? (o.color || "var(--s1)") : "var(--critical)", opacity: o.dim && o.dim[i] ? 0.55 : 1 }, svg);
    if (i % Math.ceil(n / 6) === 0 || i === n - 1) {
      const t = el("text", { x: cx, y: H - 6, "text-anchor": "middle", "font-size": 11, fill: "var(--muted)" }, svg);
      t.textContent = o.cats[i];
    }
    const hit = el("rect", { x: m.l + slot * i, y: m.t, width: slot, height: H - m.t - m.b, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (ev) => showTip(`<b>${esc(o.cats[i])}</b><br>${esc(o.name || "")}: <b>${o.fmt(o.vals[i])}</b>${o.extra ? "<br>" + o.extra(i) : ""}`, ev.clientX, ev.clientY));
    hit.addEventListener("mouseleave", hideTip);
  });
  el("line", { x1: m.l, x2: W - m.r, y1: y(0), y2: y(0), stroke: "var(--axis)", "stroke-width": 1 }, svg);
}

function rangeChart(host, rows, current, fmt) {
  host.innerHTML = "";
  const W = Math.max(300, host.clientWidth || 600), rowH = 34, m = { l: W < 480 ? 96 : 150, r: 18, t: 22, b: 22 };
  const H = m.t + m.b + rows.length * rowH;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "valuation range chart" }, host);
  const vals = rows.flatMap((r) => [r.lo, r.hi, r.mid]).concat([current]).filter(isNum);
  let lo = Math.min(...vals), hi = Math.max(...vals); const pad = (hi - lo) * 0.06 || 1; lo = Math.max(0, lo - pad); hi += pad;
  const x = (v) => m.l + ((v - lo) / (hi - lo)) * (W - m.l - m.r);
  for (const t of niceTicks(lo, hi, W < 480 ? 3 : 5)) {
    el("line", { x1: x(t), x2: x(t), y1: m.t, y2: H - m.b, stroke: "var(--grid)" }, svg);
    const tx = el("text", { x: x(t), y: H - 6, "text-anchor": "middle", "font-size": 11, fill: "var(--muted)" }, svg);
    tx.textContent = fmt(t);
  }
  rows.forEach((r, i) => {
    const cy = m.t + rowH * i + rowH / 2;
    const lab = el("text", { x: m.l - 10, y: cy + 4, "text-anchor": "end", "font-size": 12, fill: "var(--ink-2)" }, svg);
    lab.textContent = r.label;
    if (!isNum(r.lo) || !isNum(r.hi)) return;
    const a = x(Math.min(r.lo, r.hi)), b = x(Math.max(r.lo, r.hi));
    el("rect", { x: a, y: cy - 7, width: Math.max(3, b - a), height: 14, rx: 4, fill: r.color || "var(--s1)", opacity: r.soft ? 0.35 : 0.85 }, svg);
    if (isNum(r.mid)) el("rect", { x: x(r.mid) - 1.5, y: cy - 10, width: 3, height: 20, rx: 1.5, fill: "var(--ink)" }, svg);
    const hit = el("rect", { x: m.l, y: cy - rowH / 2, width: W - m.l - m.r, height: rowH, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (ev) => showTip(`<b>${esc(r.label)}</b><br>${fmt(Math.min(r.lo, r.hi))} – ${fmt(Math.max(r.lo, r.hi))}${isNum(r.mid) ? `<br>${esc(r.midLabel || "Base")}: <b>${fmt(r.mid)}</b>` : ""}${r.note ? `<br><span class="muted">${esc(r.note)}</span>` : ""}`, ev.clientX, ev.clientY));
    hit.addEventListener("mouseleave", hideTip);
  });
  if (isNum(current)) {
    el("line", { x1: x(current), x2: x(current), y1: m.t - 6, y2: H - m.b, stroke: "var(--critical)", "stroke-width": 1.5 }, svg);
    const t = el("text", { x: x(current), y: m.t - 9, "text-anchor": "middle", "font-size": 11, "font-weight": 600, fill: "var(--ink)" }, svg);
    t.textContent = `Price ${fmt(current)}`;
  }
}

// ---------------------------------------------------------------------------------------------
// shared table
// ---------------------------------------------------------------------------------------------
function rankedTable(rows, { showRank = true, starred = true } = {}) {
  if (!rows.length) return `<div class="empty">No companies to show.</div>`;
  const body = rows.map((r) => `
    <tr>
      ${starred ? `<td><button class="star ${watch.has(r.ticker) ? "on" : ""}" data-star="${esc(r.ticker)}" aria-label="Watch ${esc(r.ticker)}">★</button></td>` : ""}
      ${showRank ? `<td class="n">${r.rank ?? ""}</td>` : ""}
      <td><a class="co" href="#/stock/${encodeURIComponent(r.ticker)}"><b>${esc(r.name)}</b><span>${esc(r.ticker)} · ${esc(r.segment || "")}</span></a></td>
      <td class="n"><div class="two">${usd(r.price)}<span class="${cls(r.return_1d)}">${pct(r.return_1d, 1, true)} 1d</span></div></td>
      <td class="n">${big(r.market_cap)}</td>
      <td class="n"><div class="two">${pct(r.revenue_growth_ttm)}<span>fwd ${pct(r.consensus_growth_ntm)}</span></div></td>
      <td class="n"><div class="two">${mult(r.ev_sales_ttm)} EV/S<span>IV ${usd(r.intrinsic)} (${pct(r.margin_of_safety, 0, true)})</span></div></td>
      <td class="n"><div class="two">${usd(r.target_12m)}<span>${usd(r.target_24m)} 24m</span></div></td>
      <td class="n"><div class="two"><span class="${cls(r.return_12m)}" style="font-size:13px">${pct(r.return_12m, 0, true)}</span><span>${pct(r.return_24m, 0, true)} 24m</span></div></td>
      <td class="n"><div class="two"><span class="pos" style="font-size:13px">${pct(r.bull_return_24m, 0, true)}</span><span class="neg">${pct(r.bear_return_24m, 0, true)}</span></div></td>
      <td>${scoreBar(r.score)}</td>
      <td><div class="two">${tierBadge(r.tier)}<span>${esc(r.action)}</span></div></td>
      <td>${sparkline(r.spark)}</td>
    </tr>`).join("");
  return `<div class="table-wrap"><table>
    <thead><tr>${starred ? "<th></th>" : ""}${showRank ? '<th class="n">#</th>' : ""}<th>Company</th><th class="n">Price</th><th class="n">Mkt cap</th>
    <th class="n">Rev growth</th><th class="n">Valuation</th><th class="n">Target 12m</th><th class="n">Upside</th>
    <th class="n">Bull / bear 24m</th><th>Score</th><th>Action</th><th>6 months</th></tr></thead>
    <tbody>${body}</tbody></table></div>`;
}
function bindStars(root, rerender) {
  $$("[data-star]", root).forEach((b) => b.addEventListener("click", (e) => {
    e.preventDefault(); watch.toggle(b.dataset.star); b.classList.toggle("on"); if (rerender) rerender();
  }));
}

// ---------------------------------------------------------------------------------------------
// pages
// ---------------------------------------------------------------------------------------------
async function pageHome(app) {
  const L = await getJSON("latest.json");
  if (!L) { app.innerHTML = `<div class="empty"><h2>No report yet</h2><p>The first nightly run has not published data. Trigger the workflow in GitHub Actions → “Daily scan” → Run workflow.</p></div>`; return; }
  const c = L.counts;
  const showAll = store.get("scanner-showall", false);
  const rows = showAll ? L.rows : L.rows.filter((r) => r.tier !== "Avoid" && r.tier !== "Insufficient data");
  const market = L.market.map((m) => `<div><div class="t">${esc(m.name)}</div><div class="p num">${m.close.toFixed(2)} <span class="${cls(m.return_1d)} small">${pct(m.return_1d, 1, true)}</span></div><div class="small muted num">1m ${pct(m.return_1m, 1, true)} · YTD ${pct(m.return_ytd, 1, true)}</div></div>`).join("");
  const er = L.tech_developments.earnings_releases_last_3_days || [];
  const ch = L.changes || {};
  const changeBits = ch.first_report ? `<p class="sub">First report — changes will appear from the next trading day.</p>` : `
    <ul class="list">
      ${[["new_attractive", "Newly attractive"], ["upgrades", "Upgraded"], ["downgrades", "Downgraded (thesis weakened)"], ["removed", "Dropped out"], ["score_moves", "Score moved ≥5"]]
        .filter(([k]) => (ch[k] || []).length).map(([k, label]) => `<li><b>${label}:</b> ${(ch[k]).map((x) => `<a href="#/stock/${esc(x.ticker)}">${esc(x.ticker)}</a>${x.from !== undefined && x.to !== undefined ? ` <span class="muted small">(${isNum(x.from) ? x.from.toFixed(0) : esc(x.from)} → ${isNum(x.to) ? x.to.toFixed(0) : esc(x.to)})</span>` : ""}`).join(", ")}</li>`).join("") || "<li class='muted'>No material changes.</li>"}
      ${(ch.unchanged_attractive || []).length ? `<li><b>Still attractive, unchanged:</b> ${ch.unchanged_attractive.map((t) => esc(t)).join(", ")}</li>` : ""}
    </ul><p class="small muted">Compared with ${esc(ch.previous_date)}.</p>`;
  const tops = (L.top || []).map((t) => {
    const n = t.narrative || {};
    return `<div class="card">
      <div class="section-head"><div><div class="eyebrow">${esc(t.ticker)}</div><h2>${esc(t.name)}</h2></div><div>${tierBadge(t.tier)} <span class="sub small">score ${t.score.toFixed(0)}</span></div></div>
      <div class="prose"><p><b>Thesis.</b> ${esc(n.thesis)}</p><p><b>Why it may be mispriced.</b> ${esc(n.why_undervalued)}</p><p><b>Path to 2x.</b> ${esc(n.path_to_double)}</p></div>
      <a href="#/stock/${encodeURIComponent(t.ticker)}">Full analysis →</a></div>`;
  }).join("");
  app.innerHTML = `
    <div class="hero">
      <div class="card">
        <div class="eyebrow">Daily report · ${esc(L.date)}</div>
        <div class="headline">${esc(L.headline)}</div>
        <div class="sub small">Generated ${esc(L.generated_at.slice(0, 16).replace("T", " "))} ET · prices as of close ${esc(L.data_snapshot.prices_as_of_close)}</div>
        <div class="funnel">
          <div><div class="v num">${c.universe}</div><div class="l">Companies in universe</div></div>
          <div><div class="v num">${c.with_usable_data}</div><div class="l">With current data</div></div>
          <div><div class="v num">${c.passed_initial_screen}</div><div class="l">Met initial criteria</div></div>
          <div><div class="v num">${c.deep_analysed}</div><div class="l">Fully valued</div></div>
          <div><div class="v num">${c.high_quality}</div><div class="l">Tier 1 + Tier 2</div></div>
        </div>
        <div class="small muted" style="margin-top:8px">Tier 1: ${c.tier1} · Tier 2: ${c.tier2} · Tier 3 (watchlist): ${c.tier3} · Avoid: ${c.avoid}</div>
        ${L.concentration?.warning ? `<div class="callout warn small">${esc(L.concentration.warning)}</div>` : ""}
      </div>
      <div class="card">
        <div class="eyebrow">Market</div>
        <div class="market" style="margin-top:8px">${market}</div>
        <div class="small muted" style="margin-top:10px">${er.length ? `Earnings releases filed (last 3 days): ${er.map((e) => `<a href="#/stock/${esc(e.ticker)}">${esc(e.ticker)}</a>`).join(", ")}` : "No earnings releases filed by universe companies in the last 3 days."}</div>
      </div>
    </div>
    <div class="section">
      <div class="section-head"><h2>Ranked candidates</h2>
        <label class="small sub"><input type="checkbox" id="showall" ${showAll ? "checked" : ""}> Include Avoid</label></div>
      ${rankedTable(rows)}
      <p class="small muted">Targets are base-case model estimates; upside shows 12-month (24-month beneath). Bull/Bear are 24-month scenario returns. Hover rows in the stock pages for detail.</p>
    </div>
    <div class="section"><div class="section-head"><h2>Top opportunities</h2></div>
      ${tops || `<div class="card empty">No Tier 1 or Tier 2 candidates today. Nothing meets the quality, valuation and risk/reward bar — that is an acceptable outcome.</div>`}
    </div>
    <div class="section grid g2">
      <div class="card"><h2>Changes since last report</h2>${changeBits}</div>
      <div class="card"><h2>Alerts</h2>${(L.alerts || []).length ? `<ul class="list">${L.alerts.map((a) => `<li><span class="chip">${esc(a.kind.replace(/_/g, " "))}</span> ${esc(a.message)}</li>`).join("")}</ul>` : `<p class="sub">No alerts today.</p>`}<a href="#/alerts" class="small">How alerts work →</a></div>
    </div>`;
  $("#showall").addEventListener("change", (e) => { store.set("scanner-showall", e.target.checked); route(); });
  bindStars(app);
}

const FILTERS0 = { mcapMin: 0, mcapMax: 0, segment: "", growth: "", opm: "", fcf: "", evs: "", pfcf: "", upside: "", downside: "", score: "", earn: "", tier: "" };
async function pageScreener(app) {
  const S = await getJSON("screener.json");
  if (!S) { app.innerHTML = `<div class="empty">No screener data yet.</div>`; return; }
  const f = Object.assign({}, FILTERS0, store.get("scanner-filters", {}));
  let sortKey = store.get("scanner-sort", "score"), sortDir = store.get("scanner-sortdir", -1);
  const segs = [...new Set(S.rows.map((r) => r.segment).filter(Boolean))].sort();
  const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
  const cols = [
    ["name", "Company"], ["market_cap", "Mkt cap"], ["revenue_growth_ttm", "Growth"], ["operating_margin", "Op margin"],
    ["fcf_margin", "FCF margin"], ["ev_sales_ttm", "EV/S"], ["p_fcf_ttm", "P/FCF"], ["expected_return_24m", "Exp. 24m"],
    ["bear_return_24m", "Bear 24m"], ["score", "Score"], ["tier", "Tier"], ["earnings_in_days", "Earnings"]];
  function apply() {
    const out = S.rows.filter((r) => {
      if (f.mcapMin && !(r.market_cap >= f.mcapMin)) return false;
      if (f.mcapMax && !(r.market_cap <= f.mcapMax)) return false;
      if (f.segment && r.segment !== f.segment) return false;
      if (num(f.growth) !== null && !(r.revenue_growth_ttm >= num(f.growth) / 100)) return false;
      if (num(f.opm) !== null && !(r.operating_margin >= num(f.opm) / 100)) return false;
      if (num(f.fcf) !== null && !(r.fcf_margin >= num(f.fcf) / 100)) return false;
      if (num(f.evs) !== null && !(r.ev_sales_ttm <= num(f.evs))) return false;
      if (num(f.pfcf) !== null && !(isNum(r.p_fcf_ttm) && r.p_fcf_ttm <= num(f.pfcf))) return false;
      if (num(f.upside) !== null && !(r.expected_return_24m >= num(f.upside) / 100)) return false;
      if (num(f.downside) !== null && !(r.bear_return_24m >= -num(f.downside) / 100)) return false;
      if (num(f.score) !== null && !(r.score >= num(f.score))) return false;
      if (num(f.earn) !== null && !(isNum(r.earnings_in_days) && r.earnings_in_days <= num(f.earn))) return false;
      if (f.tier && r.tier !== f.tier) return false;
      return true;
    });
    out.sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      if (typeof x === "string" || typeof y === "string") return sortDir * String(x ?? "").localeCompare(String(y ?? ""));
      return sortDir * ((isNum(x) ? x : -Infinity) - (isNum(y) ? y : -Infinity));
    });
    $("#count").textContent = `${out.length} of ${S.rows.length} companies`;
    $("#sbody").innerHTML = out.map((r) => `<tr>
      <td><button class="star ${watch.has(r.ticker) ? "on" : ""}" data-star="${esc(r.ticker)}" aria-label="Watch ${esc(r.ticker)}">★</button></td>
      <td><a class="co" href="#/stock/${encodeURIComponent(r.ticker)}"><b>${esc(r.name)}</b><span>${esc(r.ticker)} · ${esc(r.segment || "")}</span></a></td>
      <td class="n">${big(r.market_cap)}</td><td class="n">${pct(r.revenue_growth_ttm)}</td><td class="n">${pct(r.operating_margin)}</td>
      <td class="n">${pct(r.fcf_margin)}</td><td class="n">${mult(r.ev_sales_ttm)}</td><td class="n">${mult(r.p_fcf_ttm)}</td>
      <td class="n ${cls(r.expected_return_24m)}">${pct(r.expected_return_24m, 0, true)}</td><td class="n neg">${pct(r.bear_return_24m, 0, true)}</td>
      <td>${scoreBar(r.score)}</td><td>${tierBadge(r.tier)}</td>
      <td class="n">${isNum(r.earnings_in_days) ? `${r.earnings_in_days}d` : "–"}</td></tr>`).join("") || `<tr><td colspan="13" class="empty">No companies match these filters.</td></tr>`;
    bindStars($("#sbody"));
  }
  const inp = (k, label, ph) => `<label>${label}<input type="number" inputmode="decimal" data-f="${k}" value="${esc(f[k])}" placeholder="${ph}"></label>`;
  const capOpt = (v, l) => `<option value="${v}">${l}</option>`;
  app.innerHTML = `
    <div class="section-head"><div><h1>Stock screener</h1><p class="sub">Every company that passed today's initial screen. “Not shortlisted” rows were valued without analyst estimates.</p></div></div>
    <div class="card"><div class="filters">
      <label>Min market cap<select data-f="mcapMin">${capOpt(0, "Any")}${capOpt(2e9, "$2B")}${capOpt(1e10, "$10B")}${capOpt(5e10, "$50B")}${capOpt(2e11, "$200B")}</select></label>
      <label>Max market cap<select data-f="mcapMax">${capOpt(0, "Any")}${capOpt(1e10, "$10B")}${capOpt(5e10, "$50B")}${capOpt(2e11, "$200B")}</select></label>
      <label>Sector / industry<select data-f="segment"><option value="">All</option>${segs.map((s) => `<option>${esc(s)}</option>`).join("")}</select></label>
      ${inp("growth", "Min revenue growth %", "e.g. 20")}${inp("opm", "Min operating margin %", "e.g. 10")}${inp("fcf", "Min FCF margin %", "e.g. 5")}
      ${inp("evs", "Max EV/Sales", "e.g. 15")}${inp("pfcf", "Max P/FCF", "e.g. 40")}${inp("upside", "Min expected 24m upside %", "e.g. 30")}
      ${inp("downside", "Max bear-case downside %", "e.g. 40")}${inp("score", "Min score", "e.g. 60")}${inp("earn", "Earnings within (days)", "e.g. 30")}
      <label>Tier<select data-f="tier"><option value="">All</option>${["Tier 1", "Tier 2", "Tier 3", "Avoid", "Not shortlisted"].map((t) => `<option>${t}</option>`).join("")}</select></label>
      <label>&nbsp;<button class="btn" id="reset">Reset filters</button></label>
    </div></div>
    <div class="section-head" style="margin-top:16px"><span class="sub small" id="count"></span></div>
    <div class="table-wrap"><table><thead><tr><th></th>${cols.map(([k, l]) => `<th class="sortable ${k === "name" || k === "tier" ? "" : "n"}" data-k="${k}">${l}${k === sortKey ? (sortDir > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead><tbody id="sbody"></tbody></table></div>`;
  $$("[data-f]", app).forEach((e) => {
    if (e.tagName === "SELECT") e.value = String(f[e.dataset.f] ?? "");
    e.addEventListener(e.tagName === "SELECT" ? "change" : "input", () => {
      f[e.dataset.f] = e.tagName === "SELECT" && e.dataset.f.startsWith("mcap") ? Number(e.value) : e.value;
      store.set("scanner-filters", f); apply();
    });
  });
  $("#reset").addEventListener("click", () => { store.set("scanner-filters", FILTERS0); route(); });
  $$("th[data-k]", app).forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.k; sortDir = k === sortKey ? -sortDir : (k === "name" || k === "tier" ? 1 : -1); sortKey = k;
    store.set("scanner-sort", sortKey); store.set("scanner-sortdir", sortDir); route();
  }));
  apply();
}

function kv(pairs) {
  return `<dl class="kv">${pairs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

async function pageStock(app, t) {
  const [d, S, L] = await Promise.all([getJSON(`stocks/${t}.json`), getJSON("screener.json"), getJSON("latest.json")]);
  const row = (S?.rows || []).find((r) => r.ticker === t);
  if (!d) {
    app.innerHTML = row ? `<div class="card"><h1>${esc(row.name)} (${esc(t)})</h1><p class="sub">This company passed the initial screen but was not in today's shortlist for full analysis (analyst estimates were not fetched). Preliminary score ${isNum(row.score) ? row.score.toFixed(0) : "–"}.</p><a href="#/screener">← Screener</a></div>`
      : `<div class="empty"><h2>${esc(t)} is not in today's analysis</h2><p>It may have failed the initial screen or lack current data.</p><a href="#/screener">← Screener</a></div>`;
    return;
  }
  const m = d.metrics, v = d.val, s = d.score, rk = d.risk, n = d.narrative || {};
  const sc = v.scenarios || {}, cs = sc.cases || {}, iv = v.intrinsic || {}, en = v.entry || {}, p2 = v.path_to_double || {};
  const demo = (L?.data_snapshot?.sources || "").includes("SYNTHETIC");
  const fx = (x) => usd(x);
  const flags = rk.flags || {};
  const comp = s.components;
  const compNames = { valuation: "Valuation & margin of safety", growth: "Revenue growth & durability", quality: "Business quality (proxies)", financial_strength: "Financial strength & cash", upside: "12–24-month upside", catalysts: "Catalysts & expectations" };
  const srcLinks = [];
  if (!demo) {
    srcLinks.push(`<li><a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${m.cik}&type=10-&dateb=&owner=include&count=40" target="_blank" rel="noopener">SEC EDGAR filings for ${esc(m.name)}</a> <span class="muted small">(fundamentals; latest period ${esc(m.period_end)})</span></li>`);
    (d.cats || []).filter((c) => c.url).forEach((c) => srcLinks.push(`<li><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a> <span class="muted small">filed ${esc(c.date)}</span></li>`));
    srcLinks.push(`<li><a href="https://finance.yahoo.com/quote/${esc(t.replace(".", "-"))}" target="_blank" rel="noopener">Yahoo Finance quote</a> <span class="muted small">(price ${esc(m.price_date)}; consensus snapshot ${esc(d.date)})</span></li>`);
  }
  const dcf = v.dcf || {}, A = dcf.assumptions || {};
  const comps = v.comps;
  app.innerHTML = `
    <a href="#/" class="small">← Opportunities</a>
    <div class="card" style="margin-top:10px">
      <div class="stock-head">
        <div>
          <div class="eyebrow">${esc(t)} · ${esc(m.segment || "")}</div>
          <h1>${esc(m.name)}</h1>
          <div style="margin-top:8px">${tierBadge(s.tier)} <span class="sub">${esc(s.action)}</span> <button class="star ${watch.has(t) ? "on" : ""}" data-star="${esc(t)}" aria-label="Watch">★</button></div>
        </div>
        <div style="text-align:right">
          <div class="price-big num">${usd(m.price)}</div>
          <div class="small sub">close ${esc(m.price_date)} · <span class="${cls(m.return_1d)}">${pct(m.return_1d, 1, true)}</span> 1d · mkt cap ${big(m.market_cap)}</div>
        </div>
      </div>
      <div class="grid g4" style="margin-top:16px">
        <div class="stat"><span class="l">Score</span><span class="v num">${s.total.toFixed(0)}<span class="muted" style="font-size:14px">/100</span></span></div>
        <div class="stat"><span class="l">Intrinsic value (blend)</span><span class="v num">${usd(iv.per_share)}</span><span class="small ${cls(iv.margin_of_safety)}">${pct(iv.margin_of_safety, 0, true)} vs price</span></div>
        <div class="stat"><span class="l">Expected return (prob.-weighted)</span><span class="v num ${cls(sc.expected_return_24m)}">${pct(sc.expected_return_24m, 0, true)}</span><span class="small muted">24m · ${pct(sc.expected_return_12m, 0, true)} 12m</span></div>
        <div class="stat"><span class="l">Entry range (model)</span><span class="v num" style="font-size:18px">${usd(en.low)}–${usd(en.high)}</span><span class="small muted">${esc(en.status || "n/a")}</span></div>
      </div>
      <div style="margin-top:12px">${flagChips(null, flags)}</div>
      ${s.caps_applied?.length ? `<div class="callout warn small">Score ${esc(s.caps_applied.join("; "))} — raw ${s.raw}.</div>` : ""}
    </div>

    <div class="section grid g2">
      <div class="card prose"><h2 style="margin-bottom:10px">Investment thesis</h2>
        <p>${esc(n.thesis)}</p><p><b>Why it may be undervalued.</b> ${esc(n.why_undervalued)}</p><p><b>Fundamentals.</b> ${esc(n.fundamentals)}</p>
        <p><b>Verdict.</b> ${esc(n.verdict)}</p><p class="small muted">Text: ${esc(n.generated_by || "")}. Every figure comes from the calculations below.</p></div>
      <div class="card"><h2>Valuation range</h2><p class="small sub">Bars span bear–bull; the black tick is the base case. Red line = current price.</p>
        <div class="chart" id="c-range"></div></div>
    </div>

    <div class="section card"><div class="section-head"><h2>Scenarios (12 and 24 months)</h2><span class="small muted">Exit metric: ${esc(sc.metric || "–")} · today ${mult(sc.current_multiple)} · dilution ${pct(sc.annual_dilution, 1)}/yr</span></div>
      <div class="scen">${["bear", "base", "bull"].map((k) => { const x = cs[k] || {}; return `<div>
        <div class="eyebrow">${k} · p=${pct((sc.probabilities || {})[k])}</div>
        <div class="v num">${usd(x.price_24m)}</div><div class="small ${cls(x.return_24m)}">${pct(x.return_24m, 0, true)} in 24m · ${usd(x.price_12m)} (${pct(x.return_12m, 0, true)}) 12m</div>
        <div class="small muted" style="margin-top:6px">Growth ${pct(x.initial_growth)} fading · op margin → ${pct(x.target_margin)} · exit ${mult(x.exit_multiple_24m)}</div></div>`; }).join("")}</div>
      <p class="small sub" style="margin-top:10px">Upside/downside ratio ${mult(sc.up_down_ratio_24m)} (24m). Base-case exit multiple moves only halfway from today's multiple toward the growth-justified level; peak multiples are never assumed.${sc.optimistic_only ? " <b>Attractive only in the bull case.</b>" : ""}</p>
    </div>

    <div class="section grid g2">
      <div class="card"><h2>Path to a potential double</h2>
        <p class="sub">${esc(n.path_to_double)}</p>
        ${kv([["Verdict", `<b>${esc(p2.verdict || "–")}</b>`], ["2x price (24m)", usd(p2.target_price_24m)], ["Required initial growth", pct(p2.required_initial_growth)], ["Base-case growth", pct(p2.base_initial_growth)], ["Required exit multiple", mult(p2.required_exit_multiple)], ["Growth-justified multiple", mult(p2.justified_multiple)],
          ["From fundamentals", mult(p2.decomposition?.fundamental_growth_factor)], ["From multiple change", mult(p2.decomposition?.multiple_change_factor)], ["Dilution drag", mult(p2.decomposition?.dilution_factor)]])}</div>
      <div class="card"><h2>Price, entry range & intrinsic value</h2><div class="chart" id="c-price"></div>
        <p class="small muted">Weekly closes, 3 years. Shaded band = model entry range; dashed = blended intrinsic value.</p></div>
    </div>

    <div class="section grid g2">
      <div class="card"><h2>Quarterly revenue</h2><div class="chart" id="c-rev"></div><p class="small muted">From 10-Q/10-K XBRL. Lighter bars were derived (e.g. Q4 = fiscal year − nine months).</p></div>
      <div class="card"><h2>Year-over-year revenue growth</h2><div class="chart" id="c-yoy"></div></div>
    </div>

    <div class="section grid g3">
      <div class="card"><h2>Growth</h2>${kv([["TTM revenue", big(m.revenue_ttm)], ["TTM growth", pct(m.revenue_growth_ttm, 1)], ["Prior-year TTM growth", pct(m.revenue_growth_prior_ttm, 1)], ["3-yr CAGR", pct(m.revenue_cagr_3y, 1)], ["5-yr CAGR", pct(m.revenue_cagr_5y, 1)], ["Acceleration (2Q)", pct(m.growth_acceleration, 1, true)], ["Consensus NTM growth", pct(m.consensus_growth_ntm, 1)], ["Analysts", m.consensus_analysts ?? "–"], ["RPO / backlog", big(m.rpo)], ["RPO growth", pct(m.rpo_growth, 1)], ["Acquisitions (2y, % rev)", pct(m.acquisitions_2y_pct_revenue)]])}</div>
      <div class="card"><h2>Profitability & cash</h2>${kv([["Gross margin", pct(m.gross_margin, 1)], ["Operating margin (GAAP)", pct(m.operating_margin, 1)], ["Prior-year op margin", pct(m.operating_margin_prior, 1)], ["Net margin", pct(m.net_margin, 1)], ["FCF (TTM)", big(m.fcf_ttm)], ["FCF margin", pct(m.fcf_margin, 1)], ["FCF after SBC", big(m.fcf_after_sbc)], ["SBC / revenue", pct(m.sbc_pct, 1)], ["R&D / revenue", pct(m.rnd_pct, 1)], ["Capex / revenue", pct(m.capex_pct, 1)], ["ROIC", pct(m.roic, 1)]])}</div>
      <div class="card"><h2>Balance sheet & market</h2>${kv([["Cash & investments", big(m.cash_and_investments)], ["Debt", big(m.debt)], ["Net cash", big(m.net_cash)], ["Net debt / EBITDA", mult(m.net_debt_to_ebitda)], ["Current ratio", mult(m.current_ratio)], ["Share growth YoY", pct(m.share_growth_yoy, 1, true)], ["EV", big(m.enterprise_value)], ["EV/Sales · EV/EBIT", `${mult(m.ev_sales_ttm)} · ${mult(m.ev_ebit_ttm)}`], ["P/E fwd · PEG", `${mult(m.pe_forward)} · ${isNum(m.peg) ? m.peg.toFixed(2) : "–"}`], ["P/FCF", mult(m.p_fcf_ttm)], ["Beta (2y weekly)", isNum(m.beta) ? m.beta.toFixed(2) : "–"], ["Short % float", pct(m.short_pct_float, 1)], ["Institutional %", pct(m.institutional_pct)]])}</div>
    </div>

    <div class="section grid g2">
      <div class="card"><h2>Discounted cash flow</h2>
        ${kv([["DCF value / share", `<b>${usd(dcf.per_share)}</b>`], ["DCF bear · bull", `${usd(v.dcf_scenarios?.bear)} · ${usd(v.dcf_scenarios?.bull)}`], ["WACC", `${pct(v.wacc?.wacc, 1)} <span class="muted small">(β ${isNum(v.wacc?.beta_used) ? v.wacc.beta_used.toFixed(2) : "–"})</span>`], ["Initial growth", `${pct(A.initial_growth, 1)} <span class="muted small">${esc(v.growth_source)}</span>`], ["Terminal growth", pct(A.terminal_growth, 1)], ["Op margin path", `${pct(A.start_margin, 1)} → ${pct(A.target_margin, 1)}`], ["Margin ceiling (gross × k)", pct(v.margins?.ceiling, 1)], ["Tax rate", pct(A.tax_rate)], ["Sales-to-capital", mult(A.sales_to_capital)], ["Terminal value share of EV", pct(dcf.terminal_share_of_ev)], ["Price implies initial growth", `<b>${pct(v.reverse_dcf?.implied_growth, 1)}</b>`]])}
        <p class="small muted">${esc(v.reverse_dcf?.note || "")}${iv.dcf_low_confidence ? " DCF down-weighted: company is currently unprofitable." : ""}${v.margins?.cyclical_bear ? " Cyclical: bear case revisits the 5-year trough margin." : ""}</p>
        <details><summary>Year-by-year projection</summary><div class="table-wrap" style="margin-top:8px"><table><thead><tr><th class="n">Yr</th><th class="n">Revenue</th><th class="n">Growth</th><th class="n">Op margin</th><th class="n">NOPAT</th><th class="n">Reinvest</th><th class="n">FCFF</th><th class="n">PV</th></tr></thead><tbody>
        ${(dcf.years || []).map((y) => `<tr><td class="n">${y.year}</td><td class="n">${big(y.revenue)}</td><td class="n">${pct(y.growth, 1)}</td><td class="n">${pct(y.op_margin, 1)}</td><td class="n">${big(y.nopat)}</td><td class="n">${big(y.reinvestment)}</td><td class="n">${big(y.fcff)}</td><td class="n">${big(y.pv)}</td></tr>`).join("")}</tbody></table></div></details>
      </div>
      <div class="card"><h2>Comparable companies</h2>
        ${comps ? `${kv(Object.entries(comps.methods).map(([k, x]) => [k.replace(/_/g, " "), `${usd(x.per_share)} <span class="muted small">at ${mult(x.implied_multiple)}</span>`]).concat([["Comps value / share", `<b>${usd(comps.per_share)}</b>`], ["Peer group", `${esc(comps.peer_group)} (${comps.peer_count})`]]))}
        <p class="small muted">${esc(comps.note)}</p>
        <div class="table-wrap"><table><thead><tr><th>Peer</th><th class="n">EV/NTM S</th><th class="n">EV/NTM EBIT</th><th class="n">Growth</th><th class="n">Op margin</th></tr></thead><tbody>
        ${comps.peers.map((p) => `<tr><td><a href="#/stock/${esc(p.ticker)}">${esc(p.ticker)}</a></td><td class="n">${mult(p.ev_sales_ntm)}</td><td class="n">${mult(p.ev_ebit_ntm)}</td><td class="n">${pct(p.growth)}</td><td class="n">${pct(p.op_margin)}</td></tr>`).join("")}</tbody></table></div>`
        : `<p class="sub">Not enough comparable companies with usable data.</p>`}
      </div>
    </div>

    <div class="section grid g2">
      <div class="card"><h2>Own valuation history (EV / TTM sales)</h2><div class="chart" id="c-hist"></div>
        <p class="small muted">${isNum(m.ev_sales_hist_percentile) ? `Today sits at the ${pct(m.ev_sales_hist_percentile)} percentile of its 5-year range. A low percentile is not by itself evidence of undervaluation.` : "Not enough history for a percentile."}</p></div>
      <div class="card"><h2>Score breakdown</h2>
        ${Object.entries(comp).map(([k, c]) => `<div style="margin:10px 0"><div style="display:flex;justify-content:space-between" class="small"><span>${esc(compNames[k] || k)}</span><span class="num"><b>${c.points.toFixed(1)}</b> / ${c.max}</span></div>
          <div style="height:6px;border-radius:3px;background:var(--grid);margin-top:4px"><div style="height:6px;border-radius:3px;background:var(--s1);width:${(c.points / c.max) * 100}%"></div></div>
          <details><summary class="small">Inputs</summary><table style="margin-top:6px"><tbody>${c.details.map((x) => `<tr><td class="small">${esc(x.factor)}</td><td class="n small">${esc(x.value)}</td><td class="n small">${x.score === null ? "penalty" : (x.missing ? "missing (30%)" : (x.score * 100).toFixed(0) + "%")}</td></tr>`).join("")}</tbody></table></details></div>`).join("")}
        <p class="small muted">Tier logic: ${esc((s.tier_reasons || []).join(" · "))}</p></div>
    </div>

    <div class="section grid g2">
      <div class="card"><h2>Catalysts</h2><ul class="list">${(d.cats || []).map((c) => `<li><span class="chip ${c.type === "confirmed" ? "confirmed" : ""}">${esc(c.type)}</span> <b>${esc(c.title)}</b>${c.url ? ` · <a href="${esc(c.url)}" target="_blank" rel="noopener">filing</a>` : ""}<div class="small sub">${esc(c.mechanism)}</div>${c.priced_in ? `<div class="small muted">Priced in? ${esc(c.priced_in)}</div>` : ""}</li>`).join("") || "<li class='muted'>No verifiable catalysts found.</li>"}</ul>
        ${d.ai_catalysts?.catalysts?.length ? `<h3 style="margin-top:12px">From the latest earnings release <span class="chip">AI-extracted · verify</span></h3><ul class="list">${d.ai_catalysts.catalysts.map((a) => `<li><b>${esc(a.title)}</b> <span class="muted small">(${esc(a.classification)})</span><div class="small sub">“${esc(a.quote)}”</div></li>`).join("")}</ul><a class="small" href="${esc(d.ai_catalysts.source_url)}" target="_blank" rel="noopener">Source filing (${esc(d.ai_catalysts.filed)})</a>` : ""}
        <p class="small muted">Types: confirmed = SEC filing; scheduled = dated event; analyst = consensus; reported = company's own numbers; model = this tool's hypothesis.</p></div>
      <div class="card"><h2>Key risks</h2><ul class="list">${(rk.top_risks || []).map((r) => `<li><b>${esc(r.title)}</b> <span class="muted small">${esc(r.basis)}</span><div class="small sub">${esc(r.detail)}</div></li>`).join("")}</ul>
        <p class="small sub">Adverse case: ${pct(rk.adverse_return_12m, 0, true)} in 12m, ${pct(rk.adverse_return_24m, 0, true)} in 24m.</p></div>
    </div>

    <div class="section grid g2">
      <div class="card"><h2>What to monitor</h2><ul class="list">${(n.what_to_monitor || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul><p class="small sub"><b>Invalidation.</b> ${esc(n.invalidation)}</p></div>
      <div class="card"><h2>Data quality & sources</h2>
        ${kv([["Latest period", esc(m.period_end)], ["TTM method", esc(m.ttm_method)], ["Balance sheet date", esc(m.balance_sheet_date)], ["Debt method", esc(m.debt_method)], ["Share count basis", `<span class="small">${esc(m.share_count_basis)}</span>`], ["Reporting currency", esc(m.reporting_currency)]])}
        ${(m.data_quality?.missing || []).length ? `<p class="small"><b>Missing:</b> ${esc(m.data_quality.missing.join(", "))}</p>` : ""}
        ${(m.data_quality?.notes || []).map((x) => `<p class="small sub">${esc(x)}</p>`).join("")}
        ${demo ? `<p class="small sub">Demo data — fictional company, no real sources.</p>` : `<ul class="list small">${srcLinks.join("")}</ul>`}
      </div>
    </div>`;
  bindStars(app);
  // charts
  const ranges = [
    { label: "DCF (bear–bull)", lo: v.dcf_scenarios?.bear, hi: v.dcf_scenarios?.bull, mid: dcf.per_share, color: "var(--s1)" },
    { label: "Comparables", lo: comps ? Math.min(...Object.values(comps.methods).map((x) => x.per_share)) : null, hi: comps ? Math.max(...Object.values(comps.methods).map((x) => x.per_share)) : null, mid: comps?.per_share, color: "var(--s3)", note: comps ? "range across comparable methods" : "" },
    { label: "Scenario 12m", lo: cs.bear?.price_12m, hi: cs.bull?.price_12m, mid: cs.base?.price_12m, color: "var(--s2)" },
    { label: "Scenario 24m", lo: cs.bear?.price_24m, hi: cs.bull?.price_24m, mid: cs.base?.price_24m, color: "var(--s2)" },
    { label: "Entry range", lo: en.low, hi: en.high, color: "var(--s1)", soft: true },
  ].filter((r) => isNum(r.lo) && isNum(r.hi));
  const draw = () => {
    const cr = $("#c-range"); if (cr) rangeChart(cr, ranges, m.price, (x) => usd(x, x >= 100 ? 0 : 2));
    const pw = m.price_weekly || [];
    if (pw.length > 2) lineChart($("#c-price"), { label: "price history", series: [{ name: "Close", pts: pw.map(([d, c]) => ({ x: d, y: c })) }], band: isNum(en.low) ? { y0: en.low, y1: en.high, label: "Entry" } : null, hlines: [{ y: iv.per_share, label: "IV", dash: true }], yFmt: (x) => usd(x, x >= 100 ? 0 : 2), xFmt: mon, tipX: (x) => x });
    const q = m.quarterly_revenue || [];
    if (q.length) {
      barChart($("#c-rev"), { cats: q.map((r) => mon(r.end)), vals: q.map((r) => r.revenue), fmt: big, name: "Revenue", dim: q.map((r) => r.derived), extra: (i) => (q[i].derived ? "<span class='muted'>derived from YTD/annual</span>" : "") + (isNum(q[i].yoy) ? `<br>YoY ${pct(q[i].yoy, 1, true)}` : "") });
      const qy = q.filter((r) => isNum(r.yoy));
      if (qy.length > 1) lineChart($("#c-yoy"), { label: "yoy growth", series: [{ name: "YoY growth", pts: qy.map((r) => ({ x: r.end, y: r.yoy })) }], yFmt: (x) => pct(x), xFmt: mon, tipX: (x) => `Quarter ending ${x}`, rightPad: 18, height: 220 });
    }
    const h = m.ev_sales_history || [];
    if (h.length > 2) lineChart($("#c-hist"), { label: "EV/Sales history", series: [{ name: "EV/Sales", pts: h.map((r) => ({ x: r.end, y: r.ev_sales })) }], hlines: [{ y: m.ev_sales_hist_median, label: "median", dash: true }, { y: m.ev_sales_ttm, label: "today", color: "var(--critical)" }], yFmt: (x) => mult(x), xFmt: mon, tipX: (x) => `Quarter ending ${x}` });
    else { const el2 = $("#c-hist"); if (el2) el2.innerHTML = `<div class="empty small">Not enough history.</div>`; }
  };
  draw();
  redraw = draw;
}

async function pageWatchlist(app) {
  const S = await getJSON("screener.json"), L = await getJSON("latest.json");
  const list = watch.list();
  const rows = (S?.rows || []).filter((r) => list.includes(r.ticker));
  const missing = list.filter((t) => !rows.find((r) => r.ticker === t));
  app.innerHTML = `
    <h1>Watchlist</h1>
    <p class="sub">Stored in this browser only. Star any company to add it. For e-mailed alerts, add tickers to <code>config/watchlist.txt</code> in the repository — the nightly job opens a GitHub issue when a watched stock enters its entry range, nears earnings, or its thesis changes.</p>
    <div class="card" style="margin:14px 0"><form id="addw" style="display:flex;gap:8px;flex-wrap:wrap"><input id="wt" placeholder="Ticker, e.g. NVDA" autocomplete="off" style="text-transform:uppercase"><button class="btn primary">Add</button></form></div>
    ${rows.length ? `<div class="table-wrap"><table><thead><tr><th></th><th>Company</th><th class="n">Price</th><th class="n">Intrinsic</th><th class="n">Entry range</th><th>Status</th><th class="n">Exp. 24m</th><th>Score</th><th>Tier</th><th class="n">Earnings</th><th class="n">1m</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td><button class="star on" data-star="${esc(r.ticker)}">★</button></td><td><a class="co" href="#/stock/${esc(r.ticker)}"><b>${esc(r.name)}</b><span>${esc(r.ticker)}</span></a></td>
      <td class="n">${usd(r.price)}</td><td class="n">${usd(r.intrinsic)}</td><td class="n">${usd(r.entry_low)}–${usd(r.entry_high)}</td>
      <td>${r.entry_status ? `<span class="badge ${r.entry_status === "above entry range" ? "tier-avoid" : "tier-2"}">${esc(r.entry_status)}</span>` : "–"}</td>
      <td class="n ${cls(r.expected_return_24m)}">${pct(r.expected_return_24m, 0, true)}</td><td>${scoreBar(r.score)}</td><td>${tierBadge(r.tier)}</td>
      <td class="n">${isNum(r.earnings_in_days) ? r.earnings_in_days + "d" : "–"}</td><td class="n ${cls(r.return_1m)}">${pct(r.return_1m, 1, true)}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="card empty">Your watchlist is empty.</div>`}
    ${missing.length ? `<p class="small muted" style="margin-top:10px">Not in today's screened set (failed screen or no data): ${missing.map((t) => `${esc(t)} <button class="star on" data-star="${esc(t)}" title="remove">★</button>`).join(" ")}</p>` : ""}
    ${L ? `<p class="small muted">Data as of ${esc(L.date)}.</p>` : ""}`;
  $("#addw").addEventListener("submit", (e) => { e.preventDefault(); const t = $("#wt").value.trim().toUpperCase(); if (t && !watch.has(t)) watch.toggle(t); route(); });
  bindStars(app, route);
}

async function pagePerformance(app) {
  const P = await getJSON("performance.json");
  if (!P) { app.innerHTML = `<div class="empty">No tracking data yet.</div>`; return; }
  const H = ["1m", "3m", "6m", "12m", "24m"];
  const tiers = Object.entries(P.by_tier || {});
  const anyH = tiers.some(([, s]) => H.some((h) => s.horizons?.[h]));
  app.innerHTML = `
    <h1>Track record</h1>
    <p class="sub" style="max-width:780px">${esc(P.basis)}</p>
    <div class="callout small">Recording since ${esc(P.first_record || "–")}. ${anyH ? "" : "No 1-month horizon has elapsed yet, so only returns-to-date are shown. Results become meaningful only after many months and market environments."} This page never shows backtested or hypothetical results.</div>
    <div class="section table-wrap"><table><thead><tr><th>Tier</th><th class="n">Recs</th><th class="n">Avg to date</th><th class="n">vs SPY</th><th class="n">% beat SPY</th>${H.map((h) => `<th class="n">${h} avg</th>`).join("")}</tr></thead><tbody>
      ${tiers.map(([t, s]) => { const td = s.horizons?.to_date || {}; return `<tr><td>${tierBadge(t)}</td><td class="n">${s.count}</td><td class="n ${cls(td.avg_return)}">${pct(td.avg_return, 1, true)}</td><td class="n ${cls(td.avg_excess_spy)}">${pct(td.avg_excess_spy, 1, true)}</td><td class="n">${pct(td.pct_beating_spy)}</td>${H.map((h) => { const x = s.horizons?.[h]; return `<td class="n ${cls(x?.avg_return)}">${x ? `${pct(x.avg_return, 1, true)} <span class="muted small">n=${x.n}</span>` : "–"}</td>`; }).join("")}</tr>`; }).join("")}
    </tbody></table></div>
    <div class="section"><h2 style="margin-bottom:10px">Recommendation events</h2><div class="table-wrap"><table><thead><tr><th>Date</th><th>Ticker</th><th>Tier</th><th class="n">Price then</th><th class="n">Return to date</th><th class="n">vs SPY</th><th class="n">Max drawdown</th></tr></thead><tbody>
      ${(P.events || []).slice().reverse().slice(0, 200).map((e) => `<tr><td>${esc(e.date)}</td><td><a href="#/stock/${esc(e.ticker)}">${esc(e.ticker)}</a></td><td>${tierBadge(e.tier)}</td><td class="n">${usd(e.entry_price)}</td><td class="n ${cls(e.return_to_date)}">${pct(e.return_to_date, 1, true)}</td><td class="n ${cls(e.excess_spy_to_date)}">${pct(e.excess_spy_to_date, 1, true)}</td><td class="n neg">${pct(e.max_drawdown, 1)}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">No events yet.</td></tr>`}
    </tbody></table></div></div>`;
}

async function pageAlerts(app) {
  const L = await getJSON("latest.json"), C = await getJSON("config.json");
  const al = L?.alerts || [];
  app.innerHTML = `
    <h1>Alerts</h1>
    <div class="grid g2 section" style="margin-top:14px">
      <div class="card"><h2>Today (${esc(L?.date || "–")})</h2>${al.length ? `<ul class="list">${al.map((a) => `<li><span class="chip">${esc(a.kind.replace(/_/g, " "))}</span> <a href="#/stock/${esc(a.ticker)}">${esc(a.ticker)}</a> — ${esc(a.message)}</li>`).join("")}</ul>` : `<p class="sub">No alerts fired today.</p>`}</div>
      <div class="card"><h2>Rules</h2><ul class="list small">
        <li><b>New Tier 1</b> — a stock newly meets every Tier 1 test.</li>
        <li><b>Entry</b> — a Tier 1/2 or watched stock falls into or below its model entry range.</li>
        <li><b>Earnings</b> — a Tier 1/2 or watched stock reports within ${esc(C?.alerts?.earnings_within_days ?? 7)} days.</li>
        <li><b>Deterioration</b> — a new high-severity flag: slowing growth, margin decline, price rising while fundamentals weaken, or balance-sheet stress.</li>
        <li><b>Valuation change</b> — the intrinsic value estimate moves ±20% in a day (a material assumption changed).</li>
        <li><b>Invalidation</b> — price breaks below the prior report's bear-case 24-month value.</li>
        <li><b>Downgrade</b> — a Tier 1/2 stock drops a tier.</li></ul>
        <p class="small sub">Delivery: ${C?.alerts?.github_issue ? "the nightly job opens a GitHub issue whenever alerts fire — GitHub e-mails you (watch the repository, or enable Issues notifications)." : "GitHub issue alerts are disabled in config."}</p></div>
    </div>`;
}

function mdToHtml(md) {
  const lines = md.split("\n"); let out = "", i = 0;
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/_(.+?)_/g, "<i>$1</i>").replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("|")) {
      const rows = []; while (i < lines.length && lines[i].startsWith("|")) { rows.push(lines[i]); i++; }
      const cells = (r) => r.split("|").slice(1, -1).map((c) => c.trim());
      out += `<div class="table-wrap"><table><thead><tr>${cells(rows[0]).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows.slice(2).map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      continue;
    }
    if (l.startsWith("- ") || l.startsWith("  - ")) {
      out += "<ul>"; while (i < lines.length && /^\s*- /.test(lines[i])) { out += `<li>${inline(lines[i].replace(/^\s*- /, ""))}</li>`; i++; } out += "</ul>"; continue;
    }
    if (l.startsWith("## ")) out += `<h2>${inline(l.slice(3))}</h2>`;
    else if (l.startsWith("# ")) out += `<h1>${inline(l.slice(2))}</h1>`;
    else if (l.startsWith("> ")) out += `<blockquote>${inline(l.slice(2))}</blockquote>`;
    else if (l.trim() === "---") out += "<hr>";
    else if (l.trim()) out += `<p>${inline(l)}</p>`;
    i++;
  }
  return out;
}

async function pageReports(app, date) {
  const idx = await getJSON("reports/index.json");
  const dates = idx?.dates || [];
  const cur = date || dates[0];
  const md = cur ? await getText(`reports/${cur}.md`) : null;
  app.innerHTML = `<div class="section-head"><h1>Daily reports</h1>
    <label class="small sub">Report <select id="rd">${dates.map((d) => `<option ${d === cur ? "selected" : ""}>${esc(d)}</option>`).join("")}</select></label></div>
    <div class="card md">${md ? mdToHtml(md) : `<div class="empty">No reports archived yet.</div>`}</div>`;
  const rd = $("#rd"); if (rd) rd.addEventListener("change", () => { location.hash = `#/reports/${rd.value}`; });
}

async function pageMethod(app) {
  const C = await getJSON("config.json"), L = await getJSON("latest.json");
  const w = C?.scoring?.weights || {}, t = C?.scoring?.tiers || {}, sc = C?.screen || {}, v = C?.valuation || {};
  app.innerHTML = `
    <h1>Method & settings</h1>
    <p class="sub" style="max-width:820px">Every number on this site is produced by deterministic code from public filings and market data; explanations are templated from those numbers. Settings are read-only here — change them by editing <code>config/settings.yaml</code> in the GitHub repository (the next nightly run uses them). API keys and contact details live only in GitHub Actions secrets and never reach this page.</p>
    <div class="section grid g2">
      <div class="card"><h2>Pipeline</h2><ol class="small sub" style="padding-left:18px;margin:8px 0 0">
        <li>Universe: every Nasdaq/NYSE company in technology SIC codes (SEC), plus an always-include list.</li>
        <li>Fundamentals: SEC XBRL company facts → TTM, margins, cash flow, balance sheet, dilution.</li>
        <li>Prices, consensus estimates, earnings dates: Yahoo Finance (unofficial).</li>
        <li>Initial screen → preliminary valuation → shortlist gets estimates → full analysis.</li>
        <li>DCF + reverse DCF, growth/quality-adjusted comparables, own-history range, bear/base/bull exit scenarios.</li>
        <li>Risk flags, catalysts from filings, 100-point score with caps, tiering.</li>
        <li>Report, append-only recommendation log, alerts.</li></ol></div>
      <div class="card"><h2>Data sources & limits</h2><ul class="list small">
        <li><b>SEC EDGAR</b> — free, authoritative; ≤10 requests/s; XBRL tagging varies by company (derived values are marked).</li>
        <li><b>Yahoo Finance via yfinance</b> — free, unofficial, no SLA; occasional gaps or throttling. Consensus is a snapshot without history before tracking began.</li>
        <li><b>Not covered (free tier)</b> — customer concentration, insider transaction classification, full news feed. Flagged in the risk section.</li></ul>
        <p class="small muted">Current run: ${esc(L?.data_snapshot?.sources || "–")}</p></div>
    </div>
    <div class="section grid g3">
      <div class="card"><h2>Score weights</h2>${kv(Object.entries(w).map(([k, x]) => [k.replace(/_/g, " "), x]))}<p class="small muted">Caps: weak balance sheet ≤ ${C?.scoring?.caps?.weak_balance_sheet}; extreme valuation or optimism-only ≤ ${C?.scoring?.caps?.extreme_valuation}. Missing inputs earn 30% credit.</p></div>
      <div class="card"><h2>Tiers</h2>${kv([["Tier 1 score", `≥ ${t.tier1?.min_score}`], ["Tier 1 margin of safety", `≥ ${pct(t.tier1?.min_margin_of_safety)}`], ["Tier 1 expected 24m", `≥ ${pct(t.tier1?.min_expected_return_24m)}`], ["Tier 1 up/down", `≥ ${t.tier1?.min_up_down_ratio}x`], ["Tier 2 score", `≥ ${t.tier2?.min_score}`], ["Tier 2 expected 24m", `≥ ${pct(t.tier2?.min_expected_return_24m)}`], ["Tier 3 score", `≥ ${t.tier3?.min_score}`]])}<p class="small muted">Tier 1 also requires no high-severity flags, consensus data, and a credible path to 2x.</p></div>
      <div class="card"><h2>Screen & valuation</h2>${kv([["Min revenue (TTM)", big(sc.min_revenue_ttm)], ["Min growth", pct(sc.min_revenue_growth)], ["Min market cap", big(sc.min_market_cap)], ["Small-co exception", `${big(sc.small_company_exception?.min_revenue_ttm)} & ${pct(sc.small_company_exception?.min_revenue_growth)} growth`], ["Risk-free / ERP", `${pct(v.risk_free_rate, 1)} / ${pct(v.equity_risk_premium, 1)}`], ["WACC bounds", `${pct(v.wacc_min)}–${pct(v.wacc_max)}`], ["Terminal growth", pct(v.terminal_growth, 1)], ["Scenario weights", `${pct(v.scenario_probabilities?.bear)} / ${pct(v.scenario_probabilities?.base)} / ${pct(v.scenario_probabilities?.bull)}`]])}</div>
    </div>
    <div class="section card"><h2>Full configuration</h2><details><summary>Show settings.yaml as loaded</summary><pre class="small" style="white-space:pre-wrap;overflow-x:auto">${esc(JSON.stringify(C, null, 2))}</pre></details></div>`;
}

// ---------------------------------------------------------------------------------------------
// router
// ---------------------------------------------------------------------------------------------
let redraw = null;
async function route() {
  const app = $("#app"); hideTip(); redraw = null;
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const r = parts[0] || "home";
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.r === (r === "stock" ? "home" : r)));
  try {
    if (r === "screener") await pageScreener(app);
    else if (r === "stock") await pageStock(app, decodeURIComponent(parts[1] || "").toUpperCase());
    else if (r === "watchlist") await pageWatchlist(app);
    else if (r === "performance") await pagePerformance(app);
    else if (r === "alerts") await pageAlerts(app);
    else if (r === "reports") await pageReports(app, parts[1]);
    else if (r === "method") await pageMethod(app);
    else await pageHome(app);
  } catch (e) {
    console.error(e);
    app.innerHTML = `<div class="empty"><h2>Something went wrong rendering this page</h2><p class="small">${esc(e.message)}</p></div>`;
  }
  window.scrollTo(0, 0);
}
let lastW = window.innerWidth;
window.addEventListener("resize", () => { if (Math.abs(window.innerWidth - lastW) > 40 && redraw) { lastW = window.innerWidth; redraw(); } });
window.addEventListener("hashchange", route);
$("#theme").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("scanner-theme", next); } catch (e) { /* ignore */ }
  if (redraw) redraw();
});
(async () => {
  const L = await getJSON("latest.json");
  if (L && (L.data_snapshot?.sources || "").includes("SYNTHETIC")) {
    $("#demo").innerHTML = `<div class="demo-banner">Demo mode — fictional companies and synthetic data for testing the engine. Not real securities.</div>`;
  }
  $("#foot").innerHTML = `${L ? `Report ${esc(L.date)} · generated ${esc(L.generated_at.slice(0, 16).replace("T", " "))} ET. ` : ""}${esc(L?.disclaimer || "Research tool output — not investment advice.")}`;
  route();
})();
})();
