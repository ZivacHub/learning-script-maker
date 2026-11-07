/* Build page: say "Izrađujem…" and keep the button from being pressed twice while the script is being made, and
   hand the preview's progress to the app (D40). Without this file the form still works and the preview still runs,
   but what is marked as learned in it is not remembered by the app. */
(function () {
  var form = document.querySelector('form[data-build]');
  if (!form) return;
  form.addEventListener('submit', function () {
    var button = form.querySelector('button');
    var wait = form.querySelector('[data-build-wait]');
    if (wait) wait.hidden = false;
    window.setTimeout(function () { if (button) button.disabled = true; }, 0);
  });
})();

(function () {
  var frame = document.querySelector('iframe.b-preview[data-progress]');
  if (!frame) return;
  var base = frame.getAttribute('data-progress');
  var out = document.getElementById('sync-status');
  var MAX_ITEMS = 5000;

  function say(text, bad) {
    if (!out) return;
    out.textContent = text;
    out.classList.toggle('is-error', !!bad);
  }

  /* the preview is locked (no storage, no address of its own): it asks, and we answer with what the app knows */
  function sendState() {
    fetch(base + '/stanje', { headers: { Accept: 'application/json' }, cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('state'); return r.json(); })
      .then(function (state) {
        if (frame.contentWindow) frame.contentWindow.postMessage({ sm: 'progress', kind: 'state', state: state }, '*');
        say('Povezano s aplikacijom: što označiš kao naučeno sprema se i u aplikaciju.', false);
      })
      .catch(function () { say('Napredak iz aplikacije se nije učitao. Pregled radi, ali ga aplikacija neće zapamtiti.', true); });
  }

  function save(items) {
    fetch(base + '/sinkronizacija', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: items })
    })
      .then(function (r) { if (!r.ok) throw new Error('sync'); return r.json(); })
      .then(function (r) { say('Spremljeno u aplikaciji. Ukupno ' + r.done + ' od ' + r.total + ' prolaza.', false); })
      .catch(function () { say('Nije spremljeno u aplikaciji. Pokušaj ponovno.', true); });
  }

  window.addEventListener('message', function (e) {
    var m = e.data;
    if (e.source !== frame.contentWindow || !m || m.sm !== 'progress') return;
    if (m.kind === 'hello') sendState();
    else if (m.kind === 'change' && Array.isArray(m.items)) save(m.items.slice(0, MAX_ITEMS));
  });
  frame.addEventListener('load', sendState);
})();
