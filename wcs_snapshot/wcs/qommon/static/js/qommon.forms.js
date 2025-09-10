String.prototype.similarity = function(string) {
  // adapted from https://github.com/jordanthomas/jaro-winkler (licensed as MIT)
  var s1 = this, s2 = string;
  var m = 0;
  var i;
  var j;

  // Exit early if either are empty.
  if (s1.length === 0 || s2.length === 0) {
    return 0;
  }

  // Convert to upper
  s1 = s1.toUpperCase();
  s2 = s2.toUpperCase();

  // Exit early if they're an exact match.
  if (s1 === s2) {
    return 1;
  }

  var range = (Math.floor(Math.max(s1.length, s2.length) / 2)) - 1;
  var s1Matches = new Array(s1.length);
  var s2Matches = new Array(s2.length);

  for (i = 0; i < s1.length; i++) {
    var low  = (i >= range) ? i - range : 0;
    var high = (i + range <= (s2.length - 1)) ? (i + range) : (s2.length - 1);

    for (j = low; j <= high; j++) {
      if (s1Matches[i] !== true && s2Matches[j] !== true && s1[i] === s2[j]) {
        ++m;
        s1Matches[i] = s2Matches[j] = true;
        break;
      }
    }
  }

  // Exit early if no matches were found.
  if (m === 0) {
    return 0;
  }

  // Count the transpositions.
  var k = 0;
  var numTrans = 0;

  for (i = 0; i < s1.length; i++) {
    if (s1Matches[i] === true) {
      for (j = k; j < s2.length; j++) {
        if (s2Matches[j] === true) {
          k = j + 1;
          break;
        }
      }

      if (s1[i] !== s2[j]) {
        ++numTrans;
      }
    }
  }

  var weight = (m / s1.length + m / s2.length + (m - (numTrans / 2)) / m) / 3;
  var l = 0;
  var p = 0.1;

  if (weight > 0.7) {
    while (s1[l] === s2[l] && l < 4) {
      ++l;
    }

    weight = weight + l * p * (1 - weight);
  }

  return weight;
}

/* Make table widget responsive
 *    new Responsive_table_widget(table)
 */
const Responsive_table_widget = function (table) {
    'use strict';
    this.table = table;
    this.col_headers = table.querySelectorAll('thead th');
    this.col_headers_text = [];
    this.body_rows = table.querySelectorAll('tbody tr');
    this.parent = table.parentElement;
    this.init();
};
Responsive_table_widget.prototype.storeHeaders = function () {
    'use strict';
    let _self = this;
    $(this.col_headers).each(function (i, header) {
        _self.col_headers_text.push(header.innerText);
    });
    $(this.body_rows).each(function (i, tr) {
        $(tr.querySelectorAll('td')).each(function (i, td) {
            td.dataset.colHeader = _self.col_headers_text[i];
        });
    });
};
Responsive_table_widget.prototype.fit = function () {
    'use strict';
    if (this.parent.clientWidth < this.table.clientWidth) {
        this.table.style.width = "100%";
    } else if (! $(this.parent).parent().is('[class*=" grid-"]')) {
        this.table.style.width = "auto";
    }
};
Responsive_table_widget.prototype.init = function () {
    'use strict';
    let _self = this;
    this.table.classList.add('responsive-tableWidget');
    this.storeHeaders();
    this.fit();
    // debounce resize event
    let callback;
    window.addEventListener("resize", function () {
        clearTimeout(callback);
        callback = setTimeout( function () {
            _self.fit.call(_self)
        }, 200);
    });
};

