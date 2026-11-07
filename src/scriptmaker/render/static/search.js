/* Search and the "only questions without an answer" filter for the question lists in Gradivo. */
(function () {
  'use strict';
  var box = document.getElementById('search');
  var chip = document.getElementById('f-none');
  var status = document.getElementById('search-status');
  if (!box || !chip) return;

  var rows = Array.prototype.slice.call(document.querySelectorAll('#gradivo ol.index li'));
  var groups = Array.prototype.slice.call(document.querySelectorAll('#gradivo .group'));
  var text = rows.map(function (li) { return SM.norm(li.textContent); });
  var onlyNone = false;

  function apply() {
    var query = box.value || '';
    var shown = 0;
    rows.forEach(function (li, i) {
      var ok = (!query.trim() || SM.matches(text[i], query)) && (!onlyNone || li.getAttribute('data-st') === 'n');
      li.classList.toggle('hidden', !ok);
      if (ok) shown++;
    });
    groups.forEach(function (g) {
      g.hidden = !g.querySelector('ol.index li:not(.hidden)');
    });
    var filtered = onlyNone || query.trim() !== '';
    if (!filtered) status.textContent = '';
    else if (!shown) status.textContent = 'Nijedno pitanje ne odgovara.';
    else status.textContent = 'Prikazano ' + shown + ' od ' + rows.length + ' pitanja.';
  }

  box.addEventListener('input', apply);
  chip.addEventListener('click', function () {
    onlyNone = !onlyNone;
    chip.setAttribute('aria-pressed', onlyNone ? 'true' : 'false');
    chip.textContent = onlyNone ? 'Prikaži sva pitanja' : 'Prikaži samo bez odgovora';
    apply();
  });
})();
