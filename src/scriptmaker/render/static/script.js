/* Study-script behaviour. Plain JavaScript, no dependencies, no network.
   This first file also defines the small helper object `SM` that nav.js, search.js, favorites.js and exams.js use. */
(function () {
  'use strict';
  var root = document.documentElement;
  var KEY = window.__SM_KEY || 'sm';

  function safe(fn) { try { return fn(); } catch (e) { return null; } }

  window.SM = {
    key: KEY,
    root: root,
    each: function (list, fn) { Array.prototype.forEach.call(list, fn); },
    /* lower case, no diacritics: "čuva" is found by typing "cuva" */
    norm: function (s) {
      return String(s || '').toLowerCase().replace(/đ/g, 'd').normalize('NFD').replace(/[̀-ͯ]/g, '');
    },
    /* every typed word must occur in the text */
    matches: function (haystack, query) {
      var words = SM.norm(query).trim().split(/\s+/).filter(Boolean);
      return words.every(function (w) { return haystack.indexOf(w) !== -1; });
    },
    get: function (name) { return safe(function () { return localStorage.getItem(KEY + ':' + name); }); },
    set: function (name, value) { safe(function () { localStorage.setItem(KEY + ':' + name, value); }); },
    getJSON: function (name, fallback) {
      var raw = SM.get(name);
      if (!raw) return fallback;
      return safe(function () { return JSON.parse(raw); }) || fallback;
    },
    setJSON: function (name, value) { SM.set(name, JSON.stringify(value)); },
    /* same word with the right ending: SM.plural(3, 'favorit', 'favorita') */
    plural: function (n, one, many) { return (n % 10 === 1 && n % 100 !== 11) ? one : many; }
  };

  /* ---- formulas: KaTeX turns each .tex element's text into typeset maths; on any failure the LaTeX stays visible.
     Typesetting takes about 1 ms per formula (several times that on a phone), so a long script would freeze on load.
     Formulas are therefore typeset when they come near the screen; SM.typeset(scope) does a part of the page at
     once (used for cards copied by the favourites page) and printing typesets everything first. */
  /* Wide content scrolls sideways (formulas, code, long lines). A keyboard user must be able to reach it, and a
     screen reader needs a name for it, so each one that really overflows becomes a labelled, focusable group. */
  var SCROLLERS = [['.tex-display', 'Formula'], ['.pane pre', 'Kôd'], ['.verbatim, .own-block', 'Odgovor']];
  var scanPending = false;
  function scanScrollRegions() {
    scanPending = false;
    SCROLLERS.forEach(function (pair) {
      SM.each(document.querySelectorAll(pair[0]), function (el) {
        var wide = el.scrollWidth > el.clientWidth + 1;
        var mine = el.getAttribute('data-scroll') === '1';
        if (wide && !mine && !el.querySelector('a[href], button, summary, input, [tabindex]') && !el.hasAttribute('tabindex')) {
          el.setAttribute('tabindex', '0');
          el.setAttribute('role', 'group');
          el.setAttribute('aria-label', pair[1] + ' (pomicanje lijevo i desno)');
          el.setAttribute('data-scroll', '1');
        } else if (!wide && mine) {
          el.removeAttribute('tabindex');
          el.removeAttribute('role');
          el.removeAttribute('aria-label');
          el.removeAttribute('data-scroll');
        }
      });
    });
  }
  SM.scanScroll = function () {
    if (scanPending) return;
    scanPending = true;
    (window.requestAnimationFrame || window.setTimeout)(scanScrollRegions);
  };
  window.addEventListener('resize', SM.scanScroll);
  document.addEventListener('sm:mode', SM.scanScroll);
  document.addEventListener('toggle', SM.scanScroll, true);   /* a fold-out opened */
  window.addEventListener('load', SM.scanScroll);

  function typesetOne(el) {
    if (el.classList.contains('ok') || el.classList.contains('tex-fail')) return;
    var source = el.textContent;
    try {
      katex.render(source, el, {
        displayMode: el.classList.contains('tex-display'),
        throwOnError: true,
        strict: 'ignore',
        trust: false
      });
      el.classList.add('ok');
    } catch (e) {
      el.textContent = source;            /* keep the raw LaTeX */
      el.classList.add('tex-fail');
      el.title = 'Formula se nije mogla prikazati; prikazan je izvorni zapis.';
    }
    SM.scanScroll();
  }
  SM.typeset = function (scope) {
    if (typeof katex === 'undefined') return;
    SM.each((scope || document).querySelectorAll('.tex'), typesetOne);
    SM.scanScroll();
  };
  (function () {
    if (typeof katex === 'undefined') return;
    var nodes = document.querySelectorAll('.tex');
    if (!('IntersectionObserver' in window)) { SM.typeset(); return; }
    var watcher = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        watcher.unobserve(entry.target);
        typesetOne(entry.target);
      });
    }, { rootMargin: '700px 0px 700px 0px' });
    SM.each(nodes, function (el) { watcher.observe(el); });
  })();
  window.addEventListener('beforeprint', function () { SM.typeset(); });

  /* ---- light / dark switch */
  var themeBtn = document.getElementById('theme-btn');
  function isDark() {
    var t = root.getAttribute('data-theme');
    return t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var next = isDark() ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      SM.set('theme', next);
    });
  }

  /* ---- fold-out hint boxes open for printing, then return to how the reader left them */
  var wasOpen = [];
  window.addEventListener('beforeprint', function () {
    var hints = document.querySelectorAll('details.hint, details.more, .ex-q details');
    wasOpen = Array.prototype.map.call(hints, function (d) { return d.open; });
    SM.each(hints, function (d) { d.open = true; });
  });
  window.addEventListener('afterprint', function () {
    SM.each(document.querySelectorAll('details.hint, details.more, .ex-q details'), function (d, i) { d.open = !!wasOpen[i]; });
  });

  /* ---- back to top */
  var top = document.getElementById('top-btn');
  if (top) {
    top.addEventListener('click', function () {
      var start = document.getElementById('vrh');
      if (start) start.scrollIntoView({ block: 'start' }); else window.scrollTo({ top: 0 });
    });
    var onScroll = function () { top.classList.toggle('show', window.scrollY > 700); };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }
})();
