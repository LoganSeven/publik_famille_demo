$(function() {

  function prepare_dynamic_widgets()
  {
    $('[data-dynamic-display-parent]').off('change input').on('change input', function() {
      var sel1 = '[data-dynamic-display-child-of="' + $(this).attr('name') + '"]';
      var sel2 = '[data-dynamic-display-value="' + $(this).val() + '"]';
      var sel3 = '[data-dynamic-display-invert-value][data-dynamic-display-invert-value!="' + $(this).val() + '"]';
      var sel4 = '[data-dynamic-display-value-in*="' + $(this).val() + '"]';
      var sel5 = '[data-dynamic-display-checked="' + $(this).prop('checked') + '"]';
      $(sel1).addClass('widget-hidden');
      $(sel1 + sel2).removeClass('widget-hidden');
      $(sel1 + sel3).removeClass('widget-hidden');
      $(sel1 + sel4).removeClass('widget-hidden');
      $(sel1 + sel5).removeClass('widget-hidden');
      // cascade .widget-hidden to grand children
      $(sel1 + '.widget-hidden[data-dynamic-display-parent]').each(function(i, elem) {
        $('[data-dynamic-display-child-of="' + $(elem).attr('name') + '"]').addClass('widget-hidden');
      });
      $(sel1 + ':not(.widget-hidden)[data-dynamic-display-parent]').each(function(i, elem) {
        if ($(elem).is('input:checked') || $(elem).is('select')) {
          $(elem).trigger('change');
        }
      });
      // refresh maps that may have been shown
      $(this).parents('form').find('.qommon-map').trigger('qommon:invalidate');
    });
    $('[data-dynamic-display-child-of]').addClass('widget-hidden');
    $('select[data-dynamic-display-parent]').trigger('change');
    $('[data-dynamic-display-parent]:checked').trigger('change');
  }

  function prepare_autocomplete_widgets() {
    if (! $('select').select2) return;
    var select2_options = {
      language: {
        errorLoading: function() { return WCS_I18N.s2_errorloading; },
        noResults: function () { return WCS_I18N.s2_nomatches; },
        inputTooShort: function (input, min) { return WCS_I18N.s2_tooshort; },
        loadingMore: function () { return WCS_I18N.s2_loadmore; },
        searching: function () { return WCS_I18N.s2_searching; }
      }
    }
    if ($('select[data-autocomplete]').parents('.ui-dialog').length) {
      select2_options.width = '100%';
      select2_options.dropdownParent = $('select[data-autocomplete]').parents('.ui-dialog');
    }

    $('select[data-autocomplete]').each(function(idx, elem) {
      $(elem).select2(select2_options);
    });
  }

  function prepare_confirmation_buttons() {
    $('button[data-ask-for-confirmation]').off('click').on('click', function() {
      var text = $(this).data('ask-for-confirmation');
      if (text === true) {
        text = WCS_I18N.confirmation;
      }
      if (confirm(text) != true) {
        return false;
      } else {
        return true;
      }
    });
  }
  window.prepare_confirmation_buttons = prepare_confirmation_buttons;

  function prepare_select_empty_label() {
    $('[data-first-element-empty-label]').off('change').on('change', function() {
      var $widgets = $(this).parents('.widget');
      if ($widgets.length > 1) {
        var values = $widgets.find('select').map((idx, elt) => {return $(elt).val()}).toArray().slice(1)
        if (values.every(v => (v === ""))) { // all empty
          $widgets.find('select').first().find('option[value=""]').first().text($(this).attr('data-first-element-empty-label'));
        } else {
          $widgets.find('select').first().find('option[value=""]').first().text('---');
        }
      }
    }).trigger('change');
  }

  $('[data-content-url]').each(function(idx, elem) {
    $.ajax({url: $(elem).data('content-url'),
            xhrFields: { withCredentials: true },
            async: true,
            dataType: 'jsonp',
            crossDomain: true,
            success: function(data) { $(elem).html(data.content); },
            error: function(error) { windows.console && console.log('bouh', error); }
           });
  });

  function prepare_widgets() {
    prepare_dynamic_widgets();
    prepare_autocomplete_widgets();
    prepare_select_empty_label();
    prepare_confirmation_buttons();
  }

  prepare_widgets();
  $(document).on('gadjo:dialog-loaded', prepare_widgets);
  $(document).on('wcs:new-widgets-on-page', prepare_widgets);
});
