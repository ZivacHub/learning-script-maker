/* A form with data-confirm asks "are you sure?" before it is sent. Without this file the form is simply sent. */
(function () {
  document.addEventListener('submit', function (e) {
    var message = e.target.getAttribute && e.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)) e.preventDefault();
  });
})();
