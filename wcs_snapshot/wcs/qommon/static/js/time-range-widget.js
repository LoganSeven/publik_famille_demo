class DaysSlider {
  constructor (sliderEl) {
    this.scrollable = sliderEl.querySelector('.TimeRange--days-list')

    const controls = {
      prev: sliderEl.querySelector('.TimeRange--slider-control.prev'),
      next: sliderEl.querySelector('.TimeRange--slider-control.next'),
    }

    const observerOptions = {
      root: this.scrollable,
      rootMargin: '0px',
      threshold: 0.9,
    }

    const observerCallback = (entries) => {
      entries.forEach(entry => {
        entry.target.classList.toggle('on-view', entry.isIntersecting)
      })
      controls.prev.hidden = this.items[0].classList.contains('on-view')
      controls.next.hidden = this.items[this.items.length - 1].classList.contains('on-view')
    }

    this.observer = new IntersectionObserver(observerCallback, observerOptions)

    this.scrollOptions = {
      behavior: 'smooth',
      block: 'nearest',
    }

    controls.next.addEventListener('click', e => {
      e.preventDefault()
      const nextItem = [...this.items].find((element, index, array) => {
        return array[index - 1] && !element.classList.contains('on-view') && array[index - 1].classList.contains('on-view')
      })
      this.scrollTo(nextItem)
    })

    controls.prev.addEventListener('click', e => {
      e.preventDefault()
      const prevItem = [...this.items].find((element, index, array) => {
        return array[index + 1] && !element.classList.contains('on-view') && array[index + 1].classList.contains('on-view')
      })
      this.scrollTo(prevItem)
    })
  }

  scrollTo (element) {
    if (element) { element.scrollIntoView(this.scrollOptions) }
  }

  init () {
    this.items = this.scrollable.querySelectorAll('li')
    this.items.forEach(item => this.observer.observe(item))
  }
}


class TimeRangeWidget {
  constructor (widgetId) {
    this.widget = document.querySelector('.TimeRangeWidget[data-field-id="' + widgetId + '"]')
    this.startHourSelect = this.widget.querySelector('select[name="f' + widgetId + '$start_hour"]')
    this.endHourSelect = this.widget.querySelector('select[name="f' + widgetId + '$end_hour"]')

    this.uiWrapper = this.widget.querySelector('.TimeRange')
    this.daysGroup = this.uiWrapper.querySelector('.TimeRange--days-group')
    this.daysList = this.uiWrapper.querySelector('.TimeRange--days-list')
    this.daysRadios = this.daysList.querySelectorAll('.TimeRange--day-radio')
    this.slotsGroup = this.uiWrapper.querySelector('.TimeRange--slots-group')
    this.slotsList = this.uiWrapper.querySelector('.TimeRange--slots-list')
    this.slotDayLabel = this.uiWrapper.querySelector('.TimeRange--slots-day')
    this.slotTemplate = this.widget.querySelector('.TimeRange--slot-item-template')
    this.noDaysMessage = this.widget.querySelector('.TimeRange--no-days-message')

    this.selectedHours = undefined

    this.slider = new DaysSlider(this.uiWrapper.querySelector('.TimeRange--slider'))

    this.daysList.addEventListener('wcs:options-change', (e) => {
      this.daysRadios = this.daysList.querySelectorAll('.TimeRange--day-radio')

      if (!this.hasDays())
        return

      this.daysRadios.forEach((radio, i) => {
        radio.dataset.openingHours = JSON.stringify(e.detail[i]['attributes']['opening_hours'])
        radio.dataset.verboseLabel = e.detail[i]['attributes']['verbose_label']
      })
      this.init()
    })

    if (!this.hasDays())
      return

    this.init()
  }

  hasDays () {
    if (!this.daysRadios[0].value) {
      this.noDaysMessage.hidden = false
      this.daysGroup.hidden = true
      this.cleanSlots()
      return false
    } else {
      this.noDaysMessage.hidden = true
      this.daysGroup.hidden = false
      return true
    }
  }

  init () {
    this.slider.init()
    if (!this.selectedHours)
      this.selectedHours = this.getSelectedHours()

    this.daysRadios.forEach(radio => {
      radio.addEventListener('change', (e) => {
        this.displaySlots(e.target)
      })
      if (radio.checked) {
        this.slider.scrollTo(radio.closest('.TimeRange--days-list > li'))
        this.displaySlots(radio)
      }
    })

    this.startHourSelect.addEventListener('change', (e) => {
      this.filterEndHours(e.target.selectedOptions[0])
      this.selectedHours = this.getSelectedHours()
    })

    this.endHourSelect.addEventListener('change', () => {
      this.selectedHours = this.getSelectedHours()
    })
  }