function add_js_behaviours($base) {
  // common domains we want to offer suggestions for.
  var well_known_domains = Array();
  // existing domains we know but don't want to use in suggestion engine.
  var known_domains = Array();
  if (typeof WCS_WELL_KNOWN_DOMAINS !== 'undefined') {
    var well_known_domains = WCS_WELL_KNOWN_DOMAINS;
    var known_domains = WCS_VALID_KNOWN_DOMAINS;
  }

  $base.find('input[type=email]').on('change wcs:change', function() {
    var $email_input = $(this);
    var val = $email_input.val();
    var val_domain = val.split('@')[1];
    var $domain_hint_div = this.domain_hint_div;
    var highest_ratio = 0;
    var suggestion = null;

    if (typeof val_domain === 'undefined' || known_domains.indexOf(val_domain) > -1) {
      // domain not yet typed in, or known domain, don't suggest anything.
      if ($domain_hint_div) {
        $domain_hint_div.hide();
      }
      return;
    }

    for (var i=0; i < well_known_domains.length; i++) {
      var domain = well_known_domains[i];
      var ratio = val_domain.similarity(domain);
      if (ratio > highest_ratio) {
        highest_ratio = ratio;
        suggestion = domain;
      }
    }
    if (highest_ratio > 0.80 && highest_ratio < 1) {
      if ($domain_hint_div === undefined) {
        $domain_hint_div = $('<div class="field-live-hint"><p class="message"></p><button type="button" class="action"></button><button type="button" class="close"><span class="sr-only"></span></button></div>');
        this.domain_hint_div = $domain_hint_div;
        $(this).after($domain_hint_div);
        $domain_hint_div.find('button.action').on('click', function() {
          $email_input.val($email_input.val().replace(/@.*/, '@' + $(this).data('suggestion')));
          $email_input.trigger('wcs:change');
          $domain_hint_div.hide();
          return false;
        });
        $domain_hint_div.find('button.close').on('click', function() {
          $domain_hint_div.hide();
          return false;
        });
      }
      $domain_hint_div.find('p').text(WCS_I18N.email_domain_suggest + ' @' + suggestion + ' ?');
      $domain_hint_div.find('button.action').text(WCS_I18N.email_domain_fix);
      $domain_hint_div.find('button.action').data('suggestion', suggestion);
      $domain_hint_div.find('button.close span.sr-only').text(WCS_I18N.close);
      $domain_hint_div.show();
    } else if ($domain_hint_div) {
      $domain_hint_div.hide();
    }
  });

  /* searchable select */
  $base.find('select[data-autocomplete]').each(function(i, elem) {
    var required = $(elem).data('required');
    var options = {
      language: {
        errorLoading: function() { return WCS_I18N.s2_errorloading; },
        noResults: function () { return WCS_I18N.s2_nomatches; },
        inputTooShort: function (input, min) { return WCS_I18N.s2_tooshort; },
        loadingMore: function () { return WCS_I18N.s2_loadmore; },
        searching: function () { return WCS_I18N.s2_searching; }
      }
    };
    options.placeholder = $(elem).find('[data-hint]').data('hint');
    if (!required) {
      if (!options.placeholder) options.placeholder = '...';
      options.allowClear = true;
    }
    $(elem).select2(options);
    $(elem).on('select2:open', function(ev) {
      var search_field = document.querySelector('.select2-search__field')
      var label_field = document.querySelector(`label[for="${this.id}"]`)
      search_field.setAttribute('aria-labelledby', label_field.id)
    })
  });

  /* searchable select using a data source */
  $base.find('select[data-select2-url]').each(function(i, elem) {
    var required = $(elem).data('required');
    // create an additional hidden field to hold the label of the selected
    // option, it is necessary as the server may not have any knowledge of
    // possible options.
    var $input_display_value = $('<input>', {
            type: 'hidden',
            name: $(elem).attr('name') + '_display',
            value: $(elem).data('initial-display-value')
    });
    $input_display_value.insertAfter($(elem));
    var options = {
      minimumInputLength: 1,
      formatResult: function(result) { return result.text; },
      language: {
        errorLoading: function() { return WCS_I18N.s2_errorloading; },
        noResults: function () { return WCS_I18N.s2_nomatches; },
        inputTooShort: function (input, min) { return WCS_I18N.s2_tooshort; },
        loadingMore: function () { return WCS_I18N.s2_loadmore; },
        searching: function () { return WCS_I18N.s2_searching; }
      },
      templateSelection: function(data, container) {
        if (data.edit_related_url) {
          $(data.element).attr('data-edit-related-url', data.edit_related_url);
        }
        if (data.view_related_url) {
          $(data.element).attr('data-view-related-url', data.view_related_url);
        }
        return data.text;
      }
    };
    if (!required) {
      options.allowClear = true;
    }
    options.placeholder = $(elem).find('[data-hint]').data('hint');
    if (!required && !options.placeholder) {
      options.placeholder = '...';
    }
    var url = $(elem).data('select2-url');
    if (url.indexOf('/api/') == 0) {  // local proxying
      var data_type = 'json';
    } else {
      var data_type = 'jsonp';
    }
    options.ajax = {
      delay: 250,
      dataType: data_type,
      data: function(params) {
        return {q: params.term, page_limit: 50};
      },
      processResults: function (data, params) {
        return {results: data.data};
      },
      url: function() {
        var url = $(elem).data('select2-url');
        url = url.replace(/\[var_.+?\]/g, function(match, g1, g2) {
          // compatibility: if there are [var_...] references in the URL
          // replace them by looking for other select fields on the same
          // page.
          var related_select = $('#' + match.slice(1, -1));
          var value_container_id = $(related_select).data('valuecontainerid');
          return $('#' + value_container_id).val() || '';
        });
        return url;
      }
    };
    var select2 = $(elem).select2(options);
    $(elem).on('select2:open', function(ev) {
      var search_field = document.querySelector('.select2-search__field')
      var label_field = document.querySelector(`label[for="${this.id}"]`)
      search_field.setAttribute('aria-labelledby', label_field.id)
    })
    $(elem).on('change', function() {
      // update _display hidden field with selected text
      var $selected = $(elem).find(':selected').first();
      var text = $selected.text();
      $input_display_value.val(text);
      // update edit-related button and view-related link href
      $(elem).siblings('.edit-related').attr('href', '').hide();
      $(elem).siblings('.view-related').attr('href', '').hide();
      if ($selected.attr('data-edit-related-url')) {
        $(elem).siblings('.edit-related').attr('href', $selected.attr('data-edit-related-url') + '?_popup=1').show();
      }
      if ($selected.attr('data-view-related-url')) {
        $(elem).siblings('.view-related').attr('href', $selected.attr('data-view-related-url')).show();
      }
    });
    if ($input_display_value.val()) {
      // if the _display hidden field was created with an initial value take it
      // and create a matching <option> in the real <select> widget, and use it
      // to set select2 initial state.
      var option = $('<option></option>', {value: $(elem).data('value')});
      option.appendTo($(elem));
      option.text($input_display_value.val());
      if ($(elem).data('initial-edit-related-url')) {
        option.attr('data-edit-related-url', $(elem).data('initial-edit-related-url'));
      }
      if ($(elem).data('initial-view-related-url')) {
        option.attr('data-view-related-url', $(elem).data('initial-view-related-url'));
      }
      select2.val($(elem).data('value')).trigger('change');
      $(elem).select2('data', {id: $(elem).data('value'), text: $(elem).data('initial-display-value')});
    }
  });

  /* Make table widgets responsive */
  $base.find('.TableWidget, .SingleSelectTableWidget, .TableListRowsWidget').each(function (i, elem) {
    const table = elem.querySelector('table');
    new Responsive_table_widget(table);
  });

  /* Add class to reset error style on change */
  $base.find('.widget-with-error').each(function(i, elem) {
    $(elem).find('input, select, textarea').on('change', function() {
      $(this).parents('.widget-with-error').addClass('widget-reset-error');
    });
  });
  $base.find('div.widget-prefilled').on('change input paste', function(ev) {
    $(this).removeClass('widget-prefilled');
  });
}

