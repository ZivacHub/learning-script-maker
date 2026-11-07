/* "Pregled gradiva" page: search and the "only cards without an answer" filter. Needs script.js (helper object SM). */
(function () {
  'use strict';
  var box = document.getElementById('search');
  var chip = document.getElementById('f-none');
  var status = document.getElementById('search-status');
  if (!box || !chip) return;

  var cards = Array.prototype.slice.call(document.querySelectorAll('article.card'));
  var text = cards.map(function (c) { return SM.norm(c.textContent); });
  var onlyNone = false;

  function apply() {
    var query = box.value || '';
    var shown = 0;
    cards.forEach(function (c, i) {
      var ok = (!query.trim() || SM.matches(text[i], query)) && (!onlyNone || c.getAttribute('data-st') === 'n');
      c.hidden = !ok;
      if (ok) shown++;
    });
    SM.each(document.querySelectorAll('.ov-group'), function (g) { g.hidden = !g.querySelector('article.card:not([hidden])'); });
    SM.each(document.querySelectorAll('.ov-sec'), function (s) { s.hidden = !s.querySelector('article.card:not([hidden])'); });
    var filtered = onlyNone || query.trim() !== '';
    if (!filtered) status.textContent = '';
    else if (!shown) status.textContent = 'Nijedna kartica ne odgovara.';
    else status.textContent = 'Prikazano ' + shown + ' od ' + cards.length + ' kartica.';
  }

  box.addEventListener('input', apply);
  chip.addEventListener('click', function () {
    onlyNone = !onlyNone;
    chip.setAttribute('aria-pressed', onlyNone ? 'true' : 'false');
    chip.textContent = onlyNone ? 'Prikaži sve kartice' : 'Prikaži samo bez odgovora';
    apply();
  });
})();
