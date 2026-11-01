#!/usr/bin/env node
// Genera operativas.json para el bot de NinjaTrader (ViperBot) a partir de los análisis de index.html.
// Lo ejecuta el workflow «Operativas para el bot» cada vez que cambia index.html en main. No se edita a mano.
// Uso: node scripts/generar_operativas.js [index.html] [operativas.json]
'use strict';
const fs = require('fs');

const SRC = process.argv[2] || 'index.html';
const OUT = process.argv[3] || 'operativas.json';
const html = fs.readFileSync(SRC, 'utf8');

// ---------- Datos de la web ----------
function bloque(nombre) {
  const ini = html.indexOf('var ' + nombre + ' = [');
  if (ini < 0) return [];
  const desde = html.indexOf('[', ini);
  const fin = html.indexOf('\n];', desde);
  // eslint-disable-next-line no-new-func
  return new Function('return ' + html.slice(desde, fin + 2))();
}
const ENTRIES = (() => {
  const ini = html.indexOf('var ENTRIES = [') + 'var ENTRIES = '.length;
  // ENTRIES es JSON: se lee con un analizador que se para al cerrar el array
  let prof = 0, enCad = false, esc = false, i = ini;
  for (; i < html.length; i++) {
    const c = html[i];
    if (enCad) { if (esc) esc = false; else if (c === '\\') esc = true; else if (c === '"') enCad = false; continue; }
    if (c === '"') enCad = true; else if (c === '[' || c === '{') prof++; else if (c === ']' || c === '}') { prof--; if (prof === 0) break; }
  }
  return JSON.parse(html.slice(ini, i + 1));
})();
const CAL = bloque('CAL');

const TICK = { NQ: 0.25, ES: 0.25, YM: 1, RTY: 0.1, NKD: 5, GC: 0.1, SI: 0.005, HG: 0.0005, WTI: 0.01, CL: 0.01, NG: 0.001, KC: 0.05, CC: 1, EURUSD: 0.00005, BTC: 5, XRP: 0.0001 };