$(function() {
  $('.section.foldable').addClass('gadjo-foldable-ignore');
  $('.section.foldable > h2 [role=button]').each(function() {
     $(this).attr('tabindex', '0');
  });
  $('.section.foldable > h2 [role=button]').on('keydown', function(ev) {
    if (ev.keyCode == 13 || ev.keyCode == 32) {  // enter || space
      $(this).trigger('click');
      return false;
    }
  });
  $('.section.foldable > h2').off('click').click(function() {
     var folded = $(this).parent().hasClass('folded');
     var $button = $(this).find('[role=button]').first();
     if ($button.length) {
       $button[0].setAttribute('aria-expanded', `${folded}`);
     }
     $(this).parent().toggleClass('folded');
     $(this).parent().find('.qommon-map').trigger('qommon:invalidate');
  });

  // fill honeypot field
  const honeypot = document.querySelector('[name="f002"]')
  if (honeypot) {
    honeypot.value = honeypot.form.dataset.honeyPotValue
  }

  var autosave_timeout_id = null;
  var autosave_is_running = false;
  var autosave_button_to_click_on_complete = null;
  var last_auto_save = $('form[data-has-draft]').serialize();

  if ($('form[data-warn-on-unsaved-content]').length) {
    window.addEventListener('beforeunload', function (e) {
      var $form = $('form[data-warn-on-unsaved-content]');
      var current_data = $form.serialize();
      if (last_auto_save == current_data) return;
      if (window.disable_beforeunload) return;
      // preventDefault() and returnValue will trigger the browser alert
      // warning user about closing tag/window and losing data.
      e.preventDefault();
      e.returnValue = true;
    });
  }

  if ($('form[data-has-draft]:not([data-autosave=false])').length == 1) {
    var error_counter = 0;

    function autosave() {
      var $form = $('form[data-has-draft]');
      if ($form.hasClass('disabled-during-submit')) return;
      var new_auto_save = $form.serialize();
      if (last_auto_save == new_auto_save) {
        install_autosave();
        return;
      }
      autosave_is_running = true;
      $.ajax({
        type: 'POST',
        url: window.location.pathname + 'autosave',
        data: new_auto_save,
        success: function(json) {
          if (json.result == 'success') {
            error_counter = -1;
            last_auto_save = new_auto_save;
          }
        },
        complete: function() {
          error_counter++;
          if (error_counter > 5) {
            // stop trying to autosave unless there are new changes
            last_auto_save = new_auto_save;
          }
          autosave_is_running = false;
          if (autosave_timeout_id !== null) {
              install_autosave();
          }
          if (autosave_button_to_click_on_complete !== null) {
              autosave_button_to_click_on_complete.click();
          }
        }
      });
    }

    function install_autosave() {
       // debounce
       window.clearTimeout(autosave_timeout_id);
       autosave_timeout_id = window.setTimeout(autosave, 5000);
    }

    $(document).on('mouseover scroll keydown', function() {
        if (autosave_timeout_id !== null && ! autosave_is_running) {
            install_autosave();
        }
    });

    $(window).on('pagehide', function () {
       if (autosave_timeout_id !== null && ! $('body').hasClass('autosaving')) {
           window.clearTimeout(autosave_timeout_id);
           autosave_timeout_id = null;
           autosave();
       }
    });

    $(document).on('visibilitychange', function () {
       if (document.visibilityState == 'hidden' && autosave_timeout_id !== null && ! $('body').hasClass('autosaving')) {
           window.clearTimeout(autosave_timeout_id);
           autosave_timeout_id = null;
           autosave();
       }
    });

    install_autosave();

    $('#tracking-code a').on('click', autosave);
    $(document).on('wcs:set-last-auto-save', function() {
      last_auto_save = $('form[data-has-draft]').serialize();
    });
  }

  add_js_behaviours($('form[data-js-features]'));
  last_auto_save = $('form[data-has-draft]').serialize();

  // Form with error
  const errornotice = document.querySelector('form:not([data-backoffice-preview]) .errornotice');
  if (errornotice) {
    document.body.classList.add('form-with-error');
    errornotice.setAttribute('tabindex', '-1');
    errornotice.focus();
  }

  $(window).bind('pageshow', function(event) {
    $('form').removeClass('disabled-during-submit');
  });
  $('form button').on('click', function(event) {
    if ($(this).hasClass('download')) {
      $(this).parents('form').addClass('download-button-clicked');
    } else {
      $(this).parents('form').removeClass('download-button-clicked');
    }
    return true;
  });
  $('form .buttons.submit button').on('click', function (event) {
      if (autosave_is_running) {
          autosave_button_to_click_on_complete = event.target;
          /* prevent more autosave */
          autosave_timeout_id = null;
          event.preventDefault();
      }
  });
  $('form').on('submit', function(event) {
    var $form = $(this);
    window.disable_beforeunload = true;
    /* prevent more autosave */
    if (autosave_timeout_id !== null) {
      window.clearTimeout(autosave_timeout_id);
      autosave_timeout_id = null;
    }
    $form.addClass('disabled-during-submit');
    if ($form.hasClass('download-button-clicked')) {
      /* form cannot be disabled for download buttons as the user will stay on
       * the same page; enable it back after a few seconds. */
      setTimeout(function() {
        /* request new _ts value */
        $.getJSON(window.location.pathname + 'tsupdate', function(data) {
          $form.find('[type="hidden"][name="_ts"]').val(data.ts);
          $form.removeClass('disabled-during-submit');
        });
      }, 3000);
    }
    if ($form[0].wait_for_changes) {
      var waited = 0;
      var $button = $(event.originalEvent.submitter);
      if (! $button.is('button')) {
        $button = $('form .buttons .submit-button button');
      }
      var wait_id = setInterval(function() {
        waited += 1;
        if (! $form[0].wait_for_changes) {
          clearInterval(wait_id);
          $button.click();
          return;
        } else if (waited > 5) {
          $form[0].wait_for_changes = false;
        }
      }, 200);
      return false;
    }
    return true;
  });

  var live_evaluation = null
  var live_evaluation_params = Object()
  var live_evaluation_delay_id = null
  if ($('div[data-live-source]').length || $('.submit-user-selection').length) {
    $('form[data-live-url]').on('wcs:change', function(ev, data) {
      var $form = $(this)
      if (data.modified_field) {
        if (data.modified_block) {
          live_evaluation_params[data.modified_block] = true
          live_evaluation_params[`${data.modified_block} ${data.modified_field} ${data.modified_block_row}`] = true
        } else {
          live_evaluation_params[data.modified_field] = true
        }
      }
      if (live_evaluation === null) {
        // no delay for first call
        call_live_url($form)
      } else {
        if (live_evaluation && live_evaluation.readyState != 4 /* done */ ) {
          // live call being processed, delay this one
          timeout = 500
        } else {
          // always call after mini-delay
          timeout = 150
        }
        if (live_evaluation_delay_id) {
          clearTimeout(live_evaluation_delay_id)
        }
        live_evaluation_delay_id = setTimeout(() => { call_live_url($form) }, timeout)
      }
    })
  }

  function call_live_url($form) {
    var new_data = $form.serialize();
    for (let modified_field_id of Object.keys(live_evaluation_params)) {
      new_data += '&modified_field_id[]=' + modified_field_id
    }
    var has_user_prefill = live_evaluation_params.user
    live_evaluation_params = Object()  // reset

    $('.widget-prefilled').each(function(idx, elem) {
      var field_id = $(elem).data('field-id');
      var parent_block = $(elem).parents('.BlockWidget').first();
      if (parent_block.length) {
         field_id = `${parent_block[0].dataset.fieldId}-${field_id}-${elem.closest('.BlockSubWidget').dataset.blockRow}`;
      }
      new_data += '&prefilled_' + field_id + '=true';
    });
    var live_url = $form.data('live-url');
    live_evaluation = $.ajax({
      type: 'POST',
      url: live_url,
      dataType: 'json',
      data: new_data,
      headers: {'accept': 'application/json'},
      success: function(json) {
        if (json.result === "error") {
          console.log('error in /live request: ' + json.reason);
          return;
        }
        $.each(json.result, function(key, value) {
          if (value.block_id && value.block_row) {
            var $widget = $('[data-field-id="' + value.block_id + '"] [data-block-row="' + value.block_row + '"] [data-field-id="' + value.field_id + '"]');
          } else if (value.block_id) {
            var $widget = $('[data-field-id="' + value.block_id + '"] [data-field-id="' + value.field_id + '"]');
          } else {
            var $widget = $('[data-field-id="' + key + '"]');
          }
          if (value.visible) {
            var was_visible = $widget.is(':visible');
            $widget.css('display', '');
            if (($widget.hasClass('MapWidget') || $widget.hasClass('MapMarkerSelectionWidget') || $widget.hasClass('BlockWidget')) && !was_visible) {
              // add mini-delay to workaround wrong width calculation bug
              setTimeout(function() { $widget.find('.qommon-map').trigger('qommon:invalidate'); }, 10);
            }
          } else {
            $widget.hide();
          }
          if (value.items && ($widget.is('.RadiobuttonsWidget') || $widget.is('.TimeRangeWidget'))) {
            var current_value = $widget.find('input:checked').val();
            var input_class = $widget.find('input').attr('class');
            var input_name = $widget.find('input').attr('name');
            if (!input_name)
              input_name = $widget.data('widget-name');
            var $ul = $widget.find('.RadiobuttonsWidget--list');
            var length_first_items = 0;
            var base_for_name = $ul.data('base-for-name');
            $ul.empty();
            for (var i=0; i<value.items.length; i++) {
              var $li = $('<li>');
              var $label = $('<label>', {'for': base_for_name + i});
              var $input = $('<input>', {
                      type: 'radio', 'id': base_for_name + i,
                      value: value.items[i].id, name: input_name,
                      'class': input_class});
              if (value.items[i].id == current_value) {
                $input.prop('checked', true);
              }
              if (value.items[i].disabled) {
                $input.prop('disabled', true);
                $li.addClass('disabled');
              }
              var $span = $('<span></span>', {text: value.items[i].text});
              $input.appendTo($label);
              $span.appendTo($label);
              $label.appendTo($li);
              $li.appendTo($ul);
              if (i < 6) {
                length_first_items += value.items[i].text.length;
              }
            }
            if ($widget.is('.widget-radio-orientation-auto')) {
              if (value.items.length <= 6 && length_first_items < 40) {
                $widget.addClass('widget-inline-radio');
              } else {
                $widget.removeClass('widget-inline-radio');
              }
            }
            $ul[0].dispatchEvent(new CustomEvent('wcs:options-change', {'detail': value.items}))
          } else if (value.items && $widget.is('.CheckboxesWidget')) {
            var widget_name = $widget.data('widget-name');
            var $ul = $widget.find('ul');
            var current_value = $ul.find('input[type=checkbox]'
                    ).filter(function() {return this.checked}
                    ).map(function() {return this.name;}
                    ).toArray();
            var base_for_name = $ul.data('base-for-name');
            var input_name = $widget.data('widget-name');
            $ul.empty();
            for (var i=0; i<value.items.length; i++) {
              var $li = $('<li>');
              var $label = $('<label>', {'for': base_for_name + i});
              var $input = $('<input>', {
                      type: 'checkbox', 'id': base_for_name + i,
                      value: 'yes', name: widget_name + '$element' + value.items[i].id});
              if (current_value.indexOf(widget_name + '$element' + value.items[i].id) != -1) {
                $input.attr('checked', 'checked');
              }
              if (value.items[i].disabled) {
                $input.prop('disabled', true);
                $li.addClass('disabled');
              }
              var $span = $('<span>', {text: value.items[i].text});
              $input.appendTo($label);
              $span.appendTo($label);
              $label.appendTo($li);
              $li.appendTo($ul);
            }
          } else if (value.items && ($widget.is('.RadiobuttonsWithImagesWidget') || $widget.is('.CheckboxesWithImagesWidget'))) {
            liveUpdateImageListField($widget.get(0), value)
          } else if (value.items) {
            // replace <select> contents
            var $select = $widget.find('select');
            var current_value = $select.val();
            var hint = $widget.find('option[data-hint]').data('hint');
            $select.empty();
            if (hint) {
              var $option = $('<option></option>', {value: '', text: hint});
              $option.attr('data-hint', hint);
              $option.appendTo($select);
            }
            var group_by = null;
            var $options_parent_element = $select;
            for (var i=0; i<value.items.length; i++) {
              if (value.items[i].group_by && value.items[i].group_by != group_by) {
                var $optgroup = $('<optgroup></optgroup>', {label: value.items[i].group_by});
                $optgroup.appendTo($select);
                $options_parent_element = $optgroup;
                group_by = value.items[i].group_by;
              }
              var $option = $('<option></option>', {value: value.items[i].id, text: value.items[i].text});
              if ((Array.isArray(current_value) && current_value.indexOf(value.items[i].id.toString()) != -1) ||
                      value.items[i].id == current_value) {
                $option.attr('selected', 'selected');
                value.items[i].selected = true;
              }
              if (value.items[i].disabled) {
                $option.prop('disabled', true);
              }
              $option.appendTo($options_parent_element);
            }
            $select.trigger('wcs:options-change', {items: value.items});
          }
          if (value.items) {
            const $form = $widget.parents('form');
            $form[0].dispatchEvent(new CustomEvent('wcs:field-options-change'));
          }
          if (typeof value.content !== 'undefined') {
            $widget.each(function(idx, widget) {
              if ($widget.hasClass('comment-field')) {
                // replace comment content
                $widget.html(value.content);
              } else {
                if ($(widget).is('.widget-prefilled') || $(widget).is('.widget-readonly') || has_user_prefill) {
                  // replace text input value
                  const $text_inputs = $(widget).find('input[type=text], input[type=tel], input[type=numeric], input[type=email], input[type=date], input[type=time], input[type=url], textarea');
                  $text_inputs.val(value.content)
                  $text_inputs.each((_, el) => el.dispatchEvent(new Event('wcs:live-update')));
                  if ($(widget).is('.DateWidget')) {
                    // Set both hidden input for real value, and text input for
                    // formatted date. This will also set the old date picker
                    // to the formatted value, which is expected.
                    $(widget).find('input[type=hidden]').val(value.content);
                    $(widget).find('input[type=text]').val(value.text_content);
                  } else if ($(widget).is('.FileWithPreviewWidget')) {
                    $.WcsFileUpload.set_file(widget, value.content);
                  } else if ($widget.hasClass('CheckboxWidget')) {
                    // replace checkbox input value
                    $widget.find('input[type=checkbox]').prop('checked', value.content);
                  }
                  if ($widget.is('.JsonpSingleSelectWidget') && value.display_value) {
                    $(widget).find('select').empty();
                    var option = $('<option></option>', {value: value.content, text: value.display_value});
                    option.appendTo($(widget).find('select'));
                  }
                  // replace select value
                  $(widget).find('select').val(value.content);
                  if ($widget.is('.SingleSelectHintWidget') && $widget.find('select[data-autocomplete]').length) {
                    // autocomplete, select2 must be notifed to update its part
                    $widget.find('select[data-autocomplete]').trigger('change.select2');
                  }
                  if ($.type(value.content) == 'string' && value.content.indexOf('"') == -1) {
                    // replace radio value
                    $(widget).find('input[type=radio]').prop('checked', false);
                    $(widget).find('input[type=radio][value="'+value.content+'"]').prop('checked', true);
                  }
                  if (value.locked) {
                    $(widget).addClass('widget-readonly');
                    $(widget).find('input').attr('readonly', 'readonly');
                    $(widget).find('select').attr('readonly', 'readonly');
                    $(widget).find('select option:not(:selected)').hide();
                    if ($.fn.select2) {
                      $(widget).find('select.select2-hidden-accessible').select2({minimumResultsForSearch: Infinity});
                    }
                  } else {
                    $(widget).removeClass('widget-readonly');
                    $(widget).find('input').attr('readonly', null);
                    $(widget).find('select').attr('readonly', null);
                    $(widget).find('select option').show();
                  }
                }
              }
            });
          }
          if (value.source_url) {
            // json change of URL
            $widget.find('[data-select2-url]').data('select2-url', value.source_url);
          }
        });
      }
    });
  }
  if ($('div[data-live-source]').length) {
    $('form').each((idx, form) => {
      const inputsSelector = [
        'div[data-live-source] input:not([type=file])',
        'div[data-live-source] select',
        'div[data-live-source] textarea'
      ].join()

      function watchInputs() {
        form.querySelectorAll(inputsSelector).forEach((input) => {
          ['change', 'input', 'paste', 'wcs:change'].forEach((eventName) => {
            $(input).on(eventName, () => {
              const params = {};
              params['modified_field'] = input.closest('[data-field-id]').dataset.fieldId

              const parent = input.closest('.BlockWidget')
              if (parent !== null) {
                params['modified_block'] = parent.dataset.fieldId
                params['modified_block_row'] = input.closest('.BlockSubWidget').dataset.blockRow
              }

              $(form).trigger('wcs:change', params)
            })
          })
        })
      }

      watchInputs()

      form.addEventListener('wcs:block-row-added', watchInputs)
      form.addEventListener('wcs:field-options-change', watchInputs)
    });
  }
  $('form div[data-live-source]').parents('form').trigger('wcs:change', {modified_field: 'init'});
  $('div.widget-prefilled').on('change input paste', function(ev) {
    $(this).removeClass('widget-prefilled');
  });
  $('div.widget-prefilled input[type=radio], div.widget-prefilled input[type=checkbox]').on('change', function(ev) {
    $(this).closest('div.widget').removeClass('widget-prefilled');
  });


  function disable_single_block_remove_button() {
    $('.BlockSubWidget button.remove-button').each(function(i, elem) {
      if ($(this).parents('.BlockWidget').find('.BlockSubWidget').length == 1) {
        $(this).prop('disabled', true);
        $(this).parents('.BlockWidget').addClass('wcs-block-with-remove-button-single');
      } else {
        $(this).parents('.BlockWidget').removeClass('wcs-block-with-remove-button-single');
      }
    });
  }

  if ($('.BlockWidget').length) {
    disable_single_block_remove_button();
    $('form').on('click', '.BlockSubWidget button.remove-button', function() {
      if ($(this).parents('.BlockWidget').find('.BlockSubWidget').length > 1) {
        const $add_button = $(this).parents('.BlockWidget').find('.list-add');
        /* rename attributes in following blocks */
        const $subwidget = $(this).parents('.BlockSubWidget').first();
        const subwidget = $subwidget[0];
        const name_parts = $subwidget.data('widget-name').match(/(.*)(\d+)$/);
        const prefix = name_parts[1];
        const idx = parseInt(name_parts[2]);
        function replace_prefix(elem, prefix, idx1, idx2) {
          const prefix1 = prefix + idx1;
          const prefix2 = prefix + idx2;
          $.each(elem.attributes, function() {
              // replace attributes with value like $elementXX$
              this.value = this.value.replace(prefix1, prefix2);
              // replace attributes with value like __elementXX__
              this.value = this.value.replace(prefix1.replace('$', '__'), prefix2.replace('$', '__'));
              // replace attributes with value equals to elementXX
              if (/^element(\d+)$/.exec(this.value)) {
                this.value = this.value.replace(/^element(\d+)$/, 'element' + idx2);
              }
          });
        }
        subwidget.addEventListener('transitionend', function(event){
          if (event.propertyName != "height") return;
          $subwidget.nextAll('.BlockSubWidget').each(function(i, elem) {
            const idx1 = (idx + i + 1);
            const idx2 = (idx + i);
            replace_prefix(elem, prefix, idx1, idx2);
            $(elem).find('*').each(function(i, elem_child) { replace_prefix(elem_child, prefix, idx1, idx2); });
          });
          /* then remove row */
          $subwidget.remove();
          disable_single_block_remove_button();
          /* display button then give it focus */
          $add_button.show().find('button').attr('type', null).focus();
        })

        subwidget.style.height = subwidget.clientHeight + 'px';
        subwidget.style.transition = 'opacity 350ms, height 250ms 350ms';
        setTimeout( () => {
          subwidget.style.opacity = '0';
          subwidget.style.height = '0';
        }, 20)
      }
      return false;
    });

    $('form').on('click', 'div.BlockWidget .list-add button', function(ev) {
      ev.preventDefault();
      const $block = $(this).parents('.BlockWidget');
      const block_id = $block.data('field-id');
      const $button = $(this);
      const $form = $(this).parents('form');
      const currently_prefilled = $.map($block.find('.BlockSubWidget .widget-prefilled'), function(x) { return $(x).attr('data-widget-name') });
      var form_data = $form.serialize();
      form_data += '&' + $button.attr('name') + '=' + $button.val();
      $.ajax({
        type: 'POST',
        url: $form.attr('action') || window.location.pathname,
        data: form_data,
        headers: {'x-wcs-ajax-action': 'block-add-row'},
        success: function(result, text_status, jqXHR) {
          var new_form_token = $(result).find('input[name="_form_id"]').val()
          $('input[name="_form_id"]').val(new_form_token)
          const $new_block = $(result).find('[data-field-id="' + block_id + '"]');
          currently_prefilled.forEach((widget_name) => {
            $new_block.find(`[data-widget-name="${widget_name}"]`).addClass('widget-prefilled');
          });
          $block.replaceWith($new_block);
          const $new_blockrow = $new_block.find('.BlockSubWidget').last();
          add_js_behaviours($('[data-field-id="' + block_id + '"]'));
          $form.trigger('wcs:block-row-added');
          $form[0].dispatchEvent(new CustomEvent('wcs:block-row-added', { detail: { newBlock: $new_block[0] } } ));
          $(document).trigger('wcs:maps-init');
          if ($new_block.find('[data-live-source]')) {
            $('form div[data-live-source]').parents('form').trigger('wcs:change', {modified_field: 'init'});
          }
          $new_blockrow[0].setAttribute('tabindex', '-1');
          $new_blockrow[0].focus();
          if ($new_blockrow.position().top < window.scrollY) {
            $new_blockrow[0].scrollIntoView({behavior: "instant", block: "center", inline: "nearest"});
          }
        }
      });
    });
  }
});

