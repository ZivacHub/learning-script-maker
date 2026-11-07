/* Dashboard: reload the cards without leaving the page ("Pokušaj ponovno", and when the page comes back from history).
   The page works without this file; it only adds the loading state and the retry. */
(function () {
  var region = document.getElementById('predmeti');
  var skeleton = document.getElementById('subjects-skeleton');
  if (!region || !skeleton || !window.fetch) return;

  var busy = false;

  function reload() {
    if (busy) return;
    busy = true;
    region.innerHTML = skeleton.innerHTML;
    fetch(region.getAttribute('data-source'), { headers: { Accept: 'text/html' }, cache: 'no-store' })
      .then(function (r) { return r.text(); })
      .then(function (html) { region.innerHTML = html; })
      .catch(function () {
        region.innerHTML =
          '<div class="notice notice-error" role="alert"><h2>Predmeti se nisu učitali</h2>' +
          '<p>Aplikacija ne odgovara. Provjeri je li još pokrenuta u svom prozoru, pa pokušaj ponovno.</p>' +
          '<button class="btn" type="button" data-retry>Pokušaj ponovno</button></div>';
      })
      .then(function () { busy = false; });
  }

  region.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('[data-retry]')) reload();
  });
  window.addEventListener('pageshow', function (e) { if (e.persisted) reload(); });
})();
