/* Ispiti tab: search and star filters over the exam questions, and the "znam" ticks on the predicted sheet. */
(function () {
  'use strict';
  var sheet = document.getElementById('pe-sheet');
  var lessons = document.getElementById('ex-lessons');
  if (!sheet || !lessons) return;

  /* ---- predicted exam: ticks are remembered in this browser */
  var known = SM.getJSON('znam', {});
  var progress = document.getElementById('pe-prog');
  var items = Array.prototype.slice.call(sheet.querySelectorAll('.pe-item'));

  function paintTicks() {
    var done = 0;
    items.forEach(function (it) {
      var on = !!known[it.getAttribute('data-k')];
      it.classList.toggle('done', on);
      it.querySelector('input').checked = on;
      if (on) done++;
    });
    if (progress) progress.textContent = 'Znam ' + done + ' / ' + items.length;
  }
  sheet.addEventListener('change', function (e) {
    var it = e.target.closest('.pe-item');
    if (!it) return;
    var k = it.getAttribute('data-k');
    if (e.target.checked) known[k] = 1; else delete known[k];
    SM.setJSON('znam', known);
    paintTicks();
  });
  var reset = document.getElementById('pe-reset');
  if (reset) reset.addEventListener('click', function () { known = {}; SM.setJSON('znam', known); paintTicks(); });
  window.addEventListener('storage', function (e) {
    if (e.key !== SM.key + ':znam') return;
    known = SM.getJSON('znam', {});
    paintTicks();
  });
  paintTicks();

  /* ---- all exam questions: search + star filter */
  var search = document.getElementById('ex-search');
  var cards = Array.prototype.slice.call(lessons.querySelectorAll('.ex-q'));
  var text = cards.map(function (c) { return SM.norm(c.textContent); });
  var filters = document.querySelectorAll('#ispiti .ex-f');
  var min = 1;

  function apply() {
    var query = search ? search.value : '';
    cards.forEach(function (c, i) {
      var ok = (+c.getAttribute('data-n') >= min) && (!query.trim() || SM.matches(text[i], query));
      c.classList.toggle('ex-hide', !ok);
    });
    SM.each(lessons.querySelectorAll('.ex-les'), function (d) {
      d.classList.toggle('ex-hide', !d.querySelector('.ex-q:not(.ex-hide)'));
    });
  }
  if (search) search.addEventListener('input', apply);
  SM.each(filters, function (b) {
    b.addEventListener('click', function () {
      SM.each(filters, function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
      min = +b.getAttribute('data-min');
      apply();
    });
  });
})();
