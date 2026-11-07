/* Progress: a bar (gray -> yellow -> green) with a "Naučeno" button under every question or card, a roll-up for every
   lesson and for the whole script, a goal (number of passes) per question or per lesson, and save/load of progress.json.
   Everything stays in this browser (localStorage). Built by script, like the hearts, so the page works without it.
   The first part is the colour rule: a port of bar_state / bar_blend / auto_target_passes in contracts/progress.py,
   and tests/render/test_progress.py keeps both in agreement. */
(function () {
  'use strict';
  var SM = window.SM;

  /* ---- the rule (pure functions, no page needed) ---- */
  var AUTO_STEPS = [[20, 2], [45, 3], [100, 4]];
  var bar = {
    state: function (done, target) { return done <= 0 ? 'empty' : (done >= target ? 'done' : 'partial'); },
    fraction: function (done, target) { return Math.max(0, Math.min(1, done / target)); },
    /* 0 = pure yellow (first pass) .. 1 = pure green (goal reached); 0 while empty */
    blend: function (done, target) {
      var s = bar.state(done, target);
      return s === 'empty' ? 0 : (s === 'done' ? 1 : (done - 1) / (target - 1));
    },
    autoTarget: function (count) {
      for (var i = 0; i < AUTO_STEPS.length; i++) if (count <= AUTO_STEPS[i][0]) return AUTO_STEPS[i][1];
      return 5;
    }
  };
  SM.bar = bar;
  if (typeof document === 'undefined') return;        /* the parity test loads this file without a page */

  var each = SM.each;
  var items = [];                                      /* {el, id, unitId, question, row} */
  var byId = {};
  each(document.querySelectorAll('article[data-item]'), function (el) {
    var head = el.querySelector('.q') || el.querySelector('h3');
    if (!head) return;
    var titleEl = el.querySelector('.q h4') || el.querySelector('h3 .ct') || head;
    var clone = titleEl.cloneNode(true);
    each(clone.querySelectorAll('.exb, .tex, .katex, .heart'), function (x) { x.parentNode.removeChild(x); });
    var section = el.closest('section');
    var item = {
      el: el, head: head, id: el.getAttribute('data-item'), unitId: section ? section.id : '',
      question: (clone.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 90)
    };
    items.push(item);
    byId[item.id] = item;
  });
  if (!items.length) return;

  var cfg = window.__SM_PROG || {};
  var DEFAULT = cfg.target || bar.autoTarget(items.length);
  var MAX_TARGET = 10, MAX_PASSES = 99;

  /* ---- storage: { v: 1, items: { <item id>: { p: passes, t: own goal (only when it differs), l: last pass, u: changed } } } ---- */
  var store = { v: 1, items: {} };
  function load() {
    var saved = SM.getJSON('prog', null);
    store = { v: 1, items: {} };
    if (!saved || typeof saved !== 'object' || !saved.items) return;
    Object.keys(saved.items).forEach(function (id) {
      var r = saved.items[id];
      if (!byId[id] || !r || typeof r !== 'object') return;
      var p = Math.floor(+r.p), t = Math.floor(+r.t);
      store.items[id] = {
        p: p > 0 ? Math.min(p, MAX_PASSES) : 0,
        t: t >= 1 && t <= MAX_TARGET ? t : 0,
        l: typeof r.l === 'string' ? r.l : '',
        u: typeof r.u === 'string' ? r.u : ''
      };
    });
  }
  function save() { SM.setJSON('prog', store); }
  function record(id) { return store.items[id] || { p: 0, t: 0, l: '', u: '' }; }
  function passes(id) { return record(id).p; }
  function target(id) { return record(id).t || DEFAULT; }
  function change(id, fn) {
    var r = record(id);
    fn(r);
    r.u = new Date().toISOString();
    if (r.p <= 0 && !r.t) delete store.items[id]; else store.items[id] = r;
  }

  /* ---- markup ---- */
  function ticks(n) { var s = ''; for (var i = 0; i < n; i++) s += '<i></i>'; return s; }
  function barHTML(label) {
    return '<span class="prog" role="progressbar" aria-valuemin="0" aria-label="' + label + '">' +
      '<span class="prog-track"><span class="prog-fill"></span><span class="prog-ticks" aria-hidden="true"></span></span>' +
      '<span class="prog-text"></span></span>';
  }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }
  function options(from) {
    var s = '';
    for (var n = 1; n <= MAX_TARGET; n++) s += '<option value="' + n + '">' + n + '</option>';
    return from ? '<option value="" disabled hidden>različito</option>' + s : s;
  }

  var status = document.createElement('p');
  status.className = 'sr';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  document.body.appendChild(status);
  function say(text) { status.textContent = ''; window.setTimeout(function () { status.textContent = text; }, 30); }

  items.forEach(function (it) {
    var row = document.createElement('div');
    row.className = 'study';
    row.innerHTML =
      barHTML('Napredak: ' + esc(it.question)) +
      '<span class="study-btns"><button type="button" class="study-btn prog-learn" aria-label="Naučeno: ' + esc(it.question) + '">Naučeno</button>' +
      '<button type="button" class="study-btn prog-undo" aria-label="Poništi jedan prolaz: ' + esc(it.question) + '" title="Poništi jedan prolaz">−</button></span>' +
      '<label class="study-target"><span>Cilj</span><select class="prog-target" aria-label="Cilj (broj prolaza): ' + esc(it.question) + '">' + options(false) + '</select></label>';
    it.head.parentNode.insertBefore(row, it.head.nextSibling);
    it.row = row;
    it.bar = row.querySelector('.prog');
    it.undo = row.querySelector('.prog-undo');
    it.select = row.querySelector('.prog-target');
    var li = document.querySelector('ol.index a[href="#' + it.el.id + '"]');
    if (li && li.parentNode) {
      it.line = document.createElement('span');
      it.line.className = 'prog-line';
      it.line.setAttribute('aria-hidden', 'true');
      it.line.innerHTML = '<i></i>';
      li.parentNode.appendChild(it.line);
    }
  });

  /* lesson roll-ups sit in each lesson's header; the script's own roll-up sits in the page header */
  var units = {};
  each(document.querySelectorAll('section[id]'), function (section) {
    var head = section.querySelector('.lesson-head, .sec-head');
    var mine = items.filter(function (it) { return it.unitId === section.id; });
    if (!head || !mine.length) return;
    var title = (head.querySelector('h2') || head).textContent.replace(/\s+/g, ' ').trim();
    var row = document.createElement('div');
    row.className = 'study study-unit';
    row.innerHTML =
      barHTML('Napredak lekcije: ' + esc(title)) + '<span class="study-note"></span>' +
      '<label class="study-target"><span>Cilj za sva pitanja</span><select class="prog-unit-target" aria-label="Cilj za sva pitanja u lekciji: ' + esc(title) + '">' + options(true) + '</select></label>';
    head.appendChild(row);
    units[section.id] = { items: mine, row: row, bar: row.querySelector('.prog'), note: row.querySelector('.study-note'), select: row.querySelector('.prog-unit-target') };
  });

  var total = null;
  var host = document.querySelector('header.hero') || document.querySelector('.mast-in');
  if (host) {
    var row = document.createElement('div');
    row.className = 'study study-total';
    row.innerHTML =
      '<span class="study-label">Napredak</span>' + barHTML('Napredak ukupno') + '<span class="study-note"></span>' +
      '<span class="study-io"><button type="button" class="chip prog-save">Spremi napredak</button>' +
      '<button type="button" class="chip prog-open">Učitaj napredak</button>' +
      '<input type="file" class="prog-file" accept=".json,application/json" hidden></span>' +
      '<p class="study-msg" role="status"></p>';
    var modes = host.querySelector('.modes');
    host.insertBefore(row, modes);
    total = { row: row, bar: row.querySelector('.prog'), note: row.querySelector('.study-note'), msg: row.querySelector('.study-msg') };
  }

  /* ---- drawing ---- */
  function draw(barEl, done, goal, valueText, text) {
    barEl.setAttribute('data-state', bar.state(done, goal));
    barEl.style.setProperty('--frac', bar.fraction(done, goal).toFixed(4));
    barEl.style.setProperty('--blend', bar.blend(done, goal).toFixed(4));
    barEl.setAttribute('aria-valuemax', String(goal));
    barEl.setAttribute('aria-valuenow', String(Math.min(done, goal)));
    barEl.setAttribute('aria-valuetext', valueText);
    barEl.querySelector('.prog-text').textContent = text;
  }
  function drawTicks(barEl, goal) {
    var holder = barEl.querySelector('.prog-ticks');
    if (holder.children.length !== goal) holder.innerHTML = ticks(goal);
  }
  function drawItem(it) {
    var p = passes(it.id), t = target(it.id);
    var done = p >= t;
    draw(it.bar, p, t, p + ' od ' + t + ' prolaza' + (done ? ', gotovo' : ''), p + '/' + t + (done ? ' ✓' : ''));
    drawTicks(it.bar, t);
    it.select.value = String(t);
    it.undo.setAttribute('aria-disabled', p > 0 ? 'false' : 'true');
    it.el.setAttribute('data-prog', bar.state(p, t));
    if (it.line) {
      it.line.setAttribute('data-state', bar.state(p, t));
      it.line.style.setProperty('--frac', bar.fraction(p, t).toFixed(4));
      it.line.style.setProperty('--blend', bar.blend(p, t).toFixed(4));
    }
  }
  function sums(list) {
    var P = 0, T = 0, finished = 0;
    list.forEach(function (it) {
      var p = passes(it.id), t = target(it.id);
      P += Math.min(p, t);
      T += t;
      if (p >= t) finished++;
    });
    return { P: P, T: T, finished: finished, n: list.length };
  }
  function drawRoll(barEl, noteEl, s) {
    var text = s.P + '/' + s.T + (s.P >= s.T ? ' ✓' : '');
    draw(barEl, s.P, s.T, s.P + ' od ' + s.T + ' prolaza, gotovo ' + s.finished + ' od ' + s.n, text);
    noteEl.textContent = 'Gotovo: ' + s.finished + ' od ' + s.n;
  }
  function drawUnit(u) {
    drawRoll(u.bar, u.note, sums(u.items));
    var common = target(u.items[0].id);
    var same = u.items.every(function (it) { return target(it.id) === common; });
    u.select.value = same ? String(common) : '';
  }
  function drawTotal() { if (total) drawRoll(total.bar, total.note, sums(items)); }
  function drawAll() {
    items.forEach(drawItem);
    Object.keys(units).forEach(function (k) { drawUnit(units[k]); });
    drawTotal();
  }
  function drawAround(it) {
    drawItem(it);
    if (units[it.unitId]) drawUnit(units[it.unitId]);
    drawTotal();
  }

  /* ---- actions ---- */
  function learned(it) {
    change(it.id, function (r) { r.p = Math.min(MAX_PASSES, r.p + 1); r.l = new Date().toISOString(); });
    save();
    push([it]);
    drawAround(it);
    var p = passes(it.id), t = target(it.id);
    say('Naučeno: prolaz ' + p + ' od ' + t + (p >= t ? ', cilj postignut' : '') + '. ' + it.question);
  }
  function undone(it) {
    if (passes(it.id) <= 0) return;
    change(it.id, function (r) { r.p -= 1; });
    save();
    push([it]);
    drawAround(it);
    say('Poništen jedan prolaz: ' + passes(it.id) + ' od ' + target(it.id) + '. ' + it.question);
  }
  function retarget(list, value) {
    var n = Math.max(1, Math.min(MAX_TARGET, Math.floor(+value)));
    if (!n) return;
    list.forEach(function (it) { change(it.id, function (r) { r.t = n === DEFAULT ? 0 : n; }); });
    save();
    push(list);
    list.forEach(drawItem);
    var touched = {};
    list.forEach(function (it) { touched[it.unitId] = 1; });
    Object.keys(touched).forEach(function (k) { if (units[k]) drawUnit(units[k]); });
    drawTotal();
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.prog-learn, .prog-undo');
    if (!btn) return;
    var it = byId[btn.closest('article[data-item]').getAttribute('data-item')];
    if (!it) return;
    if (btn.classList.contains('prog-learn')) learned(it); else if (btn.getAttribute('aria-disabled') !== 'true') undone(it);
  });
  document.addEventListener('change', function (e) {
    var sel = e.target;
    if (!sel.classList) return;
    if (sel.classList.contains('prog-target')) {
      var it = byId[sel.closest('article[data-item]').getAttribute('data-item')];
      if (it) retarget([it], sel.value);
    } else if (sel.classList.contains('prog-unit-target') && sel.value) {
      var section = sel.closest('section');
      if (section && units[section.id]) retarget(units[section.id].items, sel.value);
    }
  });

  /* ---- progress.json: the same shape as ProgressFile in contracts/progress.py ---- */
  function say2(text) { if (total) total.msg.textContent = text; say(text); }
  function entry(it) {
    var r = record(it.id);
    var e = { item_id: it.id, passes_done: r.p, target_passes: target(it.id), updated_at: r.u || new Date().toISOString() };
    if (r.l) e.last_pass_at = r.l;
    return e;
  }
  function exportFile() {
    var list = [];
    items.forEach(function (it) {
      var r = record(it.id);
      if (r.p <= 0 && !r.t) return;
      list.push(entry(it));
    });
    var data = { version: 1, predmet: cfg.predmet || document.title, exported_at: new Date().toISOString(), items: list };
    var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'napredak-' + SM.key.replace(/^sm:/, '') + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    say2('Napredak je spremljen u datoteku ' + a.download + ' (' + list.length + ' pitanja).');
  }
  function stamp(text) { var t = Date.parse(text); return isNaN(t) ? 0 : t; }
  SM.mergeProgress = function (data) {
    /* returns { applied, older, unknown } or a string with the reason it was refused */
    if (!data || typeof data !== 'object' || data.version !== 1 || !Array.isArray(data.items)) return 'To nije datoteka napretka ove skripte.';
    if (cfg.predmet && data.predmet && data.predmet !== cfg.predmet) return 'Datoteka je za drugi predmet (' + data.predmet + ').';
    var out = { applied: 0, older: 0, unknown: 0 };
    data.items.forEach(function (x) {
      var it = x && byId[x.item_id];
      var p = Math.floor(+(x && x.passes_done)), t = Math.floor(+(x && x.target_passes));
      if (!it) { out.unknown++; return; }
      if (!(p >= 0) || !(t >= 1 && t <= MAX_TARGET)) { out.unknown++; return; }
      var mine = record(it.id);
      if (stamp(x.updated_at) <= stamp(mine.u) && mine.u) { out.older++; return; }      /* the newer change wins, item by item */
      store.items[it.id] = {
        p: Math.min(p, MAX_PASSES), t: t === DEFAULT ? 0 : t,
        l: typeof x.last_pass_at === 'string' ? x.last_pass_at : '', u: typeof x.updated_at === 'string' ? x.updated_at : new Date().toISOString()
      };
      if (store.items[it.id].p <= 0 && !store.items[it.id].t) delete store.items[it.id];
      out.applied++;
    });
    save();
    drawAll();
    return out;
  };
  if (total) {
    var file = total.row.querySelector('.prog-file');
    total.row.querySelector('.prog-save').addEventListener('click', exportFile);
    total.row.querySelector('.prog-open').addEventListener('click', function () { file.click(); });
    file.addEventListener('change', function () {
      var f = file.files && file.files[0];
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () {
        var data = null;
        try { data = JSON.parse(String(reader.result)); } catch (err) { data = null; }
        var res = SM.mergeProgress(data);
        if (typeof res === 'string') say2(res);
        else say2('Učitano: ' + res.applied + ' ' + SM.plural(res.applied, 'pitanje', 'pitanja') + (res.older ? ', ' + res.older + ' ostavljeno jer su novija u ovom pregledniku' : '') + (res.unknown ? ', ' + res.unknown + ' preskočeno' : '') + '.');
        file.value = '';
      };
      reader.onerror = function () { say2('Datoteka se nije mogla pročitati.'); };
      reader.readAsText(f);
    });
  }

  /* ---- the app next to this page: only when this script is shown in the app's preview frame. Changes are passed up to
     the app, which keeps them; the app answers with what it already knows (and the goal it uses for the subject).
     A downloaded file, or one opened by itself, has no parent page and does none of this. ---- */
  var app = null;
  try { if (window.parent && window.parent !== window) app = window.parent; } catch (err) { app = null; }
  function tell(kind, extra) {
    if (!app) return;
    var message = { sm: 'progress', kind: kind, predmet: cfg.predmet || '', default: DEFAULT };
    if (extra) message.items = extra;
    try { app.postMessage(message, '*'); } catch (err) { /* the page next to us went away */ }
  }
  function push(list) { tell('change', list.map(entry)); }
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (!app || e.source !== app || !m || m.sm !== 'progress' || m.kind !== 'state' || !m.state) return;
    var goal = Math.floor(+m.state.default);
    if (goal >= 1 && goal <= MAX_TARGET) DEFAULT = goal;
    var res = SM.mergeProgress({ version: 1, predmet: cfg.predmet || m.state.predmet, items: m.state.items });
    if (typeof res !== 'string') drawAll();
  });

  window.addEventListener('storage', function (e) { if (e.key === SM.key + ':prog') { load(); drawAll(); } });
  load();
  drawAll();
  tell('hello');
})();
