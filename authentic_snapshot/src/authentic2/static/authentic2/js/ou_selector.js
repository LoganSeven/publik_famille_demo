$(function () {
  var $ou_selector = $('#id_ou');
  var cache_key = 'a2_login_ou';
  var $form = $ou_selector.parents('form');
  var last_selection = localStorage.getItem(cache_key);
  if (last_selection) {
      $ou_selector.val(last_selection);
  }
  $form.on('submit', function () {
      var value = $ou_selector.val();
      if (value) {
          localStorage.setItem(cache_key, value);
      }
  });
});
