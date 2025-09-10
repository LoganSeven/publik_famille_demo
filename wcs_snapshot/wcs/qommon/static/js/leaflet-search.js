/* global L, $ */
class SearchControl extends L.Control {
  options = {
    labels: {
      hint: 'Search adresses',
      error: 'An error occured while fetching results',
      searching: 'Searching...'
    },
    position: 'topright',
    searchUrl: '/api/geocoding',
    maxResults: 5
  }

  constructor (options) {
    super()
    L.Util.setOptions(this, options)
    this._refreshTimeout = 0
  }

  onAdd (map) {
    this._map = map
    this._container = L.DomUtil.create('div', 'leaflet-search')
    this._resultLocations = []
    this._selectedIndex = -1

    this._buttonBar = L.DomUtil.create('div', 'leaflet-bar', this._container)

    this._toggleButton = L.DomUtil.create('a', '', this._buttonBar)
    this._toggleButton.href = '#'
    this._toggleButton.role = 'button'
    this._toggleButton.style.fontFamily = 'FontAwesome'
    this._toggleButton.text = '\uf002'
    this._toggleButton.title = this.options.labels.hint
    this._toggleButton.setAttribute('aria-label', this.options.labels.hint)

    this._control = L.DomUtil.create('div', 'leaflet-search--control', this._container)
    this._control.style.visibility = 'collapse'

    this._searchInput = L.DomUtil.create('input', 'leaflet-search--input', this._control)
    this._searchInput.placeholder = this.options.labels.hint

    this._feedback = L.DomUtil.create('div', '', this._control)

    this._resultList = L.DomUtil.create('div', 'leaflet-search--result-list', this._control)
    this._resultList.style.visibility = 'collapse'
    this._resultList.tabIndex = 0
    this._resultList.setAttribute('aria-role', 'list')

    L.DomEvent
      .on(this._container, 'click', L.DomEvent.stop, this)
      .on(this._control, 'focusin', this._onControlFocusIn, this)
      .on(this._control, 'focusout', this._onControlFocusOut, this)
      .on(this._control, 'keydown', this._onControlKeyDown, this)
      .on(this._toggleButton, 'click', this._onToggleButtonClick, this)
      .on(this._searchInput, 'keydown', this._onSearchInputKeyDown, this)
      .on(this._searchInput, 'input', this._onSearchInput, this)
      .on(this._searchInput, 'mousemove', this._onSearchInputMove, this)
      .on(this._searchInput, 'touchmove', this._onSearchInputMove, this)
      .on(this._resultList, 'keydown', this._onResultListKeyDown, this)

    return this._container
  }

  onRemove (map) {
  }

  _showControl () {
    this._container.classList.add('open')
    this._buttonBar.style.visibility = 'collapse'
    this._control.style.removeProperty('visibility')
    this._initialBounds = this._map.getBounds()
    setTimeout(() => this._searchInput.focus(), 50)
  }

  _hideControl (resetBounds) {
    this._container.classList.remove('open')
    if (resetBounds) {
      this._map.fitBounds(this._initialBounds)
    }

    this._buttonBar.style.removeProperty('visibility')
    this._control.style.visibility = 'collapse'
    this._toggleButton.focus()
  }

  _onControlFocusIn (event) {
    clearTimeout(this._hideTimeout)
  }

  _onControlFocusOut (event) {
    // need to debounce here because leaflet raises focusout then focusin when
    // clicking on an already focused child element.
    this._hideTimeout = setTimeout(() => this._hideControl(), 50)
  }

  _getSelectedLocation () {
    if (this._selectedIndex === -1) {
      return null
    }

    return this._resultLocations[this._selectedIndex]
  }

  _focusLocation (location) {
    if (location.bounds !== undefined) {
      this._map.fitBounds(location.bounds)
    } else {
      this._map.panTo(location.latlng)
    }
  }

  _validateLocation (location) {
    this._focusLocation(location)
    this._hideControl()
  }

  _onSearchInputMove (event) {
    event.stopPropagation()
  }