/*
 *  Live Field Update
 */

function liveUpdateImageListField(widget, value) {
  const fieldNameForId = widget.dataset.widgetNameForId
  const fieldName = widget.dataset.widgetName
  const itemTemplate = widget.querySelector(`#item_${fieldNameForId}`)

  const fieldContent = widget.querySelector('.content')
  for(let children of Array.from(fieldContent.children)) {
    if(children.tagName != "TEMPLATE") {
      children.remove()
    }
  }

  value.items.forEach((itemValue, idx) => {
    const itemId = `${fieldNameForId}_op_${idx}`
    const item = itemTemplate.content.firstElementChild.cloneNode(true)
    item.setAttribute('for', itemId)

    const itemInput = item.querySelector('.item-with-image--input')
    itemInput.setAttribute('id', itemId)
    if(itemInput.getAttribute('type').toLowerCase() == 'radio') {
      itemInput.setAttribute('name', fieldNameForId)
      itemInput.setAttribute('value', itemValue.id)
    } else {
      itemInput.setAttribute('name', `${fieldName}$element${itemValue.id}`)
      itemInput.setAttribute('value', 'yes')
    }

    const itemImage = item.querySelector('.item-with-image--picture')
    if(itemValue.image_url) {
      itemImage.setAttribute('src', itemValue.image_url)
      itemImage.setAttribute('title', itemValue.text)
    } else {
      itemImage.remove()
    }

    const itemLabel = item.querySelector('.item-with-image--label')
    itemLabel.innerHTML = itemValue.text

    fieldContent.appendChild(item)
  })
}

