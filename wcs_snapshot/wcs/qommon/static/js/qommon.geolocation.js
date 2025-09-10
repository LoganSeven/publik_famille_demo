function geoloc_prefill(element_type, element_values, widget_name=null)
{
  var selector = 'div[data-geolocation="' + element_type +'"]:first';
  if (widget_name) {
    selector = 'div[data-widget-name="' + widget_name + '"] ~ ' + selector;
  }
  var $input_widget = $(selector).find('input');
  var $text_widget = $(selector).find('textarea');
  var $select_widget = $(selector).find('select');
  var $options = $(selector).find('option');

  var found = false;
  for (var j=0; j < element_values.length && !found; j++) {
    element_value = element_values[j];
    if (typeof(element_value) == 'undefined' || element_value === null) {
      continue;
    }
    if ($input_widget.length) {
      $input_widget.val(element_value)
      $input_widget.each((idx, elt) => elt.dispatchEvent(new Event('change', {'bubbles': true})))
      found = true;
    } else if ($text_widget.length) {
      $text_widget.val(element_value)
      $text_widget.each((idx, elt) => elt.dispatchEvent(new Event('change', {'bubbles': true})))
      found = true;
    } else if ($select_widget.length) {
      if ($options.length == 0) break;
      var slugified_value = window.slugify(element_value);
      for (var i=0; i<$options.length; i++) {
        var $option = $($options[i]);
        if (window.slugify($option.val()) == slugified_value ||
            window.slugify($option.text()) == slugified_value) {
          $option.prop('selected', true);
          $option.parent().each((idx, elt) => elt.dispatchEvent(new Event('change', {'bubbles': true})))
          found = true;
          break;
        }
      }
    }
  }
  if (!found) {
    if ($input_widget.length) {
      $input_widget.val('')
      $input_widget.each((idx, elt) => elt.dispatchEvent(new Event('change')))
    } else if ($text_widget.length) {
      $text_widget.val('')
      $text_widget.each((idx, elt) => elt.dispatchEvent(new Event('change')))
    } else if ($select_widget.length && $options.length) {
      $($options[0]).prop('selected', true);
      $($options[0]).parent().each((idx, elt) => elt.dispatchEvent(new Event('change')))
    }
  }
  $(selector).removeClass('widget-prefilled');
}

