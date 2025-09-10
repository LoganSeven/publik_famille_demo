$(function() {
    $('a[rel=popup], a[data-popup]').attr('role', 'button');
    $('a[rel=popup], a[data-popup]').on('keydown', function(ev) {
      if (ev.keyCode == 13 || ev.keyCode == 32) {  // enter || space
        $(this).trigger('click');
        return false;
      }
    });
    $('a[rel=popup], a[data-popup]').data('title-selector', 'h2');
    $('a[rel=popup], a[data-popup]').data('close-button-text', WCS_I18N.close);
    $(document).on('gadjo:dialog-loaded', function(e, dialog) {
        window.disable_beforeunload = true;
        if ($(dialog).find('[name$=add_element]').length) {
            prepare_widget_list_elements();
        }
        if ($(dialog).data('js-features')) {
          add_js_behaviours($(dialog));
        }
        if (jQuery.fn.colourPicker !== undefined) {
          jQuery('select.colour-picker').colourPicker({title: ''});
        }
    });
});