/*
 *  Live Field Validation
 */

const LiveValidation = (function(){

  const excludedField = function (field) {
    if (field.disabled || field.readOnly ) return true
    const excludedType = [ 'button', 'reset', 'submit' ]
    if (excludedType.includes(field.type)) return true
    return false
  }

  /*
   * Check validity of field by HTML attributes
   * cf JS constraint validation API
   * return first error found
   */
  const hasAttrError = function (field) {
    const validityState = field.validity
    if (typeof validityState === 'undefined' || validityState.valid) return

    let errorType
    for (const key in validityState) {
      if (validityState[key]) {
        errorType = key
        break
      }
    }
    return [errorType, false]
  }

  /*
   * Check validity of field by request to server
   */
  const hasServerError = async function (name, field, form, url) {
    let json
    try {
      const response = await fetch( url+name, {
        method: 'POST',
        body: new FormData(form)
      })

      if (!response.ok) {
        throw new Error("liveValidation server, response not ok: " + response.status)
      }

      json = await response.json()

    } catch (error) {
      console.error(error.message)
      return // remove field error
    }

    if (json.err !== 1) return

    return [json.errorType, json.msg]
  }

  class FieldLiveValidation {
    constructor (widget, formDatas) {
      this.widget = widget
      this.name = widget.dataset.widgetNameForId
      this.errorClass = "widget-with-error"
      this.errorEl = this.setErrorEl(formDatas.errorTpl.content.children[0])
      this.checkUrl = formDatas.checkUrl
      this.hasError = false
      this.init()
    }

    setErrorEl(errorTpl) {
      const errorEl = document.importNode(errorTpl)
      errorEl.id = errorEl.id.replace('fieldname', this.name)
      return errorEl
    }

    async toggleStatus(field) {
      if (excludedField(field)) return

      const attrError = hasAttrError(field)
      const serverError = () => {
        return this.widget.dataset.useLiveServerValidation
          ? hasServerError(this.name, field, field.form, this.checkUrl)
          : false
      }
      const error = attrError ? attrError : await serverError()

      if (error) {
          const [errorType, overrideMsg] = error
          this.showError(field, errorType, overrideMsg)
      } else {
        this.removeError(field)
      }
    }

    showError(field, errorType, overrideMsg) {
      if(!this.hasError) {
        this.widget.classList.add(this.errorClass)
        this.widget.appendChild(this.errorEl)
        field.setAttribute("aria-invalid", "true")
        field.setAttribute("aria-describedby", this.errorEl.id)
      }

      const errorElMessage = document.getElementById(`error_${this.name}_${errorType}`).innerHTML
      this.errorEl.innerHTML = errorElMessage
      if(overrideMsg) {
        const errorMessageContainer = this.errorEl.querySelector(`#error_${this.name}_${errorType}_message`)
        errorMessageContainer.innerHTML = overrideMsg
      }
      this.hasError = errorType
    }

    removeError(field) {
      if(!this.hasError) {
        return
      }

      this.errorEl.remove()
      field.setAttribute("aria-invalid", "false")
      field.setAttribute("aria-describedby", this.errorEl.id)
      this.widget.classList.remove(this.errorClass)
      this.hasError = false
      var base_field_widget_id = null
      var current_widget = this.widget
      // for fields in blocks, a single error is displayed on top, using the block name,
      // look for it and remove it as soon as the user is correcting the form
      // (even if there are still some errors in other subfields)
      while (current_widget.nodeName != 'FORM') {
        if (current_widget.dataset.widgetNameForId) base_field_widget_id = current_widget.dataset.widgetNameForId
        current_widget = current_widget.parentNode
      }
      var comma = document.querySelector(`#field-error-links [data-field-name="${base_field_widget_id}"] + span.list-comma`)
      if (comma) comma.remove()
      const top_error_link = document.querySelector(`#field-error-links [data-field-name="${base_field_widget_id}"]`)
      if (top_error_link) {
        top_error_link.remove()
        if (! document.querySelector('#field-error-links a')) {
          document.querySelector('#field-error-links').remove()
        }
      }
    }

    init() {
      // Check if field is already on error
      if (this.widget.classList.contains(this.errorClass)) {
        this.hasError = true;
        // Check if error element exist already
        const existingErrorEl = document.getElementById(this.errorEl.id)
        if (existingErrorEl)
          this.errorEl = existingErrorEl;
      }

      // Events
      this.widget.addEventListener('blur', (event) => {
        if (event.target.type === 'checkbox') return
        this.toggleStatus(event.target)
      }, true);

      this.widget.addEventListener('change', (event) => {
        if (!event.isTrusted || event.target.type === 'checkbox') {
          // will be handled by blur handler if event.isTrusted
          this.toggleStatus(event.target)
        }
      }, true);

      // If field has Error, check when it changes with debounce
      let timeout;
      this.widget.addEventListener('input', (event) => {
        if (this.hasError) {
          clearTimeout(timeout)
          timeout = setTimeout(() => {
            this.toggleStatus(event.target)
          }, 500)
        }
      }, true);
    }
  }

  return FieldLiveValidation
})()

