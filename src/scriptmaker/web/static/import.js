/* Import wizard, confirm table: the role chosen for a whole folder is copied to every file in it, and the folder's
   choice shows "Različito" as soon as its files differ. Without this file every file still has its own choice. */
(function () {
  var groups = document.querySelectorAll('details.group');
  Array.prototype.forEach.call(groups, function (group) {
    var all = group.querySelector('[data-group-role]');
    var files = group.querySelectorAll('[data-file-role]');
    if (!all || !files.length) return;

    all.addEventListener('change', function () {
      Array.prototype.forEach.call(files, function (select) { select.value = all.value; });
    });
    Array.prototype.forEach.call(files, function (select) {
      select.addEventListener('change', function () {
        var first = files[0].value;
        var same = Array.prototype.every.call(files, function (f) { return f.value === first; });
        all.value = same ? first : 'mixed';
      });
    });
  });
})();
