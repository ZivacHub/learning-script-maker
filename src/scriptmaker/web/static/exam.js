/* Exam date and "Aktivan": the form is sent the moment the value changes. Without this file the forms still work with
   their own (noscript) buttons. */
(function () {
  document.addEventListener('change', function (e) {
    var input = e.target;
    var form = input.closest && input.closest('form.exam-date, form.exam-active');
    if (!form || !input.name) return;
    if (input.name === 'exam_date' && !input.value) return;      /* a half-typed date is not sent */
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  });
})();
