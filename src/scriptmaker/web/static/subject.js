/* Subject page: say what the automatic saving did, show or hide the "most asked" setting, and let the units list be
   reloaded. Without this file the forms still save through their own (noscript) buttons. */
(function () {
  var region = document.getElementById('cjeline-region');
  var skeleton = document.getElementById('units-skeleton');
  var clearTimer = null;

  function statusOf(form) { return form.querySelector('[data-status]'); }

  function say(form, text, keep) {
    var out = statusOf(form);
    if (!out) return;
    out.textContent = text;
    out.classList.toggle('is-error', !!keep);
    window.clearTimeout(form._sayTimer);
    if (text && !keep) form._sayTimer = window.setTimeout(function () { out.textContent = ''; }, 2500);
  }

  document.body.addEventListener('htmx:beforeRequest', function (e) {
    var form = e.detail.elt;
    if (form && form.hasAttribute && form.hasAttribute('data-autosave')) say(form, 'Spremam…', true);
  });

  document.body.addEventListener('htmx:afterRequest', function (e) {
    var form = e.detail.elt;
    if (!form || !form.hasAttribute || !form.hasAttribute('data-autosave')) return;
    if (e.detail.successful) say(form, 'Spremljeno', false);
    else say(form, 'Nije spremljeno. Pokušaj ponovno.', true);
  });

  /* the units list answers 503 when the database cannot be read: show that calm message instead of dropping it */
  document.body.addEventListener('htmx:beforeSwap', function (e) {
    if (region && e.detail.target === region && e.detail.xhr.status === 503) e.detail.shouldSwap = true;
  });
  document.body.addEventListener('htmx:sendError', function () {
    if (!region) return;
    region.innerHTML =
      '<div class="notice notice-error" role="alert"><h2>Cjeline se nisu učitale</h2>' +
      '<p>Aplikacija ne odgovara. Provjeri je li još pokrenuta u svom prozoru, pa pokušaj ponovno.</p>' +
      '<button class="btn" type="button" data-retry>Pokušaj ponovno</button></div>';
  });

  if (region && skeleton && window.htmx) {
    region.addEventListener('click', function (e) {
      if (!(e.target.closest && e.target.closest('[data-retry]'))) return;
      region.innerHTML = skeleton.innerHTML;
      window.htmx.ajax('GET', region.getAttribute('data-source'), { target: region, swap: 'innerHTML' });
    });
  }

  /* the subject's goal changed: say why in words at once, then (after it is saved) redraw the bars of the lessons,
     keeping the lessons that were open */
  function plural(n, one, few, many) {
    var m = Math.abs(n);
    if (m % 10 === 1 && m % 100 !== 11) return one;
    if (m % 10 >= 2 && m % 10 <= 4 && !(m % 100 >= 12 && m % 100 <= 14)) return few;
    return many;
  }
  function goalWords(hint, value) {
    var count = parseInt(hint.getAttribute('data-count'), 10) || 0;
    if (value !== 'auto') {
      var n = parseInt(value, 10);
      return 'Postavljeno ručno: ' + n + ' ' + plural(n, 'prolaz', 'prolaza', 'prolaza');
    }
    var steps = (hint.getAttribute('data-steps') || '').split(',').filter(Boolean).map(function (s) { return s.split(':').map(Number); });
    var goal = 5;
    for (var i = 0; i < steps.length; i++) if (count <= steps[i][0]) { goal = steps[i][1]; break; }
    return 'Automatski: ' + count + ' ' + plural(count, 'pitanje', 'pitanja', 'pitanja') + ' → ' + goal + ' ' + plural(goal, 'prolaz', 'prolaza', 'prolaza');
  }
  var goalChanged = false;
  function reloadUnits() {
    if (!region || !window.htmx) return;
    var open = Array.prototype.map.call(region.querySelectorAll('details.unit[open][data-no]'), function (d) { return d.getAttribute('data-no'); });
    window.htmx.ajax('GET', region.getAttribute('data-source'), { target: region, swap: 'innerHTML' }).then(function () {
      open.forEach(function (no) {
        var d = region.querySelector('details.unit[data-no="' + no + '"]');
        if (d) d.open = true;
      });
    });
  }
  document.body.addEventListener('htmx:afterRequest', function (e) {
    var form = e.detail.elt;
    if (goalChanged && form && form.id === 'prefs-form') {
      goalChanged = false;
      if (e.detail.successful) reloadUnits();
    }
  });

  /* a star rating shows its number next to the stars */
  document.addEventListener('change', function (e) {
    var input = e.target;
    if (!input.closest) return;
    if (input.name === 'default_target_passes') {
      var hint = document.querySelector('[data-goal-reason]');
      if (hint) hint.textContent = goalWords(hint, input.value);
      goalChanged = true;
    }
    var rel = input.closest('.rel');
    if (rel && input.name === 'reliability') {
      var out = rel.querySelector('[data-rel-value]');
      if (out) out.textContent = input.value + ' od 5';
    }
    if (input.name === 'focus') {
      var row = document.querySelector('[data-when-focus]');
      if (row) row.hidden = input.value !== row.getAttribute('data-when-focus');
    }
  });
  /* a link to something inside a folded panel (for example #h-cjeline from a progress-file redirect) opens that panel */
  function openTarget() {
    if (location.hash.length < 2) return;
    var el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    var panel = el && el.closest && el.closest('details');
    if (panel) { panel.open = true; el.scrollIntoView(); }
  }
  openTarget();
  window.addEventListener('hashchange', openTarget);
})();
