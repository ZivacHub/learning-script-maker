/* Review screen: one-key review, zoom of the page picture, "select words on the page to use them", confirm before
   deleting. Without this file every action still works with its own button. */
(function () {
  var root = document.querySelector('[data-review]');
  if (!root) return;

  function selectedCard() { return root.querySelector('.cand.is-selected') || root.querySelector('.cand'); }

  function typing(el) {
    return el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.isContentEditable);
  }

  function openEditor(card) {
    var details = card && card.querySelector('.cand-edit');
    if (!details) return;
    details.open = true;
    var area = details.querySelector('textarea');
    if (area) { area.focus(); area.setSelectionRange(area.value.length, area.value.length); }
  }

  function go(step) {
    var links = Array.prototype.slice.call(root.querySelectorAll('.rv-list li a'));
    var at = links.findIndex(function (a) { return a.getAttribute('aria-current') === 'true'; });
    var next = links[at + step];
    if (next) window.location.href = next.href;
  }

  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
    var key = e.key.toLowerCase();
    var card = selectedCard();
    if (key === 'a') {
      var accept = card && card.querySelector('[data-act="accept"]');
      if (accept) { e.preventDefault(); accept.click(); }
    } else if (key === 'e') {
      e.preventDefault(); openEditor(card);
    } else if (key === 'o') {
      var own = root.querySelector('.own-edit');
      if (own) { e.preventDefault(); own.open = true; var t = own.querySelector('textarea'); if (t) t.focus(); }
    } else if (key === 'n') {
      var none = root.querySelector('[data-act="none"]');
      if (none) { e.preventDefault(); none.click(); }
    } else if (key === 'arrowdown' || key === 'j') {
      e.preventDefault(); go(1);
    } else if (key === 'arrowup' || key === 'k') {
      e.preventDefault(); go(-1);
    } else if (key >= '1' && key <= '9') {
      var link = root.querySelector('.cand[data-rank="' + key + '"] .cand-pick');
      if (link) { e.preventDefault(); window.location.href = link.href; }
    }
  });

  /* words selected with the mouse on a page text become the text of that page's editor */
  root.addEventListener('mouseup', function (e) {
    var box = e.target.closest && e.target.closest('.cand-text');
    if (!box) return;
    var text = String(window.getSelection()).trim();
    if (!text || text.split(/\s+/).length < 3) return;
    var card = box.closest('.cand');
    var details = card.querySelector('.cand-edit');
    var area = details && details.querySelector('textarea');
    if (!area) return;
    area.value = text;
    details.open = true;
    var note = details.querySelector('.picked');
    if (!note) {
      note = document.createElement('p');
      note.className = 'muted picked';
      note.setAttribute('role', 'status');
      details.querySelector('form').insertBefore(note, area.closest('label').nextSibling);
    }
    note.textContent = 'Uzet je tekst koji si označio. Pritisni „Prihvati ovaj tekst”.';
  });

  /* zoom of the page picture */
  var img = document.getElementById('rv-img');
  var zoom = 100;
  root.addEventListener('click', function (e) {
    var button = e.target.closest && e.target.closest('[data-zoom]');
    if (!button || !img) return;
    var how = button.getAttribute('data-zoom');
    zoom = how === 'fit' ? 100 : Math.max(50, Math.min(400, zoom + (how === 'in' ? 25 : -25)));
    img.style.width = zoom + '%';
    img.style.maxWidth = zoom > 100 ? 'none' : '100%';
  });

  document.addEventListener('submit', function (e) {
    var message = e.target.getAttribute && e.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)) e.preventDefault();
  });

  /* keep the list scrolled to the question being looked at */
  var current = root.querySelector('.rv-list a[aria-current="true"]');
  if (current && current.scrollIntoView) current.scrollIntoView({ block: 'nearest' });
})();
