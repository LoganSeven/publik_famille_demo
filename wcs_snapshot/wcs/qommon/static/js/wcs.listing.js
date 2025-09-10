function prepare_page_links() {
  $('#page-links a').click(function() {
    $('form#listing-settings input[name="offset"]').val($(this).data('offset'));
    $('form#listing-settings input[name="limit"]').val($(this).data('limit'));
    refresh_table();
    return false;
  });
}

function prepare_row_links() {
  $('#listing tbody tr a').on('click auxclick', function(event) {
    event.stopPropagation();
  });
  $('#listing tbody tr').on('click auxclick', function(event) {
    var $target = $(event.target);
    if ($target.is('input[type=checkbox]')) {
      return true;
    }
    if ($target.is('td.select')) {
      $target.find('input').click();
      return true;
    }
    if (window.getSelection().toString()) {
      /* do not open links if the user was selecting text */
      return false;
    }
    var data_link = $(this).data('link');
    if (data_link) {
      if (data_link.indexOf('http://') == -1 && data_link.indexOf('https://') == -1) {
        data_link = window.location.pathname + data_link;
      }
      if (event.which == 2 || event.ctrlKey) {
        window.open(data_link, '_blank');
      } else {
        window.location = data_link;
      }
      return false;
    }
  });
  $('#listing tbody input[type=checkbox]').each(function() {
    if ($(this).is(':checked')) {
      $(this).parents('tr').addClass('checked');
    } else {
      $(this).parents('tr').removeClass('checked');
    }
  });
  if ($('#page-links .pages a').length < 2) {
    $('#info-all-rows').hide();
  }
  $('#listing input[type=checkbox]').on('change', function() {
    if ($(this).is('#top-select')) {
      // compute position of "apply on all pages" popup
      $('#info-all-rows').css('top', $('#top-select').position().top + $('#top-select').height() + 5)
    }
    if ($(this).is(':checked')) {
      if ($(this).is('#top-select')) {
        $(this).parents('table').find('tbody td.select input').prop('checked', true);
        $(this).parents('table').find('tbody tr').addClass('checked');
      } else {
        $(this).parents('tr').addClass('checked');
      }
    } else {
      if ($(this).is('#top-select')) {
        $(this).parents('table').find('tbody td.select input').prop('checked', false);
        $(this).parents('table').find('tbody tr').removeClass('checked');
      } else if ($(this).is('[value=_all]')) {
        // do nothing particular when unchecking "all"
      } else {
        $(this).parents('tr').removeClass('checked');
        $('#listing input[type=checkbox][value=_all]').prop('checked', false);
        $('#listing input[type=checkbox]#top-select').prop('checked', false);
      }
    }
    if ($('#listing tbody input[type=checkbox]:checked').length == 0) {
      $('form#multi-actions div.buttons').hide();
      return;
    } else {
      $('form#multi-actions div.buttons button').each(function(idx, elem) {
        var role_visible = false;
        var status_visible = false;
        for (var key in $(elem).first().data()) {
          if (key == 'visible_for_all') {
            role_visible = true;
          } else if (key == 'visible_all_status') {
            status_visible = true;
          } else if (key.startsWith('visible_status')) {
            if ($('input[type=checkbox][data-status_' + key.substr(15) + ']:checked').length) {
              status_visible = true;
            }
          } else if (key.startsWith('visible_for')) {
            if ($('input[type=checkbox][data-is_' + key.substr(12) + ']:checked').length) {
              role_visible = true;
            }
          }
          if (role_visible && status_visible) break;
        }
        if (role_visible && status_visible) {
          $(elem).parents('div.widget').show();
        } else {
          $(elem).parents('div.widget').hide();
        }
      });
      $('form#multi-actions div.buttons').show();
    }
  });
  // hide at first
  $('form#multi-actions div.buttons').hide();
  // but trigger events in case of prechecked rows
  $('#listing input[type=checkbox]:checked').trigger('change');
}

function prepare_column_headers() {
  var current_key = $('input[name="order_by"]').val();
  var sort_key = null;
  var reversed = false;
  if (current_key) {
    if (current_key[0] === '-') {
      sort_key = current_key.substring(1);
      reversed = true;
    } else {
      sort_key = current_key;
      reversed = false;
    }
  }
  if (reversed) {
    $('#listing thead th[data-field-sort-key="' + sort_key + '"]').addClass('headerSortUp');
  } else {
    $('#listing thead th[data-field-sort-key="' + sort_key + '"]').addClass('headerSortDown');
  }
  $('#listing thead th[data-field-sort-key]').addClass('header').click(function() {
    var new_key = $(this).data('field-sort-key');
    if (sort_key === new_key) { // same column, reverse on second click, reset on third click
      if (! reversed) {
        new_key = '-' + new_key
      } else {
        new_key = ''
      }
    }
    $('input[name="order_by"]').val(new_key);
    refresh_table();
  });
}

