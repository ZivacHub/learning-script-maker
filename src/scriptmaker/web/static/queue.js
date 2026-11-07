/* The "Za provjeru" list loads after the page appears. When it cannot be built the calm message is shown with a retry.
   Without this file the list simply stays on its loading shape. */
(function () {
  var region = document.getElementById('provjera-region');
  var skeleton = document.getElementById('provjera-skeleton');
  if (!region || !skeleton || !window.htmx) return;

  document.body.addEventListener('htmx:beforeSwap', function (e) {
    if (e.detail.target === region && e.detail.xhr.status === 503) e.detail.shouldSwap = true;
  });
  document.body.addEventListener('htmx:sendError', function () {
    region.innerHTML =
      '<div class="notice notice-error" role="alert"><h2>Popis se nije učitao</h2>' +
      '<p>Aplikacija ne odgovara. Provjeri je li još pokrenuta u svom prozoru, pa pokušaj ponovno.</p>' +
      '<button class="btn" type="button" data-retry>Pokušaj ponovno</button></div>';
  });
  region.addEventListener('click', function (e) {
    if (!(e.target.closest && e.target.closest('[data-retry]'))) return;
    region.innerHTML = skeleton.innerHTML;
    window.htmx.ajax('GET', region.getAttribute('data-source'), { target: region, swap: 'innerHTML' });
  });
})();
