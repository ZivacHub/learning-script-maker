/* Text fixing screen: text boxes grow with their text, the page picture zooms, Ctrl+Enter saves. Without this file the
   form still saves with its buttons. */
(function () {
  var form = document.getElementById('oe-form');
  if (!form) return;

  function fit(area) {
    area.style.height = 'auto';
    area.style.height = (area.scrollHeight + 2) + 'px';
  }
  Array.prototype.forEach.call(form.querySelectorAll('textarea[data-autosize]'), function (area) {
    fit(area);
    area.addEventListener('input', function () { fit(area); });
  });

  form.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      var save = form.querySelector('button[value="spremi"]');
      if (save) save.click();
    }
  });

  var img = document.getElementById('oe-img');
  var zoom = 100;
  document.addEventListener('click', function (e) {
    var button = e.target.closest && e.target.closest('[data-zoom]');
    if (!button || !img) return;
    var how = button.getAttribute('data-zoom');
    zoom = how === 'fit' ? 100 : Math.max(50, Math.min(400, zoom + (how === 'in' ? 25 : -25)));
    img.style.width = zoom + '%';
    img.style.maxWidth = zoom > 100 ? 'none' : '100%';
  });
})();
