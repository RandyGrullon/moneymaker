/* ====== OmniTrade AI — lógica compartida entre páginas ====== */

// ---------- Helpers ----------
const fmt = (n, d = 2) => n == null ? '—' : Number(n).toFixed(d);
const pnlClass = n => n > 0 ? 'green' : n < 0 ? 'red' : '';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Clasificación de símbolos por mercado
const FX_CCY = /^(EUR|GBP|USD|JPY|AUD|NZD|CAD|CHF|SEK|NOK|SGD|MXN|ZAR|TRY|PLN|HKD|DKK|CNH)/;
const CRYPTO = /(BTC|ETH|XRP|LTC|SOL|ADA|DOGE|DOT|BNB|AVAX|LINK|SHIB|MATIC|TRX)/i;
const METALS = /^(XAU|XAG|XPT|XPD|GOLD|SILVER)/i;
const INDICES = /(US30|US100|US500|NAS100|SPX|GER40|DAX|UK100|JP225|HK50|FRA40|AUS200|ESP35)/i;
function categorize(sym) {
  const s = String(sym || '').toUpperCase().replace(/[._\-]/g, '');
  if (METALS.test(s)) return 'gold';
  if (CRYPTO.test(s)) return 'crypto';
  if (INDICES.test(s)) return 'stock';
  if (s.length >= 6 && FX_CCY.test(s) && FX_CCY.test(s.slice(3))) return 'forex';
  if (FX_CCY.test(s) && s.length <= 7) return 'forex';
  return 'stock';
}
const CAT_LABEL = { forex: 'FX', gold: 'ORO', crypto: 'CRIPTO', stock: 'STOCK' };
const catTag = sym => { const c = categorize(sym); return `<span class="cat-tag cat-${c}">${CAT_LABEL[c]}</span>`; };

const KIND_LABEL = { info: 'info', think: 'piensa', decision: 'decisión', trade: 'trade', learn: 'lección', error: 'error' };

// ---------- Estado global ----------
let paused = false;
let lastData = null;

// ---------- Refresco contra el servidor ----------
async function refresh() {
  try {
    const r = await fetch('/api/state');
    if (r.status === 401) { location.href = '/login'; return; }
    lastData = await r.json();
    renderBase(lastData);
    if (typeof window.renderPage === 'function') window.renderPage(lastData);
  } catch (err) {
    const st = document.getElementById('statusText');
    if (st) st.textContent = 'sin conexión con el servidor';
    const dot = document.getElementById('statusDot');
    if (dot) { dot.style.background = 'var(--red)'; dot.style.color = 'var(--red)'; }
  }
}

function renderBase(d) {
  paused = d.paused;

  const dot = document.getElementById('statusDot');
  const color = !d.connected ? 'var(--red)' : (d.paused || d.daily_loss_triggered) ? 'var(--yellow)' : 'var(--green)';
  if (dot) { dot.style.background = color; dot.style.color = color; }
  const st = document.getElementById('statusText');
  if (st) st.textContent = d.status_text;

  const btn = document.getElementById('btnPause');
  if (btn) btn.textContent = paused ? '▶ Reanudar todo' : '⏸ Pausar nuevas';

  const pb = document.getElementById('pausedBanner');
  if (pb) pb.style.display = paused ? 'block' : 'none';
  const db = document.getElementById('dailyBanner');
  if (db) db.style.display = d.daily_loss_triggered ? 'block' : 'none';

  const mode = document.getElementById('modeChip');
  if (mode) mode.style.display = (d.settings && d.settings.trading_enabled === false) ? 'inline-block' : 'none';

  // Badges del menú lateral
  const bp = document.getElementById('badgePos');
  if (bp) bp.textContent = d.positions.length || '';
  const errs = d.events.filter(e => e.kind === 'error').length;
  const bl = document.getElementById('badgeLog');
  if (bl) { bl.textContent = errs || ''; bl.classList.toggle('alert', errs > 0); }

  // Pie del sidebar: cuenta activa
  const acc = document.getElementById('sideAccount');
  if (acc) {
    const a = d.account || {};
    acc.textContent = a.login ? `${a.login} @ ${a.server}` : 'sin sesión MT5';
  }
}

// ---------- Acciones globales ----------
async function togglePause() {
  await fetch(paused ? '/api/resume' : '/api/pause', { method: 'POST' });
  refresh();
}

async function closeAll() {
  if (!confirm('¿Cerrar TODAS las posiciones abiertas del bot a precio de mercado?')) return;
  try {
    const r = await (await fetch('/api/close_all', { method: 'POST' })).json();
    if (r.ok && r.closed > 0) alert(`Cerradas ${r.closed} posiciones.`);
    else if (!r.ok) alert('Cerrar todo: ' + (r.error || `${r.errors} errores (mira los logs)`));
    else alert(r.error || 'El bot no tiene posiciones abiertas.');
  } catch (e) {
    alert('Error de conexión con el servidor al cerrar.');
  }
  refresh();
}

async function closeOne(ticket, symbol) {
  if (!confirm(`¿Cerrar la posición #${ticket} (${symbol}) a precio de mercado?`)) return;
  try {
    const r = await (await fetch('/api/close_position', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket }),
    })).json();
    if (!r.ok) alert('No se pudo cerrar: ' + (r.error || 'error desconocido'));
  } catch (e) {
    alert('Error de conexión con el servidor al cerrar.');
  }
  refresh();
}