  getSelectedHours () {
    if (this.startHourSelect.value && this.endHourSelect.value)
      return {
        startHour: this.startHourSelect.value,
        endHour: this.endHourSelect.value,
      }
  }

  displaySlots (selectedDay) {
    this.cleanSlots()

    const hourData = JSON.parse(selectedDay.dataset.openingHours)

    let hours = []
    let startHour = hourData[0]['hour']
    let slotStatus = hourData[0]['status']

    hourData.forEach(data => {
      hours.push(data['hour'])
      if (slotStatus !== data['status']) {
        this.addSlot(startHour, data['hour'], slotStatus, hours)
        slotStatus = data['status']
        startHour = data['hour']
        hours = [startHour]
      }
    })

    this.filterStartHours()
    this.filterEndHours(this.startHourSelect.selectedOptions[0])

    this.slotDayLabel.innerText = selectedDay.dataset.verboseLabel
    this.slotsGroup.hidden = false
  }

  cleanSlots () {
    this.slotsGroup.hidden = true
    this.slotsList.innerHTML = ''
    this.startHourSelect.innerHTML = ''
    this.endHourSelect.innerHTML = ''
  }

  addSlot (start, end, bookedStatus, hours) {
    let slot = document.importNode(this.slotTemplate.content, true)
    const slotItem = slot.querySelector('.TimeRange--slot-item')
    const slotLabel = slot.querySelector('.TimeRange--slot-label')
    slotItem.dataset.start = start
    slotItem.dataset.end = end
    slotItem.style.flexGrow = hours.length - 1
    slotLabel.title = slotLabel.title.replace('__start__', start).replace('__end__', end)
    if (bookedStatus === 'booked') slotLabel.classList.add('disabled')
    if (bookedStatus === 'closed') slotLabel.classList.add('blank')
    this.slotsList.appendChild(slot)
    if (bookedStatus === 'free') {
      this.fillHours(this.startHourSelect, start, end, hours.slice(0, -1))
      this.fillHours(this.endHourSelect, start, end, hours.slice(1))
    }
  }

  fillHours (widget, start, end, hours) {
    let optGroup = document.createElement('optgroup')

    const label = this.uiWrapper.dataset.optGroupLabel
    optGroup.label = label.replace('__start__', start).replace('__end__', end)
    optGroup.dataset.range = start + '-' + end

    hours.forEach(hour => {
      const option = document.createElement('option')
      option.value = hour
      option.innerText = hour
      if (this.selectedHours) {
        if (widget === this.startHourSelect && this.selectedHours.startHour === hour) {
          option.selected = true
        }
        if (widget === this.endHourSelect && this.selectedHours.endHour === hour) {
          option.selected = true
        }
      }
      optGroup.appendChild(option)
    })
    widget.appendChild(optGroup)
  }

  filterStartHours () {
    const minimalBookingSlots = parseInt(this.uiWrapper.dataset.minimalBookingSlots)

    if (!minimalBookingSlots)
      return

    // disable last options from each option group
    this.startHourSelect.querySelectorAll('optgroup').forEach(optgroup => {
      Array.from(optgroup.children).reverse().forEach((option, i) => {
        if (i < minimalBookingSlots - 1)
          option.disabled = true
      })
    })
  }

  filterEndHours (startHourOption) {
    if (!startHourOption) return

    const startIndex = startHourOption.index;
    const endHoursOptions = Array.from(this.endHourSelect.options)
    const minimalBookingSlots = parseInt(this.uiWrapper.dataset.minimalBookingSlots)
    const maximalBookingSlots = parseInt(this.uiWrapper.dataset.maximalBookingSlots)

    let minimalIndex = startIndex
    if (minimalBookingSlots)
      minimalIndex += minimalBookingSlots - 1

    // disable not allowed end hour options
    endHoursOptions.forEach(option => {
      if (
        (option.parentElement.dataset.range === startHourOption.parentElement.dataset.range)
        && (!maximalBookingSlots || option.index < startIndex + maximalBookingSlots)
        && (option.index >= minimalIndex)
      ) {
        option.disabled = false
      } else {
        option.disabled = true
      }
    })

    if (this.endHourSelect.selectedOptions.length && this.endHourSelect.selectedOptions[0].disabled) {
      const firstEnabledOption = this.endHourSelect.querySelector('option:enabled')
      this.endHourSelect.selectedIndex = endHoursOptions.indexOf(firstEnabledOption)
    }
  }
}