function autorefresh_table() {
  if ($('#multi-actions input:checked').length) {
    // disable autorefresh when multiselection is enabled
    return;
  }
  $(document).trigger('backoffice-filter-change',
      {qs: $('form#listing-settings').serialize(), auto: true});
}

function refresh_table() {
  $(document).trigger('backoffice-filter-change',
      {qs: $('form#listing-settings').serialize(), auto: false});
}

function prepare_select2($elem) {
  var filter_field_id = $elem.data('remote-options');
  var options = {
    language: {
      errorLoading: function() { return WCS_I18N.s2_errorloading; },
      noResults: function () { return WCS_I18N.s2_nomatches; },
      inputTooShort: function (input, min) { return WCS_I18N.s2_tooshort; },
      loadingMore: function () { return WCS_I18N.s2_loadmore; },
      searching: function () { return WCS_I18N.s2_searching; },
    },
    placeholder: '',
    allowClear: true,
    minimumInputLength: 1,
    ajax: {
      url: function() {
        var pathname = window.location.pathname.replace(/^\/+/, '/').replace(/stats$/, '').replace(/map$/, '');
        var filter_settings = $('form#listing-settings').serialize();
        return pathname + 'filter-options?filter_field_id=' + filter_field_id + '&' + filter_settings;
      },
      dataType: 'json',
      data: function(params) {
        var query = {
          _search: params.term,
        }
        return query;
      },
      processResults: function (data, params) {
        return {results: data.data};
      },
    },
  };
  $elem.select2(options);
}

$(document).on('backoffice-filter-change', function(event, listing_settings) {
  /* makes sure it doesn't start with a double slash */
  var pathname = window.location.pathname.replace(/^\/+/, '/');

  if ($('[data-has-view-settings]').length) {
    $.ajax({
      url: 'view-settings?ajax=on',
      method: 'post',
      data: listing_settings.qs,
      beforeSend: function() { $('#more-user-links, #listing, #statistics').addClass('activity'); },
      complete: function() { $('#more-user-links, #listing, #statistics').removeClass('activity'); },
      success: function(data) {
        update_listing(data.content, data.qs);
      }
    });
  } else {
    $.ajax({
      url: pathname + '?ajax=true&' + listing_settings.qs,
      beforeSend: function() { $('#more-user-links, #listing, #statistics').addClass('activity'); },
      complete: function() { $('#more-user-links, #listing, #statistics').removeClass('activity'); },
      success: function(html) {
        update_listing(html, listing_settings.qs);
      }
    });
  }

  function update_listing(html, qs) {
      var $html = $(html);
      var $listing = $html;
      if ($listing.is('form')) {
        // mass action
        $listing = $listing.find('#listing');
        $('#multi-actions div.buttons').replaceWith($html.find('div.buttons'));
        $('#listing').replaceWith($listing);
        $('#page-links').replaceWith($html.find('#page-links'));
        $html.each(function () {
          if ($(this).is('#messages')) {
            $('#messages').replaceWith($(this));
            return false;
          }
        });
      } else if ($html.is('#listing')) {
        $('#page-links').remove();
        $('#listing').replaceWith($listing);
      } else if ($html.is('#statistics')) {
        $('#statistics').replaceWith($html);
        if (typeof(wcs_draw_graphs) !== 'undefined') {
          wcs_draw_graphs();
        }
      } else if ($html.find('#backoffice-map').length) {
        // update sidebar links query strings
        $('a[data-base-href]').each(function(idx, elem) {
          $(elem).attr('href', $(elem).data('base-href') + '?' + qs);
        });
        $(document).trigger('backoffice-map-filter-change', {qs: qs});
      } else {
        // no appropriate content, do not replace.
        return;
      }

      /* map in a table cell */
      if ($('#listing .qommon-map').length) {
        $(document).trigger('wcs:maps-init');
      }
      prepare_page_links();
      prepare_row_links();
      prepare_column_headers();
      window.prepare_confirmation_buttons();
      $('a[data-base-href]').each(function(idx, elem) {
        $(elem).attr('href', $(elem).data('base-href') + '?' + qs);
      });
      $('#multi-actions').attr('action', '?' + qs);
      if (window.history) {
        window.history.replaceState(null, null, pathname + '?' + qs);
      }

      /* refresh dynamic filters */
      $('[data-refresh-options]').each(function(idx, elem) {
        var $select = $(elem);
        var current_value = $select.val();
        var filter_path = pathname.replace(/stats$/, '') + 'filter-options?filter_field_id=' + $(elem).data('refresh-options') + '&' + qs;
        $.ajax({
          url: filter_path,
          success: function(data) {
            $select.empty();
            var $option = $('<option></option>', {value: ''});
            var found_current_value = false;
            $option.appendTo($select);
            for (var i=0; i<data.data.length; i++) {
              var $option = $('<option></option>', {value: data.data[i].id, text: data.data[i].text});
              if (data.data[i].id == current_value) {
                found_current_value = true;
                $option.attr('selected', 'selected');
              }
              $option.appendTo($select);
            }
            if (current_value && !found_current_value && $select.is('[data-allow-template]')) {
              var $option = $('<option></option>', {value: current_value, text: current_value});
              $option.attr('selected', 'selected');
              $option.appendTo($select);
            } else if (current_value && !found_current_value && $select.is('[data-multi-values]')) {
              var $option = $('<option data-option-for-multi-values>multi</option>');
              $option.attr('value', current_value);
              $option.attr('selected', 'selected');
              $option.appendTo($select);
            }
            $('.operator select').trigger('change', true);
          }
        });
      });

      $('.operator select').each(function(idx, elem) {
        const $select = $(elem)
        const operator = $select.val()
        if(['between', 'in', 'not_in'].includes(operator)) {
          $select.trigger('change', true);
        }
      })

      /* makes sure activity and disabled-during-submit are removed */
      $('#more-user-links, #listing, #statistics').removeClass('activity');
      $('form').removeClass('disabled-during-submit');
  }
});