// ---------- Render reutilizable ----------
function positionRow(p, withClose = true) {
  return `<tr><td>${p.ticket}</td><td class="sym-cell">${esc(p.symbol)}${catTag(p.symbol)}</td>
    <td><span class="badge ${esc(p.direction)}">${esc(p.direction).toUpperCase()}</span></td>
    <td>${p.volume}</td><td>${p.price_open}</td><td>${p.price_current}</td>
    <td>${p.sl}</td><td>${p.tp}</td>
    <td class="${pnlClass(p.profit)}">${fmt(p.profit)}</td>
    ${withClose ? `<td><button class="danger btn-mini" onclick="closeOne(${p.ticket}, '${esc(p.symbol)}')">✖ Cerrar</button></td>` : ''}</tr>`;
}

function decisionCard(sym, dec, detailed = false) {
  const conf = Math.max(0, Math.min(100, dec.confidence || 0));
  const analysis = (detailed && dec.analysis && typeof dec.analysis === 'object')
    ? Object.entries(dec.analysis).map(([k, v]) =>
        `<div class="a-step"><b>${esc(k.replace(/_/g, ' '))}:</b> ${esc(v)}</div>`).join('')
    : '';
  return `<div class="decision-card">
    <span class="sym">${esc(sym)}</span>${catTag(sym)}
    <span class="badge ${esc(dec.action)}">${esc(dec.action).toUpperCase()}</span>
    <span class="blue"> ${dec.confidence}%</span>
    <span style="float:right;color:var(--faint);font-size:11px;">${esc(dec.time)}</span>
    <div class="reason">${esc(dec.reason)}</div>
    ${analysis}
    ${detailed && dec.stop_loss_atr != null
      ? `<div class="meta">Plan de riesgo: SL ${dec.stop_loss_atr}×ATR · TP ${dec.take_profit_atr}×ATR
         · ratio ${(dec.take_profit_atr / dec.stop_loss_atr).toFixed(1)}
         · <a href="/pensamientos">pensamiento completo →</a></div>` : ''}
    <div class="conf-bar"><i style="width:${conf}%"></i></div>
  </div>`;
}

function logLine(e) {
  const label = KIND_LABEL[e.kind] || e.kind;
  return `<div class="line"><span class="t">${e.time}</span>` +
    `<span class="log-kind lk-${esc(e.kind)}">${esc(label)}</span>` +
    `<span class="k-${esc(e.kind)}">${esc(e.message)}</span></div>`;
}

// ---------- Gráfico de líneas (equity) ----------
function drawEquityChart(canvasId, hist) {
  const c = document.getElementById(canvasId);
  if (!c) return;
  const ctx = c.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const cw = c.clientWidth || 600, ch = c.clientHeight || 220;
  c.width = cw * dpr; c.height = ch * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, cw, ch);

  if (!hist || hist.length < 2) {
    ctx.fillStyle = '#4a5568'; ctx.font = '12px Segoe UI';
    ctx.fillText('Esperando datos de equity… (un punto por ciclo del bot)', 12, ch / 2);
    return;
  }

  const values = hist.map(p => p.equity);
  const min = Math.min(...values), max = Math.max(...values);
  const span = (max - min) || Math.abs(max) * 0.001 || 1;
  const padL = 8, padR = 64, padT = 12, padB = 20;
  const w = cw - padL - padR, h = ch - padT - padB;
  const x = i => padL + i / (values.length - 1) * w;
  const y = v => padT + (1 - (v - min) / span) * h;

  // Rejilla horizontal + etiquetas de valor
  ctx.font = '10px Consolas, monospace';
  ctx.strokeStyle = 'rgba(31, 39, 55, .8)';
  ctx.fillStyle = '#4a5568';
  for (let i = 0; i <= 3; i++) {
    const v = min + span * i / 3;
    const yy = y(v);
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(padL + w, yy); ctx.stroke();
    ctx.fillText(v.toFixed(2), padL + w + 6, yy + 3);
  }

  // Etiquetas de hora (inicio / fin)
  ctx.fillText(hist[0].time, padL, ch - 6);
  const lastT = hist[hist.length - 1].time;
  ctx.fillText(lastT, padL + w - ctx.measureText(lastT).width, ch - 6);

  const up = values[values.length - 1] >= values[0];
  const color = up ? '#00e08e' : '#ff5470';

  // Área degradada + línea
  const grad = ctx.createLinearGradient(0, padT, 0, padT + h);
  grad.addColorStop(0, color + '3a'); grad.addColorStop(1, color + '00');
  ctx.beginPath();
  values.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(0), y(v)));
  ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.stroke();
  ctx.lineTo(x(values.length - 1), padT + h); ctx.lineTo(padL, padT + h); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();

  // Último valor resaltado
  const lv = values[values.length - 1];
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.arc(x(values.length - 1), y(lv), 3, 0, Math.PI * 2); ctx.fill();
  ctx.font = 'bold 11px Consolas, monospace';
  ctx.fillText(lv.toFixed(2), padL + w + 6, y(lv) + 4);
}

// ---------- Arranque ----------
refresh();
setInterval(refresh, 2000);
