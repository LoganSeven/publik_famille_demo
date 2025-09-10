$(window).on('wcs:maps-init', function() {
  $('.qommon-map').each(function() {
     var $map_widget = $(this);
     var map_options = Object();
     var initial_zoom = parseInt($map_widget.data('initial_zoom'));
     if (! isNaN(initial_zoom)) {
       map_options.zoom = initial_zoom;
     } else {
       map_options.zoom = 13;
     }
     var max_zoom = parseInt($map_widget.data('max_zoom'));
     if (! isNaN(max_zoom)) {
       map_options.maxZoom = max_zoom;
     } else {
       map_options.maxZoom = 19;
     }
     var min_zoom = parseInt($map_widget.data('min_zoom'));
     if (! isNaN(min_zoom)) map_options.minZoom = min_zoom;
     map_options.zoomControl = false;
     var map_tile_urltemplate = $map_widget.data('tile-urltemplate');
     var map_attribution = $map_widget.data('map-attribution');
     if ($map_widget.data('max-bounds-lat1')) {
       map_options.maxBounds = L.latLngBounds(
               L.latLng($map_widget.data('max-bounds-lat1'), $map_widget.data('max-bounds-lng1')),
               L.latLng($map_widget.data('max-bounds-lat2'), $map_widget.data('max-bounds-lng2')));
     }
     map_options.gestureHandling = true;
     var map = L.map($(this).attr('id'), map_options);
     if (map_tile_urltemplate.indexOf('{x}') != -1) {
       map.attributionControl.setPrefix(
         '<a href="https://leafletjs.com" title="' + WCS_I18N.map_leaflet_title_attribute + '">Leaflet</a>')
     } else {
       map.attributionControl.setPrefix(
         '<a href="https://leafletjs.com" title="' + WCS_I18N.map_leaflet_title_attribute + '">Leaflet</a>' +
         ' + <a href="https://www.mapbox.com/about/maps/">Mapbox</a>')
     }
     var map_controls_position = $('body').data('map-controls-position') || 'topleft';
     if (! ($map_widget.parents('#sidebar').length || $map_widget.parents('td').length)) {
       new L.Control.Zoom({
         position: map_controls_position,
         zoomInTitle: WCS_I18N.map_zoom_in,
         zoomOutTitle: WCS_I18N.map_zoom_out
       }).addTo(map);
     }
     $map_widget[0].leaflet_map = map;
     var gps_control = new L.Control.Gps({
             position: map_controls_position,
             tooltipTitle: WCS_I18N.map_display_position
     });
     var hidden = $(this).prev();
     map.marker = null;
     var latlng;
     var initial_marker_id = $map_widget.data('markers-initial-id');
     if ($map_widget.data('init-lat')) {
       latlng = [$map_widget.data('init-lat'), $map_widget.data('init-lng')]
       if (typeof initial_marker_id == 'undefined') {
         // if markers are used they will appear via their input widget
         map.marker = L.marker(latlng);
         map.marker.addTo(map);
       }
     } else if ($map_widget.data('def-lat')) {
       latlng = [$map_widget.data('def-lat'), $map_widget.data('def-lng')]
     } else {
       latlng = [50.84, 4.36];
     }
     map.setView(latlng, map_options.zoom);
     if (map_tile_urltemplate.indexOf('{x}') != -1) {
       L.tileLayer(map_tile_urltemplate, {
             attribution: map_attribution,
             maxZoom: map_options.maxZoom
       }).addTo(map);
     } else {
       L.mapboxGL({
         style: map_tile_urltemplate,
         attribution: map_attribution,
         maxZoom: map_options.maxZoom
       }).addTo(map);
     }

     if (! $map_widget.data('readonly')) {
       map.addControl(gps_control);
     }
     if ($map_widget.data('markers-url')) {
       var radio_name = $map_widget.data('markers-radio-name');
       var markers_by_position = new Object();
       var hidden_marker_id = $('input[type=hidden][name="' + radio_name + '"]');

       function turn_marker_on(marker_id, lat, lng, position_key) {
         hidden.val(lat + ';' + lng);
         hidden_marker_id.val(marker_id);
         hidden.trigger('change');
         $map_widget.find('.marker-icon[data-position-key]').removeClass('marker-icon-on');
         $map_widget.find('.marker-icon[data-position-key="' + position_key + '"]').addClass('marker-icon-on');
       }

       $map_widget.on('change', 'input.marker-selector', function(ev) {
         var $radio = $(this);
         if ($radio.is(':checked')) {
           turn_marker_on($radio.val(), $radio.data('lat'), $radio.data('lng'), $radio.data('parent-position-key'));
         }
       });
       $.getJSON($map_widget.data('markers-url')).done(
         function(data) {
           var checked_lat = null;
           var checked_lng = null;
           var geo_json = L.geoJson(data, {
             pointToLayer: function (feature, latlng) {
               var position_key = latlng.lat.toFixed(6) + ';' + latlng.lng.toFixed(6);
               var marker = markers_by_position[position_key];
               var marker_on = (typeof initial_marker_id !== 'undefined' && feature.properties._id == initial_marker_id);
               if (typeof marker === 'undefined') {
                 var $div_content = $('<div></div>', {
                     title: feature.properties._text,
                     'data-marker-id': feature.properties._id,
                     'data-lat': latlng.lat,
                     'data-lng': latlng.lng
                 });
                 var $marker_icon = $('<span></span>', {
                     'class': 'marker-icon',
                     'data-marker-id': feature.properties._id,
                     'data-position-key': position_key
                 });
                 if (marker_on) {
                   $marker_icon.addClass('marker-icon-on');
                 }
                 $marker_icon.appendTo($div_content);
                 div_marker = L.divIcon({
                   className: 'item-marker',
                   html: $div_content.prop('outerHTML'),
                   iconSize: [25, 41]
                 });
                 marker = L.marker(latlng, {icon: div_marker});

                 // keep a list of all features at the same location
                 marker.radio_array = Array();
                 marker.radio_array.push({id: feature.properties._id, text: feature.properties._text});
                 markers_by_position[position_key] = marker;

                 // add a popup with feature text
                 var popup = L.popup().setContent($('<div></div>', {text: feature.properties._text}).prop('outerHTML'));
                 popup.marker = {id: feature.properties._id, lat: latlng.lat, lng: latlng.lng, position_key: position_key};
                 marker.bindPopup(popup, {offset: [0, -20]});
                 return marker;
               } else {
                 marker.radio_array.push({id: feature.properties._id, text: feature.properties._text});
                 var $ul_radios = $('<ul class="multi-marker-selection"></ul>');
                 $(marker.radio_array).each(function(i, feature_data) {
                    var $radio = $('<input>', {
                      value: feature_data.id,
                      name: 'radio-' + radio_name,
                      'class': 'marker-selector',
                      type: 'radio',
                      'data-lat': latlng.lat,
                      'data-lng': latlng.lng,
                      'data-parent-position-key': position_key
                    });
                    var $label = $('<label></label>');
                    $label.append($radio);
                    $label.append(feature_data.text);
                    var $li = $('<li></li>');
                    $label.appendTo($li);
                    $li.appendTo($ul_radios);
                 });
                 marker.unbindPopup().bindPopup($ul_radios.prop('outerHTML'), {offset: [0, -20]});
                 if (marker_on) {
                   var marker_icon = marker.getIcon();
                   var marker_html = $(marker_icon.options.html);
                   marker_html.find('.marker-icon').addClass('marker-icon-on');
                   marker_icon.options.html = marker_html.prop('outerHTML');
                 }
               }
             }
           });
           if (checked_lat !== null) {
             map.setView([checked_lat, checked_lng], map_options.zoom);
           } else if ($map_widget.data('def-template') || $map_widget.data('init_with_geoloc') == true) {
             // do not adjust map to fit markers as a specific location string
             // has been given.
           } else {
             map.fitBounds(geo_json.getBounds());
           }
           geo_json.addTo(map);

           map.on('popupopen', function(e) {
             var popup = e.popup;
             if (popup.marker) {
               // if popup is bound to a single marker, turn this on
               turn_marker_on(popup.marker.id, popup.marker.lat, popup.marker.lng, popup.marker.position_key);
             } else {
               // turn radio on
               var current_value = hidden_marker_id.val();
               $('input[type=radio][name="radio-' + radio_name + '"][value="' + current_value + '"]').prop('checked', true);
             }
           });

         }
       );
     } else if ($map_widget.data('readonly') && initial_marker_id) {
       // readonly and marker
         map.marker = L.marker(latlng);
         map.marker.addTo(map);
     }

     if (! $map_widget.data('readonly') && ! $map_widget.data('markers-url')) {
       map.on('click', function(e) {
         $map_widget.trigger('set-geolocation', e.latlng);
       });
     }

     if (typeof L.Control.Search != 'undefined' && $map_widget.data('search-url')) {
       var search_control = new L.Control.Search({
         labels: {
           hint: WCS_I18N.map_search_hint,
           error: WCS_I18N.map_search_error,
           searching: WCS_I18N.map_search_searching,
         },
         searchUrl: $map_widget.data('search-url')
       });
       map.addControl(search_control);
     }

     $map_widget.on('set-geolocation', function(e, coords, options) {
       if (map.marker === null) {
         map.marker = L.marker([0, 0], {alt: WCS_I18N.map_position_marker_alt});
         map.marker.addTo(map);
       }
       map.marker.setLatLng(coords);
       hidden.val(coords.lat + ';' + coords.lng);
       if (!(typeof(options) != 'undefined' && options.trigger === false)) {
         hidden.trigger('change');
       }
       $(document).trigger('wcs:set-last-auto-save');
     });
     $map_widget.on('qommon:invalidate', function() {
       map.invalidateSize();
     });
     var is_preview = $('.form-preview').length == 1;
     position_prefil = $map_widget.parent().parent().data('geolocation') == 'position';
     if (! ($map_widget.data('readonly') || is_preview) && ($map_widget.data('init_with_geoloc') || position_prefil)) {
       $map_widget.addClass('waiting-for-geolocation');
       $map_widget.removeClass('geolocation-error');
       map.on('locationfound', function(e) {
         $map_widget.removeClass('waiting-for-geolocation');
         $map_widget.parent().parent().find('label').removeClass('activity');
         if (map_options.maxBounds && ! map_options.maxBounds.contains(e.latlng)) {
           /* ouf of bounds, keep map centered on default position */
           return;
         }
         if (map.marker === null) {
           hidden.val(e.latlng.lat + ';' + e.latlng.lng);
           hidden.trigger('change');
           map.setView(e.latlng, map_options.zoom);
           if (position_prefil) {
             map.setView(e.latlng, 16);
             $map_widget.trigger('set-geolocation', e.latlng);
           }
         }
       });
       map.on('locationerror', function(e) {
         $map_widget.removeClass('waiting-for-geolocation');
         $map_widget.addClass('geolocation-error');
         var message = WCS_I18N.geoloc_unknown_error;
         if (e.code == 1) message = WCS_I18N.geoloc_permission_denied;
         else if (e.code == 2) message = WCS_I18N.geoloc_position_unavailable;
         else if (e.code == 3) message = WCS_I18N.geoloc_timeout;
         $map_widget.parent().parent().find('label').removeClass('activity');
         $map_widget.parent().parent().find('.geoloc-error').remove();
         $map_widget.parent().parent().find('label').after(' <span class="geoloc-error">' + message + '</span>');
       });
       gps_control.on('gps:located', function(e) {
         map.panTo(e.latlng);
         map.stopLocate();
         $map_widget.trigger('set-geolocation', e.latlng);
       });
       $map_widget.parent().parent().find('label').addClass('activity')
       gps_control.addLayer();
       map.locate({timeout: 10000, maximumAge: 300000, enableHighAccuracy: false});
     }

    $(document).on('backoffice-map-filter-change', function(event, listing_settings) {
      if ($('.leaflet-popup').length > 0 && listing_settings.auto == true) {
        /* disable autorefresh when a popup is open */
        return;
      }
      /* global map */
      $('.qommon-map:not([data-init-lat])').each(function() {
         var base_url = $(this).data('geojson-url').split('?')[0];
         map.eachLayer(function(layer) {
           if (layer.feature && layer.feature.type == 'Feature') {
             map.removeLayer(layer);
           }
         });
         $.getJSON(base_url + '?' + listing_settings.qs, function(data) {
           var geo_json = L.geoJson(data, {
             onEachFeature: function(feature, layer) {
               if (feature.properties.display_fields.length > 0) {
                 var popup = '';
                 $.each(feature.properties.display_fields, function(index, field) {
                   var $popup_field = $('<div><p class="popup-field"><span class="field-label"></span><span class="field-value"></span></p></div>');
                   $popup_field.find('.field-label').text(field.label);
                   if (field.html_value) {
                     $popup_field.find('.field-value').html(field.html_value);
                   } else {
                     $popup_field.find('.field-value').text(field.value);
                   }
                   popup += $popup_field.html();
                 });
               } else {
                   var popup = '<p class="popup-field formdata-name">' + feature.properties.name + '</p>';
                   popup += '<p class="popup-field status-name">' + feature.properties.status_name + '</p>';
               }
               popup += '<p class="view-link"><a href="' + feature.properties.url + '">' + feature.properties.view_label + '</a></p>';
               layer.bindPopup(popup);
             },
             pointToLayer: function (feature, latlng) {
                 var markerStyles = "background-color: "+feature.properties.status_colour+";";
                 marker = L.divIcon({iconAnchor: [0, 24],
                                     popupAnchor: [5, -36],
                                     html: '<span style="' + markerStyles + '" />'});
                 m = L.marker(latlng, {icon: marker});
                 return m;
             }
           });
           map.fitBounds(geo_json.getBounds());
           geo_json.addTo(map);
         });
      });
    });
    if ($(this).data('geojson-url')) {
        // trigger on initial load
        $(document).trigger('backoffice-map-filter-change', {qs: $('form#listing-settings').serialize()});
    }
  });
  $('.qommon-map').attr('data-map-ready', 'true');
  $(document).trigger('wcs:maps-ready');
});

$(window).on('load', function() {
  $(document).trigger('wcs:maps-init');
});