function init_sync_from_template_address() {
  // turn address-part-- class names into geolocation attributes, this allows
  // address part elements to be both prefilled with some data and recognized
  // as parts of the address block mechanism.
  $('.address-part--number-and-street').attr('data-geolocation', 'number-and-street');
  $('.address-part--house').attr('data-geolocation', 'house');
  $('.address-part--road').attr('data-geolocation', 'road');
  $('.address-part--city').attr('data-geolocation', 'city');
  $('.address-part--postcode').attr('data-geolocation', 'postcode');
  $('.address-part--country').attr('data-geolocation', 'country');

  const widget_selector = '.JsonpSingleSelectWidget.template-address';
  const hidden_parts_selector = '.hide-address-parts';

  // mark address field as required if any of its components are required.
  $(widget_selector + ':not(.widget-required)').each(function(idx, elem) {
    const $widget = $(elem);
    if ($widget.nextUntil(widget_selector, 'div[data-geolocation].widget-required:not(.template-address):not(.MapWidget)').length) {
      $widget.addClass('widget-required')
      var $required_marker = $('.title span.required').first().clone();
      $required_marker.appendTo($widget.find('.title label'));
    }
  })

  $(widget_selector + ' select').on('change', function() {
    var data = $(this).select2('data');
    var widget_name = $(this).parents('div.widget').data('widget-name');
    if (data && data.length) {
      var number_and_street = null;
      var address = undefined;
      if (typeof data[0].address == "object") {
        address = data[0].address;
      } else {
        address = data[0];
      }
      var road = address.road || address.nom_rue;
      var house_number = address.house_number || address.numero;
      var city = address.city || address.nom_commune;
      var postcode = address.postcode || address.code_postal;
      if (house_number && road) {
        number_and_street = house_number + ' ' + road;
      } else {
        number_and_street = road;
      }
      geoloc_prefill('number-and-street', [number_and_street], widget_name);
      geoloc_prefill('house', [house_number], widget_name);
      geoloc_prefill('road', [road], widget_name);
      geoloc_prefill('city', [city], widget_name);
      geoloc_prefill('postcode', [postcode], widget_name);
      geoloc_prefill('country', [address.country], widget_name);
    }
  });

  $('input.wcs-manual-address').on('change', function() {
    var widget = $(this).parents('div.widget');
    widget.nextUntil(widget_selector, 'div[data-geolocation]').find('input').attr('readonly', this.checked ? null : 'readonly');
    widget.nextUntil(widget_selector, 'div[data-geolocation]').find('textarea').attr('readonly', this.checked ? null : 'readonly');
    if (this.checked) {
      widget.nextUntil(widget_selector, 'div[data-geolocation]:not(.template-address):not(.MapWidget)').attr('hidden', null)
    } else if (widget.is(hidden_parts_selector)) {
      widget.nextUntil(widget_selector, 'div[data-geolocation]:not(.template-address):not(.MapWidget)').attr('hidden', 'hidden')
    }
  });

  if ($(widget_selector).length) {
    $('div[data-geolocation] input, div[data-geolocation] textarea').attr('readonly', 'readonly')
    if ($(widget_selector + hidden_parts_selector).length) {
      $(widget_selector + hidden_parts_selector).nextUntil(widget_selector, 'div[data-geolocation]:not(.template-address):not(.MapWidget)').attr('hidden', 'hidden');
    }
  }
  $(widget_selector).each(function(idx, elem) {
    var $manual_checkbox = $(elem).find('input.wcs-manual-address');
    if ($(elem).nextUntil('.template-address', '[data-geolocation].widget-with-error').length) {
      // enable manual address mode if there is an error in one of the manual address fields.
      $manual_checkbox.prop('checked', true).trigger('change');
    } else {
      // enable manual address mode if a manual field has data while the select is empty
      // (typically when going back to a previous page)
      var has_val = $(elem).find('select').val();
      if (! has_val) {
        var has_manual_var = false;
        $(elem).nextUntil('.template-address', 'div[data-geolocation]').find('input').each(function(idx, manual_elem) {
          if ($(manual_elem).val()) has_manual_var = true;
        })
        $(elem).nextUntil('.template-address', 'div[data-geolocation]').find('textarea').each(function(idx, manual_elem) {
          if ($(manual_elem).val()) has_manual_var = true;
        })
        if (has_manual_var) {
          $manual_checkbox.prop('checked', true).trigger('change');
        }
      }
    }
  });
}