document.addEventListener('DOMContentLoaded', function(){
  const form = document.querySelector('form[data-live-validation-url]')
  if (!form) return
  const widgetSelector = '.widget:not(.BlockWidget):not(.BlockSubWidget)'
  const formWidgets = form.querySelectorAll(widgetSelector)
  let formDatas = {
    errorTpl: document.getElementById('form_error_tpl'),
    checkUrl: form.dataset.liveValidationUrl + '?field=',
  }
  function initLiveValidation(widgets) {
    widgets.forEach((widget) => {
      new LiveValidation(widget, formDatas)
    })
  }
  initLiveValidation(formWidgets)
  form.addEventListener('wcs:block-row-added', (event) => {
    const blockWidgets = event.detail.newBlock.querySelectorAll(widgetSelector)
    initLiveValidation(blockWidgets)
  })
})

document.addEventListener('DOMContentLoaded', function(){
  const previous_page_id_input = document.querySelector('[name="previous-page-id"]')
  if (!previous_page_id_input) return
  document.querySelectorAll('.wcs-step[data-page-id]').forEach((step, idx) => {
    step.addEventListener('keydown', function(e) {
      if (e.key !== "Enter" && e.key !== " ") return
      e.preventDefault()
      previous_page_id_input.value = step.dataset.pageId
      document.querySelector('button[name="previous"]').dispatchEvent(new MouseEvent('click'))
    })
    step.addEventListener('click', function() {
      previous_page_id_input.value = step.dataset.pageId
      document.querySelector('button[name="previous"]').dispatchEvent(new MouseEvent('click'))
    })
  })
})

