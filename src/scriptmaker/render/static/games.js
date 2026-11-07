/* Igre: Kartice and Quiz, made in the page itself from the word-for-word answers (D63). Nothing is invented: every
   card and every quiz option is text that already stands in the Gradivo tab. Rounds of 10 questions; a question
   comes back until you have answered it right as often as its rule asks:
     Kartice  Ne znam = 2 right in a row, Nisam siguran/na = 1 more right, Siguran/na sam = done
              (when it comes back: Znam = one more right, Ne znam = from 2 again)
     Quiz     right = done, wrong = the full answer is explained and we move on (no repeat)
   What you have mastered is remembered in this browser only. */
(function () {
  'use strict';
  var host = document.getElementById('games-root');
  if (!host) return;
  var each = SM.each, norm = SM.norm;
  var ROUND = 10;      /* questions in a round */
  var GAP = 3;         /* a question that has to come back appears again after this many others */
  var OPTIONS = 4;     /* quiz choices */

  /* ---- the material: only the questions whose answer is word for word ---- */
  var items = [], byId = {}, units = [], unitIndex = {};
  each(document.querySelectorAll('#gradivo section.lesson'), function (sec) {
    var head = sec.querySelector('.lesson-head h2');
    var u = { id: sec.getAttribute('data-unit'), title: head ? head.textContent.trim() : '', count: 0 };
    unitIndex[u.id] = u;
    units.push(u);
  });
  function words(text) {
    var map = {};
    norm(text).split(/[^a-z0-9]+/).forEach(function (w) { if (w.length > 3) map[w] = true; });
    return map;
  }
  each(document.querySelectorAll('#gradivo article.qa[data-st="s"]'), function (a) {
    var verbatim = a.querySelector('.verbatim');
    var title = a.querySelector('.q h4');
    if (!verbatim || !title) return;
    var q = title.cloneNode(true);
    each(q.querySelectorAll('.exb'), function (x) { x.parentNode.removeChild(x); });
    var first = verbatim.firstElementChild;
    var it = {
      id: a.id, unit: a.getAttribute('data-unit'), no: a.querySelector('.q .num').textContent,
      q: q.innerHTML, article: a, verbatim: verbatim,
      opening: first ? first.innerHTML : verbatim.innerHTML,
      openingKey: norm((first || verbatim).textContent).replace(/\s+/g, ' ').slice(0, 90),
      words: words(verbatim.textContent)
    };
    items.push(it);
    byId[it.id] = it;
    if (unitIndex[it.unit]) unitIndex[it.unit].count++;
  });
  if (!items.length) return;

  /* ---- small helpers ---- */
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function shuffle(list) {
    var a = list.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }
  var saved = SM.getJSON('games', {});
  if (!saved.mastered) saved.mastered = {};
  function save() { SM.setJSON('games', saved); }
  function masteredIn(pool) { return pool.filter(function (it) { return saved.mastered[it.id]; }).length; }
  function poolFor(unit) { return unit === 'all' ? items : items.filter(function (it) { return it.unit === unit; }); }
  function times(n) { return n + ' ' + SM.plural(n, 'put', 'puta'); }

  /* ---- what is on the screen ---- */
  var state = { unit: 'all', game: null, round: null, step: null };

  function show(html, focusSelector) {
    host.innerHTML = html;
    SM.typeset(host);
    var target = focusSelector && host.querySelector(focusSelector);
    if (target) target.focus({ preventScroll: false });
    var tab = document.getElementById('igre');
    if (tab && SM.root.getAttribute('data-mode') === 'igre' && tab.getBoundingClientRect().top < 0) tab.scrollIntoView({ block: 'start' });
  }

  function chooser() {
    state.game = null; state.round = null;
    var pool = poolFor(state.unit);
    var done = masteredIn(pool);
    var options = '<option value="all"' + (state.unit === 'all' ? ' selected' : '') + '>Sve cjeline (' + items.length + ')</option>';
    units.forEach(function (u) {
      if (u.count) options += '<option value="' + esc(u.id) + '"' + (state.unit === u.id ? ' selected' : '') + '>' + esc(u.title) + ' (' + u.count + ')</option>';
    });
    var quizOk = items.length >= OPTIONS;
    show(
      '<p class="g-lead">Pitanja dolaze samo iz odgovora koji su doslovno prepisani iz tvog gradiva: ' + items.length + ' ' + SM.plural(items.length, 'pitanje', 'pitanja') + '. Runda ima do ' + ROUND + ' pitanja, a ona koja ne znaš vraćaju se dok ih ne svladaš.</p>' +
      '<label class="g-field"><span>Cjelina</span><select id="g-unit">' + options + '</select></label>' +
      '<p class="g-mastered"><span>Savladano: <b>' + done + '</b> od <b>' + pool.length + '</b></span>' +
      (done ? ' <button type="button" class="g-link" id="g-reset">Počni ispočetka</button>' : '') + '</p>' +
      '<div class="g-games">' +
      '<button type="button" class="g-game" data-game="kartice"><b>Kartice</b><span>Okreneš karticu i kažeš koliko znaš.</span></button>' +
      '<button type="button" class="g-game" data-game="quiz"' + (quizOk ? '' : ' disabled') + '><b>Quiz</b><span>' + (quizOk ? 'Odabereš pravi početak odgovora od četiri ponuđena.' : 'Treba barem četiri doslovna odgovora.') + '</span></button>' +
      '</div>'
    );
  }

  /* ---- a round ---- */
  function startRound(game) {
    var pool = poolFor(state.unit);
    if (!pool.length) { chooser(); return; }
    var fresh = shuffle(pool.filter(function (it) { return !saved.mastered[it.id]; }));
    var chosen = fresh.length ? fresh.slice(0, ROUND) : shuffle(pool).slice(0, ROUND);
    state.game = game;
    state.round = { queue: chosen.slice(), total: chosen.length, done: 0, st: {}, missed: [] };
    chosen.forEach(function (it) { state.round.st[it.id] = { need: 0, seen: false }; });
    step();
  }

  function head() {
    var r = state.round;
    return '<div class="g-top"><span class="g-count" role="status">Svladano ' + r.done + ' od ' + r.total + '</span>' +
      '<button type="button" class="g-link" id="g-end">Završi rundu</button></div>' +
      '<div class="g-bar" aria-hidden="true"><i style="width:' + Math.round(100 * r.done / r.total) + '%"></i></div>';
  }
  function where(it) {
    var u = unitIndex[it.unit];
    return '<p class="g-where">' + esc(u ? u.title : '') + ' · pitanje ' + esc(it.no) + '</p>';
  }
  function againNote(s) {
    return s.seen && s.need > 0 ? '<p class="g-again">Vraća se: još ' + times(s.need) + ' točno zaredom.</p>' : '';
  }
  function sourceLine(it) {
    var src = it.article.querySelector('.answer .src');
    return src ? '<p class="g-src">' + esc(src.textContent.split(' — ')[0].replace(/\.$/, '')) + '</p>' : '';
  }

  function step() {
    var r = state.round;
    if (!r.queue.length) { summary(); return; }
    var it = r.queue[0], s = r.st[it.id];
    state.step = { item: it, revealed: false, answered: false };
    if (state.game === 'kartice') {
      show(head() + '<div class="g-card" tabindex="-1" data-id="' + esc(it.id) + '">' + where(it) + '<h3 class="g-q">' + it.q + '</h3>' + againNote(s) +
        '<div class="g-back" id="g-back" hidden>' + sourceLine(it) + '<div class="verbatim">' + it.verbatim.innerHTML + '</div></div>' +
        '<div class="g-actions" id="g-actions"><button type="button" class="g-btn primary" id="g-flip">Prikaži odgovor</button></div></div>', '#g-flip');
    } else {
      var opts = quizOptions(it);
      var html = head() + '<div class="g-card" tabindex="-1" data-id="' + esc(it.id) + '">' + where(it) + '<h3 class="g-q">' + it.q + '</h3>' + againNote(s) +
        '<p class="g-ask">Koji je početak točnog odgovora?</p><ol class="g-options">';
      opts.forEach(function (o, i) {
        html += '<li><button type="button" class="g-opt" data-id="' + esc(o.id) + '"><span class="g-key" aria-hidden="true">' + (i + 1) + '</span><span class="g-opt-text">' + o.opening + '</span></button></li>';
      });
      html += '</ol><div class="g-feedback" id="g-feedback" role="status" aria-live="polite"></div>' +
        '<div class="g-back" id="g-back" hidden></div><div class="g-actions" id="g-actions"></div></div>';
      show(html, '.g-card');
    }
  }

  function flip() {
    var st = state.step;
    if (!st || st.revealed) return;
    st.revealed = true;
    document.getElementById('g-back').hidden = false;
    var s = state.round.st[st.item.id];
    var actions = document.getElementById('g-actions');
    actions.innerHTML = s.seen
      ? '<button type="button" class="g-btn" data-rate="ne">Ne znam</button><button type="button" class="g-btn primary" data-rate="znam">Znam</button>'
      : '<button type="button" class="g-btn" data-rate="ne">Ne znam</button><button type="button" class="g-btn" data-rate="nisam">Nisam siguran/na</button><button type="button" class="g-btn primary" data-rate="siguran">Siguran/na sam</button>';
    var first = actions.querySelector('button');
    if (first) first.focus();
    SM.typeset(document.getElementById('g-back'));
  }

  /* one answer is done: set what the question still needs and move on */
  function settle(right, level) {
    var r = state.round, it = state.step.item, s = r.st[it.id];
    if (right) {
      if (!s.seen && level === 'nisam') s.need = 1;
      else if (!s.seen) s.need = 0;
      else s.need = Math.max(0, s.need - 1);
    } else {
      s.need = 2;
      if (r.missed.indexOf(it.id) === -1) r.missed.push(it.id);
    }
    s.seen = true;
    r.queue.shift();
    if (s.need > 0) {
      r.queue.splice(Math.min(GAP, r.queue.length), 0, it);
    } else {
      r.done++;
      saved.mastered[it.id] = true;
      save();
    }
  }

  /* quiz, wrong answer: the full answer is shown and we move on; it does not come back in this round, is not counted
     as mastered, and is listed at the end (it can show up again in a later round) */
  function passOver() {
    var r = state.round, it = state.step.item;
    r.st[it.id].seen = true;
    if (r.missed.indexOf(it.id) === -1) r.missed.push(it.id);
    r.queue.shift();
  }

  function rate(level) {
    var st = state.step;
    if (!st || !st.revealed || st.answered) return;
    st.answered = true;
    if (level === 'ne') settle(false);
    else if (level === 'nisam') settle(true, 'nisam');
    else settle(true, level);
    step();
  }

  /* ---- quiz: the right answer and three answers of other questions of the same lesson, the most similar first ---- */
  function quizOptions(it) {
    var seen = {}; seen[it.openingKey] = true;
    var same = items.filter(function (o) { return o !== it && o.unit === it.unit; });
    var other = items.filter(function (o) { return o !== it && o.unit !== it.unit; });
    function rank(list) {
      return list.map(function (o) {
        var shared = 0;
        Object.keys(o.words).forEach(function (w) { if (it.words[w]) shared++; });
        return { o: o, score: shared + Math.random() * 2 };
      }).sort(function (a, b) { return b.score - a.score; }).map(function (x) { return x.o; });
    }
    var picked = [];
    rank(same).concat(shuffle(other)).forEach(function (o) {
      if (picked.length >= OPTIONS - 1 || seen[o.openingKey]) return;
      seen[o.openingKey] = true;
      picked.push(o);
    });
    return shuffle(picked.concat([it]));
  }

  function tag(text) {
    var span = document.createElement('span');
    span.className = 'g-tag';
    span.textContent = text;
    return span;
  }

  function choose(id) {
    var st = state.step;
    if (!st || st.answered) return;
    st.answered = true;
    var right = id === st.item.id;
    each(host.querySelectorAll('.g-opt'), function (b) {
      b.disabled = true;
      var isRight = b.getAttribute('data-id') === st.item.id;
      var mine = b.getAttribute('data-id') === id;
      if (isRight) { b.classList.add('is-right'); b.appendChild(tag('Točan odgovor')); }
      else if (mine) { b.classList.add('is-wrong'); b.appendChild(tag('Tvoj odgovor, netočno')); }
    });
    document.getElementById('g-feedback').textContent = right ? 'Točno.' : 'Netočno. Evo cijelog odgovora.';
    var back = document.getElementById('g-back');
    back.innerHTML = sourceLine(st.item) + '<div class="verbatim">' + st.item.verbatim.innerHTML + '</div>';
    back.hidden = false;
    SM.typeset(back);
    if (right) settle(true);
    else passOver();
    var actions = document.getElementById('g-actions');
    actions.innerHTML = '<button type="button" class="g-btn primary" id="g-next">Dalje</button>';
    document.getElementById('g-next').focus();
  }

  function summary() {
    var r = state.round, pool = poolFor(state.unit);
    var left = pool.length - masteredIn(pool);
    var html = '<div class="g-card g-summary" tabindex="-1"><h3 class="g-q">Runda je gotova</h3>' +
      '<p>Svladano ' + r.done + ' od ' + r.total + ' ' + 'pitanja' + '.</p>';
    if (r.missed.length) {
      html += '<p>Ova su ti zadala posla:</p><ul class="g-missed">';
      r.missed.forEach(function (id) { html += '<li><span class="g-num">' + esc(byId[id].no) + '</span> ' + byId[id].q + '</li>'; });
      html += '</ul>';
    } else {
      html += '<p>Nijedno pitanje nisi pogriješio/la.</p>';
    }
    html += '<div class="g-actions">' +
      (left ? '<button type="button" class="g-btn primary" id="g-more">Sljedećih ' + Math.min(ROUND, left) + '</button>' : '<p class="g-lead">Sve u ovom izboru je savladano.</p>') +
      '<button type="button" class="g-btn" id="g-back-to">Natrag na izbor</button></div></div>';
    show(html, '.g-summary');
  }

  /* ---- clicks and keys ---- */
  host.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('button');
    if (!t || t.disabled) return;
    if (t.classList.contains('g-game')) startRound(t.getAttribute('data-game'));
    else if (t.id === 'g-flip') flip();
    else if (t.hasAttribute('data-rate')) rate(t.getAttribute('data-rate'));
    else if (t.classList.contains('g-opt')) choose(t.getAttribute('data-id'));
    else if (t.id === 'g-next') step();
    else if (t.id === 'g-more') startRound(state.game);
    else if (t.id === 'g-back-to' || t.id === 'g-end') chooser();
    else if (t.id === 'g-reset') {
      poolFor(state.unit).forEach(function (it) { delete saved.mastered[it.id]; });
      save();
      chooser();
    }
  });
  host.addEventListener('change', function (e) {
    if (e.target.id === 'g-unit') { state.unit = e.target.value; chooser(); }
  });
  host.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || /input|select|textarea/i.test(e.target.tagName || '')) return;
    var n = parseInt(e.key, 10);
    if (!n) return;
    var buttons = host.querySelectorAll('.g-opt:not([disabled]), #g-actions [data-rate]');
    if (buttons[n - 1]) { e.preventDefault(); buttons[n - 1].click(); }
  });

  chooser();
})();