$(function() {
  var must_reload_page = false;

  /* column settings */
  $('#columns-settings').click(function() {
    var dialog = $('<form>');
    var $dialog_filter = $('#columns-filter').clone().attr('id', null);
    $dialog_filter.appendTo(dialog);
    $dialog_filter.find('button.expand-relations').each(function(elem, i) {
      $(this).removeClass('opened');
      var field_id = $(this).parents('li.has-relations-field').data('field-id');
      $(this).parents('li').find('~ li[data-relation-attr=' + field_id + ']').addClass('collapsed');
    });
    $dialog_filter.find('button.expand-relations').on('click', function() {
      $(this).toggleClass('opened');
      var field_id = $(this).parents('li.has-relations-field').data('field-id');
      $(this).parents('li').find('~ li[data-relation-attr=' + field_id + ']').toggleClass('collapsed');
      return false;
    });
    $dialog_filter.find('[type="checkbox"]').on('change', function() {
      if ($dialog_filter.find('[type="checkbox"]:checked').length == 0) {
        $dialog_filter.find('.columns-default-value-message').show();
      } else {
        $dialog_filter.find('.columns-default-value-message').hide();
      }
    });
    $dialog_filter.find('ul').sortable({handle: '.handle'})
    $(dialog).dialog({
            closeText: WCS_I18N.close,
            modal: true,
            resizable: false,
            title: $('#columns-settings').attr('title'),
            width: '30em'});
    $(dialog).dialog('option', 'buttons', [
            {text: $('form#listing-settings button.submit-button').text(),
             click: function() {
                var $container = $('#columns-filter').parent();
                $('#columns-filter').remove();
                $dialog_filter.attr('id', 'columns-filter');
                $dialog_filter.appendTo($container);
                $('[name="columns-order"]').val($('#columns-filter input:checked').map(function() { return $(this).attr('name'); }).get().join());
                $(this).dialog('close');
                $('form#listing-settings').submit();
              }
            }]);
    return false;
  });

  /* filter settings */
  $('#filter-settings').click(function() {
    $('div.ui-dialog').remove();
    var dialog = $('<form>');
    $('#field-filter').clone().appendTo(dialog);
    $(dialog).find('input').each(function() { $(this).attr('id', 'dlg-' + $(this).attr('id')); });
    $(dialog).find('label').each(function() { $(this).attr('for', 'dlg-' + $(this).attr('for')); });
    $(dialog).dialog({
            closeText: WCS_I18N.close,
            modal: true,
            resizable: false,
            title: $('#filter-settings').parents('h3').find('span:first-child').text(),
            width: '30em'});
    $(dialog).dialog('option', 'buttons', [
            {text: $('form#listing-settings button.submit-button').text(),
             click: function() {
                $(this).find('input[type="checkbox"]').each(function(idx, elem) {
                $('form#listing-settings input[name="' + $(elem).attr('name') + '"]').prop('checked',
                        $(elem).prop('checked'));
                });
                $(this).dialog('close');
                must_reload_page = true;
                $('form#listing-settings').submit();
              }
            }]);
    return false;
  });

  /* set filter options from server (select2) */
  $('[data-remote-options]').each(function(idx, elem) {
    prepare_select2($(elem));
  });

  $('button#save-view').on('click', function() {
    $('div.ui-dialog').remove();
    var div_dialog = $('<div>');
    $('#save-custom-view').clone().attr('hidden', null).attr('id', null).appendTo(div_dialog);
    $(div_dialog).find('[name=qs]').val($('form#listing-settings').serialize());
    $(div_dialog).find('.buttons').hide();
    $(div_dialog).find('input').each(function() { $(this).attr('id', 'dlg-' + $(this).attr('id')); });
    $(div_dialog).find('label').each(function() { $(this).attr('for', 'dlg-' + $(this).attr('for')); });
    var dialog = $(div_dialog).dialog({
            closeText: WCS_I18N.close,
            modal: true,
            resizable: false,
            title: $(this).text(),
            width: 'auto',
            buttons: [
              {text: $(div_dialog).find('.cancel-button').text(),
               class: 'cancel-button',
               click: function() { $(this).dialog('close'); }
              },
              {text: $(div_dialog).find('.submit-button').text(),
               class: 'submit-button',
               click: function() { $(div_dialog).find('.submit-button button').click(); return false; }
              }
            ]
    });
    $(document).trigger('gadjo:dialog-loaded', $(dialog));
    return false;
  });

  /* automatically refresh onfilter change */
  $(document).on('change', 'form#listing-settings input[type=date], form#listing-settings input[type=text], form#listing-settings .value select, form.global-filters select', function () {
    if (this.type == 'date' && $(this).val() && $(this).val()[0] == '0') {
      // input date with year starting with 0, it's currently being typed in,
      // wait for the year to be complete before acting on it.
      return;
    }

    const $filter_widget = $(this).closest('.operator-and-value-widget')
    const operator_select = $filter_widget.find('.operator select')
    const operator = operator_select.val()
    if(operator == 'between') {
      const filter_inputs = Array.from($filter_widget.find('[data-for-multi-values] input, [data-for-multi-values] select'))
      if(filter_inputs.some(input => !input.value)) {
        return
      }
    }

    if ($(this).is('select[data-allow-template]') && $(this).val() == '{}') {
      var replacement_input = $('<input></input>', {type: 'text', name: $(this).attr('name')});
      $(this).parent().removeClass('SingleSelectWidget').addClass('StringWidget');
      if ($(this).data('select2-id')) {
          $(this).select2('destroy');
      }
      $(this).replaceWith(replacement_input);
      return;
    }

    $('form#listing-settings').submit();
  });
  /* partial table refresh */
  $('form#listing-settings').submit(function(event) {
    $('form#listing-settings input[name="offset"]').val('0');

    if (must_reload_page) {
      return true;
    }

    event.preventDefault();
    $(document).trigger('backoffice-filter-change', {qs: $('form#listing-settings').serialize()});

    return false;
  });

  /* operator selection */
  $('.operator select').change(function (event, init) {
    var operator = $(this).val();
    var $container = $(this).parents('.operator-and-value-widget');
    var $value_container = $container.find('.value:not([data-for-multi-values])');
    var $value_input = $value_container.find('input');
    var $value_select = $value_container.find('select');
    if ($value_select.prop('name') == 'filter') {
        // status filter, disable waiting status for in ant not_in operators
        if (operator == 'in' || operator == 'not_in') {
            $value_select.find('option[value="waiting"]').prop('disabled', true)
        } else {
            $value_select.find('option[value="waiting"]').prop('disabled', false)
        }
    }

    $value_container.show();
    // remove added 'on' value of select options
    $value_select.find('option[data-on-option]').remove();
    // remove inputs and selects for multi values
    $container.find('.value[data-for-multi-values]').each(function() {
      $(this).remove();
      if ($(this).data('select2-id')) {
          $(this).select2('destroy');
      }
    });
    // remove added 'multi' value of select options
    $value_select.find('option[data-option-for-multi-values]').remove();

    if (operator == 'absent' || operator == 'existing' || operator == 'is_today' || operator == 'is_tomorrow' || operator == 'is_yesterday' || operator == 'is_this_week' || operator == 'is_future' || operator == 'is_past' || operator == 'is_today_or_future' || operator == 'is_today_or_past') {
      // no input/select to set a value, but 'on' value is needed to apply filter
      $value_input.val('on');
      if ($value_select.length) {
        // add an 'on' value for absent/existing operators
        $('<option value="on" data-on-option>on</option>').appendTo($value_select);
      }
      $value_select.val('on');
      $value_container.hide();
    } else {
      if ($value_input.val() == 'on') {
        $value_input.val('');
      }
      if ($value_select.val() == 'on') {
        $value_select.val('');
      }
    }

    if (operator == 'between' || operator == 'in' || operator == 'not_in') {
      // operator with multi values
      // hide original value input or select
      $value_container.hide();
      // multi input/select needed
      var $value_element = null;
      if ($value_select.length) {
        $value_element = $value_select;
      } else {
        $value_element = $value_input;
      }
      var values = []
      var values_str = ''
      if ($value_element.attr('data-multi-values')) {
        values_str = $value_element.attr('data-multi-values')
      } else if ($value_element.val()) {
        values_str = $value_element.val()
      }
      if (values_str.indexOf('{{') == -1 && values_str.indexOf('{%') == -1) {
        values = values_str.split('|')
      } else {
        values = [values_str]
      }

      if(operator == 'between') {
        while(values.length < 2) {
          values.push('');
        }
        values = values.slice(0, 2);
      } else {
        values = values.filter(val => val != '')
      }

      for (let value of values) {
        var $copy = $value_container.clone().attr('data-for-multi-values', '').show();
        $copy.find($value_element.prop('tagName'))
              .removeAttr('id')
              .removeAttr('data-refresh-options')
              .removeAttr('data-multi-values')
              .attr('name', 'multi-' + $value_element.attr('name'))
              .val(value);

        if (operator != 'between') {
          $remove_button = $('<button class="multi-value-filter--remove-value-button">-</button>')
          $remove_button.click(function() {
            values = values.filter(item => item !== value)
            if($value_select.length) {
              $value_select.attr('data-multi-values', values.join('|'));
              $value_select
                .find('option[data-option-for-multi-values]')
                .attr('value', values.join('|'));
            }
            else {
              $copy.find('select').val('').trigger('change');
              $value_input.val(values.join('|'))
            }

            $('form#listing-settings').submit();
          });
          $copy.append($remove_button);
        }

        $container.append($copy);
        if ($value_select.data('select2-id')) {
          const text_values_element = document.getElementById('filter-options-' + $value_select.attr('name'))
          const text_values = text_values_element ? JSON.parse(text_values_element.textContent) : Object()
          const container_text_values = $container[0].text_values || Object()
          $copy.find('span.select2').remove();
          if (! $copy.find('option[value="' + value + '"]').length) {
            // get text value from server provided json or from dynamic vlaue
            // added to container widget.
            var $option = $('<option></option>', {value: value, text: text_values[value] || container_text_values[value] || value});
            $option.attr('selected', 'selected');
            $option.appendTo($copy.find('select'));
          }
          $select = $copy.find('select')
          prepare_select2($select);
          if(operator != "between") {
            $select.on('select2:opening', (e) => {
              e.preventDefault()
            })
          }
        }
      }

      if(operator != 'between') {
        var $add_item = $value_container.clone().attr('data-for-multi-values', '').show();
        $add_item.find($value_element.prop('tagName'))
              .removeAttr('id')
              .removeAttr('data-refresh-options')
              .removeAttr('data-multi-values')
              .attr('name', 'multi-' + $value_element.attr('name'))
              .val('');

        const $add_input = $add_item.find('input, select')
        const $add_button = $('<button disabled class="multi-value-filter--add-value-button">+</button>')
        $add_item.append($add_button)

        $add_input.on('input', function() {
          $add_button.prop('disabled', $add_input.val() === '')
        })

        $add_input.on('change', function() {
          $add_button.prop('disabled', $add_input.val() === '')
        })

        $add_button.click(function() {
          $('form#listing-settings').submit();
        });

        if ($value_select.data('select2-id')) {
          $add_item.find('span.select2').remove();
          prepare_select2($add_item.find('select'));
        }

        $container.append($add_item)
      }

      if ($value_select.length && values.length) {
        // add an option for 'multi' value
        var $option = $('<option data-option-for-multi-values>multi</option>');
        $option.attr('value', values.join('|'));
        $option.attr('selected', 'selected');
        $option.appendTo($value_select);
        $value_select.attr('data-multi-values', values.join('|'));
      } else if ($value_input.length && values.length) {
        $value_input.val(values.join('|'));
      }
    } else {
      // remove multi values
      if ($value_input.length && $value_input.val() && $value_input.val().indexOf('{{') == -1 && $value_input.val().indexOf('{%') == -1) {
        var values = $value_input.val().split('|');
        $value_input.val(values[0]);
      }
      if ($value_select.length && $value_select.attr('data-multi-values')) {
        var values = $value_select.attr('data-multi-values').split('|');
        $value_select.val(values[0]);
        $value_select.removeAttr('data-multi-values')
        if ($value_select.data('select2-id') && ! $value_select.find('option[value="' + values[0] + '"]').length) {
          var $option = $('<option></option>', {value: values[0], text: values[0]});
          $option.attr('selected', 'selected');
          $option.appendTo($value_select);
        }
      }
    }

    if (!init) {
      $('form#listing-settings').submit();
    }
  });
  $('.operator select').trigger('change', true);

  $(document).on('change', '.value[data-for-multi-values] input, .value[data-for-multi-values] select', function () {
    if (! this.parentElement) return;
    var $container = $(this).parents('.operator-and-value-widget');
    var $operator = $container.find('.operator select');
    var $value_input = $container.find('.value:not([data-for-multi-values]) input');
    var $value_select = $container.find('.value:not([data-for-multi-values]) select');
    var values = [];
    var text_values = [];
    $container[0].text_values = Object();
    $container.find('.value[data-for-multi-values] input, .value[data-for-multi-values] select').each(function () {
      if ($(this).val()) {
        values.push($(this).val());
        // keep a copy of text values in the container widget
        $container[0].text_values[$(this).val()] = $(this).find(':selected').text();
      }
    });
    if ($operator.val() == 'between' && values.length != 2) {
      // wrong number of values, don't call backoffice
      return;
    }
    if ($value_input.length) {
      // set multi value
      $value_input.val(values.join('|'));
    } else {
      // add a 'multi' value
      var $option = $('<option data-option-for-multi-values>multi</option>');
      $option.attr('value', values.join('|'));
      $option.attr('selected', 'selected');
      $option.appendTo($value_select);
      // and update data-multi-values
      $value_select.attr('data-multi-values', values.join('|'));
    }
    // submit form
    $('form#listing-settings').submit();
  });

  // filters on pending submission page
  document.querySelectorAll('#btn-submissions-filter button').forEach((btn) => {
    btn.addEventListener('click', (ev) => {
      const value = btn.dataset.value
      document.querySelector(`#btn-submissions-filter button[data-value="${value}"]`).classList.add('active')
      document.querySelector(`#btn-submissions-filter button:not([data-value="${value}"])`).classList.remove('active')
      document.querySelector('#listing-settings [name="mine"]').value = btn.dataset.value
      refresh_table();
    })
  })

  /* refresh every 30 seconds (idle_id) after any user activity
   * on inactivity for more than 5 minutes (longidle_id), stop refreshing (clear idle_id)
   */
  if ($('#statistics').length == 0) {
    var idle_id = null;
    var longidle_id = null;
    $(window).on('mousemove keydown mousedown touchstart', function() {
      /* if refresh timer exists, clear it */
      if (idle_id) window.clearInterval(idle_id);
      /* if stop refreshing timer exists, clear it */
      if (longidle_id) window.clearTimeout(longidle_id);
      /* launch timer to refresh every 30 seconds */
      idle_id = setInterval(autorefresh_table, 30000);
      /* launch timer to stop refreshing after 5 minutes idle */
      longidle_id = setTimeout(function () {
          if (idle_id) idle_id = window.clearInterval(idle_id);
          longidle_id = undefined;
      }, 300 * 1000);
    });
  }

  prepare_page_links();
  prepare_row_links();
  prepare_column_headers();

});
