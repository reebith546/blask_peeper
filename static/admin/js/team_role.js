// Раздел «Сотрудники»: галочки разделов нужны только продавцу. При выборе
// роли «Директор» прячем блок «Разделы продавца», чтобы форма не путала.
(function () {
  'use strict';

  function rowFor(fieldName) {
    return (
      document.querySelector('.form-row.field-' + fieldName) ||
      document.querySelector('.form-row.field-box.field-' + fieldName) ||
      document.querySelector('.field-' + fieldName)
    );
  }

  function selectedRole() {
    var checked = document.querySelector('input[name="role"]:checked');
    if (checked) return checked.value;
    var select = document.querySelector('select[name="role"], #id_role');
    return select ? select.value : null;
  }

  function sync() {
    var row = rowFor('sections');
    if (!row) return;
    row.style.display = selectedRole() === 'director' ? 'none' : '';
  }

  document.addEventListener('DOMContentLoaded', function () {
    var inputs = document.querySelectorAll('input[name="role"], select[name="role"], #id_role');
    if (!inputs.length) return;
    inputs.forEach(function (el) {
      el.addEventListener('change', sync);
    });
    sync();
  });
})();
