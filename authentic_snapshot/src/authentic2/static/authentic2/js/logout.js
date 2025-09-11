window.iframe_count = 1;

$(window).on('load', function () {window.iframe_count -= 1});

$(function() {
  var redir_timeout = $('body').data('redir-timeout');
  var next_url = $('body').data('next-url');

  window.iframe_count += document.getElementsByTagName("iframe").length;
  var refresh_launched = 0;
  setInterval(function () {
    if (iframe_count == 0) {
      if (refresh_launched == 0) {
        refresh_launched = 1;
        setTimeout(function () { window.location = next_url; }, 300);
      }
    }
  }, redir_timeout);
});
