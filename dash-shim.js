/* Minimal stand-in for the dashboard runtime: loads the CSV files, runs the calculations,
   keeps the view settings in the address bar, and redraws the page when they change. */
(function () {
  'use strict';

  // RFC 4180 CSV parser: quoted fields, commas and line breaks inside quotes, CRLF or LF.
  function parseCSV(text) {
    const rows = [];
    let row = [], field = '', i = 0, q = false;
    text = text.replace(/^﻿/, '');
    while (i < text.length) {
      const c = text[i];
      if (q) {
        if (c === '"') { if (text[i + 1] === '"') { field += '"'; i += 2; continue; } q = false; i++; continue; }
        field += c; i++; continue;
      }
      if (c === '"') { q = true; i++; continue; }
      if (c === ',') { row.push(field); field = ''; i++; continue; }
      if (c === '\r') { i++; continue; }
      if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; i++; continue; }
      field += c; i++;
    }
    if (field !== '' || row.length) { row.push(field); rows.push(row); }
    const header = rows.shift() || [];
    const data = rows.filter((r) => r.length > 1 || r[0] !== '').map((r) => Object.fromEntries(header.map((h, k) => [h, r[k] ?? ''])));
    data.columns = header;
    return data;
  }

  // The few d3 helpers the page uses.
  const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  window.d3 = {
    max: (a, f) => a.reduce((m, x) => { const v = f ? f(x) : x; return v > m || m === undefined ? v : m; }, undefined),
    utcFormat: () => (d) => DAYS[d.getUTCDay()] + ' ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate(),
    csvParse: parseCSV,
  };

  const BOOL = ['hca', 'shrink', 'rest'];
  function readHash() {
    const out = {};
    new URLSearchParams(location.hash.slice(1)).forEach((v, k) => { out[k] = BOOL.includes(k) ? v === '1' : v; });
    return out;
  }
  function writeHash(p) {
    const s = new URLSearchParams();
    Object.entries(p).forEach(([k, v]) => { if (v !== null && v !== undefined) s.set(k, BOOL.includes(k) ? (v ? '1' : '0') : String(v)); });
    const h = s.toString();
    history.replaceState(null, '', h ? '#' + h : location.pathname + location.search);
  }

  const calcs = [];
  const store = {};
  const listeners = [];
  let params = readHash();
  const empty = (status, message) => ({ status, data: [], columns: [], meta: [], message: message || '', refreshing: false });

  function emit() { listeners.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } }); }

  window.dash = {
    calc(id, def) { calcs.push({ id, ...def }); },
    data(id) { return store[id] || empty('loading'); },
    onData(fn) { listeners.push(fn); try { fn(); } catch (e) { console.error(e); } },
    params() { return { ...params }; },
    setParams(p) { params = { ...params, ...p }; writeHash(params); emit(); },
    resetParams() { params = {}; writeHash(params); emit(); },
    colors: ['var(--lk-accent)', '#5A86D0', '#B9850F', '#4FA37A', '#C2569B', '#7A6FD6', '#D9743A', '#3E9BB5'],
  };

  window.addEventListener('hashchange', () => { params = readHash(); emit(); });
  let rt;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(emit, 150); });

  // Load every file, then run the calculations in the order the page declares them.
  window.dashLoad = async function (files) {
    await Promise.all(Object.entries(files).map(async ([id, url]) => {
      try {
        const res = await fetch(url, { cache: 'no-cache' });
        if (!res.ok) throw new Error(url + ': HTTP ' + res.status);
        const text = await res.text();
        const data = url.endsWith('.json') ? JSON.parse(text) : parseCSV(text);
        store[id] = { status: 'ok', data, columns: data.columns || [], meta: [], message: '', refreshing: false };
      } catch (e) {
        store[id] = empty('error', String(e.message || e));
      }
    }));
    for (const c of calcs) {
      const ins = c.inputs.map((k) => store[k]);
      if (ins.some((s) => !s || s.status !== 'ok')) { store[c.id] = empty('error', 'An input did not load'); continue; }
      try {
        const data = c.fn(...ins.map((s) => s.data));
        store[c.id] = { status: 'ok', data, columns: Array.isArray(data) && data[0] ? Object.keys(data[0]) : [], meta: [], message: '', refreshing: false };
      } catch (e) {
        console.error(c.id, e);
        store[c.id] = empty('error', String(e.message || e));
      }
    }
    emit();
  };
})();