  _onControlKeyDown (event) {
    if (event.keyCode === 27) { // escape
      this._hideControl(true)
      event.preventDefault()
    } else if (event.keyCode === 13) { // enter
      const selectedLocation = this._getSelectedLocation()
      if (selectedLocation) {
        this._validateLocation(selectedLocation)
      }
      event.preventDefault()
    }
  }

  _onToggleButtonClick () {
    this._showControl()
  }

  _selectIndex (index) {
    for (const resultItem of this._resultList.children) {
      resultItem.classList.remove('selected')
    }

    this._selectedIndex = index

    if (index === -1) {
      this._map.fitBounds(this._initialBounds)
      this._searchInput.focus()
    } else {
      this._focusLocation(this._resultLocations[index])
      const selectedElement = this._resultList.children[index]
      selectedElement.classList.add('selected')
      this._resultList.focus()
    }
  }

  _onSearchInputKeyDown (event) {
    const results = this._resultLocations
    if (results.length === 0) {
      return
    }

    if (event.keyCode === 38) {
      this._selectIndex(results.length - 1)
      event.preventDefault()
    } else if (event.keyCode === 40) {
      this._selectIndex(0)
      event.preventDefault()
    }
  }

  _clearResults () {
    while (this._resultList.lastElementChild) {
      this._resultList.removeChild(this._resultList.lastElementChild)
    }
    this._resultList.style.visibility = 'collapse'
    this._resultLocations = []
  }

  _fetchResults () {
    const searchString = this._searchInput.value

    if (!searchString) {
      return
    }

    this._clearResults()

    this._feedback.innerHTML = this.options.labels.searching
    this._feedback.classList.remove('error')

    $.ajax({
      url: this.options.searchUrl,
      data: { q: searchString },
      success: (data) => {
        this._feedback.innerHTML = ''
        this._resultLocations = []
        const firstResults = data.slice(0, this.options.maxResults)

        if (firstResults.length === 0) {
          return
        }

        this._resultList.style.removeProperty('visibility')

        for (const result of firstResults) {
          const resultItem = L.DomUtil.create('div', 'leaflet-search--result-item', this._resultList)
          resultItem.innerHTML = result.display_name
          resultItem.title = result.display_name
          resultItem.setAttribute('aria-role', 'list-item')
          L.DomEvent.on(resultItem, 'click', this._onResultItemClick, this)

          const itemLocation = {
            latlng: L.latLng(result.lat, result.lon)
          }

          const bbox = result.boundingbox

          if (bbox !== undefined) {
            itemLocation.bounds = L.latLngBounds(
              L.latLng(bbox[0], bbox[2]),
              L.latLng(bbox[1], bbox[3])
            )
          }

          this._resultLocations.push(itemLocation)
        }
      },
      error: () => {
        this._feedback.innerHTML = this.options.labels.error
        this._feedback.classList.add('error')
      }
    })
  }

  _onSearchInput () {
    clearTimeout(this._refreshTimeout)
    if (this._searchInput.value === '') {
      this._clearResults()
    } else {
      this._refreshTimeout = setTimeout(() => this._fetchResults(), 250)
    }
  }

  _onResultItemClick (event) {
    const elementIndex = Array.prototype.indexOf.call(this._resultList.children, event.target)
    this._selectIndex(elementIndex)
    const selectedLocation = this._getSelectedLocation()
    this._validateLocation(selectedLocation)
  }

  _onResultListKeyDown (event) {
    const results = this._resultLocations
    if (event.keyCode === 38) {
      this._selectIndex(this._selectedIndex - 1)
      event.preventDefault()
    } else if (event.keyCode === 40) {
      if (this._selectedIndex === results.length - 1) {
        this._selectIndex(-1)
      } else {
        this._selectIndex(this._selectedIndex + 1)
      }
      event.preventDefault()
    }
  }
}

Object.assign(SearchControl.prototype, L.Mixin.Events)

L.Control.Search = SearchControl
