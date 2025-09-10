/* adapted from https://github.com/stefanocudini/leaflet-gps
 *
 * Leaflet Control plugin for tracking gps position, with more options
 * by Stefano Cudini, stefano.cudini@gmail.com, http://labs.easyblog.it/
 * published under the MIT license.
 */

(function (factory) {
  if (typeof window.L === 'undefined')
    throw 'Leaflet must be loaded first';
  factory(window.L);
})(function (L) {

L.LocationIcon = L.Icon.extend({
	createIcon: function () {
		var div = document.createElement('div');
		div.className = 'location-icon';
		return div;
	},

	createShadow: function () {
		return null;
	}
});

L.Control.Gps = L.Control.extend({
	includes: L.Mixin.Events,
	options: {
		style: {
			radius: 5,
			weight: 2,
			color: '#c20',
			opacity: 1,
			fillColor: '#f23',
			fillOpacity: 1
		},
		position: 'topleft',
		tooltipTitle: 'Display my position'
	},

	initialize: function(options) {
		if(options && options.style)
			options.style = L.Util.extend({}, this.options.style, options.style);
		L.Util.setOptions(this, options);
		this._isActive = false; //global state of gps
		this._firstMoved = false; //global state of gps
		this._currentLocation = null; //store last location
	},

	onAdd: function (map) {
		this._map = map;

		this._container = L.DomUtil.create('div', 'leaflet-control-gps leaflet-bar');

		this._button = L.DomUtil.create('a', 'gps-button', this._container);
		this._button.href = '#';
		this._button.text = '\uf192';
		this._button.title = this.options.tooltipTitle;
		this._button.style.fontFamily = 'FontAwesome';
		L.DomEvent
			.on(this._button, 'click', L.DomEvent.stop, this)
			.on(this._button, 'click', this._askGps, this);

		var locationIcon = new L.LocationIcon();
		this._gpsMarker = new L.Marker([0,0], {icon: locationIcon});

		this._map
			.on('locationfound', this._drawGps, this)
			.on('locationerror', this._errorGps, this);

		return this._container;
	},

	onRemove: function(map) {
		this.deactivate();
	},

	_askGps: function() {
		this._firstMoved = false;
		this._container.classList.add('pending');
		this._map._container.classList.remove('geolocation-error');
		this._map._container.classList.add('waiting-for-geolocation');
		this.activate();
	},

	getLocation: function() {
		return this._currentLocation;
	},

	addLayer: function() {
		this._map.addLayer(this._gpsMarker);
	},

	activate: function() {
		this._isActive = true;
		this.addLayer();
		this._map.locate({
			enableHighAccuracy: true,
			maximumAge: 120000,
			watch: true,
			setView: false,
			maxZoom: null
		});
	},

	deactivate: function() {
		this._container.classList.remove('pending');
		this._map._container.classList.remove('geolocation-error');
		this._map._container.classList.remove('waiting-for-geolocation');
		this._isActive = false;
		this._firstMoved = false;
		this._map.stopLocate();
		this._map.removeLayer( this._gpsMarker );
		this.fire('gps:disabled');
	},

	_drawGps: function(e) {
		this._container.classList.remove('pending');
		this._map._container.classList.remove('geolocation-error');
		this._map._container.classList.remove('waiting-for-geolocation');
		this._currentLocation = e.latlng;

		this._gpsMarker.setLatLng(this._currentLocation);

		if(this._isActive && !this._firstMoved) {
			this._moveTo(this._currentLocation);
			this._map.stopLocate();
		}

                if (this._isActive) {
			this.fire('gps:located', {latlng: this._currentLocation, marker: this._gpsMarker});
                }
	},

	_moveTo: function(latlng) {
		this._firstMoved = true;
		this._map.panTo(latlng);
	},

	_errorGps: function(e) {
		this.deactivate();
		this._map._container.classList.add('geolocation-error');
	},

});

L.Map.addInitHook(function () {
	if (this.options.gpsControl) {
		this.gpsControl = L.control.gps(this.options.gpsControl);
		this.addControl(this.gpsControl);
	}
});

L.control.gps = function (options) {
	return new L.Control.Gps(options);
};

return L.Control.Gps;

});