document.addEventListener('DOMContentLoaded', function() {
  const formdata_page_div = document.getElementById('formdata-page')
  if (! formdata_page_div) return
  const processing = formdata_page_div.dataset.workflowProcessing
  if (! processing) return
  const afterjob_id = formdata_page_div.dataset.workflowProcessingAfterjobId
  var progress_url = `${window.location.pathname}check-workflow-progress`
  if (afterjob_id) {
    progress_url += `?job=${afterjob_id}`
  }

  var wait_count = 0

  function check_workflow_progress() {
    wait_count += 1
    fetch(progress_url).then((response) => {
      if (! response.ok) {
        clearTimeout(timeout_id)
        return
      }
      return response.json()
    }).then((json) => {
      if (json && json.status == 'idle') {
        clearTimeout(timeout_id)
        if (json.url) {
          window.location = json.url
        } else {
          window.location = window.location.pathname
        }
      } else {
        // check again
        var delay = 5000
        if (wait_count < 5) {
          delay = 200
        } else if (wait_count < 20) {
          delay = 1000
        }
        timeout_id = setTimeout(check_workflow_progress, delay)
      }
    })
  }

  var timeout_id = setTimeout(check_workflow_progress, 200)
})

document.addEventListener('DOMContentLoaded', function() {
  var waiting_for_scan_spans = document.querySelectorAll('span.waiting-for-scan-file')
  if (waiting_for_scan_spans.length == 0) return
  var scan_url = `${window.location.pathname}scan`

  var wait_count = 0

  function check_scan_progress() {
    wait_count += 1
    fetch(scan_url).then((response) => {
      if (! response.ok) {
        clearTimeout(timeout_id)
        return
      }
      return response.json()
    }).then((json) => {
      if (!json || json.err !== 0) {
        clearTimeout(timeout_id)
      } else {
        for (let i = 0; i < json.data.length; i++) {
          var scan_data = json.data[i]
          var target = document.querySelector(`span.waiting-for-scan-file[data-clamd-digest="${scan_data.digest}"]`)
          if (target) {
            target.setAttribute('class', scan_data.span_class)
            target.textContent = scan_data.span_msg
          }
        }
        waiting_for_scan_spans = document.querySelectorAll('span.waiting-for-scan-file')
        if (waiting_for_scan_spans.length != 0) {
          var delay
          if (wait_count < 5) {
            delay = 1000
          } else if (wait_count < 20) {
            delay = 2000
          } else {
            delay = 5000
          }
          timeout_id = setTimeout(check_scan_progress, delay)
        }
      }
    })
  }

  var timeout_id = setTimeout(check_scan_progress, 1000)
})
