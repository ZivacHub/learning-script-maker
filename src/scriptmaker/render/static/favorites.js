/* Favourites: a heart next to every question (in Gradivo and, when present, Ispiti), and the Favoriti tab that
   lists the chosen questions by lesson with search, filters and their answers. Everything stays in this browser. */
(function () {
  'use strict';
  var root = document.getElementById('favoriti');
  if (!root) return;
  var each = SM.each, norm = SM.norm;
  var HEART = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
  var CHEV = '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  var STATUS_TITLE = {
    s: 'doslovno prepisano iz materijala',
    o: 'vlastiti primjer / izračun po postupku iz materijala',
    n: 'nema u materijalima'
  };

  /* ---- the questions of Gradivo ---- */
  var meta = {}, order = [];
  each(document.querySelectorAll('#gradivo article.qa'), function (a, position) {
    var title = a.querySelector('.q h4').cloneNode(true);
    each(title.querySelectorAll('.exb'), function (x) { x.parentNode.removeChild(x); });
    meta[a.id] = {
      id: a.id,
      unit: a.getAttribute('data-unit'),
      position: position,
      num: a.querySelector('.q .num').textContent,
      html: title.innerHTML,
      text: title.textContent.replace(/\s+/g, ' ').trim(),
      search: norm(title.textContent),
      status: a.getAttribute('data-st') || 's',
      freq: +a.getAttribute('data-freq') || 0,
      answer: null                              /* searchable answer text, read on first use */
    };
    order.push(a.id);
  });

  /* ---- hearts ---- */
  function heart(ids) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'heart';
    b.setAttribute('data-ids', ids.join(' '));
    b.setAttribute('aria-pressed', 'false');
    b.innerHTML = HEART;
    return b;
  }
  each(document.querySelectorAll('#gradivo ol.index li'), function (li) {
    var a = li.querySelector('a[href^="#"]');
    var id = a && a.getAttribute('href').slice(1);
    if (id && meta[id]) li.appendChild(heart([id]));
  });
  each(document.querySelectorAll('#gradivo article.qa'), function (a) {
    if (meta[a.id]) a.querySelector('.q').appendChild(heart([a.id]));
  });
  each(document.querySelectorAll('#ispiti [data-a]'), function (c) {
    var ids = (c.getAttribute('data-a') || '').split(' ').filter(function (id) { return meta[id]; });
    var host = c.querySelector('.anss') || c.querySelector('.r');
    if (ids.length && host) host.appendChild(heart(ids));
  });

  /* ---- storage ---- */
  var favs = {};
  function load() {
    favs = {};
    var saved = SM.getJSON('fav', []);
    if (Array.isArray(saved)) saved.forEach(function (id) { if (meta[id]) favs[id] = 1; });
  }
  function save() { SM.setJSON('fav', Object.keys(favs)); }
  function count() { return Object.keys(favs).length; }

  function syncHearts() {
    each(document.querySelectorAll('.heart[data-ids]'), function (b) {
      var ids = b.getAttribute('data-ids').split(' ');
      var on = ids.every(function (id) { return favs[id]; });
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      var label = (on ? 'Ukloni iz favorita' : 'Dodaj u favorite') + (ids.length > 1 ? ' (vodi na ' + ids.length + ' odgovora)' : '');
      b.title = label;
      b.setAttribute('aria-label', ids.length === 1 && meta[ids[0]] ? label + ': ' + meta[ids[0]].text : label);
    });
  }
  function updateCounts() {
    var perUnit = {};
    Object.keys(favs).forEach(function (id) { perUnit[meta[id].unit] = (perUnit[meta[id].unit] || 0) + 1; });
    var n = count();
    document.getElementById('fav-count').textContent = n ? String(n) : '';
    var card = document.getElementById('fav-mc-n');
    if (card) card.textContent = n ? 'Označeno: ' + n : 'Još ništa nije označeno';
    each(document.querySelectorAll('.pills-f .pill'), function (pill) {
      var section = document.getElementById(pill.getAttribute('href').slice(1));
      var unit = section && section.getAttribute('data-unit');
      pill.querySelector('.fc').textContent = perUnit[unit] ? String(perUnit[unit]) : '';
    });
  }

  /* ---- cards in the Favoriti tab ---- */
  var cards = {};
  function makeCard(id) {
    var m = meta[id];
    var badge = m.freq >= 2 ? '<span class="exb" title="Na ispitima: pojavilo se u ' + m.freq + ' izvora">★ +' + m.freq + '</span>' : '';
    var c = document.createElement('article');
    c.className = 'fav-card';
    c.id = 'fav-' + id;
    c.setAttribute('data-id', id);
    c.innerHTML =
      '<div class="fav-h"><button type="button" class="fav-toggle" aria-expanded="false" aria-controls="favb-' + id + '">' +
      '<span class="num">' + m.num + '</span><span class="txt">' + m.html + badge + '</span>' +
      '<span class="meta"><i class="dot ' + m.status + '" role="img" aria-label="' + STATUS_TITLE[m.status] + '"></i>' + CHEV + '</span></button></div>' +
      '<div class="fav-body" id="favb-' + id + '" hidden></div>';
    c.querySelector('.fav-h').appendChild(heart([id]));
    SM.typeset(c);                                  /* the title may hold formulas that were not typeset yet */
    return c;
  }
  function fillBody(c) {
    var body = c.querySelector('.fav-body');
    if (body.getAttribute('data-filled')) return;
    var id = c.getAttribute('data-id');
    var source = document.querySelector('#' + id + ' .answer');
    if (!source) return;
    var copy = source.cloneNode(true);
    each(copy.querySelectorAll('a.back'), function (x) { x.parentNode.removeChild(x); });
    each(copy.querySelectorAll('[id]'), function (el) { el.removeAttribute('id'); });   /* ids must stay unique */
    body.appendChild(copy);
    SM.typeset(body);                               /* the copy may hold formulas that were not typeset yet */
    var go = document.createElement('a');
    go.className = 'fav-go';
    go.href = '#' + id;
    go.textContent = 'otvori u gradivu →';
    body.appendChild(go);
    body.setAttribute('data-filled', '1');
  }
  function setOpen(c, open) {
    var toggle = c.querySelector('.fav-toggle'), body = c.querySelector('.fav-body');
    if (open) fillBody(c);
    body.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    c.classList.toggle('open', !!open);
  }
  function reconcile() {
    Object.keys(favs).forEach(function (id) { if (!cards[id]) cards[id] = makeCard(id); });
    Object.keys(cards).forEach(function (id) {
      if (!favs[id]) {
        var c = cards[id];
        if (c.parentNode) c.parentNode.removeChild(c);
        delete cards[id];
      }
    });
  }
  /* a heart un-ticked while looking at the tab keeps its card (struck through) until you leave, so it can be undone */
  function markRemoved() {
    Object.keys(cards).forEach(function (id) { cards[id].classList.toggle('removed', !favs[id]); });
  }

  /* ---- search, filters, order ---- */
  var filter = { q: '', inAnswer: false, freq: 0, status: 'all', sort: 'order' };
  function answerText(id) {
    var m = meta[id];
    if (m.answer === null) {
      var a = document.querySelector('#' + id + ' .answer');
      m.answer = norm(a ? a.textContent : '');
    }
    return m.answer;
  }
  function passes(id) {
    var m = meta[id];
    if (m.freq < filter.freq) return false;
    if (filter.status !== 'all' && m.status !== filter.status) return false;
    if (filter.q.trim()) {
      var hay = m.search + (filter.inAnswer ? ' ' + answerText(id) : '');
      if (!SM.matches(hay, filter.q)) return false;
    }
    return true;
  }
  function compare(a, b) {
    if (filter.sort === 'freq') {
      var d = meta[b].freq - meta[a].freq;
      if (d) return d;
    }
    return meta[a].position - meta[b].position;
  }
  function filtersActive() { return !!(filter.q.trim() || filter.freq || filter.status !== 'all'); }
  function noun(n) { return SM.plural(n, 'favorit', 'favorita'); }

  function apply() {
    var active = filtersActive(), shownAll = 0, totalAll = 0;
    each(root.querySelectorAll('.fav-les'), function (section) {
      var unit = section.getAttribute('data-unit');
      var list = section.querySelector('.fav-list');
      var ids = Object.keys(cards).filter(function (id) { return meta[id].unit === unit; }).sort(compare);
      var current = Array.prototype.slice.call(list.children);
      var same = current.length === ids.length && current.every(function (el, i) { return el === cards[ids[i]]; });
      var shown = 0, total = 0, visibleCards = 0;
      ids.forEach(function (id) {
        var ok = passes(id);
        cards[id].classList.toggle('fav-hide', !ok);
        if (ok) visibleCards++;
        if (favs[id]) { total++; if (ok) shown++; }
      });
      if (!same) ids.forEach(function (id) { list.appendChild(cards[id]); });
      var n = section.querySelector('.n'), none = section.querySelector('.fav-none');
      n.textContent = total ? ((active && shown !== total) ? shown + ' od ' + total + ' ' + noun(total) : total + ' ' + noun(total)) : 'nema favorita';
      none.hidden = visibleCards > 0;
      none.textContent = total ? 'Nijedan favorit iz ove lekcije ne odgovara pretrazi ili filterima.'
                               : 'Još nema favorita u ovoj lekciji. Klikni srce uz pitanje da ga dodaš.';
      shownAll += shown;
      totalAll += total;
    });
    var sum = document.getElementById('fav-sum');
    sum.textContent = '';
    if (!totalAll) {
      var span = document.createElement('span');
      span.appendChild(document.createTextNode('Još nemaš favorita. Klikni srce uz pitanje u '));
      var g = document.createElement('a'); g.href = '#gradivo'; g.textContent = 'gradivu';
      span.appendChild(g);
      if (document.getElementById('ispiti')) {
        span.appendChild(document.createTextNode(' ili '));
        var i = document.createElement('a'); i.href = '#ispiti'; i.textContent = 'ispitima';
        span.appendChild(i);
      }
      span.appendChild(document.createTextNode(' i ono će se pojaviti ovdje, razvrstano po lekcijama.'));
      sum.appendChild(span);
    } else {
      var line = document.createElement('span');
      line.innerHTML = 'Prikazano <b>' + shownAll + '</b> od <b>' + totalAll + '</b> favorita';
      sum.appendChild(line);
      if (active) {
        var reset = document.createElement('button');
        reset.type = 'button'; reset.className = 'fav-linkbtn'; reset.id = 'fav-reset';
        reset.textContent = 'Očisti pretragu i filtere';
        sum.appendChild(reset);
      }
    }
  }
  function refresh() { reconcile(); markRemoved(); apply(); syncHearts(); updateCounts(); }  /* cards must be in the page before their hearts are synced */

  function resetFilters() {
    filter = { q: '', inAnswer: false, freq: 0, status: 'all', sort: 'order' };
    document.getElementById('fav-search').value = '';
    document.getElementById('fav-inans').setAttribute('aria-pressed', 'false');
    var defaults = { freq: '0', st: 'all', sort: 'order' };
    each(root.querySelectorAll('button[data-g]'), function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-v') === defaults[b.getAttribute('data-g')] ? 'true' : 'false');
    });
    apply();
  }

  /* ---- events ---- */
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.heart');
    if (!b) return;
    e.preventDefault();
    var ids = b.getAttribute('data-ids').split(' ');
    var on = ids.every(function (id) { return favs[id]; });
    ids.forEach(function (id) { if (on) delete favs[id]; else favs[id] = 1; });
    save(); syncHearts(); updateCounts();
    if (!on) { b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); }
    if (SM.mode.get() === 'favoriti') { markRemoved(); apply(); }
  });

  var armTimer = null;
  var CLEAR_LABEL = 'Ukloni sve favorite';
  root.addEventListener('click', function (e) {
    var toggle = e.target.closest('.fav-toggle');
    if (toggle && root.contains(toggle)) {
      var card = toggle.closest('.fav-card');
      setOpen(card, !card.classList.contains('open'));
      return;
    }
    var b = e.target.closest('button');
    if (!b || !root.contains(b) || b.classList.contains('heart')) return;
    var group = b.getAttribute('data-g');
    if (group) {
      var v = b.getAttribute('data-v');
      each(root.querySelectorAll('button[data-g="' + group + '"]'), function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
      if (group === 'freq') filter.freq = +v; else if (group === 'st') filter.status = v; else if (group === 'sort') filter.sort = v;
      apply();
      return;
    }
    if (b.id === 'fav-inans') { filter.inAnswer = !filter.inAnswer; b.setAttribute('aria-pressed', filter.inAnswer ? 'true' : 'false'); apply(); return; }
    if (b.id === 'fav-reset') { resetFilters(); return; }
    if (b.id === 'fav-open-all' || b.id === 'fav-close-all') {
      var open = b.id === 'fav-open-all';
      Object.keys(cards).forEach(function (id) { if (!cards[id].classList.contains('fav-hide')) setOpen(cards[id], open); });
      return;
    }
    if (b.id === 'fav-clear') {
      if (!count()) return;
      if (b.hasAttribute('data-arm')) {                 /* second press within 3.5 s confirms */
        clearTimeout(armTimer); b.removeAttribute('data-arm'); b.textContent = CLEAR_LABEL;
        favs = {}; save(); refresh();
      } else {
        b.setAttribute('data-arm', '1'); b.textContent = 'Klikni ponovno za potvrdu';
        armTimer = setTimeout(function () { b.removeAttribute('data-arm'); b.textContent = CLEAR_LABEL; }, 3500);
      }
    }
  });
  document.getElementById('fav-search').addEventListener('input', function () { filter.q = this.value || ''; apply(); });

  document.addEventListener('sm:mode', function (e) { if (e.detail === 'favoriti') refresh(); });
  window.addEventListener('storage', function (e) {
    if (e.key !== SM.key + ':fav') return;
    load(); syncHearts(); updateCounts();
    if (SM.mode.get() === 'favoriti') refresh();
  });

  /* ---- printing the Favoriti tab: every answer open, then back as the reader had it ---- */
  var wasOpen = null;
  window.addEventListener('beforeprint', function () {
    if (SM.mode.get() !== 'favoriti') return;
    wasOpen = {};
    Object.keys(cards).forEach(function (id) {
      wasOpen[id] = cards[id].classList.contains('open');
      setOpen(cards[id], true);
      each(cards[id].querySelectorAll('details.hint'), function (d) { d.open = true; });
    });
  });
  window.addEventListener('afterprint', function () {
    if (!wasOpen) return;
    Object.keys(wasOpen).forEach(function (id) { if (cards[id]) setOpen(cards[id], wasOpen[id]); });
    wasOpen = null;
  });

  /* ---- start ---- */
  var noneChip = root.querySelector('button[data-g="st"][data-v="n"]');
  if (noneChip && order.some(function (id) { return meta[id].status === 'n'; })) noneChip.hidden = false;
  load();
  refresh();
})();
