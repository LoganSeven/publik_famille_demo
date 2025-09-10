$(function() {
  $(window).on('popstate', function(event) {
    var data = event.state;
    $('#services > ul > li.active').removeClass('active');
  });
  $(window).on('hashchange', function(event) {
    if (location.hash && location.hash.length > 1) {
      $('#services > ul > li').removeClass('active').addClass('inactive');
      $(location.hash).removeClass('inactive').addClass('active');
      $('#services').addClass('active');
    } else {
      $('#services > ul > li').removeClass('active').removeClass('inactive');
      $('#services').removeClass('active');
    }
  });
  $(window).on('load', function(event) {
    if (location.hash && location.hash.length > 1) {
      $(window).trigger('hashchange');
    }
  });

  $('#top h1').click(function(e) {
    window.location = $(this).find('a').attr('href');
  });
  $('a#menu').click(function() { $('#nav-user').hide(); $('#nav-site').toggle('slide'); });
  $('a#gear').click(function() { $('#nav-site').hide(); $('#nav-user').toggle('slide'); });
  $('#services ul strong').click(function() {
    var is_already_active = $(this).parent().hasClass('active');
    var title = $(this).text();
    $('#services > ul > li').removeClass('active');
    if (is_already_active) {
      $('#services > ul > li').removeClass('inactive');
      $('#services').removeClass('active');
      history.pushState(document.title, document.title, '#');
    } else {
      history.pushState(document.title, document.title, '#');
      history.pushState(title, title, '#'+$(this).parent().attr('id'));
      $('#services > ul > li').addClass('inactive');
      $(this).parent().removeClass('inactive').addClass('active');
      $('#services').addClass('active');
    }
    return true;
  });
});
