/* Tabs (Gradivo / Ispiti / Favoriti) and the "back to the question" button. */
(function () {
  'use strict';
  var root = SM.root;
  var MODES = window.__SM_MODES || ['gradivo'];

  function modeOf(el) {
    if (!el || !el.closest) return null;
    if (el.closest('#ispiti')) return 'ispiti';
    if (el.closest('#favoriti')) return 'favoriti';
    if (el.closest('#igre')) return 'igre';
    if (el.closest('#gradivo')) return 'gradivo';
    return null;
  }
  function targetOf(hash) {
    if (!hash || hash.length < 2) return null;
    try { return document.getElementById(decodeURIComponent(hash.slice(1))); } catch (e) { return null; }
  }
  function current() { return root.getAttribute('data-mode') || 'gradivo'; }

  function paint() {
    var m = current();
    SM.each(document.querySelectorAll('.mode-sw button'), function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-mode') === m ? 'true' : 'false');
    });
    SM.each(document.querySelectorAll('.mode-card'), function (c) {
      if (c.getAttribute('data-mode') === m) c.setAttribute('aria-current', 'page'); else c.removeAttribute('aria-current');
    });
  }
  function setMode(m) {
    if (MODES.indexOf(m) === -1 || m === current()) return;
    root.setAttribute('data-mode', m);
    SM.set('mode', m);
    paint();
    document.dispatchEvent(new CustomEvent('sm:mode', { detail: m }));
  }
  SM.mode = { get: current, set: setMode, of: modeOf };

  /* a link into the page opens in the tab that holds its target */
  var first = targetOf(location.hash);
  if (first && modeOf(first)) {
    root.setAttribute('data-mode', modeOf(first));
    SM.set('mode', modeOf(first));
  }
  paint();
  if (first) setTimeout(function () { first.scrollIntoView({ behavior: 'instant', block: 'start' }); }, 0);  /* instant: a smooth scroll is cancelled while the page is still loading */

  SM.each(document.querySelectorAll('.mode-sw button'), function (b) {
    b.addEventListener('click', function () {
      var m = b.getAttribute('data-mode');
      setMode(m);
      var section = document.getElementById(m);
      if (section) section.scrollIntoView({ block: 'start' });
    });
  });
  window.addEventListener('hashchange', function () {
    var t = targetOf(location.hash);
    var m = modeOf(t);
    if (m && m !== current()) { setMode(m); t.scrollIntoView(); }
  });

  /* ---- the lesson menu on the left (setting "menu"): opens with the button, closes with a click outside, Esc or a link */
  var menuBtn = document.getElementById('menu-btn');
  var menu = document.getElementById('menu');
  var scrim = document.getElementById('menu-scrim');
  if (menuBtn && menu && root.getAttribute('data-menu') === 'side') {
    var setMenu = function (open, back) {
      root.classList.toggle('menu-open', open);
      menuBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { var first = menu.querySelector('.menu-close'); if (first) first.focus({ preventScroll: true }); }
      else if (back) menuBtn.focus({ preventScroll: true });
    };
    menuBtn.addEventListener('click', function () { setMenu(!root.classList.contains('menu-open')); });
    if (scrim) scrim.addEventListener('click', function () { setMenu(false); });
    var closeBtn = menu.querySelector('.menu-close');
    if (closeBtn) closeBtn.addEventListener('click', function () { setMenu(false, true); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && root.classList.contains('menu-open')) { setMenu(false, true); }
    });
    menu.addEventListener('click', function (e) {
      if (e.target.closest && e.target.closest('a.pill')) setMenu(false);
    });
  }

  /* ---- back to the question: remembers where a link was followed from (index, exam list, favourites) */
  var stack = [];
  var ret = document.getElementById('ret-btn');
  var retText = ret && ret.querySelector('.rt');

  function backLinks() {
    SM.each(document.querySelectorAll('a.back'), function (a) {
      a.textContent = stack.length ? '↩ natrag na pitanje' : '↑ natrag na popis pitanja';
    });
  }
  function showRet() {
    backLinks();
    if (!ret) return;
    if (!stack.length) { ret.classList.remove('show'); return; }
    var last = stack[stack.length - 1];
    retText.textContent = last.label;
    ret.setAttribute('data-from', last.mode);
    ret.classList.add('show');
  }
  function goBack() {
    var last = stack.pop();
    if (!last) return;
    setMode(last.mode);
    last.el.scrollIntoView({ block: 'center' });
    last.el.classList.remove('ret-flash');
    void last.el.offsetWidth;                 /* restart the flash if it is already running */
    last.el.classList.add('ret-flash');
    showRet();
  }
  if (ret) ret.addEventListener('click', goBack);

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    if (a.classList.contains('back') && stack.length) { e.preventDefault(); goBack(); return; }
    var target = targetOf(a.getAttribute('href'));
    if (!target) return;
    var m = modeOf(target);
    if (m) setMode(m);
    var article = target.closest('article.qa');
    if (article && !a.closest('article.qa')) {
      var origin = a.closest('li, .ex-q, .pe-item, .fav-card') || a;
      var label = origin.querySelector('.txt, .qt, .t');
      var text = ((label || origin).textContent || '').replace(/★\s*\+\d+/g, '').replace(/\s+/g, ' ').trim();
      stack.push({ el: origin, mode: modeOf(origin) || 'gradivo', label: text });
      if (stack.length > 30) stack.shift();
      showRet();
    }
  }, true);
  showRet();
})();