// ---------- Utilidades ----------
const num = s => +String(s).replace(/\./g, '').replace(',', '.');
const NUM = '(\\d[\\d.]*(?:,\\d+)?)';
function addDias(f, n) { const d = new Date(f + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
function diaSemana(f) { return new Date(f + 'T12:00:00Z').getUTCDay(); } // 0 domingo … 6 sábado

// Misma regla que la web (slMode en index.html): decide el modo del SL+
function slMode(text, intra) {
  const f0 = (text.match(/Va a favor de ([^.]*)\./) || [])[1] || '', all = /todos los marcos|cuatro marcos/.test(f0);
  if (intra) {
    const ctr = (text.match(/contra ([^.]*)\./) || [])[1] || '';
    return (all || /\bH1\b/.test(f0.split(/\by contra\b|;|:/)[0])) && !/\bH4\b/.test(ctr) ? 'estructural' : 'escalera';
  }
  const f = f0.split(/\by contra\b|;|:/)[0];
  const D = all || /\b(D|diario)\b/.test(f), H4 = all || /\bH4\b/.test(f);
  return D && H4 ? 'estructural' : 'escalera';
}
function frase(text, re) { const m = text.match(re); return m ? { tf: m[1], lado: m[2], nivel: num(m[3]) } : null; }

// ---------- Operativas ----------
const ops = [], avisos = [];
const porActivoSwing = {};
ENTRIES.filter(e => e.mode === 'swing').forEach(e => { (porActivoSwing[e.asset] = porActivoSwing[e.asset] || []).push(e.date); });
Object.values(porActivoSwing).forEach(v => v.sort());

ENTRIES.slice().sort((a, b) => (a.date + a.asset).localeCompare(b.date + b.asset)).forEach(e => {
  const intra = e.mode === 'intra';
  if (!intra && e.mode !== 'swing') return;
  const plan = {}; (e.plan || []).forEach(p => { plan[p.k] = p; });
  const F = intra ? 'M15' : 'H4';

  // Vigencia (hora de Madrid)
  let desde, hasta;
  if (intra) {
    const ahora = (e.plan || []).map(p => (p.k.match(/^Ahora \((\d{1,2}):(\d{2})/) || [])).find(m => m.length);
    desde = e.date + 'T' + (ahora ? ahora[1].padStart(2, '0') + ':' + ahora[2] : '09:00');
    hasta = e.date + 'T22:00';
  } else {
    const ds = diaSemana(e.date), fin1 = ds === 6 || ds === 0;
    const lunes = addDias(e.date, ds === 6 ? 2 : ds === 0 ? 1 : 1 - ds);
    desde = (fin1 ? addDias(e.date, 1) : e.date) + 'T00:00';
    // Vale hasta el viernes de esa semana o hasta el siguiente swing del mismo activo
    let fin = addDias(lunes, 4) + 'T23:00';
    const sig = (porActivoSwing[e.asset] || []).find(d => d > e.date);
    if (sig && addDias(sig, 1) + 'T00:00' < fin) fin = addDias(sig, 1) + 'T00:00';
    hasta = fin;
  }

  (e.plays || []).forEach((pl, n) => {
    const p = plan[pl.k], text = p ? p.text : '';
    const id = e.asset + '-' + e.date + '-' + (intra ? 'I' : 'S') + (n + 1);
    const stopTxt = (text.match(new RegExp('Stop (?:bajo|sobre) ' + NUM)) || [])[1];
    const stop = pl.stop != null ? pl.stop : stopTxt ? num(stopTxt) : null;
    if (stop == null || !(pl.targets || []).length || !p) { avisos.push(id + ': sin stop, objetivos o texto; no se exporta'); return; }
    const long = pl.dir === 'long';
    const lo = Array.isArray(pl.entry) ? Math.min(pl.entry[0], pl.entry[1]) : pl.entry;
    const hi = Array.isArray(pl.entry) ? Math.max(pl.entry[0], pl.entry[1]) : pl.entry;

    const ruptura = /ruptura|continuaci/i.test(pl.k) || /^Cierre (H4|M15|H1) (sobre|bajo)/.test(text);
    let rup = null;
    if (ruptura) {
      rup = frase(text, new RegExp('Cierre (H4|M15|H1) (sobre|bajo) ' + NUM)) || { tf: F, lado: long ? 'sobre' : 'bajo', nivel: long ? lo : hi };
    }
    const filtro = frase(text, new RegExp('Filtro (H4|M15): si la vela \\S+ cierra (bajo|sobre) ' + NUM)) || { tf: F, lado: long ? 'bajo' : 'sobre', nivel: long ? lo : hi, porDefecto: true };
    const anula = frase(text, new RegExp('Se anula con (?:un )?cierre (\\S+)(?: de nuevo)? (bajo|sobre) ' + NUM))
      || frase(String(pl.off || ''), new RegExp('cierre (\\S+) (bajo|sobre) ' + NUM))
      || { tf: F, lado: long ? 'bajo' : 'sobre', nivel: long ? lo : hi, porDefecto: true };
    const favor = (text.match(/(Va (?:a favor|contra)[^]*?)\.(?=\s+[A-ZÁÉÍÓÚ¿]|\s*$)/) || [])[1] || '';
    const modo = slMode(text, intra);

    // Niveles por el camino al 1.er objetivo (como el SL+ «por contexto» de la web; el bot solo los usa en swing)
    const t1 = pl.targets[0], ref0 = long ? hi : lo;
    const a = Math.min(ref0, t1), b = Math.max(ref0, t1), d = Math.abs(t1 - ref0), tol = d * 0.04;
    const niveles = intra ? [] : (e.levels || [])
      .filter(l => l.cls !== 'now' && typeof l.p === 'number' && l.p > a + tol && l.p < b - tol && (l.p < lo - tol || l.p > hi + tol))
      .map(l => ({ p: l.p, lbl: String(l.lbl || '') }))
      .sort((x, y) => Math.abs(x.p - ref0) - Math.abs(y.p - ref0));

    ops.push({
      id, activo: e.asset, fecha: e.date, modo: intra ? 'intradia' : 'swing', nombre: pl.k,
      dir: long ? 'largo' : 'corto', zona: [lo, hi], stop, objetivos: pl.targets,
      tick: TICK[e.asset] || null,
      ruptura: rup, filtro, anula,
      slplus: modo, favor: favor ? favor + '.' : '',
      reducido: /[Tt]amaño (reducido|pequeño)/.test(text),
      niveles, desde, hasta
    });
  });
});

// ---------- Datos del calendario (para no entrar en la vela del dato) ----------
const datos = CAL.filter(c => c.k !== 'hol' && (c.s || 0) >= 2 && /^\d{1,2}:\d{2}$/.test(c.h || ''))
  .map(c => ({ fecha: c.d, hora: c.h.padStart(5, '0'), imp: c.s, ev: c.ev, activos: c.a || [] }));
// Horas de «Cuándo» del intradía (por si un dato no está en el Calendario)
ENTRIES.filter(e => e.mode === 'intra').forEach(e => {
  const q = (e.plan || []).find(p => /^Cuándo/.test(p.k)); if (!q) return;
  (q.text.match(/\b\d{1,2}:\d{2}\b/g) || []).forEach(h => {
    h = h.padStart(5, '0');
    if (!datos.some(x => x.fecha === e.date && x.hora === h && x.activos.includes(e.asset)))
      datos.push({ fecha: e.date, hora: h, imp: 2, ev: 'Hora citada en «Cuándo» del intradía', activos: [e.asset] });
  });
});
datos.sort((x, y) => (x.fecha + x.hora).localeCompare(y.fecha + y.hora));

const out = { version: 1, zonaHoraria: 'Europe/Madrid', nota: 'Generado desde index.html por scripts/generar_operativas.js. Precios en futuro. No editar a mano.', operativas: ops, datos };
const txt = JSON.stringify(out, null, 1) + '\n';
let prev = ''; try { prev = fs.readFileSync(OUT, 'utf8'); } catch (er) {}
if (prev !== txt) fs.writeFileSync(OUT, txt);
console.log(ops.length + ' operativas, ' + datos.length + ' datos' + (prev === txt ? ' (sin cambios)' : ''));
avisos.forEach(a => console.log('  aviso: ' + a));