$(function() {
  init_sync_from_template_address();
  $('form').on('wcs:block-row-added', function() {
    init_sync_from_template_address();
  });
  $(document).on('set-geolocation', function(event, coords, options) {
    var widget_name = $(event.target).parents('div.widget').data('widget-name');
    $.getJSON(WCS_ROOT_URL + '/api/reverse-geocoding?lat=' + coords.lat + '&lon=' + coords.lng, function(data) {
      unset_sync_callback()
      if (data === null) {
        data = {address: {road: undefined, house_number: undefined}}
      } else if (data.err) {
        return
      }

      var $prefilled_by_id = $('div[data-geolocation="address-id"]');
      if ($prefilled_by_id.length) {
        // prefill by id first as the .change() event will empty the other
        // address fields.
        var option = $('<option></option>', {value: data.id, text: data.display_name});
        option.appendTo($prefilled_by_id.find('select'));
        $prefilled_by_id.find('select').val(data.id).change();
      }

      if (typeof(options) == 'undefined' || !options.force_house_number === false || data.address.house_number) {
        geoloc_prefill('house', [data.address.house_number], widget_name);
      }
      var number_and_street = null;
      var street = data.address.road;
      if (!street && data.address.pedestrian) {
        street = data.address.pedestrian;
      } else if (!street && data.address.footway) {
        street = data.address.footway;
      } else if (!street && data.address.path) {
        street = data.address.path;
      } else if (!street && data.address.cycleway) {
        street = data.address.cycleway;
      } else if (!street && data.address.park) {
        street = data.address.park;
      }
      geoloc_prefill('road', [street], widget_name);
      if (street && data.address.house_number) {
        number_and_street = data.address.house_number + ' ' + street;
      } else {
        number_and_street = street;
      }
      geoloc_prefill('number-and-street', [number_and_street], widget_name);
      geoloc_prefill('postcode', [data.address.postcode], widget_name);
      geoloc_prefill('city', [data.address.village, data.address.town, data.address.city, data.address.locality, data.address.municipality, data.address.county], widget_name);
      geoloc_prefill('country', [data.address.country], widget_name);
      $(document).trigger('wcs:set-last-auto-save');
      set_sync_callback()
    });
  });

  if ($('.qommon-map').length == 0 && $('.JsonpSingleSelectWidget.template-address').length == 0) {
    /* if there's no map on the page, we do the geolocation without leaflet. */
    if (navigator.geolocation) {
      $('div[data-geolocation] label').addClass('activity');
      navigator.geolocation.getCurrentPosition(
        function (position) {
          $('div[data-geolocation] label').removeClass('activity');
          var coords = {lat: position.coords.latitude, lng: position.coords.longitude};
          $(document).trigger('set-geolocation', coords);
        },
        function (error_msg) {
          $('div[data-geolocation] label').removeClass('activity');
          $($('div[data-geolocation] label')[0]).after(
                          '<span class="geoloc-error">' + error_msg.message + '</span>');
        },
        {timeout: 10000, maximumAge: 300000, enableHighAccuracy: false}
      );
    }
  }


  function set_sync_callback() {
    var $map = $('.qommon-map');
    if ($map.length == 0) return;
    var $form = $map.parents('form');
    if ($form.length == 0) return;  // probably a geolocation map in sidebar
    $form[0].wait_for_changes = false;
    if (! $map.data('address-sync')) return;
    $('div[data-geolocation]').on('change', 'input[type=text], textarea, select', function(event) {
      if ($('.wcs-manual-address:checked').length && $(this).parents('.template-address').length == 0) {
        // do not sync address and map is the address is being entered manually
        // as it probably means the address isn't known and reverse/geocoding
        // won't be correct.
        return;
      }
      $form[0].wait_for_changes = true;
      var address = '';
      var found_city = false;
      var found_country = false;
      $(['number-and-street', 'house', 'road', 'postcode', 'city', 'country']).each(function(idx, elem) {
        var part = $('div[data-geolocation="' + elem + '"]').find('input, textarea').val();
        if (! part) {
          part = $('div[data-geolocation="' + elem + '"]').find('select option:selected:not([data-hint))').text()
        }
        if (part) {
          address += part + ' ';
          if (elem == 'number-and-street' || elem == 'road' || elem == 'city') {
            address += ', ';
          }
          if (elem == 'postcode' || elem == 'city') {
            found_city = true;
          }
          if (elem == 'country') {
            found_country = true;
          }
        }
      });
      if (found_city) {
        if (!found_country && typeof(WCS_DEFAULT_GEOCODING_COUNTRY) !== 'undefined') {
          address += WCS_DEFAULT_GEOCODING_COUNTRY;
        }
        $.getJSON(WCS_ROOT_URL + '/api/geocoding?q=' + address, function(data) {
          if (data && $(data).length > 0) {
            var coords = {lat: data[0].lat, lng: data[0].lon};
            var map = $map[0].leaflet_map;
            map.flyTo(coords);
            if (map.marker === null) {
              map.marker = L.marker([0, 0]);
              map.marker.addTo(map);
            }
            $map.trigger('set-geolocation', [coords, {'trigger': false, 'force_house_number': false}]);
            $form[0].wait_for_changes = false;
          }
        });
      }
    });
  }
  function unset_sync_callback() {
    $('div[data-geolocation]').off('change', 'input[type=text], textarea, select');
  }
  set_sync_callback();
});
